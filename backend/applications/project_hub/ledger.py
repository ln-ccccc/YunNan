"""项目推理成果台账导出（优化建议五.3：按项目自动汇总台账报表）。"""

import datetime
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

from openpyxl import Workbook
from sqlalchemy import bindparam, text
from sqlalchemy.orm import load_only

from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.inference_job import InferenceJob

VECTOR_STATUS_LABELS = {
    "ready": "已出矢量",
    "ready_empty": "矢量空",
    "vector_failed": "矢量失败",
}


# 台账抽取的并行度：按 id 均分切片，每个切片一个数据库连接（用完即还）。
LEDGER_EXTRACT_WORKERS = max(1, int(os.getenv("LEDGER_EXTRACT_WORKERS", "4")))
# MySQL "Invalid JSON text"：快路径不预先做全文校验，真遇到非法文本再退回带校验的慢路径
_MYSQL_INVALID_JSON_ERROR = 3141


def _is_invalid_json_error(error):
    return getattr(getattr(error, "orig", None), "args", (None,))[0] == _MYSQL_INVALID_JSON_ERROR


def _fetch_rows(engine, statement, params):
    """独立连接执行一条只读抽取。并行切片时每个线程各用自己的连接，不碰请求会话。

    engine 必须由调用线程解析后传入：Flask-SQLAlchemy 的 db.engine 要经当前应用上下文
    解析，工作线程里没有上下文，直接在里面写 db.engine 会抛 "No application found"
    （生产库活体探针抓到，SQLite 单测走不到并行路径）。"""
    with engine.connect() as connection:
        return connection.execute(statement, params).fetchall()


def _query_in_shards(engine, build_query, ids):
    """把 id 均分成若干片并行查询后合并；只有一片时直接串行。

    为什么在数据库侧解析：逐成果只需要几个标量或一个小数组，但它们埋在 MEDIUMTEXT 里
    （项目 10 成果矢量合计 711MB、推理任务 result_json 合计 1.4GB）——整行拉回应用侧
    同样要付这笔解析成本，外加传输。而 MySQL 每调用一次 JSON 函数就把整篇文本重新解析
    一遍（生产库实测：成果单遍约 3.5 秒、任务结果单遍约 13 秒），所以：
    - 每个 JSON 列只调用一次 JSON_EXTRACT（类别名称由类别码按固定类别表推出，
      存量 454 条成果抽样 100% 一致）；
    - 不在 WHERE 里预先 JSON_VALID（那又是一遍全文解析）——数据都是 json.dumps 写出的
      合法文本，遇到非法文本（3141）由调用方退回带校验的慢路径跳过坏行；
    - 按 id 均分切片并行：MySQL 侧是 CPU 解析，4 核开 4 个连接。
    """
    ids = list(ids)
    if not ids:
        return []
    workers = max(1, min(int(LEDGER_EXTRACT_WORKERS), len(ids)))
    quotient, remainder = divmod(len(ids), workers)
    # 均分：前 remainder 片各多一个（10 个 id 切 4 片是 3/3/2/2，不是 3/3/3/1）
    sizes = [quotient + (1 if index < remainder else 0) for index in range(workers)]
    shards = []
    offset = 0
    for size in sizes:
        shards.append(ids[offset:offset + size])
        offset += size
    if len(shards) == 1:
        return _fetch_rows(engine, *build_query(shards[0]))
    with ThreadPoolExecutor(max_workers=len(shards)) as pool:
        return [
            row
            for rows in pool.map(lambda shard: _fetch_rows(engine, *build_query(shard)), shards)
            for row in rows
        ]


def _extract_with_json_fallback(engine, build_query, ids):
    """快路径抽取；只有 MySQL 报"非法 JSON 文本"(3141)时才退回带 JSON_VALID 的慢路径。

    build_query(shard, guarded) 返回 (statement, params)。其余异常原样抛出。"""
    try:
        return _query_in_shards(engine, lambda shard: build_query(shard, False), ids)
    except Exception as error:  # noqa: BLE001 - 仅非法 JSON 文本回退，其余照常抛出
        if not _is_invalid_json_error(error):
            raise
        return _query_in_shards(engine, lambda shard: build_query(shard, True), ids)


def _to_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed
    return None


def _timings_from_extracted(extracted):
    """把一次 JSON_EXTRACT(result_json, '$.stage_durations', '$.total_seconds') 的结果
    拆成 (推理秒数, 总秒数)。MySQL 把多路径结果摊平成数组且缺失的路径不占位，
    所以按值的类型区分：对象是阶段计时、数字是总耗时。"""
    try:
        values = json.loads(extracted) if isinstance(extracted, (str, bytes)) else extracted
    except (TypeError, ValueError):
        return None, None
    if values is None:
        return None, None
    if not isinstance(values, list):
        values = [values]
    inference = total = None
    for value in values:
        if isinstance(value, dict):
            inference = _to_number(value.get("inference"))
        elif not isinstance(value, list):
            total = _to_number(value)
    return inference, total


def _prefetch_job_timings(job_ids):
    """批量预取推理计时：{job_id: (推理秒数, 总秒数)}，缺失为 None。

    result_json 单条均值 2.4MB、项目 10 关联 585 个任务共 1.42GB——逐任务 Query.get 是
    GB 级传输（曾实测 389 秒）。此前改成服务端抽取后仍要 38 秒：WHERE 里的 JSON_VALID
    加两次 JSON_EXTRACT 把每篇文本解析了三遍；现在一遍、四路并行。
    非 MySQL 方言（测试环境）逐个读取后解析。
    """
    timings = {}
    unique_ids = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
    if not unique_ids:
        return timings
    if db.engine.dialect.name != "mysql":
        for job_id in unique_ids:
            job = InferenceJob.query.get(job_id)
            if job is None or not job.result_json:
                continue
            try:
                result = json.loads(job.result_json)
            except (TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            durations = result.get("stage_durations")
            timings[str(job_id)] = (
                _to_number(durations.get("inference")) if isinstance(durations, dict) else None,
                _to_number(result.get("total_seconds")),
            )
        return timings

    def build_query(shard, guarded):
        guard = "AND JSON_VALID(result_json)" if guarded else ""
        statement = text(
            "SELECT id, JSON_EXTRACT(result_json, '$.stage_durations', '$.total_seconds') "
            "FROM inference_jobs WHERE id IN :ids AND result_json IS NOT NULL " + guard
        ).bindparams(bindparam("ids", expanding=True))
        return statement, {"ids": shard}

    # engine 在有应用上下文的调用线程里解析，线程内只认引擎对象
    rows = _extract_with_json_fallback(db.engine, build_query, unique_ids)
    for job_id, extracted in rows:
        timings[str(job_id)] = _timings_from_extracted(extracted)
    return timings


def _job_timings(inference_job_id, _timings_cache=None):
    """从关联推理任务的 result_json 取逐阶段计时（性能优化的数据来源）。"""
    empty = (None, None)
    if not inference_job_id:
        return empty
    if _timings_cache is not None:
        return _timings_cache.get(str(inference_job_id), empty)
    job = InferenceJob.query.get(inference_job_id)
    if job is None or not job.result_json:
        return empty
    try:
        result = json.loads(job.result_json)
        durations = result.get("stage_durations") or {}
        return durations.get("inference"), result.get("total_seconds")
    except (TypeError, ValueError):
        return empty


# 台账是给人看的 Excel：DB 时间为 UTC，输出统一转北京时间（UTC+8）并标注，
# 避免与平台页面显示（formatDateTime 按 Z 换算）出现 8 小时口径差
_LEDGER_TZ_OFFSET = datetime.timedelta(hours=8)


def _format_time(value):
    if isinstance(value, datetime.datetime):
        return (value + _LEDGER_TZ_OFFSET).strftime("%Y-%m-%d %H:%M:%S")
    return value


def _iter_class_counts(feature_collection_json):
    try:
        collection = json.loads(feature_collection_json or "null")
    except (TypeError, ValueError):
        return
    # vector_failed 等状态的成果 current_feature_collection_json 为 NULL，
    # json.loads("null") 得到 None，必须跳过而非 .get 崩溃
    if not isinstance(collection, dict):
        return
    for feature in collection.get("features") or []:
        properties = feature.get("properties") or {}
        yield properties.get("class_code"), properties.get("class_name")


# 台账单次生成的时间预算（秒）。后端必须在链路上任何一跳放弃之前给出明确结果并清理
# 半成品，才不会出现"前端已看到失败、后端仍在生成并最终落一条 completed 记录、用户
# 重试又建一份"（2026-11-24 审计 D1）。取 4 分钟：短于 Node 默认的请求超时
# （requestTimeout 5 分钟，BFF 与静态站代理都没显式改）与 BFF 的 10 分钟；
# 抽取改为一遍解析并行后项目 10 的台账是秒级，正常碰不到它
LEDGER_TIME_BUDGET_SECONDS = int(os.getenv("EXPORT_TIME_BUDGET_SECONDS", "240"))


class LedgerBudgetExceeded(RuntimeError):
    """台账生成超出时间预算——调用方据此把导出记录落为 failed 并清理目录。"""


def collect_project_results(project):
    """台账数据快照：工作簿内容与导出 manifest 来源共用同一次查询，避免内容与
    清单分别读取后口径漂移（2026-11-14 审查问题31，自 main 移植）。

    只取台账与 manifest 用得上的九个标量列（load_only）。矢量 JSON 不在这里取：
    项目 10 的 2979 条成果 current+auto 矢量合计 1.43GB，台账只需要每个图斑的
    类别码——由 _prefetch_class_counts 在数据库侧抽取。台账与 _ledger_result_sources
    访问的列必须都在此列表内，否则 deferred 列会退化成逐行查询。
    """
    return (
        ClassificationResult.query.filter_by(project_id=project.id)
        .options(
            load_only(
                ClassificationResult.id,
                ClassificationResult.mine_fid,
                ClassificationResult.year,
                ClassificationResult.vector_status,
                ClassificationResult.model_id,
                ClassificationResult.inference_job_id,
                ClassificationResult.create_time,
                ClassificationResult.current_revision_no,
                ClassificationResult.mine_resource_version,
            )
        )
        .order_by(ClassificationResult.mine_fid.asc(), ClassificationResult.year.asc())
        .all()
    )


def _tally(counts, result_id, pairs):
    pairs = list(pairs)
    if not pairs:
        return  # 没有图斑（矢量失败/已清空）的成果不留空条目，两种方言口径一致
    tally = counts.setdefault(result_id, {})
    for key in pairs:
        tally[key] = tally.get(key, 0) + 1


def _class_name_of(class_code):
    from applications.kml_roi.vectorization.classification import CLASS_DEFINITIONS

    definition = CLASS_DEFINITIONS.get(class_code) if isinstance(class_code, int) and not isinstance(class_code, bool) else None
    return definition["class_name"] if definition else None


def _pairs_from_codes(codes):
    """JSON_EXTRACT 取回的类别码数组 -> [(class_code, class_name)]。空成果/无类别属性时为 None。"""
    try:
        values = json.loads(codes) if isinstance(codes, (str, bytes)) else codes
    except (TypeError, ValueError):
        return []
    if values is None:
        return []
    if not isinstance(values, list):
        values = [values]
    return [(code, _class_name_of(code)) for code in values]


def _prefetch_class_counts(project_id):
    """逐成果的地类图斑计数：{result_id: {(class_code, class_name): count}}（按首次出现排序）。

    台账慢的真正来源：逐成果计数只需要每个图斑的类别码，此前却把成果整行（含矢量
    MEDIUMTEXT）拉回应用侧解析；改成数据库侧抽取后仍要 14 秒，因为校验加两次抽取把
    每篇解析了三遍。现在只抽类别码、一遍、四路并行（原因见 _query_in_shards）。
    非 MySQL 方言（测试环境）只取所需两列后在应用侧解析。
    """
    counts = {}
    result_ids = [
        row_id for (row_id,) in db.session.query(ClassificationResult.id).filter_by(project_id=project_id).all()
    ]
    if not result_ids:
        return counts
    if db.engine.dialect.name != "mysql":
        rows = (
            db.session.query(ClassificationResult.id, ClassificationResult.current_feature_collection_json)
            .filter(ClassificationResult.id.in_(result_ids))
            .all()
        )
        for result_id, collection_json in rows:
            # 与 MySQL 路径同源：类别名由类别码按固定类别表推出，不取矢量里存的名称
            codes = [class_code for class_code, _stored_name in _iter_class_counts(collection_json)]
            _tally(counts, result_id, _pairs_from_codes(codes))
        return counts

    def build_query(shard, guarded):
        guard = "AND JSON_VALID(current_feature_collection_json)" if guarded else ""
        statement = text(
            "SELECT id, JSON_EXTRACT(current_feature_collection_json, '$.features[*].properties.class_code') "
            "FROM classification_results WHERE id IN :ids " + guard
        ).bindparams(bindparam("ids", expanding=True))
        return statement, {"ids": shard}

    # engine 在有应用上下文的调用线程里解析，线程内只认引擎对象
    rows = _extract_with_json_fallback(db.engine, build_query, result_ids)
    for result_id, codes in rows:
        _tally(counts, result_id, _pairs_from_codes(codes))
    return counts


def build_project_ledger_workbook(project, results=None, class_counts=None):
    if results is None:
        results = collect_project_results(project)
    started = time.monotonic()

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "推理成果台账"
    sheet.append(["成果ID", "矿山FID", "年份", "矢量化状态", "模型", "推理任务ID", "生成时间(北京时间)", "推理耗时(秒)", "总耗时(秒)"])

    class_sheet = workbook.create_sheet("地类图斑统计")
    class_sheet.append(["成果ID", "矿山FID", "年份", "类别代码", "类别名称", "图斑数量"])

    timings_cache = _prefetch_job_timings([result.inference_job_id for result in results])
    if class_counts is None:
        class_counts = _prefetch_class_counts(project.id)
    for index, result in enumerate(results):
        if index % 200 == 0 and time.monotonic() - started > LEDGER_TIME_BUDGET_SECONDS:
            raise LedgerBudgetExceeded(
                f"台账生成超过 {LEDGER_TIME_BUDGET_SECONDS}s 时间预算（已处理 {index}/{len(results)} 条成果）"
            )
        inference_seconds, total_seconds = _job_timings(result.inference_job_id, timings_cache)
        sheet.append([
            result.id,
            result.mine_fid,
            result.year,
            VECTOR_STATUS_LABELS.get(result.vector_status, result.vector_status),
            result.model_id,
            result.inference_job_id,
            _format_time(result.create_time),
            inference_seconds,
            total_seconds,
        ])
        for (class_code, class_name), count in class_counts.get(result.id, {}).items():
            class_sheet.append([result.id, result.mine_fid, result.year, class_code, class_name, count])

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream
