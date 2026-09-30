import csv
import hashlib
import json
import logging
import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath

from applications.extensions import db
from applications.common.utils.utc_time import to_utc_z
from applications.models.inference_job import InferenceJob
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectBackupRecord,
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.models.project_spatial import ProjectSpatialJob
from applications.schemas.project import (
    ProjectActivityLogSchema,
    ProjectAssetViewSchema,
    ProjectBackupRecordSchema,
    ProjectDatasetSchema,
    ProjectExportRecordSchema,
    ProjectMineBindingSchema,
    ProjectOverviewViewSchema,
    ProjectSummarySchema,
)
from applications.project_hub.assets import list_project_assets
from applications.project_hub.project_storage import (
    ProjectStorageValidationError,
    export_root,
    resolve_project_record_file,
    resolve_project_record_root,
    snapshot_root,
    write_json_atomic,
)
from applications.project_hub.readiness import (
    build_capabilities,
    build_next_actions,
    build_readiness,
)
from applications.project_hub.spatial_service import sanitize_public_geojson_value
from applications.project_hub.spatial_storage import (
    SUPPORTED_INCOMING_RASTER_FORMATS,
    get_storage_root,
    resolve_storage_path,
)
from applications.project_hub.exports import (
    ALLOWED_EXPORT_FORMATS,
    create_export,
    list_exports,
    resolve_project_export_artifact,
)
from applications.project_hub._foundation import (
    _append_activity,
    _cleanup_storage_directory,
    _export_directory,
    _file_checksum,
    _get_project_or_404,
    _json_dump,
    _json_load,
    _log_storage_cleanup_failure,
    _mark_backup_failed,
    _mark_export_failed,
    _parse_iso_datetime,
    _rollback_after_storage_failure,
    _snapshot_directory,
    _storage_key,
    _utc_timestamp,
    _validate_incoming_tiff_storage_key,
    _write_artifact_atomic,
    _write_xlsx,
)
from applications.project_hub.spatial_state import serialize_project_spatial_state


ALLOWED_PROJECT_STATUSES = {"draft", "active", "completed", "archived"}
ALLOWED_DATASET_KINDS = {"imagery", "inference_result", "report", "export_package", "mine_indices"}
ACTION_CODE_BY_EVENT_TYPE = {
    "project_created": "PROJECT_CREATED",
    "project_updated": "PROJECT_UPDATED",
    "mine_binding_replaced": "MINE_BINDING_REPLACED",
    "dataset_created": "DATASET_REGISTERED",
    "project_archived": "PROJECT_ARCHIVED",
    "project_restored": "PROJECT_RESTORED",
    "spatial_resource_removed": "SPATIAL_RESOURCE_REMOVED",
    "spatial_resource_activated": "SPATIAL_RESOURCE_ACTIVATED",
    "spatial_job_queued": "SPATIAL_JOB_QUEUED",
    "spatial_job_retried": "SPATIAL_JOB_RETRIED",
    "spatial_job_cancel_requested": "SPATIAL_JOB_CANCEL_REQUESTED",
    "export_created": "EXPORT_CREATED",
    "backup_created": "SNAPSHOT_CREATED",
    "backup_restored": "SNAPSHOT_RESTORED",
    "inference_result_published": "INFERENCE_RESULT_PUBLISHED",
    "inference_failed": "INFERENCE_FAILED",
}
_FORBIDDEN_ACTIVITY_KEYS = {
    "file_path",
    "source_path",
    "normalized_path",
    "tile_path",
    "manifest_path",
    "output_dir",
}
LOGGER = logging.getLogger(__name__)


def _serialize_summary(project, feature_count=None, latest_inference=None, include_card_fields=False):
    latest_activity = project.activities[0].create_time if project.activities else None
    spatial_state = serialize_project_spatial_state(project)
    payload = {
        "id": project.id,
        "name": project.name,
        "region": project.region,
        "manager": project.manager,
        "remark": project.remark,
        "status": project.status,
        # 语义化别名（AGENTS §4）：lifecycle_status 与 status 同值，
        # 供消费方按四层状态模型取词，前端不再需要 || status 回退
        "lifecycle_status": project.status,
        "monitor_start_year": project.monitor_start_year,
        "monitor_end_year": project.monitor_end_year,
        "mine_count": len(project.mines),
        "dataset_count": len(project.datasets),
        **spatial_state,
        "latest_activity_at": latest_activity,
        "create_time": project.create_time,
        "update_time": project.update_time,
    }
    # M1 主控台卡片字段：仅项目列表注入（overview/fixture 的 summary 契约不含它们）；
    # latest_inference 键必须无条件存在（None 表示暂无解译），marshmallow 才会序列化 null
    if include_card_fields:
        payload["feature_count"] = feature_count or 0
        payload["latest_inference"] = (
            {
                "job_id": latest_inference.get("job_id"),
                "status": latest_inference.get("status"),
                "create_time": latest_inference.get("create_time"),
            }
            if latest_inference
            else None
        )
    return ProjectSummarySchema().dump(payload)


def _serialize_overview_summary(project, readiness, assets=None):
    """overview summary 的空间三键（spatial_status/map_ready/missing_resources）
    唯一来源是 readiness + 资产读模型（M2 双轨统一）：此前 _serialize_summary 先写
    A 轨表状态、此处再按 readiness 覆写同名键，判据分裂；现在显式派生——
    列表接口的三键仍为 A 轨轻量口径（spatial_state 直查表），两者判据同源
    （资产读模型从空间资源表映射），语义文档见 vector-result/架构契约。"""
    summary = _serialize_summary(project)
    for field_name in ("latest_activity_at", "create_time", "update_time"):
        summary[field_name] = _utc_timestamp(summary.get(field_name))
    checks = readiness.get("checks", ())
    passed_by_code = {
        check.get("code"): check.get("status") == "passed"
        for check in checks
        if isinstance(check, dict)
    }
    resource_checks = (
        ("MINE_BOUNDARY", "mine_boundary"),
        ("ACTIVE_BASEMAP", "active_basemap"),
        ("INFERENCE_INPUT", "inference_input"),
        ("REVIEWABLE_RESULT", "reviewable_result"),
    )
    summary["missing_resources"] = [
        resource_name
        for check_code, resource_name in resource_checks
        if not passed_by_code.get(check_code, False)
    ]
    has_boundary = passed_by_code.get("MINE_BOUNDARY", False)
    has_basemap = passed_by_code.get("ACTIVE_BASEMAP", False)
    summary["map_ready"] = has_boundary and has_basemap
    # processing/failed 判定改由资产读模型派生（映射自空间资源表 pending/
    # processing/failed），不再依赖 A 轨先写入的 spatial_status 幸存值
    spatial_assets = [
        asset for asset in (assets or ())
        if isinstance(asset, dict) and asset.get("asset_type") in ("mine_boundary", "basemap")
    ]
    # A 轨语义等价：资源表 pending（资产映射 registered）与 processing 都算处理中
    has_processing = any(
        asset.get("status") in ("registered", "processing") for asset in spatial_assets
    )
    has_failed_missing = any(
        asset.get("status") == "failed"
        and not passed_by_code.get(
            "MINE_BOUNDARY" if asset.get("asset_type") == "mine_boundary" else "ACTIVE_BASEMAP",
            False,
        )
        for asset in spatial_assets
    )
    if has_processing:
        summary["spatial_status"] = "processing"
    elif has_failed_missing:
        summary["spatial_status"] = "failed"
    elif has_boundary and has_basemap:
        summary["spatial_status"] = "ready"
    elif has_boundary:
        summary["spatial_status"] = "partial"
    else:
        summary["spatial_status"] = "unconfigured"
    return summary


def _is_unsafe_activity_value(value, storage_root):
    if isinstance(value, dict):
        return any(
            str(key).strip().casefold() in _FORBIDDEN_ACTIVITY_KEYS
            or _is_unsafe_activity_value(nested_value, storage_root)
            for key, nested_value in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_is_unsafe_activity_value(item, storage_root) for item in value)
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("\\", "/")
    normalized_root = str(storage_root or "").strip().replace("\\", "/").rstrip("/")
    if normalized_root and normalized_root.casefold() in normalized.casefold():
        return True
    if normalized.casefold().startswith("file:"):
        return True
    if normalized.startswith("//") or normalized.startswith("/"):
        return True
    return (
        len(normalized) >= 3
        and normalized[0].isalpha()
        and normalized[1] == ":"
        and normalized[2] == "/"
    )


def _serialize_overview_activity(activity):
    event = _json_load(activity.payload_json, None)
    if not isinstance(event, dict):
        return None
    storage_root = str(get_storage_root()).replace("\\", "/").rstrip("/")
    if _is_unsafe_activity_value(event, storage_root):
        return None

    action_code = event.get("action_code")
    actor_id = event.get("actor_id")
    actor_type = event.get("actor_type")
    target_type = event.get("target_type")
    target_id = event.get("target_id")
    result = event.get("result")
    job_id = event.get("job_id")
    payload = event.get("payload")
    if (
        isinstance(action_code, str)
        and action_code.strip()
        and isinstance(actor_id, str)
        and actor_id.strip()
        and isinstance(actor_type, str)
        and actor_type.strip()
        and isinstance(target_type, str)
        and target_type.strip()
        and target_id not in (None, "")
        and not isinstance(target_id, bool)
        and isinstance(target_id, (str, int))
        and isinstance(result, str)
        and result.strip()
        and isinstance(payload, dict)
        and not isinstance(job_id, bool)
        and (job_id is None or isinstance(job_id, (str, int)))
    ):
        return {
            "action_code": action_code,
            "actor_id": actor_id,
            "actor_type": actor_type,
            "target_type": target_type,
            "target_id": str(target_id),
            "result": result,
            "job_id": str(job_id) if job_id is not None else None,
            "payload": payload,
            "created_at": _utc_timestamp(activity.create_time),
        }

    target = event.get("target")
    result = event.get("result", "success")
    target_type = target.get("type") if isinstance(target, dict) else None
    target_id = target.get("id") if isinstance(target, dict) else None
    if (
        not isinstance(target_type, str)
        or not target_type.strip()
        or target_id in (None, "")
        or isinstance(target_id, bool)
        or not isinstance(target_id, (str, int))
        or not isinstance(result, str)
        or not result.strip()
    ):
        return None

    actor_id = activity.actor if isinstance(activity.actor, str) else "system"
    actor_id = actor_id.strip() or "system"
    payload = {key: value for key, value in event.items() if key not in {"target", "result"}}
    job_id = payload.get("job_id")
    if isinstance(job_id, bool) or (job_id is not None and not isinstance(job_id, (str, int))):
        job_id = None
    return {
        "action_code": ACTION_CODE_BY_EVENT_TYPE.get(
            activity.event_type,
            str(activity.event_type).upper(),
        ),
        "actor_id": actor_id,
        "actor_type": "system" if actor_id == "system" else "user",
        "target_type": target_type,
        "target_id": str(target_id),
        "result": result,
        "job_id": str(job_id) if job_id is not None else None,
        "payload": payload,
        "created_at": _utc_timestamp(activity.create_time),
    }


def _serialize_overview_activity_list(activities):
    return [
        item
        for activity in activities
        if (item := _serialize_overview_activity(activity)) is not None
    ]


def _build_project_counts(project, assets):
    project_id = project.id

    def job_count(status):
        return (
            ProjectSpatialJob.query.filter_by(project_id=project_id, status=status).count()
            + InferenceJob.query.filter_by(project_id=project_id, status=status).count()
        )

    return {
        "mines": len(project.mines),
        "assets": len(assets),
        "queued_jobs": job_count("queued"),
        "running_jobs": job_count("running"),
        "failed_assets": sum(asset["status"] == "failed" for asset in assets),
    }


def record_inference_failure(project_id, payload=None, actor="system"):
    """推理任务失败的审计事件（推理 worker 失败路径经此公共服务函数写入，
    推理运行时模块自身不直接操作项目域表）。项目不存在时静默跳过。"""
    try:
        project = _get_project_or_404(project_id)
    except ValueError:
        return None
    _append_activity(
        project.id,
        "inference_failed",
        payload=payload,
        actor=actor,
        result="failure",
    )
    db.session.commit()
    return True


def _project_list_features():
    """项目 → 图斑要素数（M1 看板）：每 (fid, year) 取最新一条成果（同年多任务
    不重复计数），合计其 feature_count 冗余列。"""
    from applications.models.classification_result import ClassificationResult

    rows = (
        db.session.query(
            ClassificationResult.project_id,
            ClassificationResult.mine_fid,
            ClassificationResult.year,
            ClassificationResult.id,
            ClassificationResult.feature_count,
        )
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest = {}
    for project_id, mine_fid, year, result_id, feature_count in rows:
        key = (project_id, mine_fid, year)
        if key not in latest:
            latest[key] = feature_count or 0
    totals = {}
    for (project_id, _fid, _year), count in latest.items():
        totals[project_id] = totals.get(project_id, 0) + count
    return totals


def _project_list_latest_inference():
    """项目 → 最近一次推理任务（状态/时间），供项目卡片"最新解译进度"。"""
    latest = (
        InferenceJob.query.filter(InferenceJob.project_id.isnot(None))
        .order_by(InferenceJob.project_id, InferenceJob.create_time.desc())
        .all()
    )
    summary = {}
    for job in latest:
        if job.project_id not in summary:
            summary[job.project_id] = {
                "job_id": job.id,
                "status": job.status,
                "create_time": job.create_time,
            }
    return summary


def list_projects(filters=None):
    query = Project.query.filter_by(deleted_at=None).order_by(Project.update_time.desc())
    filters = filters or {}
    if filters.get("name"):
        query = query.filter(Project.name.like(f"%{filters['name']}%"))
    if filters.get("region"):
        query = query.filter(Project.region.like(f"%{filters['region']}%"))
    if filters.get("status"):
        query = query.filter(Project.status == filters["status"])
    if filters.get("monitor_year"):
        year_value = int(filters["monitor_year"])
        query = query.filter(
            Project.monitor_start_year <= year_value,
            Project.monitor_end_year >= year_value,
        )
    projects = query.all()
    feature_totals = _project_list_features()
    latest_inference = _project_list_latest_inference()
    items = [
        _serialize_summary(
            project,
            feature_count=feature_totals.get(project.id, 0),
            latest_inference=latest_inference.get(project.id),
            include_card_fields=True,
        )
        for project in projects
    ]
    return {"items": items, "count": len(items)}


def create_project(payload, actor="system"):
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("项目名称不能为空")
    status = str(payload.get("status") or "draft").strip()
    if status not in ALLOWED_PROJECT_STATUSES:
        raise ValueError("项目状态不合法")
    project = Project(
        name=name,
        region=str(payload.get("region") or "").strip() or None,
        manager=str(payload.get("manager") or "").strip() or None,
        remark=str(payload.get("remark") or "").strip() or None,
        status=status,
        monitor_start_year=payload.get("monitor_start_year"),
        monitor_end_year=payload.get("monitor_end_year"),
    )
    db.session.add(project)
    db.session.flush()
    _append_activity(
        project.id,
        "project_created",
        {"name": project.name},
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return _serialize_summary(project)


def update_project(project_id, payload, actor="system"):
    project = _get_project_or_404(project_id)
    for field in ("name", "region", "manager", "remark", "monitor_start_year", "monitor_end_year"):
        if field in payload:
            setattr(project, field, payload.get(field))
    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status not in ALLOWED_PROJECT_STATUSES:
            raise ValueError("项目状态不合法")
        project.status = status
    _append_activity(
        project.id,
        "project_updated",
        {"fields": sorted(payload.keys())},
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return _serialize_summary(project)


def replace_project_mines(project_id, mines, actor="system"):
    project = _get_project_or_404(project_id)
    mines = mines or []
    project.mines[:] = []
    db.session.flush()
    for item in mines:
        project.mines.append(
            ProjectMineBinding(
                mine_fid=int(item["mine_fid"]),
                mine_name_snapshot=str(item.get("mine_name_snapshot") or "").strip() or None,
                city_snapshot=str(item.get("city_snapshot") or "").strip() or None,
                area_snapshot=item.get("area_snapshot"),
                status_snapshot=str(item.get("status_snapshot") or "").strip() or None,
                sort_order=int(item.get("sort_order") or 0),
            )
        )
    _append_activity(
        project.id,
        "mine_binding_replaced",
        {"mine_count": len(mines)},
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return {"mine_count": len(project.mines), "items": ProjectMineBindingSchema(many=True).dump(project.mines)}


def create_dataset(project_id, payload, actor="system"):
    project = _get_project_or_404(project_id)
    if "file_path" in payload:
        raise ProjectStorageValidationError("请使用 storage_key 登记数据文件")
    dataset_kind = str(payload.get("dataset_kind") or "").strip()
    if dataset_kind != "imagery":
        raise ProjectStorageValidationError("当前接口仅支持登记影像数据集")
    display_name = str(payload.get("display_name") or "").strip()
    if not display_name:
        raise ValueError("数据集名称不能为空")
    storage_key, source_format = _validate_incoming_tiff_storage_key(payload.get("storage_key"))
    dataset = ProjectDataset(
        project_id=project.id,
        dataset_kind=dataset_kind,
        display_name=display_name,
        file_path=storage_key,
        source_format=source_format,
        mine_fid=payload.get("mine_fid"),
        year_start=payload.get("year_start"),
        year_end=payload.get("year_end"),
        slice_config_json=_json_dump(payload.get("slice_config_json")),
    )
    db.session.add(dataset)
    db.session.flush()
    _append_activity(
        project.id,
        "dataset_created",
        {
            "dataset_id": dataset.id,
            "dataset_kind": dataset.dataset_kind,
            "display_name": dataset.display_name,
        },
        actor=actor,
        target={"type": "dataset", "id": str(dataset.id)},
    )
    if project.status == "draft":
        project.status = "active"
    db.session.commit()
    return {
        "id": dataset.id,
        "asset_id": f"imagery:{dataset.id}",
        "status": "registered",
    }


def get_project_detail(project_id):
    project = _get_project_or_404(project_id)
    return {
        "summary": _serialize_summary(project),
        "mines": ProjectMineBindingSchema(many=True).dump(project.mines),
        "datasets": ProjectDatasetSchema(many=True).dump(project.datasets),
        "recent_activity": ProjectActivityLogSchema(many=True).dump(project.activities[:20]),
        "timeline": get_project_timeline(project_id)["items"],
        "exports": ProjectExportRecordSchema(many=True).dump(project.exports),
        "backups": ProjectBackupRecordSchema(many=True).dump(project.backups),
    }


def get_project_assets(project_id, filters=None):
    project = _get_project_or_404(project_id)
    filters = filters or {}
    items = list_project_assets(
        project,
        asset_type=filters.get("asset_type"),
        status=filters.get("status"),
    )
    return {
        "items": ProjectAssetViewSchema(many=True).dump(items),
        "count": len(items),
    }


def get_project_overview(project_id):
    project = _get_project_or_404(project_id)
    assets = list_project_assets(project)
    readiness, blockers = build_readiness(project, assets)
    return ProjectOverviewViewSchema().dump(
        {
            "project_id": project.id,
            "lifecycle_status": project.status,
            "summary": _serialize_overview_summary(project, readiness, assets),
            "readiness": readiness,
            "capabilities": build_capabilities(project, assets, readiness),
            "blockers": blockers,
            "next_actions": build_next_actions(readiness),
            "counts": _build_project_counts(project, assets),
            "recent_activity": _serialize_overview_activity_list(project.activities[:20]),
        }
    )


def get_project_timeline(project_id):
    project = _get_project_or_404(project_id)
    storage_root = get_storage_root()
    items = []
    for activity in sorted(project.activities, key=lambda row: row.create_time, reverse=True):
        raw_payload = _json_load(activity.payload_json, {})
        if isinstance(raw_payload, dict):
            payload = dict(raw_payload)
            target = payload.pop("target", {})
            result = payload.pop("result", "success")
        else:
            payload = raw_payload
            target = {}
            result = "success"
        if not isinstance(target, dict) or _is_unsafe_activity_value(target, storage_root):
            target = {}
        if not isinstance(payload, dict) or _is_unsafe_activity_value(payload, storage_root):
            payload = {}
        if (
            not isinstance(result, str)
            or not result
            or _is_unsafe_activity_value(result, storage_root)
        ):
            result = "success"
        actor = activity.actor
        if not isinstance(actor, str) or not actor or _is_unsafe_activity_value(actor, storage_root):
            actor = "system"
        timestamp = _utc_timestamp(activity.create_time)
        items.append(
            {
                "id": activity.id,
                "event_type": activity.event_type,
                "action_code": ACTION_CODE_BY_EVENT_TYPE.get(
                    activity.event_type,
                    str(activity.event_type).upper(),
                ),
                "actor": actor,
                "target": target,
                "result": result,
                "payload": payload,
                "created_at": timestamp,
                "timestamp": timestamp,
            }
        )
    return {"items": items, "count": len(items)}


def archive_project(project_id, actor="system"):
    project = _get_project_or_404(project_id)
    project.status = "archived"
    _append_activity(
        project.id,
        "project_archived",
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return _serialize_summary(project)


class ProjectDeleteNotAllowed(ValueError):
    """活动项目不可直接删除（须先归档）——携带 409 语义。"""


def delete_project(project_id, actor="system"):
    """删除项目（M2 计划 §3：仅归档项目可删）。

    - DB 软删（deleted_at 置位，审计轨迹保留，列表默认过滤）；
    - project_storage/projects/<id>/ 整目录移入 trash/<id>_<时间戳>/（不物理清除，
      误删可人工移回恢复）；
    - 活动项目必须先归档，防止误删在建数据。
    """
    project = _get_project_or_404(project_id)
    if project.status != "archived":
        raise ProjectDeleteNotAllowed("仅已归档项目可以删除，请先归档项目")

    from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

    storage_root = get_storage_root()
    project_root = resolve_storage_path(storage_root, Path("projects") / str(project.id))
    trash_target = None
    if project_root.exists():
        trash_root = resolve_storage_path(storage_root, Path("trash"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        trash_target = trash_root / f"{project.id}_{timestamp}_{uuid.uuid4().hex[:8]}"
        trash_root.mkdir(parents=True, exist_ok=True)
        shutil.move(str(project_root), str(trash_target))

    project.deleted_at = datetime.now()
    _append_activity(
        project.id,
        "project_deleted",
        {"trash_target": trash_target.name if trash_target else None},
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return {
        "id": project.id,
        "name": project.name,
        "deleted": True,
        "trash_target": trash_target.name if trash_target else None,
    }


def batch_archive_projects(project_ids, actor="system"):
    """批量归档：逐项复用单项目服务，部分失败不影响其余项（逐项报告结果）。"""
    results = []
    for raw_id in project_ids or []:
        try:
            project_id = int(raw_id)
        except (TypeError, ValueError):
            results.append({"project_id": raw_id, "ok": False, "msg": "项目 ID 不合法"})
            continue
        try:
            summary = archive_project(project_id, actor=actor)
            results.append({"project_id": project_id, "ok": True, "name": summary.get("name")})
        except ValueError as exc:
            results.append({"project_id": project_id, "ok": False, "msg": str(exc)})
    succeeded = sum(1 for item in results if item.get("ok"))
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


def batch_delete_projects(project_ids, actor="system"):
    """批量删除：同批量归档（活动项目逐项报"须先归档"，其余照删）。"""
    results = []
    for raw_id in project_ids or []:
        try:
            project_id = int(raw_id)
        except (TypeError, ValueError):
            results.append({"project_id": raw_id, "ok": False, "msg": "项目 ID 不合法"})
            continue
        try:
            outcome = delete_project(project_id, actor=actor)
            results.append({
                "project_id": project_id,
                "ok": True,
                "name": outcome.get("name"),
                "trash_target": outcome.get("trash_target"),
            })
        except ProjectDeleteNotAllowed as exc:
            results.append({"project_id": project_id, "ok": False, "msg": str(exc)})
        except ValueError as exc:
            results.append({"project_id": project_id, "ok": False, "msg": str(exc)})
    succeeded = sum(1 for item in results if item.get("ok"))
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


def restore_project(project_id, actor="system"):
    project = _get_project_or_404(project_id)
    project.status = "active"
    project.deleted_at = None
    _append_activity(
        project.id,
        "project_restored",
        actor=actor,
        target={"type": "project", "id": str(project.id)},
    )
    db.session.commit()
    return _serialize_summary(project)


def _serialize_internal_dataset(dataset):
    return {
        "dataset_kind": dataset.dataset_kind,
        "display_name": dataset.display_name,
        "file_path": dataset.file_path,
        "source_format": dataset.source_format,
        "mine_fid": dataset.mine_fid,
        "year_start": dataset.year_start,
        "year_end": dataset.year_end,
        "slice_config_json": _json_load(dataset.slice_config_json, {}),
    }


def _serialize_internal_export(record):
    return {
        "format": record.format,
        "file_path": record.file_path,
        "status": record.status,
        "request_params": _json_load(record.request_params_json, {}),
    }


def _serialize_internal_activity(activity):
    return {
        "event_type": activity.event_type,
        "actor": activity.actor,
        "payload": _json_load(activity.payload_json, {}),
        # 审计时间线的主数据：恢复时回写原始时间，避免整段历史被压缩到恢复时刻
        "created_at": activity.create_time.isoformat() if activity.create_time else None,
    }


def _project_manifest(project, backup_id):
    return {
        "snapshot_version": 1,
        "project_id": project.id,
        "backup_id": backup_id,
        "summary": {
            "name": project.name,
            "region": project.region,
            "manager": project.manager,
            "remark": project.remark,
            "status": project.status,
            "monitor_start_year": project.monitor_start_year,
            "monitor_end_year": project.monitor_end_year,
        },
        "mines": ProjectMineBindingSchema(many=True).dump(project.mines),
        "datasets": [_serialize_internal_dataset(dataset) for dataset in project.datasets],
        "exports": [_serialize_internal_export(record) for record in project.exports],
        "activities": [_serialize_internal_activity(activity) for activity in project.activities],
    }


def resolve_project_backup_manifest(project_id, backup_id):
    """配置快照清单下载解析：校验记录归属与完成态，返回 (record, 落盘路径)。"""
    record = ProjectBackupRecord.query.get(backup_id)
    if record is None or record.project_id != project_id:
        raise ProjectStorageValidationError("项目配置快照不存在")
    if record.status != "completed":
        raise ValueError("快照尚未完成，无法下载")
    path = resolve_project_record_file(
        project_id, "snapshots", backup_id, "manifest.json", require_exists=True
    )
    return record, path


def create_backup(project_id, payload, actor="system"):
    if not isinstance(payload, dict):
        raise ProjectStorageValidationError("项目配置快照请求格式不合法")
    if "output_dir" in payload:
        raise ProjectStorageValidationError("不支持指定服务端输出目录，请移除 output_dir")
    if set(payload).difference({"scope"}):
        raise ProjectStorageValidationError("项目配置快照请求包含不支持字段")
    project = _get_project_or_404(project_id)
    scope = str(payload.get("scope") or "metadata_index").strip() or "metadata_index"
    if scope != "metadata_index":
        raise ProjectStorageValidationError("当前仅支持 metadata_index 项目配置快照")
    backup = ProjectBackupRecord(
        project_id=project.id,
        scope=scope,
        manifest_path="",
        status="pending",
        restorable=False,
    )
    project_identifier = project.id
    backup_id = None
    try:
        db.session.add(backup)
        db.session.flush()
        backup_id = backup.id
        manifest_key = _storage_key("projects", str(project_identifier), "snapshots", str(backup_id), "manifest.json")
        backup.manifest_path = manifest_key
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "snapshots",
            project_identifier,
            backup_id,
            "initial_pending_rollback",
        )
        raise
    try:
        snapshot_root(project_identifier, backup_id)
        manifest_path = resolve_project_record_file(
            project_identifier,
            "snapshots",
            backup_id,
            "manifest.json",
        )
        write_json_atomic(
            manifest_path,
            _project_manifest(project, backup_id),
            area="snapshots",
            project_id=project_identifier,
            record_id=backup_id,
        )
        backup.status = "completed"
        backup.restorable = True
        _append_activity(
            project_identifier,
            "backup_created",
            {"backup_id": backup_id, "scope": scope},
            actor=actor,
            target={"type": "snapshot", "id": str(backup_id)},
        )
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "snapshots",
            project_identifier,
            backup_id,
            "final_snapshot_rollback",
        )
        _cleanup_storage_directory(project_identifier, "snapshots", backup_id)
        _mark_backup_failed(project_identifier, backup_id)
        raise
    return ProjectBackupRecordSchema().dump(backup)


def list_backups(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectBackupRecordSchema(many=True).dump(project.backups), "count": len(project.backups)}


def _snapshot_manifest_path(project_id, backup_id, manifest_key):
    raw_key = str(manifest_key or "").strip()
    expected_key = _storage_key(
        "projects",
        str(project_id),
        "snapshots",
        str(backup_id),
        "manifest.json",
    )
    windows_path = PureWindowsPath(raw_key)
    if (
        not raw_key
        or raw_key != expected_key
        or Path(raw_key).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectStorageValidationError("配置快照路径不合法")
    return resolve_project_record_file(
        project_id,
        "snapshots",
        backup_id,
        "manifest.json",
        require_exists=True,
    )


def _validate_backup_manifest(manifest, project_id, backup_id):
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_version") != 1
        or isinstance(manifest.get("project_id"), bool)
        or not isinstance(manifest.get("project_id"), int)
        or manifest.get("project_id") != project_id
        or isinstance(manifest.get("backup_id"), bool)
        or not isinstance(manifest.get("backup_id"), int)
        or manifest.get("backup_id") != backup_id
        or not isinstance(manifest.get("summary"), dict)
    ):
        raise ProjectStorageValidationError("配置快照内容不合法")
    required_fields = {
        "mines": ("mine_fid",),
        "datasets": ("dataset_kind", "display_name", "file_path"),
        "exports": ("format",),
        "activities": ("event_type",),
    }
    for section, fields in required_fields.items():
        items = manifest.get(section)
        if not isinstance(items, list):
            raise ProjectStorageValidationError("配置快照内容不合法")
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in fields):
                raise ProjectStorageValidationError("配置快照内容不合法")
    return manifest


def _validate_import_manifest(manifest):
    """导入校验：结构同 _validate_backup_manifest，但不要求 project_id/backup_id 匹配
    （跨环境迁移时 ID 必然不同）。"""
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_version") != 1
        or not isinstance(manifest.get("summary"), dict)
    ):
        raise ProjectStorageValidationError("导入的配置快照内容不合法")
    required_fields = {
        "mines": ("mine_fid",),
        "datasets": ("dataset_kind", "display_name", "file_path"),
        "exports": ("format",),
        "activities": ("event_type",),
    }
    for section, fields in required_fields.items():
        items = manifest.get(section)
        if not isinstance(items, list):
            raise ProjectStorageValidationError("导入的配置快照内容不合法")
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in fields):
                raise ProjectStorageValidationError("导入的配置快照内容不合法")
    return manifest


def import_backup_manifest(project_id, manifest, actor="system"):
    """跨环境快照导入（M2 计划 §3）：上传 manifest JSON 应用到目标项目。

    与 restore_backup 的差异：ID 不匹配场景合法（只校验结构）；不覆盖目标项目
    的活动时间线（导入记一条 project_imported 事件，原 activities 仅存档不重放）；
    mines/datasets 覆盖式重建（与 restore 同口径），exports 忽略（跨环境路径无意义）。
    """
    from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

    manifest = _validate_import_manifest(manifest)
    project = _get_project_or_404(project_id)
    try:
        summary = manifest["summary"]
        project.name = summary.get("name") or project.name
        project.region = summary.get("region")
        project.manager = summary.get("manager")
        project.remark = summary.get("remark")
        project.monitor_start_year = summary.get("monitor_start_year")
        project.monitor_end_year = summary.get("monitor_end_year")

        ProjectMineBinding.query.filter_by(project_id=project.id).delete()
        ProjectDataset.query.filter_by(project_id=project.id).delete()
        db.session.flush()

        for item in manifest["mines"]:
            db.session.add(
                ProjectMineBinding(
                    project_id=project.id,
                    mine_fid=item["mine_fid"],
                    mine_name_snapshot=item.get("mine_name_snapshot"),
                    city_snapshot=item.get("city_snapshot"),
                    area_snapshot=item.get("area_snapshot"),
                    status_snapshot=item.get("status_snapshot"),
                    sort_order=item.get("sort_order") or 0,
                )
            )
        # 导入数据集的 file_path 只允许落在本项目根内（2026-09-22 收官审查 S1：
        # manifest 直传任意路径曾被原样入库，间接读取服务器任意栅格）
        project_root = resolve_storage_path(
            get_storage_root(), Path("projects") / str(project.id)
        )

        def _safe_dataset_path(raw):
            text = str(raw or "").strip()
            if not text:
                raise ProjectStorageValidationError("导入的数据集缺少路径")
            candidate = Path(text).expanduser()
            # 相对路径按 storage 根解析（导出快照里就是 projects/<id>/... 形态）；
            # 绝对路径直接 resolve——两者最终都必须落在本项目根内
            if not candidate.is_absolute():
                candidate = get_storage_root() / candidate
            candidate = candidate.resolve()
            try:
                candidate.relative_to(project_root)
            except ValueError:
                raise ProjectStorageValidationError(
                    "导入的数据集路径不在本项目存储内，已拒绝"
                ) from None
            return str(candidate)

        for item in manifest["datasets"]:
            db.session.add(
                ProjectDataset(
                    project_id=project.id,
                    dataset_kind=item["dataset_kind"],
                    display_name=item["display_name"],
                    file_path=_safe_dataset_path(item["file_path"]),
                    source_format=item.get("source_format"),
                    mine_fid=item.get("mine_fid"),
                    year_start=item.get("year_start"),
                    year_end=item.get("year_end"),
                    slice_config_json=_json_dump(item.get("slice_config_json")),
                )
            )

        # 导入的 manifest 原样存档（追溯导入来源），活动只记一条事件
        storage_root = get_storage_root()
        import_dir = resolve_storage_path(
            storage_root, Path("projects") / str(project.id) / "imports"
        )
        import_dir.mkdir(parents=True, exist_ok=True)
        import_path = import_dir / f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
        write_json_atomic(import_path, manifest)

        _append_activity(
            project.id,
            "project_imported",
            {
                "source_project_id": manifest.get("project_id"),
                "source_backup_id": manifest.get("backup_id"),
                "mines": len(manifest["mines"]),
                "datasets": len(manifest["datasets"]),
            },
            actor=actor,
            target={"type": "project", "id": str(project.id)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_project_overview(project.id)


def restore_backup(project_id, backup_id, actor="system"):
    project = _get_project_or_404(project_id)
    backup = ProjectBackupRecord.query.filter_by(id=backup_id, project_id=project.id).first()
    if not backup:
        raise ValueError(f"备份不存在: {backup_id}")
    if backup.status != "completed" or not backup.restorable:
        raise ProjectStorageValidationError("配置快照不可恢复")
    try:
        manifest_path = _snapshot_manifest_path(project.id, backup.id, backup.manifest_path)
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = _validate_backup_manifest(json.load(handle), project.id, backup.id)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectStorageValidationError("配置快照内容不合法") from exc

    try:
        summary = manifest["summary"]
        project.name = summary.get("name") or project.name
        project.region = summary.get("region")
        project.manager = summary.get("manager")
        project.remark = summary.get("remark")
        project.status = "active"
        project.monitor_start_year = summary.get("monitor_start_year")
        project.monitor_end_year = summary.get("monitor_end_year")
        project.deleted_at = None

        ProjectMineBinding.query.filter_by(project_id=project.id).delete()
        ProjectDataset.query.filter_by(project_id=project.id).delete()
        ProjectExportRecord.query.filter_by(project_id=project.id).delete()
        ProjectActivityLog.query.filter_by(project_id=project.id).delete()
        db.session.flush()

        for item in manifest["mines"]:
            db.session.add(
                ProjectMineBinding(
                    project_id=project.id,
                    mine_fid=item["mine_fid"],
                    mine_name_snapshot=item.get("mine_name_snapshot"),
                    city_snapshot=item.get("city_snapshot"),
                    area_snapshot=item.get("area_snapshot"),
                    status_snapshot=item.get("status_snapshot"),
                    sort_order=item.get("sort_order") or 0,
                )
            )

        for item in manifest["datasets"]:
            db.session.add(
                ProjectDataset(
                    project_id=project.id,
                    dataset_kind=item["dataset_kind"],
                    display_name=item["display_name"],
                    file_path=item["file_path"],
                    source_format=item.get("source_format"),
                    mine_fid=item.get("mine_fid"),
                    year_start=item.get("year_start"),
                    year_end=item.get("year_end"),
                    slice_config_json=_json_dump(item.get("slice_config_json")),
                )
            )

        for item in manifest["exports"]:
            db.session.add(
                ProjectExportRecord(
                    project_id=project.id,
                    format=item["format"],
                    file_path=item.get("file_path"),
                    status=item.get("status") or "completed",
                    request_params_json=_json_dump(item.get("request_params")),
                )
            )

        for item in manifest["activities"]:
            created_at = _parse_iso_datetime(item.get("created_at"))
            db.session.add(
                ProjectActivityLog(
                    project_id=project.id,
                    event_type=item["event_type"],
                    actor=item.get("actor") or "system",
                    payload_json=_json_dump(item.get("payload")),
                    create_time=created_at or datetime.now(),
                )
            )

        _append_activity(
            project.id,
            "backup_restored",
            {"backup_id": backup.id},
            actor=actor,
            target={"type": "snapshot", "id": str(backup.id)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return get_project_overview(project.id)
