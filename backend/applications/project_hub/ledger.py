"""项目推理成果台账导出（优化建议五.3：按项目自动汇总台账报表）。"""

import datetime
import io
import json

from openpyxl import Workbook
from sqlalchemy import bindparam, text

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


def build_project_ledger_workbook(project):
    results = (
        ClassificationResult.query.filter_by(project_id=project.id)
        .order_by(ClassificationResult.mine_fid.asc(), ClassificationResult.year.asc())
        .all()
    )

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "推理成果台账"
    sheet.append(["成果ID", "矿山FID", "年份", "矢量化状态", "模型", "推理任务ID", "生成时间(北京时间)", "推理耗时(秒)", "总耗时(秒)"])

    class_sheet = workbook.create_sheet("地类图斑统计")
    class_sheet.append(["成果ID", "矿山FID", "年份", "类别代码", "类别名称", "图斑数量"])

    timings_cache = _prefetch_job_timings([result.inference_job_id for result in results])
    for result in results:
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
        counts = {}
        order = []
        for class_code, class_name in _iter_class_counts(result.current_feature_collection_json):
            key = (class_code, class_name)
            if key not in counts:
                counts[key] = 0
                order.append(key)
            counts[key] += 1
        for key in order:
            class_code, class_name = key
            class_sheet.append([result.id, result.mine_fid, result.year, class_code, class_name, counts[key]])

    stream = io.BytesIO()
    workbook.save(stream)
    stream.seek(0)
    return stream
