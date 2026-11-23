"""项目推理成果台账导出（优化建议五.3：按项目自动汇总台账报表）。"""

import datetime
import io
import json
import os
import time

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


def _prefetch_job_timings(job_ids):
    """批量预取推理计时（2026-11-24 修复：项目 10 台账实测 556s）。

    result_json 单条均 2.4MB/最大 12.8MB、项目 10 关联 585 个 job 共 1.42GB
    ——逐成果 Query.get 是 GB 级传输（389s）且随成果数恶化。改为服务端
    JSON_EXTRACT 只取两个标量（KB 级传输，MySQL/SQLite 通用路径语法）；
    result_json 非法的行以 JSON_VALID 过滤跳过，与原 try/except 容错同口径。
    """
    timings = {}
    unique_ids = [job_id for job_id in dict.fromkeys(job_ids) if job_id]
    if not unique_ids:
        return timings
    stmt = text(
        "SELECT id, "
        "JSON_EXTRACT(result_json, '$.\"stage_durations\".\"inference\"') AS inference_seconds, "
        "JSON_EXTRACT(result_json, '$.\"total_seconds\"') AS total_seconds "
        "FROM inference_jobs "
        "WHERE id IN :ids AND JSON_VALID(result_json)"
    ).bindparams(bindparam("ids", expanding=True))
    for start in range(0, len(unique_ids), 200):
        rows = db.session.execute(stmt, {"ids": unique_ids[start:start + 200]}).fetchall()
        for job_id, inference_seconds, total_seconds in rows:
            def _num(value):
                try:
                    parsed = json.loads(value) if isinstance(value, str) else value
                    return float(parsed) if isinstance(parsed, (int, float)) else None
                except (TypeError, ValueError):
                    return None
            timings[str(job_id)] = (_num(inference_seconds), _num(total_seconds))
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
# 服务端抽取类别码后项目 10 的台账是秒级，正常永远碰不到它
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
    tally = counts.setdefault(result_id, {})
    for key in pairs:
        tally[key] = tally.get(key, 0) + 1


def _pairs_from_arrays(codes_json, names_json):
    """把 JSON_EXTRACT 取回的两个数组配对成 (class_code, class_name)；对不齐返回 None。"""
    try:
        codes = json.loads(codes_json) if isinstance(codes_json, (str, bytes)) else codes_json
        names = json.loads(names_json) if isinstance(names_json, (str, bytes)) else names_json
    except (TypeError, ValueError):
        return None
    if codes is None and names is None:
        return []
    if not isinstance(codes, list) or not isinstance(names, list) or len(codes) != len(names):
        return None
    return list(zip(codes, names))


def _prefetch_class_counts(project_id):
    """逐成果的地类图斑计数：{result_id: {(class_code, class_name): count}}（按首次出现排序）。

    这是台账 55 秒的真正来源：逐成果地类计数只需要每个图斑的类别码/名称，此前却把
    成果整行（含三个 MEDIUMTEXT 矢量大字段）全部拉回应用侧再解析——项目 10 实测
    2979 条成果共 1.43GB，而数据库侧抽取后的全部结果只有几 MB。
    MySQL 走服务端 JSON_EXTRACT（通配路径返回数组；单要素也是数组、空数组为 NULL）；
    个别行两个数组长度不一致（要素缺 class_name）时只对这一行回退到应用侧解析。
    SQLite（测试环境）不支持该通配路径，整体回退为只取所需列后在应用侧解析。
    """
    counts = {}
    fallback_ids = []
    if db.engine.dialect.name == "mysql":
        stmt = text(
            "SELECT id, "
            "JSON_EXTRACT(current_feature_collection_json, '$.features[*].properties.class_code') AS codes, "
            "JSON_EXTRACT(current_feature_collection_json, '$.features[*].properties.class_name') AS names "
            "FROM classification_results "
            "WHERE project_id = :project_id AND JSON_VALID(current_feature_collection_json)"
        ).bindparams(bindparam("project_id"))
        for result_id, codes_json, names_json in db.session.execute(stmt, {"project_id": project_id}).fetchall():
            pairs = _pairs_from_arrays(codes_json, names_json)
            if pairs is None:
                fallback_ids.append(result_id)
                continue
            _tally(counts, result_id, pairs)
        if not fallback_ids:
            return counts
    else:
        fallback_ids = [
            row_id for (row_id,) in db.session.query(ClassificationResult.id).filter_by(
                project_id=project_id
            ).all()
        ]
    rows = (
        db.session.query(ClassificationResult.id, ClassificationResult.current_feature_collection_json)
        .filter(ClassificationResult.id.in_(fallback_ids))
        .all()
        if fallback_ids
        else []
    )
    for result_id, collection_json in rows:
        _tally(counts, result_id, _iter_class_counts(collection_json))
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
