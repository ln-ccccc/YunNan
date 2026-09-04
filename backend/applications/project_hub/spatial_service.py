import json
import shutil
import uuid
from pathlib import Path

from osgeo import gdal, ogr, osr
from sqlalchemy import func

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
from applications.project_hub.spatial_storage import ensure_storage_layout, resolve_storage_path


MAX_MINE_VECTOR_BYTES = 50 * 1024 * 1024
SUPPORTED_MINE_EXTENSIONS = {".geojson", ".json", ".kml"}
FIELD_CANDIDATES = {
    "fid": ("FID_1", "FID", "OBJECTID", "id"),
    "name": ("name", "mine_name", "GGKSMC", "SBKSMC", "ZLKSMC", "矿山名称", "矿山名"),
    "region": ("city", "region", "county", "SHI", "SHI_1", "区域", "行政区"),
    "status": ("status", "state", "HFZLQK", "ZLHFZLQK", "状态"),
    "area": ("area", "shape_area", "TBTYMJ_1", "TBTYMJ", "面积"),
}


def _content_bytes(content):
    if isinstance(content, bytes):
        raw = content
    else:
        raw = str(content or "").encode("utf-8")
    if not raw:
        raise ValueError("矿山文件不能为空")
    if len(raw) > MAX_MINE_VECTOR_BYTES:
        raise ValueError("矿山文件不能超过 50 MB")
    return raw


def _suggest_fields(field_names):
    lookup = {name.casefold(): name for name in field_names}
    result = {}
    for target, candidates in FIELD_CANDIDATES.items():
        result[target] = next((lookup[item.casefold()] for item in candidates if item.casefold() in lookup), None)
    return result


def _open_vector(filename, content):
    extension = Path(str(filename or "")).suffix.lower()
    if extension not in SUPPORTED_MINE_EXTENSIONS:
        raise ValueError("矿山文件仅支持 KML、GeoJSON")
    raw = _content_bytes(content)
    virtual_path = f"/vsimem/project-mine-{uuid.uuid4().hex}{extension}"
    gdal.FileFromMemBuffer(virtual_path, raw)
    dataset = ogr.Open(virtual_path)
    if dataset is None:
        gdal.Unlink(virtual_path)
        raise ValueError("矿山文件无法解析，请检查格式")
    return dataset, virtual_path, extension


def _extract_vector(filename, content):
    dataset, virtual_path, extension = _open_vector(filename, content)
    try:
        layer = dataset.GetLayer(0)
        if layer is None:
            raise ValueError("矿山文件不包含可用图层")
        definition = layer.GetLayerDefn()
        field_names = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
        source_crs = layer.GetSpatialRef()
        target_crs = osr.SpatialReference()
        target_crs.ImportFromEPSG(4326)
        target_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = None
        if source_crs is not None:
            source_crs = source_crs.Clone()
            source_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
            if not source_crs.IsSame(target_crs):
                transform = osr.CoordinateTransformation(source_crs, target_crs)

        features = []
        bounds = None
        for source_feature in layer:
            geometry = source_feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                raise ValueError("矿山要素存在空几何")
            geometry = geometry.Clone()
            geometry_type = ogr.GT_Flatten(geometry.GetGeometryType())
            if geometry_type not in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
                raise ValueError("矿山要素必须是 Polygon 或 MultiPolygon")
            if transform is not None:
                geometry.Transform(transform)
            envelope = geometry.GetEnvelope()
            item_bounds = [envelope[0], envelope[2], envelope[1], envelope[3]]
            if bounds is None:
                bounds = item_bounds
            else:
                bounds = [
                    min(bounds[0], item_bounds[0]),
                    min(bounds[1], item_bounds[1]),
                    max(bounds[2], item_bounds[2]),
                    max(bounds[3], item_bounds[3]),
                ]
            properties = {name: source_feature.GetField(name) for name in field_names}
            features.append(
                {
                    "type": "Feature",
                    "properties": properties,
                    "geometry": json.loads(geometry.ExportToJson()),
                }
            )
        if not features:
            raise ValueError("矿山文件不包含要素")
        return {
            "extension": extension,
            "field_names": field_names,
            "features": features,
            "bounds": bounds,
        }
    finally:
        dataset = None
        gdal.Unlink(virtual_path)


def _validate_fids(features, fid_field):
    if not fid_field:
        raise ValueError("未识别到唯一 FID 字段，请手动选择")
    values = []
    for feature in features:
        value = feature["properties"].get(fid_field)
        if value in (None, ""):
            raise ValueError("FID 不能为空")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("FID 必须是整数") from exc
        if normalized <= 0 or normalized > 2_147_483_647:
            raise ValueError("FID 必须在 1 到 2147483647 之间")
        values.append(normalized)
    if len(values) != len(set(values)):
        raise ValueError("FID duplicate：FID 必须唯一")
    return values


def preview_mine_vector(filename, content):
    extracted = _extract_vector(filename, content)
    suggested_mapping = _suggest_fields(extracted["field_names"])
    _validate_fids(extracted["features"], suggested_mapping["fid"])
    return {
        "filename": Path(filename).name,
        "source_format": "kml" if extracted["extension"] == ".kml" else "geojson",
        "feature_count": len(extracted["features"]),
        "field_names": extracted["field_names"],
        "suggested_mapping": suggested_mapping,
        "crs": "EPSG:4326",
        "bounds": extracted["bounds"],
        "sample": [feature["properties"] for feature in extracted["features"][:5]],
    }


def _serialize_resource(resource):
    return {
        "id": resource.id,
        "project_id": resource.project_id,
        "resource_type": resource.resource_type,
        "version": resource.version,
        "status": resource.status,
        "source_path": resource.source_path,
        "normalized_path": resource.normalized_path,
        "tile_path": resource.tile_path,
        "source_format": resource.source_format,
        "feature_count": resource.feature_count,
        "crs": resource.crs,
        "bounds": json.loads(resource.bounds_json or "{}"),
        "min_zoom": resource.min_zoom,
        "max_zoom": resource.max_zoom,
        "error_message": resource.error_message,
    }


def _serialize_job(job):
    return {
        "id": job.id,
        "project_id": job.project_id,
        "resource_id": job.resource_id,
        "job_type": job.job_type,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "error_message": job.error_message,
    }


def _mapped_value(properties, mapping, target):
    field_name = mapping.get(target)
    return properties.get(field_name) if field_name else None


def _binding_rows(features, mapping):
    fids = _validate_fids(features, mapping.get("fid"))
    rows = []
    for index, (feature, fid) in enumerate(zip(features, fids)):
        properties = feature["properties"]
        raw_area = _mapped_value(properties, mapping, "area")
        try:
            area = float(raw_area) if raw_area not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise ValueError("面积字段必须是数字") from exc
        rows.append(
            ProjectMineBinding(
                mine_fid=fid,
                mine_name_snapshot=str(_mapped_value(properties, mapping, "name") or "").strip() or None,
                city_snapshot=str(_mapped_value(properties, mapping, "region") or "").strip() or None,
                area_snapshot=area,
                status_snapshot=str(_mapped_value(properties, mapping, "status") or "").strip() or None,
                sort_order=index,
            )
        )
    return rows


def _activity(project_id, event_type, payload):
    db.session.add(
        ProjectActivityLog(
            project_id=project_id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )


def import_mine_vector(project_id, filename, content, field_mapping=None):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")

    extracted = _extract_vector(filename, content)
    suggested = _suggest_fields(extracted["field_names"])
    mapping = {**suggested, **(field_mapping or {})}
    valid_fields = set(extracted["field_names"])
    for target, field_name in mapping.items():
        if field_name and field_name not in valid_fields:
            raise ValueError(f"字段映射不存在: {target}={field_name}")
    bindings = _binding_rows(extracted["features"], mapping)
    raw = _content_bytes(content)

    latest_version = (
        db.session.query(func.max(ProjectSpatialResource.version))
        .filter_by(project_id=project_id, resource_type="mine_vector")
        .scalar()
        or 0
    )
    extension = extracted["extension"]
    resource = ProjectSpatialResource(
        project_id=project_id,
        resource_type="mine_vector",
        version=latest_version + 1,
        status="processing",
        source_path="pending",
        source_format="kml" if extension == ".kml" else "geojson",
        feature_count=len(extracted["features"]),
        crs="EPSG:4326",
        bounds_json=json.dumps(extracted["bounds"]),
    )
    db.session.add(resource)
    db.session.flush()

    storage_root = ensure_storage_layout()
    relative_dir = Path("projects") / str(project_id) / "mines" / str(resource.id)
    resource_dir = storage_root / relative_dir
    source_relative = relative_dir / f"source{extension}"
    normalized_relative = relative_dir / "mines.geojson"
    resource.source_path = source_relative.as_posix()
    resource.normalized_path = normalized_relative.as_posix()
    try:
        resource_dir.mkdir(parents=True, exist_ok=False)
        (storage_root / source_relative).write_bytes(raw)
        normalized = {"type": "FeatureCollection", "features": extracted["features"]}
        (storage_root / normalized_relative).write_text(
            json.dumps(normalized, ensure_ascii=False),
            encoding="utf-8",
        )

        previous_active = ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type="mine_vector",
            status="active",
        ).all()
        for previous in previous_active:
            previous.status = "retained"
        project.mines[:] = []
        db.session.flush()
        project.mines.extend(bindings)
        resource.status = "active"

        retained = (
            ProjectSpatialResource.query.filter_by(
                project_id=project_id,
                resource_type="mine_vector",
                status="retained",
            )
            .order_by(ProjectSpatialResource.version.desc())
            .all()
        )
        removed_paths = []
        for obsolete in retained[1:]:
            removed_paths.append(resolve_storage_path(storage_root, Path(obsolete.source_path).parent))
            _activity(
                project_id,
                "spatial_resource_removed",
                {"resource_id": obsolete.id, "resource_type": obsolete.resource_type, "version": obsolete.version},
            )
            db.session.delete(obsolete)
        _activity(
            project_id,
            "spatial_resource_activated",
            {
                "resource_id": resource.id,
                "resource_type": resource.resource_type,
                "version": resource.version,
                "feature_count": resource.feature_count,
            },
        )
        db.session.commit()
        for obsolete_path in removed_paths:
            shutil.rmtree(obsolete_path, ignore_errors=True)
    except Exception:
        db.session.rollback()
        shutil.rmtree(resource_dir, ignore_errors=True)
        raise

    return {
        "resource": _serialize_resource(resource),
        "mine_count": len(bindings),
        "field_mapping": mapping,
    }


def _raster_metadata(path):
    dataset = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)
    if dataset is None:
        raise ValueError(f"无法读取 GeoTIFF: {path.name}")
    try:
        projection = dataset.GetProjectionRef()
        if not projection:
            raise ValueError(f"GeoTIFF 缺少 CRS: {path.name}")
        source_crs = osr.SpatialReference()
        source_crs.ImportFromWkt(projection)
        source_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target_crs = osr.SpatialReference()
        target_crs.ImportFromEPSG(4326)
        target_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = None if source_crs.IsSame(target_crs) else osr.CoordinateTransformation(source_crs, target_crs)
        geotransform = dataset.GetGeoTransform(can_return_null=True)
        if geotransform is None:
            raise ValueError(f"GeoTIFF 缺少地理变换: {path.name}")

        def corner(pixel, line):
            x = geotransform[0] + pixel * geotransform[1] + line * geotransform[2]
            y = geotransform[3] + pixel * geotransform[4] + line * geotransform[5]
            if transform is not None:
                x, y, _ = transform.TransformPoint(x, y)
            return x, y

        corners = [
            corner(0, 0),
            corner(dataset.RasterXSize, 0),
            corner(0, dataset.RasterYSize),
            corner(dataset.RasterXSize, dataset.RasterYSize),
        ]
        xs = [item[0] for item in corners]
        ys = [item[1] for item in corners]
        authority = source_crs.GetAuthorityName(None)
        code = source_crs.GetAuthorityCode(None)
        crs_name = f"{authority}:{code}" if authority and code else source_crs.GetName()
        return {
            "crs": crs_name,
            "bounds": [min(xs), min(ys), max(xs), max(ys)],
            "width": dataset.RasterXSize,
            "height": dataset.RasterYSize,
        }
    finally:
        dataset = None


def _bounds_intersect(first, second):
    if not first or not second or len(first) != 4 or len(second) != 4:
        return False
    return not (
        first[2] < second[0]
        or first[0] > second[2]
        or first[3] < second[1]
        or first[1] > second[3]
    )


def _active_mine_bounds(project_id):
    resource = (
        ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type="mine_vector",
            status="active",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .first()
    )
    return json.loads(resource.bounds_json) if resource else None


def _candidate_relative_path(candidate):
    value = Path(str(candidate or ""))
    if value.is_absolute() or not value.parts or value.parts[0].casefold() != "incoming":
        raise ValueError("底图必须来自 project_storage/incoming 目录")
    if value.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("底图仅支持 TIF/TIFF")
    return value


def _sidecar_paths(path):
    names = [
        Path(f"{path}.ovr"),
        path.with_suffix(".tfw"),
        path.with_suffix(".prj"),
        path.with_suffix(".enp"),
        Path(f"{path}.enp"),
        Path(f"{path}.aux.xml"),
        path.with_suffix(".aux.xml"),
    ]
    return sorted({item.name for item in names if item.is_file()})


def list_basemap_candidates(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    root = ensure_storage_layout()
    mine_bounds = _active_mine_bounds(project_id)
    items = []
    for path in sorted((root / "incoming").rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        metadata = _raster_metadata(path)
        items.append(
            {
                "candidate": path.relative_to(root).as_posix(),
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sidecars": _sidecar_paths(path),
                **metadata,
                "intersects_mines": _bounds_intersect(metadata["bounds"], mine_bounds),
            }
        )
    return items


def register_basemap(project_id, candidate, min_zoom=8, max_zoom=15):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    candidate_relative = _candidate_relative_path(candidate)
    storage_root = ensure_storage_layout()
    source = resolve_storage_path(storage_root, candidate_relative)
    try:
        source.relative_to(storage_root / "incoming")
    except ValueError as exc:
        raise ValueError("底图必须来自 project_storage/incoming 目录") from exc
    if not source.is_file():
        raise ValueError("底图候选文件不存在")
    if not (0 <= int(min_zoom) <= int(max_zoom) <= 22):
        raise ValueError("瓦片层级必须满足 0 <= min_zoom <= max_zoom <= 22")
    metadata = _raster_metadata(source)
    mine_bounds = _active_mine_bounds(project_id)
    if mine_bounds is None:
        raise ValueError("请先导入并激活项目矿山")
    if not _bounds_intersect(metadata["bounds"], mine_bounds):
        raise ValueError("底图范围与项目矿山范围完全不相交")
    disk = shutil.disk_usage(storage_root)
    required_bytes = max(source.stat().st_size * 2, 100 * 1024 * 1024)
    if disk.free < required_bytes:
        raise ValueError(f"磁盘空间不足，至少需要 {required_bytes} 字节可用空间")

    latest_version = (
        db.session.query(func.max(ProjectSpatialResource.version))
        .filter_by(project_id=project_id, resource_type="basemap")
        .scalar()
        or 0
    )
    resource = ProjectSpatialResource(
        project_id=project_id,
        resource_type="basemap",
        version=latest_version + 1,
        status="pending",
        source_path=candidate_relative.as_posix(),
        source_format=source.suffix.lower().lstrip("."),
        crs=metadata["crs"],
        bounds_json=json.dumps(metadata["bounds"]),
        min_zoom=int(min_zoom),
        max_zoom=int(max_zoom),
    )
    db.session.add(resource)
    db.session.flush()
    resource.tile_path = f"projects/{project_id}/tiles/{resource.id}"
    job = ProjectSpatialJob(
        id=str(uuid.uuid4()),
        project_id=project_id,
        resource_id=resource.id,
        job_type="basemap_tiles",
        status="queued",
        stage="queued",
        progress=0.0,
    )
    db.session.add(job)
    _activity(
        project_id,
        "spatial_job_queued",
        {"job_id": job.id, "resource_id": resource.id, "resource_type": "basemap"},
    )
    db.session.commit()
    return {"resource": _serialize_resource(resource), "job": _serialize_job(job)}


def get_project_spatial(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    from applications.project_hub.spatial_state import serialize_project_spatial_state

    return {
        **serialize_project_spatial_state(project),
        "resources": [_serialize_resource(item) for item in project.spatial_resources],
        "jobs": [_serialize_job(item) for item in project.spatial_jobs],
    }


def get_spatial_job(project_id, job_id):
    job = ProjectSpatialJob.query.filter_by(id=job_id, project_id=project_id).first()
    if job is None:
        raise ValueError("空间处理任务不存在")
    return _serialize_job(job)


def retry_spatial_job(project_id, job_id):
    job = ProjectSpatialJob.query.filter_by(id=job_id, project_id=project_id).first()
    if job is None:
        raise ValueError("空间处理任务不存在")
    if job.status not in {"failed", "cancelled"}:
        raise ValueError("只有失败或已取消的任务可以重试")
    resource = ProjectSpatialResource.query.filter_by(id=job.resource_id, project_id=project_id).first()
    if resource is None:
        raise ValueError("空间资源不存在")
    job.status = "queued"
    job.stage = "queued"
    job.progress = 0.0
    job.cancel_requested = False
    job.error_message = None
    job.worker_id = None
    job.heartbeat_at = None
    resource.status = "pending"
    resource.error_message = None
    _activity(project_id, "spatial_job_retried", {"job_id": job.id, "resource_id": resource.id})
    db.session.commit()
    return _serialize_job(job)


def cancel_spatial_job(project_id, job_id):
    job = ProjectSpatialJob.query.filter_by(id=job_id, project_id=project_id).first()
    if job is None:
        raise ValueError("空间处理任务不存在")
    if job.status in {"succeeded", "failed", "cancelled"}:
        raise ValueError("任务已经结束")
    if job.status == "queued":
        job.status = "cancelled"
        job.stage = "cancelled"
        resource = ProjectSpatialResource.query.filter_by(id=job.resource_id, project_id=project_id).first()
        if resource is not None:
            resource.status = "retained"
    else:
        job.cancel_requested = True
    _activity(project_id, "spatial_job_cancel_requested", {"job_id": job.id})
    db.session.commit()
    return _serialize_job(job)
