"""项目推理成果台账导出（优化建议五.3：按项目自动汇总台账报表）。"""

import datetime
import io
import json

from openpyxl import Workbook

from applications.models.classification_result import ClassificationResult

VECTOR_STATUS_LABELS = {
    "ready": "已出矢量",
    "ready_empty": "矢量空",
    "vector_failed": "矢量失败",
}


def _format_time(value):
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def _iter_class_counts(feature_collection_json):
    try:
        collection = json.loads(feature_collection_json or "null")
    except (TypeError, ValueError):
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
    sheet.append(["成果ID", "矿山FID", "年份", "矢量化状态", "模型", "推理任务ID", "生成时间"])

    class_sheet = workbook.create_sheet("地类图斑统计")
    class_sheet.append(["成果ID", "矿山FID", "年份", "类别代码", "类别名称", "图斑数量"])

    for result in results:
        sheet.append([
            result.id,
            result.mine_fid,
            result.year,
            VECTOR_STATUS_LABELS.get(result.vector_status, result.vector_status),
            result.model_id,
            result.inference_job_id,
            _format_time(result.create_time),
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
