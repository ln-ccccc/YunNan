import logging
import shutil
import uuid

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
from applications.project_hub.spatial_storage import (
    SUPPORTED_INCOMING_RASTER_FORMATS,
    get_storage_root,
    resolve_storage_path,
)
from applications.project_hub.snapshots import (
    create_backup,
    import_backup_manifest,
    list_backups,
    resolve_project_backup_manifest,
    restore_backup,
)
from applications.project_hub.overview import (
    ACTION_CODE_BY_EVENT_TYPE,
    _build_project_counts,
    _is_unsafe_activity_value,
    _serialize_overview_activity,
    _serialize_overview_activity_list,
    _serialize_overview_summary,
    _serialize_summary,
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
LOGGER = logging.getLogger(__name__)


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

