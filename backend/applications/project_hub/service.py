import csv
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from applications.extensions import db
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectBackupRecord,
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.schemas.project import (
    ProjectActivityLogSchema,
    ProjectBackupRecordSchema,
    ProjectDatasetSchema,
    ProjectExportRecordSchema,
    ProjectMineBindingSchema,
    ProjectSummarySchema,
)
from applications.project_hub.spatial_state import serialize_project_spatial_state


ALLOWED_PROJECT_STATUSES = {"draft", "active", "completed", "archived"}
ALLOWED_DATASET_KINDS = {"imagery", "inference_result", "report", "export_package", "mine_indices"}
ALLOWED_EXPORT_FORMATS = {"geojson", "csv", "shp"}


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


def _ensure_dir(path_value):
    path_obj = Path(path_value)
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


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


def _append_activity(project_id, event_type, payload=None, actor="system"):
    row = ProjectActivityLog(
        project_id=project_id,
        event_type=event_type,
        actor=actor,
        payload_json=_json_dump(payload),
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


def create_project(payload):
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
    _append_activity(project.id, "project_created", {"name": project.name})
    db.session.commit()
    return _serialize_summary(project)


def update_project(project_id, payload):
    project = _get_project_or_404(project_id)
    for field in ("name", "region", "manager", "remark", "monitor_start_year", "monitor_end_year"):
        if field in payload:
            setattr(project, field, payload.get(field))
    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        if status not in ALLOWED_PROJECT_STATUSES:
            raise ValueError("项目状态不合法")
        project.status = status
    _append_activity(project.id, "project_updated", {"fields": sorted(payload.keys())})
    db.session.commit()
    return _serialize_summary(project)


def replace_project_mines(project_id, mines):
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
    _append_activity(project.id, "mine_binding_replaced", {"mine_count": len(mines)})
    db.session.commit()
    return {"mine_count": len(project.mines), "items": ProjectMineBindingSchema(many=True).dump(project.mines)}


def create_dataset(project_id, payload):
    project = _get_project_or_404(project_id)
    dataset_kind = str(payload.get("dataset_kind") or "").strip()
    if dataset_kind not in ALLOWED_DATASET_KINDS:
        raise ValueError("数据集类型不合法")
    display_name = str(payload.get("display_name") or "").strip()
    if not display_name:
        raise ValueError("数据集名称不能为空")
    file_path = str(payload.get("file_path") or "").strip()
    if not file_path:
        raise ValueError("数据文件路径不能为空")
    dataset = ProjectDataset(
        project_id=project.id,
        dataset_kind=dataset_kind,
        display_name=display_name,
        file_path=file_path,
        source_format=str(payload.get("source_format") or "").strip() or None,
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
    )
    if project.status == "draft":
        project.status = "active"
    db.session.commit()
    return ProjectDatasetSchema().dump(dataset)


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


def get_project_timeline(project_id):
    project = _get_project_or_404(project_id)
    items = []
    for activity in sorted(project.activities, key=lambda row: row.create_time, reverse=True):
        payload = _json_load(activity.payload_json, {})
        items.append(
            {
                "id": activity.id,
                "event_type": activity.event_type,
                "actor": activity.actor,
                "payload": payload,
                "timestamp": activity.create_time.isoformat(),
            }
        )
    return {"items": items, "count": len(items)}


def archive_project(project_id):
    project = _get_project_or_404(project_id)
    project.status = "archived"
    _append_activity(project.id, "project_archived", {})
    db.session.commit()
    return _serialize_summary(project)


def restore_project(project_id):
    project = _get_project_or_404(project_id)
    project.status = "active"
    project.deleted_at = None
    _append_activity(project.id, "project_restored", {})
    db.session.commit()
    return _serialize_summary(project)


def _make_geojson_payload(project_id, features):
    normalized = []
    for feature in features or []:
        item = dict(feature)
        item["properties"] = dict(item.get("properties") or {})
        item["properties"].setdefault("project_id", project_id)
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
        "file_path",
    ]
    dataset_by_mine = {}
    for dataset in project.datasets:
        dataset_by_mine.setdefault(dataset.mine_fid, []).append(dataset)
    with open(path_obj, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        if not project.mines and not project.datasets:
            writer.writerow([project.id, project.name, "", "", "", "", "", "", "", ""])
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
                        getattr(dataset, "file_path", ""),
                    ]
                )


def _write_shp_zip(path_obj, geojson_payload):
    try:
        from osgeo import ogr, osr
    except Exception as exc:
        raise RuntimeError(f"SHP 导出依赖不可用: {exc}")

    temp_dir = path_obj.parent / f"{path_obj.stem}_tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    base_path = temp_dir / path_obj.stem

    driver = ogr.GetDriverByName("ESRI Shapefile")
    dataset = driver.CreateDataSource(str(base_path.with_suffix(".shp")))
    if dataset is None:
        raise RuntimeError("无法创建 SHP 数据源")

    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    layer = dataset.CreateLayer(path_obj.stem, srs, ogr.wkbPolygon)
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
    shutil.rmtree(temp_dir, ignore_errors=True)


def create_export(project_id, payload):
    project = _get_project_or_404(project_id)
    export_format = str(payload.get("format") or "").strip().lower()
    if export_format not in ALLOWED_EXPORT_FORMATS:
        raise ValueError("导出格式不合法")
    output_dir = _ensure_dir(payload.get("output_dir") or Path("static") / "project_exports" / str(project_id))
    record = ProjectExportRecord(
        project_id=project.id,
        format=export_format,
        status="pending",
        request_params_json=_json_dump({k: v for k, v in payload.items() if k != "features"}),
    )
    db.session.add(record)
    db.session.flush()

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    suffix = {"geojson": ".geojson", "csv": ".csv", "shp": ".zip"}[export_format]
    target_path = output_dir / f"project_{project.id}_{record.id}_{timestamp}{suffix}"

    try:
        if export_format == "geojson":
            geojson_payload = _make_geojson_payload(project.id, payload.get("features") or [])
            _write_geojson(target_path, geojson_payload)
        elif export_format == "csv":
            _write_csv(target_path, project)
        else:
            geojson_payload = _make_geojson_payload(project.id, payload.get("features") or [])
            _write_shp_zip(target_path, geojson_payload)
        record.file_path = str(target_path.resolve())
        record.status = "completed"
        _append_activity(
            project.id,
            "export_created",
            {"export_id": record.id, "format": export_format, "file_path": record.file_path},
        )
        db.session.commit()
    except Exception:
        record.status = "failed"
        db.session.commit()
        raise
    return ProjectExportRecordSchema().dump(record)


def list_exports(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectExportRecordSchema(many=True).dump(project.exports), "count": len(project.exports)}


def _project_manifest(project):
    return {
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
        "datasets": ProjectDatasetSchema(many=True).dump(project.datasets),
        "exports": ProjectExportRecordSchema(many=True).dump(project.exports),
        "activities": ProjectActivityLogSchema(many=True).dump(project.activities),
    }


def create_backup(project_id, payload):
    project = _get_project_or_404(project_id)
    scope = str(payload.get("scope") or "metadata_index").strip() or "metadata_index"
    output_dir = _ensure_dir(payload.get("output_dir") or Path("static") / "project_backups" / str(project_id))
    manifest_path = output_dir / f"project_{project.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(_project_manifest(project), handle, ensure_ascii=False, indent=2)
    backup = ProjectBackupRecord(
        project_id=project.id,
        scope=scope,
        manifest_path=str(manifest_path.resolve()),
        status="completed",
        restorable=True,
    )
    db.session.add(backup)
    db.session.flush()
    _append_activity(
        project.id,
        "backup_created",
        {"backup_id": backup.id, "manifest_path": backup.manifest_path, "scope": scope},
    )
    db.session.commit()
    return ProjectBackupRecordSchema().dump(backup)


def list_backups(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectBackupRecordSchema(many=True).dump(project.backups), "count": len(project.backups)}


def restore_backup(project_id, backup_id):
    project = _get_project_or_404(project_id)
    backup = ProjectBackupRecord.query.filter_by(id=backup_id, project_id=project.id).first()
    if not backup:
        raise ValueError(f"备份不存在: {backup_id}")
    with open(backup.manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    summary = manifest.get("summary") or {}
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

    for item in manifest.get("mines") or []:
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

    for item in manifest.get("datasets") or []:
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

    for item in manifest.get("exports") or []:
        db.session.add(
            ProjectExportRecord(
                project_id=project.id,
                format=item["format"],
                file_path=item.get("file_path"),
                status=item.get("status") or "completed",
                request_params_json=_json_dump(item.get("request_params")),
            )
        )

    for item in manifest.get("activities") or []:
        db.session.add(
            ProjectActivityLog(
                project_id=project.id,
                event_type=item["event_type"],
                actor=item.get("actor") or "system",
                payload_json=_json_dump(item.get("payload")),
            )
        )

    _append_activity(project.id, "backup_restored", {"backup_id": backup.id})
    db.session.commit()
    return get_project_detail(project.id)
