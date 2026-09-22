"""图斑溯源聚合（M3，2026-09-22）：按矿山一键调取历年全链数据。

GET /api/projects/<id>/mines/<fid>/traceability 一次返回：
- years[]: 按年条目（成果图 URL/result_id/vector_status/feature_count/类别占比/
           修订次数），同年多任务取最新成功 job 的成果（计划 §4.5）
- ratio_series: class_ratio_percent.json 的多年占比序列（直读，绕过 fid+ 前缀
  静态路由的命名限制）
- change: 最近两期变化矩阵（outputs/change_matrix/<fid>.json）
- indices: NDVI 等指数时序（outputs/indices/<fid>.json）
- 原始影像引用复用 list_project_original_imagery（M2 已有）
"""
import json
import logging
from pathlib import Path

from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
)
from applications.models.project import Project
from applications.project_hub.project_map import (
    list_project_original_imagery,
    _project as _get_project,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)


def _read_json_file(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _years_payload(project, fid_value):
    """按年条目：同年多任务取最新（id 最大）成果；附修订时间线与占比。"""
    rows = (
        ClassificationResult.query.filter_by(project_id=project.id, mine_fid=fid_value)
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest_by_year = {}
    for result in rows:
        # 同年多任务取最新"成功"成果：矢量化失败的新行不应抹掉既有好成果
        # （收官审查 P2：一次失败曾让溯源/看板/清单四端同年度归零）
        if result.year in latest_by_year:
            continue
        if result.vector_status == "vector_failed" and any(
            other.year == result.year and other.vector_status != "vector_failed"
            for other in rows
        ):
            continue
        latest_by_year[result.year] = result

    inference_root = _project_output_root(project.id) / "inference" / str(fid_value)
    ratio_doc = _read_json_file(inference_root / "class_ratio_percent.json") or {}
    ratio_years = ratio_doc.get("years") or []

    years = []
    for year in sorted(latest_by_year):
        result = latest_by_year[year]
        base = f"{fid_value}+{year}"
        audits = (
            ClassificationEditAudit.query.filter_by(result_id=result.id)
            .order_by(ClassificationEditAudit.create_time.desc())
            .limit(50)
            .all()
        )
        ratio_index = ratio_years.index(year) if year in ratio_years else None
        entry = {
            "year": year,
            "result_id": result.id,
            "vector_status": result.vector_status,
            "feature_count": result.feature_count or 0,
            "current_revision_no": result.current_revision_no,
            "result_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}.png",
            "source_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}_src.png",
            "mask_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}_mask.png",
            "job_id": result.inference_job_id,
            "created_at": result.create_time.isoformat() if result.create_time else None,
            "class_ratio_percent": (
                {
                    name: round(float(values[ratio_index]), 2)
                    for name, values in (ratio_doc.get("series_percent") or {}).items()
                    if ratio_index is not None and ratio_index < len(values)
                }
                if ratio_index is not None
                else None
            ),
            "revisions": [
                {
                    "revision_no": audit.revision_no,
                    "base_revision_no": audit.base_revision_no,
                    "action": audit.action,
                    "actor": audit.actor,
                    "request_source": audit.request_source,
                    "created_at": audit.create_time.isoformat() if audit.create_time else None,
                }
                for audit in audits
            ],
        }
        years.append(entry)
    return years, ratio_doc


def _project_output_root(project_id):
    storage_root = get_storage_root()
    return resolve_storage_path(storage_root, Path("projects") / str(project_id) / "outputs")


def build_mine_traceability(project_id, fid):
    """溯源聚合主入口（只读）。文件缺失的维度返回 None 而非报错——溯源要能
    在部分数据缺失时仍展示可得部分。"""
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _get_project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")

    years, ratio_doc = _years_payload(project, fid_value)
    output_root = _project_output_root(project.id)
    change = _read_json_file(output_root / "change_matrix" / f"{fid_value}.json")
    indices = _read_json_file(output_root / "indices" / f"{fid_value}.json")
    original_imagery = list_project_original_imagery(project_id, fid_value)

    return {
        "project_id": project.id,
        "fid": fid_value,
        "years": years,
        "ratio_series": {
            "class_names": ratio_doc.get("class_names") or [],
            "years": ratio_doc.get("years") or [],
            "series_percent": ratio_doc.get("series_percent") or {},
        } if ratio_doc else None,
        "change_matrix": change,
        "indices": indices,
        "original_imagery": original_imagery,
    }

def list_project_classification_results(project_id):
    """项目全部分类成果清单（M3 编辑导航）：轻量摘要，同年多任务全列出，
    每条带 vector_status/feature_count 供编辑入口门控（ready/ready_empty 可编辑）。"""
    project = _get_project(project_id)
    rows = (
        ClassificationResult.query.filter_by(project_id=project.id)
        .order_by(ClassificationResult.year.desc(), ClassificationResult.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "result_id": result.id,
                "mine_fid": result.mine_fid,
                "year": result.year,
                "vector_status": result.vector_status,
                "feature_count": result.feature_count or 0,
                "current_revision_no": result.current_revision_no,
                "created_at": result.create_time.isoformat() if result.create_time else None,
            }
            for result in rows
        ],
        "count": len(rows),
    }
