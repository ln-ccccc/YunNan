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
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path
from applications.project_hub.spatial_state import serialize_project_spatial_state


ALLOWED_PROJECT_STATUSES = {"draft", "active", "completed", "archived"}
ALLOWED_DATASET_KINDS = {"imagery", "inference_result", "report", "export_package", "mine_indices"}
ALLOWED_EXPORT_FORMATS = {"geojson", "csv", "shp"}
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


def _log_storage_cleanup_failure(area, project_id, record_id, stage, exc):
    LOGGER.warning(
        "项目存储清理失败 area=%s project_id=%s record_id=%s stage=%s",
        area,
        project_id,
        record_id,
        stage,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _rollback_after_storage_failure(area, project_id, record_id, stage):
    try:
        db.session.rollback()
    except Exception as rollback_exc:
        _log_storage_cleanup_failure(area, project_id, record_id, stage, rollback_exc)


def _json_dump(value):
    return json.dumps(value or {}, ensure_ascii=False)


def _json_load(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _validate_incoming_tiff_storage_key(value):
    storage_key = str(value or "").strip()
    path = Path(storage_key)
    windows_path = PureWindowsPath(storage_key)
    if (
        not storage_key
        or ".." in path.parts
        or ".." in windows_path.parts
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectStorageValidationError("storage_key 必须是 incoming/ 下的相对 TIFF 文件")
    storage_root = get_storage_root()
    try:
        source_path = resolve_storage_path(storage_root, storage_key)
        incoming_root = resolve_storage_path(storage_root, "incoming")
        source_path.relative_to(incoming_root)
    except ValueError as exc:
        raise ProjectStorageValidationError("storage_key 必须位于 incoming/ 目录") from exc
    source_format = source_path.suffix.lower().lstrip(".")
    if source_format not in {"tif", "tiff"}:
        raise ProjectStorageValidationError("storage_key 必须是 tif 或 tiff 文件")
    if not source_path.is_file():
        raise ProjectStorageValidationError("storage_key 指向的文件不存在")
    return source_path.relative_to(storage_root).as_posix(), source_format


def _storage_key(*parts):
    return Path(*parts).as_posix()


def _serialize_summary(project):
    latest_activity = project.activities[0].create_time if project.activities else None
    spatial_state = serialize_project_spatial_state(project)
    return ProjectSummarySchema().dump({
        "id": project.id,
        "name": project.name,
        "region": project.region,
        "manager": project.manager,
        "remark": project.remark,
        "status": project.status,
        "monitor_start_year": project.monitor_start_year,
        "monitor_end_year": project.monitor_end_year,
        "mine_count": len(project.mines),
        "dataset_count": len(project.datasets),
        **spatial_state,
        "latest_activity_at": latest_activity,
        "create_time": project.create_time,
        "update_time": project.update_time,
    })


def _utc_timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        timestamp = value
    else:
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return value
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    return timestamp.isoformat().replace("+00:00", "Z")


def _serialize_overview_summary(project, readiness):
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
    if summary.get("spatial_status") in {"processing", "failed"}:
        return summary
    if has_boundary and has_basemap:
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


def _append_activity(
    project_id,
    event_type,
    payload=None,
    actor="system",
    target=None,
    result="success",
):
    event_payload = dict(payload or {})
    event_payload.setdefault("target", target or {})
    event_payload.setdefault("result", result)
    row = ProjectActivityLog(
        project_id=project_id,
        event_type=event_type,
        actor=actor or "system",
        payload_json=_json_dump(event_payload),
    )
    db.session.add(row)
    return row


def _get_project_or_404(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if not project:
        raise ValueError(f"项目不存在: {project_id}")
    return project


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
    items = [_serialize_summary(project) for project in query.all()]
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
            "summary": _serialize_overview_summary(project, readiness),
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


def _feature_mine_fid(properties):
    for field_name in ("FID_1", "FID", "OBJECTID", "id"):
        value = properties.get(field_name)
        if isinstance(value, bool):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _build_project_export_features(project):
    from applications.project_hub.project_map import get_project_geojson

    mine_features = {}
    for feature in get_project_geojson(project.id).get("features", []):
        if not isinstance(feature, dict) or not feature.get("geometry"):
            continue
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            continue
        mine_fid = _feature_mine_fid(properties)
        if mine_fid is not None:
            mine_features[mine_fid] = feature

    datasets_by_mine = {}
    for dataset in project.datasets:
        datasets_by_mine.setdefault(dataset.mine_fid, dataset)

    features = []
    for binding in project.mines:
        feature = mine_features.get(binding.mine_fid)
        if feature is None:
            continue
        properties = feature.get("properties") or {}
        dataset = datasets_by_mine.get(binding.mine_fid)
        features.append(
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": {
                    "project_id": project.id,
                    "mine_fid": binding.mine_fid,
                    "mine_name": binding.mine_name_snapshot
                    or properties.get("mine_name")
                    or properties.get("name")
                    or "",
                    "dataset_id": dataset.id if dataset is not None else None,
                    "result_type": dataset.dataset_kind if dataset is not None else None,
                    "year_start": dataset.year_start if dataset is not None else None,
                    "year_end": dataset.year_end if dataset is not None else None,
                },
            }
        )
    return features


def _export_reference_id(value):
    if isinstance(value, bool):
        return None
    try:
        identifier = int(value)
    except (TypeError, ValueError):
        return None
    return identifier if identifier > 0 else None


def _make_geojson_payload(project, features):
    bindings_by_fid = {binding.mine_fid: binding for binding in project.mines}
    datasets_by_id = {dataset.id: dataset for dataset in project.datasets if dataset.id is not None}
    normalized = []
    for feature in features or []:
        item = sanitize_public_geojson_value(dict(feature))
        properties = dict(item.get("properties") or {})
        binding = bindings_by_fid.get(_export_reference_id(properties.get("mine_fid")))
        dataset = datasets_by_id.get(_export_reference_id(properties.get("dataset_id")))
        if binding is not None and dataset is not None and dataset.mine_fid not in (None, binding.mine_fid):
            dataset = None
        properties.update(
            {
                "project_id": project.id,
                "mine_fid": binding.mine_fid if binding is not None else None,
                "mine_name": (
                    binding.mine_name_snapshot
                    or properties.get("mine_name")
                    or properties.get("name")
                    or ""
                )
                if binding is not None
                else "",
                "dataset_id": dataset.id if dataset is not None else None,
                "result_type": dataset.dataset_kind if dataset is not None else None,
                "year_start": dataset.year_start if dataset is not None else None,
                "year_end": dataset.year_end if dataset is not None else None,
            }
        )
        item["properties"] = properties
        normalized.append(item)
    return {"type": "FeatureCollection", "features": normalized}


def _write_geojson(path_obj, payload):
    with open(path_obj, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _write_csv(path_obj, project):
    headers = [
        "project_id",
        "project_name",
        "mine_fid",
        "mine_name",
        "dataset_id",
        "dataset_kind",
        "display_name",
        "year_start",
        "year_end",
    ]
    dataset_by_mine = {}
    for dataset in project.datasets:
        dataset_by_mine.setdefault(dataset.mine_fid, []).append(dataset)
    with open(path_obj, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        if not project.mines and not project.datasets:
            writer.writerow([project.id, project.name, "", "", "", "", "", "", ""])
            return
        for binding in project.mines:
            rows = dataset_by_mine.get(binding.mine_fid) or [None]
            for dataset in rows:
                writer.writerow(
                    [
                        project.id,
                        project.name,
                        binding.mine_fid,
                        binding.mine_name_snapshot or "",
                        getattr(dataset, "id", ""),
                        getattr(dataset, "dataset_kind", ""),
                        getattr(dataset, "display_name", ""),
                        getattr(dataset, "year_start", ""),
                        getattr(dataset, "year_end", ""),
                    ]
                )


def _write_shp_zip(path_obj, geojson_payload, stable_stem=None, project_id=None, record_id=None):
    try:
        from osgeo import ogr, osr
    except Exception as exc:
        raise RuntimeError(f"SHP 导出依赖不可用: {exc}")

    archive_stem = str(stable_stem or path_obj.stem).strip()
    temp_dir = path_obj.parent / f".{archive_stem}.{uuid.uuid4().hex}.tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        base_path = temp_dir / archive_stem

        driver = ogr.GetDriverByName("ESRI Shapefile")
        dataset = driver.CreateDataSource(str(base_path.with_suffix(".shp")))
        if dataset is None:
            raise RuntimeError("无法创建 SHP 数据源")

        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        layer = dataset.CreateLayer(archive_stem, srs, ogr.wkbPolygon)
        layer.CreateField(ogr.FieldDefn("project_id", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("mine_fid", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("dataset_id", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("result_type", ogr.OFTString))
        layer.CreateField(ogr.FieldDefn("year_start", ogr.OFTInteger))
        layer.CreateField(ogr.FieldDefn("year_end", ogr.OFTInteger))

        for feature in geojson_payload.get("features", []):
            geometry = ogr.CreateGeometryFromJson(json.dumps(feature.get("geometry")))
            if geometry is None:
                continue
            row = ogr.Feature(layer.GetLayerDefn())
            props = feature.get("properties") or {}
            row.SetField("project_id", int(props.get("project_id") or 0))
            row.SetField("mine_fid", int(props.get("mine_fid") or 0))
            row.SetField("dataset_id", int(props.get("dataset_id") or 0))
            row.SetField("result_type", str(props.get("result_type") or ""))
            row.SetField("year_start", int(props.get("year_start") or 0))
            row.SetField("year_end", int(props.get("year_end") or 0))
            row.SetGeometry(geometry)
            layer.CreateFeature(row)
            row = None
        dataset = None

        with zipfile.ZipFile(path_obj, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for ext in (".shp", ".shx", ".dbf", ".prj"):
                file_obj = base_path.with_suffix(ext)
                if file_obj.exists():
                    archive.write(file_obj, arcname=file_obj.name)
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError as cleanup_exc:
            _log_storage_cleanup_failure(
                "exports",
                project_id,
                record_id,
                "shp_staging_cleanup",
                cleanup_exc,
            )


def _write_artifact_atomic(target_path, writer, *args, project_id=None, record_id=None):
    temporary_path = target_path.parent / f".{target_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        writer(temporary_path, *args)
        os.replace(temporary_path, target_path)
    except Exception:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as cleanup_exc:
                _log_storage_cleanup_failure(
                    "exports",
                    project_id,
                    record_id,
                    "artifact_temp_cleanup",
                    cleanup_exc,
                )
        raise


def _export_directory(project_id, export_id):
    return resolve_project_record_root(project_id, "exports", export_id, create=False)


def _snapshot_directory(project_id, backup_id):
    return resolve_project_record_root(project_id, "snapshots", backup_id, create=False)


def _cleanup_storage_directory(project_id, area, record_id):
    try:
        if area == "exports":
            path_obj = _export_directory(project_id, record_id)
        else:
            path_obj = _snapshot_directory(project_id, record_id)
        shutil.rmtree(path_obj)
    except (OSError, ProjectStorageValidationError) as cleanup_exc:
        _log_storage_cleanup_failure(
            area,
            project_id,
            record_id,
            "record_directory_cleanup",
            cleanup_exc,
        )
        return


def _mark_export_failed(project_id, record_id):
    try:
        db.session.remove()
        record = ProjectExportRecord.query.get(record_id)
        if record is not None:
            record.status = "failed"
            db.session.commit()
    except Exception as update_exc:
        _rollback_after_storage_failure(
            "exports",
            project_id,
            record_id,
            "mark_failed_rollback",
        )
        _log_storage_cleanup_failure(
            "exports",
            project_id,
            record_id,
            "mark_failed_status",
            update_exc,
        )


def _mark_backup_failed(project_id, backup_id):
    try:
        db.session.remove()
        backup = ProjectBackupRecord.query.get(backup_id)
        if backup is not None:
            backup.status = "failed"
            backup.restorable = False
            db.session.commit()
    except Exception as update_exc:
        _rollback_after_storage_failure(
            "snapshots",
            project_id,
            backup_id,
            "mark_failed_rollback",
        )
        _log_storage_cleanup_failure(
            "snapshots",
            project_id,
            backup_id,
            "mark_failed_status",
            update_exc,
        )


def _file_checksum(path_obj):
    checksum = hashlib.sha256()
    with open(path_obj, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _export_source_assets(project, export_format, features):
    imagery_by_id = {
        dataset.id: dataset
        for dataset in project.datasets
        if dataset.id is not None and dataset.dataset_kind == "imagery"
    }
    if export_format == "csv":
        dataset_ids = sorted(imagery_by_id)
    else:
        dataset_ids = set()
        for feature in features or []:
            properties = (feature or {}).get("properties") or {}
            dataset_id = properties.get("dataset_id")
            if isinstance(dataset_id, bool):
                continue
            try:
                dataset_id = int(dataset_id)
            except (TypeError, ValueError):
                continue
            if dataset_id in imagery_by_id:
                dataset_ids.add(dataset_id)
        dataset_ids = sorted(dataset_ids)
    source_asset_ids = [f"imagery:{dataset_id}" for dataset_id in dataset_ids]
    return source_asset_ids, {asset_id: 1 for asset_id in source_asset_ids}


def _export_manifest(project, record, artifact_name, artifact_path, features):
    source_asset_ids, source_versions = _export_source_assets(
        project,
        record.format,
        features,
    )
    return {
        "project_id": project.id,
        "export_id": record.id,
        "format": record.format,
        "artifact_name": artifact_name,
        "created_at": _utc_timestamp(record.create_time),
        "source_asset_ids": source_asset_ids,
        "source_versions": source_versions,
        "sha256": _file_checksum(artifact_path),
    }


def create_export(project_id, payload, actor="system"):
    if not isinstance(payload, dict):
        raise ProjectStorageValidationError("导出请求格式不合法")
    if "output_dir" in payload:
        raise ProjectStorageValidationError("不支持指定服务端输出目录，请移除 output_dir")
    if set(payload).difference({"format", "features"}):
        raise ProjectStorageValidationError("导出请求包含不支持字段")
    if "features" in payload and not isinstance(payload["features"], list):
        raise ProjectStorageValidationError("导出要素格式不合法")
    project = _get_project_or_404(project_id)
    export_format = str(payload.get("format") or "").strip().lower()
    if export_format not in ALLOWED_EXPORT_FORMATS:
        raise ValueError("导出格式不合法")
    features = payload.get("features")
    if features is None and export_format in {"geojson", "shp"}:
        features = _build_project_export_features(project)
    record = ProjectExportRecord(
        project_id=project.id,
        format=export_format,
        status="pending",
        request_params_json=_json_dump({k: v for k, v in payload.items() if k != "features"}),
    )
    project_identifier = project.id
    record_id = None
    try:
        db.session.add(record)
        db.session.flush()
        record_id = record.id
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "exports",
            project_identifier,
            record_id,
            "initial_pending_rollback",
        )
        raise

    suffix = {"geojson": ".geojson", "csv": ".csv", "shp": ".zip"}[export_format]
    artifact_name = f"artifact{suffix}"
    artifact_key = _storage_key("projects", str(project_identifier), "exports", str(record_id), artifact_name)
    export_features = []

    try:
        export_root(project_identifier, record_id)
        target_path = resolve_project_record_file(
            project_identifier,
            "exports",
            record_id,
            artifact_name,
        )
        if export_format == "geojson":
            geojson_payload = _make_geojson_payload(project, features or [])
            export_features = geojson_payload["features"]
            _write_artifact_atomic(
                target_path,
                _write_geojson,
                geojson_payload,
                project_id=project_identifier,
                record_id=record_id,
            )
        elif export_format == "csv":
            _write_artifact_atomic(
                target_path,
                _write_csv,
                project,
                project_id=project_identifier,
                record_id=record_id,
            )
        else:
            geojson_payload = _make_geojson_payload(project, features or [])
            export_features = geojson_payload["features"]
            _write_artifact_atomic(
                target_path,
                _write_shp_zip,
                geojson_payload,
                target_path.stem,
                project_identifier,
                record_id,
                project_id=project_identifier,
                record_id=record_id,
            )
        manifest_path = resolve_project_record_file(
            project_identifier,
            "exports",
            record_id,
            "manifest.json",
        )
        write_json_atomic(
            manifest_path,
            _export_manifest(project, record, artifact_name, target_path, export_features),
            area="exports",
            project_id=project_identifier,
            record_id=record_id,
        )
        record.file_path = artifact_key
        record.status = "completed"
        _append_activity(
            project_identifier,
            "export_created",
            {"export_id": record_id, "format": export_format},
            actor=actor,
            target={"type": "export", "id": str(record_id)},
        )
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "exports",
            project_identifier,
            record_id,
            "final_export_rollback",
        )
        _cleanup_storage_directory(project_identifier, "exports", record_id)
        _mark_export_failed(project_identifier, record_id)
        raise
    return ProjectExportRecordSchema().dump(record)


def list_exports(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectExportRecordSchema(many=True).dump(project.exports), "count": len(project.exports)}


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
            db.session.add(
                ProjectActivityLog(
                    project_id=project.id,
                    event_type=item["event_type"],
                    actor=item.get("actor") or "system",
                    payload_json=_json_dump(item.get("payload")),
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
