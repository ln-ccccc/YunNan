"""图斑溯源聚合（M3，2026-09-22）：按矿山一键调取历年全链数据。

GET /api/projects/<id>/mines/<fid>/traceability 一次返回：
- years[]: 按年条目（成果图 URL/result_id/vector_status/feature_count/类别占比/
           修订时间线），同年多成果取「有效成果」
           （classification_stats.select_effective_result_refs，全系统唯一规则）
- ratio_series: 多年占比序列。未修订年份取推理占比（class_ratio_percent.json，
  推理发布重建的纯推理产物）；人工修订过的年份取该修订自己的派生统计
  （revisions/<版本号>.stats.json，与修订同生命周期）
- change_matrix: 最近两期变化矩阵（outputs/change_matrix/<fid>.json），并附
  change_matrix_state 说明它相对当前有效成果是否现值；需要重算时顺手补一次后台请求
- indices: NDVI 等指数时序（outputs/indices/<fid>.json）
- 原始影像引用复用 list_project_original_imagery（M2 已有）

口径要点（2026-11-24 重构）：修订年份的占比不再依赖任何「按矿山+年份共享」的
派生文件，所以不存在回写丢失、被别的年份/别的成果覆盖、被推理发布冲掉的问题；
保存较旧成果时，溯源取的成果与统计取的成果一定是同一条。
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import load_only

from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
)
from applications.models.project import Project
from applications.project_hub.classification_stats import (
    MATRIX_STATE_PENDING,
    _AREA_SOURCE_INFERENCE,
    _AREA_SOURCE_REVISED,
    evaluate_change_matrix_state,
    request_change_matrix_rebuild,
    resolve_result_stats,
    select_effective_result_refs,
)
from applications.project_hub.project_map import (
    list_project_original_imagery,
    _project as _get_project,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path
from applications.common.utils.utc_time import to_utc_z

LOGGER = logging.getLogger(__name__)
# 序列里同时含修订年份与未修订年份时的口径标注
_AREA_SOURCE_MIXED = "mixed"


def _read_json_file(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _inference_percent(ratio_doc, ratio_index):
    """未修订年份的占比：推理占比序列在该年份位置的值。"""
    return {
        name: round(float(values[ratio_index]), 2)
        for name, values in ((ratio_doc or {}).get("series_percent") or {}).items()
        if isinstance(values, list) and ratio_index < len(values)
    }


def _years_payload(project, fid_value):
    """按年条目（附修订时间线与占比）与该矿山的有效成果引用。

    返回 (years, ratio_doc, refs)。占比口径：修订过的年份（修订号 > 0）用该修订
    自己的派生统计；未修订年份读推理占比序列。
    """
    rows = (
        ClassificationResult.query.filter_by(project_id=project.id, mine_fid=fid_value)
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    refs = select_effective_result_refs(
        [(row.id, row.year, row.vector_status, row.current_revision_no) for row in rows]
    )
    by_id = {row.id: row for row in rows}
    effective_by_year = {year: by_id[result_id] for year, (result_id, _revision) in refs.items()}

    inference_root = _project_output_root(project.id) / "inference" / str(fid_value)
    ratio_doc = _read_json_file(inference_root / "class_ratio_percent.json") or {}
    ratio_years = ratio_doc.get("years") or []

    years = []
    for year in sorted(effective_by_year):
        result = effective_by_year[year]
        base = f"{fid_value}+{year}"
        audits = (
            ClassificationEditAudit.query.filter_by(result_id=result.id)
            .order_by(ClassificationEditAudit.create_time.desc())
            .limit(50)
            .all()
        )
        ratio_index = ratio_years.index(year) if year in ratio_years else None
        manual = result.vector_status != "vector_failed" and int(result.current_revision_no or 0) > 0
        if manual:
            # 修订统计随修订落盘；缺失则按当前矢量现算同一个纯函数。显式清空时为全 0
            class_ratio = dict(resolve_result_stats(project.id, result)["percent"])
            area_source = _AREA_SOURCE_REVISED
        elif ratio_index is not None:
            class_ratio = _inference_percent(ratio_doc, ratio_index)
            area_source = _AREA_SOURCE_INFERENCE
        else:
            class_ratio = None
            area_source = None
        years.append({
            "year": year,
            "result_id": result.id,
            "vector_status": result.vector_status,
            "feature_count": result.feature_count or 0,
            "current_revision_no": result.current_revision_no,
            "result_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}.png",
            "source_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}_src.png",
            "mask_image_url": f"/api/projects/{project.id}/outputs/inference/{fid_value}/{base}_mask.png",
            "job_id": result.inference_job_id,
            "created_at": to_utc_z(result.create_time),
            "class_ratio_percent": class_ratio,
            # 逐年份标注口径：修订年份 vector_shoelace，其余 inference_mask
            "class_ratio_area_source": area_source,
            "revisions": [
                {
                    "revision_no": audit.revision_no,
                    "base_revision_no": audit.base_revision_no,
                    "action": audit.action,
                    "actor": audit.actor,
                    "request_source": audit.request_source,
                    "created_at": to_utc_z(audit.create_time),
                }
                for audit in audits
            ],
        })
    return years, ratio_doc, refs


def _project_output_root(project_id):
    storage_root = get_storage_root()
    return resolve_storage_path(storage_root, Path("projects") / str(project_id) / "outputs")


def _ratio_series_block(years, ratio_doc):
    """序列视图：推理占比序列为底，修订年份用条目里的值覆盖对应位置，保证与逐年条目一致。"""
    if not ratio_doc:
        return None
    ratio_years = [int(value) for value in (ratio_doc.get("years") or [])]
    series = {
        name: [float(value) for value in values]
        for name, values in (ratio_doc.get("series_percent") or {}).items()
        if isinstance(values, list)
    }
    sources = {entry["class_ratio_area_source"] for entry in years}
    for entry in years:
        if entry["class_ratio_area_source"] != _AREA_SOURCE_REVISED or entry["year"] not in ratio_years:
            continue
        index = ratio_years.index(entry["year"])
        for name, value in (entry["class_ratio_percent"] or {}).items():
            values = series.get(name)
            if values is not None and index < len(values):
                values[index] = value
    if sources <= {None}:
        area_source = None
    elif sources <= {_AREA_SOURCE_REVISED, None}:
        area_source = _AREA_SOURCE_REVISED
    elif sources <= {_AREA_SOURCE_INFERENCE, None}:
        area_source = _AREA_SOURCE_INFERENCE
    else:
        area_source = _AREA_SOURCE_MIXED
    return {
        "class_names": ratio_doc.get("class_names") or [],
        "years": ratio_years,
        "series_percent": series,
        "area_source": area_source,
    }


def build_mine_traceability(project_id, fid):
    """溯源聚合主入口。文件缺失的维度返回 None 而非报错——溯源要能在部分数据缺失时
    仍展示可得部分。

    唯一的副作用：发现变化矩阵需要重算时，向后台队列补一次重算请求（不改任何业务
    数据；重算是幂等的派生缓存维护）。进程重启丢掉的重算任务靠这里自愈。
    """
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _get_project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")

    years, ratio_doc, refs = _years_payload(project, fid_value)
    output_root = _project_output_root(project.id)
    change = _read_json_file(output_root / "change_matrix" / f"{fid_value}.json")
    indices = _read_json_file(output_root / "indices" / f"{fid_value}.json")
    original_imagery = list_project_original_imagery(project_id, fid_value)

    # 变化矩阵相对当前有效成果的状态。待重算（保存后后台任务还没轮到、进程重启丢了任务、
    # 推理发布刚覆盖了矩阵）就再补一次请求；消费端按 change_matrix_note 提示并稍后刷新
    matrix_state, matrix_note = evaluate_change_matrix_state(project.id, fid_value, refs)
    if matrix_state == MATRIX_STATE_PENDING:
        try:
            request_change_matrix_rebuild(project.id, fid_value)
        except Exception:  # noqa: BLE001 - 补请求失败不影响溯源展示
            LOGGER.warning(
                "溯源触发审校矩阵重算失败 project_id=%s mine_fid=%s",
                project.id, fid_value, exc_info=True,
            )

    return {
        "project_id": project.id,
        "fid": fid_value,
        "years": years,
        "ratio_series": _ratio_series_block(years, ratio_doc),
        "change_matrix": change,
        "change_matrix_state": matrix_state,
        "change_matrix_note": matrix_note,
        "indices": indices,
        "original_imagery": original_imagery,
    }


def list_project_classification_results(project_id):
    """项目全部分类成果清单（M3 编辑导航）：轻量摘要，同年多任务全列出，
    每条带 vector_status/feature_count 供编辑入口门控（ready/ready_empty 可编辑）。"""
    project = _get_project(project_id)
    rows = (
        ClassificationResult.query.filter_by(project_id=project.id)
        # 只取清单摘要所需 7 列：整行加载会连带 roi/auto/current 三个
        # MEDIUMTEXT 大字段（同 assets.py 2026-11-22 修法），项目 10 实测
        # 2979 条把编辑导航清单拖到 50s、BFF 15s 超时 502
        .options(
            load_only(
                ClassificationResult.id,
                ClassificationResult.mine_fid,
                ClassificationResult.year,
                ClassificationResult.vector_status,
                ClassificationResult.feature_count,
                ClassificationResult.current_revision_no,
                ClassificationResult.create_time,
            )
        )
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
                "created_at": to_utc_z(result.create_time),
            }
            for result in rows
        ],
        "count": len(rows),
    }
