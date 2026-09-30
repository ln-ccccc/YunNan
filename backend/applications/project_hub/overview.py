# -*- coding: utf-8 -*-
"""项目概览/摘要序列化域（H3 拆分，2026-10-01）：summary DTO、overview 空间三键
（M2：readiness+资产单轨派生）、活动时间线安全序列化与看板计数。契约不变。"""

from applications.models.inference_job import InferenceJob
from applications.models.project_spatial import ProjectSpatialJob
from applications.project_hub._foundation import _json_load, _utc_timestamp
from applications.project_hub.spatial_state import serialize_project_spatial_state
from applications.project_hub.spatial_storage import get_storage_root
from applications.schemas.project import ProjectSummarySchema


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
