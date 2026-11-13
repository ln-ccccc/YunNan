import json
import math
import os
import re
import shutil
import uuid
from pathlib import Path

from osgeo import gdal, ogr, osr
from werkzeug.exceptions import Conflict, RequestEntityTooLarge
from sqlalchemy import func

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource
from applications.project_hub.spatial_storage import ensure_storage_layout, resolve_storage_path


MAX_MINE_VECTOR_BYTES = 50 * 1024 * 1024
# 历史边界保留策略：与底图 RETAINED_BASEMAP_KEEP 对齐保留最近 2 个；
# 被活动任务引用的版本另行保护（见 _active_job_referenced_mine_resource_ids）
RETAINED_MINE_VECTOR_KEEP = 2
SUPPORTED_MINE_EXTENSIONS = {".geojson", ".json", ".kml"}
FIELD_CANDIDATES = {
    "fid": ("FID_1", "FID", "OBJECTID", "id"),
    "name": ("name", "mine_name", "GGKSMC", "SBKSMC", "ZLKSMC", "矿山名称", "矿山名"),
    "region": ("city", "region", "county", "SHI", "SHI_1", "区域", "行政区"),
    "status": ("status", "state", "HFZLQK", "ZLHFZLQK", "状态"),
    "area": ("area", "shape_area", "TBTYMJ_1", "TBTYMJ", "面积"),
}
PRIVATE_GEOJSON_PROPERTY_KEYS = frozenset(
    {
        "file_path",
        "source_path",
        "normalized_path",
        "tile_path",
        "manifest_path",
        "output_dir",
    }
)


def _is_public_geojson_property_key(key):
    return str(key).strip().casefold() not in PRIVATE_GEOJSON_PROPERTY_KEYS


def sanitize_public_geojson_value(value):
    if isinstance(value, dict):
        return {
            key: sanitize_public_geojson_value(item)
            for key, item in value.items()
            if _is_public_geojson_property_key(key)
        }
    if isinstance(value, list):
        return [sanitize_public_geojson_value(item) for item in value]
    return value


def sanitize_public_geojson_properties(properties):
    if not isinstance(properties, dict):
        return {}
    return sanitize_public_geojson_value(properties)


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
        raw_field_names = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
        field_names = [name for name in raw_field_names if _is_public_geojson_property_key(name)]
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
            properties = sanitize_public_geojson_properties(
                {name: source_feature.GetField(name) for name in raw_field_names}
            )
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
        if isinstance(value, bool):
            raise ValueError("FID 必须是整数")
        if isinstance(value, int):
            normalized = value
        elif isinstance(value, float):
            if not value.is_integer():
                raise ValueError("FID 必须是整数")
            normalized = int(value)
        elif isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
            normalized = int(value)
        else:
            raise ValueError("FID 必须是整数")
        if normalized <= 0 or normalized > 2_147_483_647:
            raise ValueError("FID 必须在 1 到 2147483647 之间")
        values.append(normalized)
    if len(values) != len(set(values)):
        raise ValueError("FID duplicate：FID 必须唯一")
    return values


def _suggested_fid_validation(features, fid_field):
    if not fid_field:
        return {"status": "needs_selection", "message": "未识别到唯一 FID 字段，请手动选择"}
    try:
        _validate_fids(features, fid_field)
    except ValueError as exc:
        return {"status": "invalid", "message": str(exc)}
    return {"status": "valid", "message": None}


def preview_mine_vector(filename, content):
    extracted = _extract_vector(filename, content)
    suggested_mapping = _suggest_fields(extracted["field_names"])
    return {
        "filename": Path(filename).name,
        "source_format": "kml" if extracted["extension"] == ".kml" else "geojson",
        "feature_count": len(extracted["features"]),
        "field_names": extracted["field_names"],
        "suggested_mapping": suggested_mapping,
        "suggested_fid_validation": _suggested_fid_validation(
            extracted["features"], suggested_mapping["fid"]
        ),
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
        "source_format": resource.source_format,
        "feature_count": resource.feature_count,
        "crs": resource.crs,
        "bounds": json.loads(resource.bounds_json or "{}"),
        "min_zoom": resource.min_zoom,
        "max_zoom": resource.max_zoom,
        "error_message": _public_error_message(resource.error_message),
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
        "error_message": _public_error_message(job.error_message),
    }


def _public_error_message(value):
    text = str(value or "").strip()
    normalized = text.replace("\\", "/")
    if not text:
        return None
    if "/" in normalized or normalized.casefold().startswith("file:"):
        return "空间资源处理失败"
    return text


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


def _canonicalize_mine_features(features, fid_field):
    fids = _validate_fids(features, fid_field)
    return [
        {
            **feature,
            "properties": {**feature["properties"], "FID_1": fid},
        }
        for feature, fid in zip(features, fids)
    ]


def _activity(
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
    db.session.add(
        ProjectActivityLog(
            project_id=project_id,
            event_type=event_type,
            actor=actor or "system",
            payload_json=json.dumps(event_payload, ensure_ascii=False),
        )
    )


def _active_job_referenced_mine_resource_ids(project_id):
    """queued/running 推理任务引用的矿山边界资源 ID（问题6）：任务 payload
    绑定了具体 mine_resource_id 与边界文件路径，被引用的版本清理时必须保留，
    否则连续导入新边界会把排队/运行中任务的输入静默删除。"""
    from applications.models.inference_job import InferenceJob

    referenced = set()
    jobs = InferenceJob.query.filter_by(project_id=int(project_id)).filter(
        InferenceJob.status.in_(("queued", "running"))
    )
    for job in jobs:
        payload = job.request_payload_json
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                continue
        if not isinstance(payload, dict):
            continue
        resource_id = payload.get("mine_resource_id")
        if isinstance(resource_id, int):
            referenced.add(resource_id)
        # 旧 payload 兜底：kml_path 形如 .../mines/<resource_id>/mines.geojson
        match = re.search(r"[\\/]mines[\\/]([0-9]+)[\\/]", str(payload.get("kml_path") or ""))
        if match:
            referenced.add(int(match.group(1)))
    return referenced


def _editable_project(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    if project.status == "archived":
        raise ValueError("归档项目不允许修改空间资源")
    return project


def import_mine_vector(project_id, filename, content, field_mapping=None, actor="system"):
    project = _editable_project(project_id)

    extracted = _extract_vector(filename, content)
    suggested = _suggest_fields(extracted["field_names"])
    mapping = {**suggested, **(field_mapping or {})}
    valid_fields = set(extracted["field_names"])
    for target, field_name in mapping.items():
        if field_name and field_name not in valid_fields:
            raise ValueError(f"字段映射不存在: {target}={field_name}")
    bindings = _binding_rows(extracted["features"], mapping)
    normalized_features = _canonicalize_mine_features(extracted["features"], mapping.get("fid"))
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
        normalized = {"type": "FeatureCollection", "features": normalized_features}
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
        referenced_ids = _active_job_referenced_mine_resource_ids(project_id)
        for obsolete in retained[RETAINED_MINE_VECTOR_KEEP:]:
            if obsolete.id in referenced_ids:
                _activity(
                    project_id,
                    "spatial_resource_retained_active_job",
                    {
                        "resource_id": obsolete.id,
                        "resource_type": obsolete.resource_type,
                        "version": obsolete.version,
                    },
                    actor=actor,
                    target={"type": "spatial_resource", "id": str(obsolete.id)},
                )
                continue
            removed_paths.append(resolve_storage_path(storage_root, Path(obsolete.source_path).parent))
            _activity(
                project_id,
                "spatial_resource_removed",
                {"resource_id": obsolete.id, "resource_type": obsolete.resource_type, "version": obsolete.version},
                actor=actor,
                target={"type": "spatial_resource", "id": str(obsolete.id)},
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
            actor=actor,
            target={"type": "spatial_resource", "id": str(resource.id)},
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
        raise ValueError(f"无法读取栅格文件（支持：{_BASEMAP_FORMAT_HINT}）: {path.name}")
    try:
        projection = dataset.GetProjectionRef()
        if not projection:
            raise ValueError(
                f"栅格缺少 CRS: {path.name}（JPG/PNG 需同名世界文件与 .prj，ENVI 需成对 .hdr）"
            )
        source_crs = osr.SpatialReference()
        source_crs.ImportFromWkt(projection)
        source_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        target_crs = osr.SpatialReference()
        target_crs.ImportFromEPSG(4326)
        target_crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        transform = None if source_crs.IsSame(target_crs) else osr.CoordinateTransformation(source_crs, target_crs)
        geotransform = dataset.GetGeoTransform(can_return_null=True)
        if geotransform is None:
            raise ValueError(f"栅格缺少地理变换: {path.name}（JPG/PNG 需同名世界文件）")

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


# 投递约定（甲方建议 2026-09-03）：复制中的半文件必须以这些标记段命名
# （如 base.tif.part / base.staging.tif），复制完成后才改回正式名；
# 可选地由投递工具写入 <name>.ready 作为完成标记（系统不强制）。
_INCOMPLETE_COPY_MARKERS = ("staging", "part", "tmp", "crdownload")

# 底图格式白名单（优化建议二.1 扩格式的安全切面：底图切片链 metadata/tiling
# 均走 GDAL 通用驱动，与推理输入固定 GeoTIFF 相互独立）。
# JPG/PNG 必须配同名世界文件（.jgw/.pgw 等）与 .prj 才有 CRS；
# ENVI 约定 <同名>.dat + <同名>.hdr 成对投递；IMG (Erdas HFA) 自带 CRS。
ALLOWED_BASEMAP_EXTENSIONS = {".tif", ".tiff", ".img", ".jpg", ".jpeg", ".png", ".dat"}
_BASEMAP_FORMAT_HINT = "TIF/TIFF、IMG、JPG/PNG（需同名世界文件+.prj）或 ENVI(.dat+.hdr)"

# 世界文件/头文件等辅助文件：登记时展示、拷贝时随源文件同行；
# .ige/.rrd 是 Erdas IMG 大文件的外部影像数据/金字塔（Erdas/ArcGIS 处理过的
# 真实交付常带，缺 .ige 时拷贝后的 .img 数据不完整、切片必然失败）
_BASEMAP_SIDECAR_SUFFIXES = (".jgw", ".pgw", ".gfw", ".wld", ".hdr", ".ige", ".rrd")


def _is_incomplete_copy_name(name):
    segments = str(name).casefold().replace(" ", "").split(".")
    return any(marker in segments for marker in _INCOMPLETE_COPY_MARKERS)


def _candidate_relative_path(candidate):
    value = Path(str(candidate or ""))
    if value.is_absolute() or not value.parts or value.parts[0].casefold() != "incoming":
        raise ValueError("底图必须来自 project_storage/incoming 目录")
    if value.suffix.lower() not in ALLOWED_BASEMAP_EXTENSIONS:
        raise ValueError(f"底图仅支持 {_BASEMAP_FORMAT_HINT}")
    if _is_incomplete_copy_name(value.name):
        raise ValueError("底图文件仍在复制中（.staging/.part），请等待复制完成后再登记")
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
    names.extend(path.with_suffix(suffix) for suffix in _BASEMAP_SIDECAR_SUFFIXES)
    return sorted({item.name for item in names if item.is_file()})


def list_retained_basemaps(project_id):
    """列出 retained 历史底图（保留最近 2 个的策略产物），供重新激活。"""
    if Project.query.filter_by(id=project_id, deleted_at=None).first() is None:
        raise ValueError(f"项目不存在: {project_id}")
    rows = (
        ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type="basemap",
            status="retained",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .all()
    )
    return [_serialize_resource(row) for row in rows]


def reactivate_basemap(project_id, resource_id, actor="system"):
    """重新激活 retained 历史底图：互斥激活（旧 active 置 retained）。

    前提是瓦片目录与 .active 标记仍在其自身 resource 目录中（激活期只改
    状态与标记，不复制数据——性能计划遗留项的收敛实现）。
    """
    if Project.query.filter_by(id=project_id, deleted_at=None).first() is None:
        raise ValueError(f"项目不存在: {project_id}")
    target = ProjectSpatialResource.query.filter_by(
        id=resource_id,
        project_id=project_id,
        resource_type="basemap",
    ).first()
    if target is None:
        raise ValueError("历史底图不存在")
    if target.status == "active":
        raise ValueError("该底图已是激活状态")

    storage_root = ensure_storage_layout()
    target_tile_dir = resolve_storage_path(
        storage_root,
        Path("projects") / str(project_id) / "tiles" / str(target.id),
    )
    if not (target_tile_dir / ".active").is_file():
        raise ValueError("该底图的瓦片数据已被清理，无法重新激活")

    current_active = (
        ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type="basemap",
            status="active",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .first()
    )
    if current_active is not None:
        current_tile_dir = resolve_storage_path(
            storage_root,
            Path("projects") / str(project_id) / "tiles" / str(current_active.id),
        )
        active_marker = current_tile_dir / ".active"
        if active_marker.exists():
            active_marker.unlink()
        current_active.status = "retained"

    (target_tile_dir / ".active").write_text("1", encoding="utf-8")
    target.status = "active"
    _activity(
        project_id,
        "basemap_reactivated",
        {"resource_id": target.id, "version": target.version,
         "previous_resource_id": current_active.id if current_active else None},
        actor=actor,
        target={"type": "spatial_resource", "id": str(target.id)},
    )
    db.session.commit()
    return _serialize_resource(target)


def list_basemap_candidates(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    root = ensure_storage_layout()
    mine_bounds = _active_mine_bounds(project_id)
    incoming = root / "incoming"
    items = []

    # 瓦片集目录候选（2026-11-01 后续项③）：incoming 一级子目录若为 XYZ 瓦片集
    # （{z}/{x}/{y}.png），登记时可直接激活、跳过切片。探测包 OSError 保护：
    # 一个坏目录（权限/断链）不应让整个候选列表 500（2026-11-01 审查项，
    # 与下方坏 tif 跳过同口径）
    seen_candidates = set()
    for sub_dir in sorted(incoming.glob("*")):
        try:
            if not sub_dir.is_dir() or _is_incomplete_copy_name(sub_dir.name):
                continue
            detection = detect_tileset_directory(sub_dir)
        except OSError:
            continue
        if detection is None:
            continue
        candidate = sub_dir.relative_to(root).as_posix()
        seen_candidates.add(candidate)
        bounds = detection["bounds"]
        items.append(
            {
                "type": "tileset",
                "candidate": candidate,
                "filename": sub_dir.name,
                "min_zoom": detection["min_zoom"],
                "max_zoom": detection["max_zoom"],
                "bounds": bounds,
                "intersects_mines": _bounds_intersect(bounds, mine_bounds),
            }
        )

    for path in sorted(incoming.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_BASEMAP_EXTENSIONS:
            continue
        # 复制中的半文件（.staging/.part 命名约定）不出现在候选列表
        if _is_incomplete_copy_name(path.name):
            continue
        try:
            metadata = _raster_metadata(path)
        except (ValueError, OSError, RuntimeError):
            # incoming 是运维投递区，单个截断/损坏 tif 不应让整个候选列表接口
            # 失败——跳过坏文件（注册时单独选择它仍会得到明确报错）
            continue
        candidate = path.relative_to(root).as_posix()
        if candidate in seen_candidates:
            continue
        items.append(
            {
                "type": "raster",
                "candidate": candidate,
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sidecars": _sidecar_paths(path),
                **metadata,
                "intersects_mines": _bounds_intersect(metadata["bounds"], mine_bounds),
            }
        )
    items.sort(key=lambda item: item["candidate"])
    return items


def register_basemap(project_id, candidate, min_zoom=8, max_zoom=15, actor="system"):
    project = _editable_project(project_id)
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
        actor=actor,
        target={"type": "spatial_job", "id": str(job.id)},
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
        "jobs": [
            _serialize_job(item)
            for item in ProjectSpatialJob.query.filter_by(project_id=project_id)
            .order_by(ProjectSpatialJob.create_time.desc())
            .all()
        ],
    }


def get_spatial_job(project_id, job_id):
    job = ProjectSpatialJob.query.filter_by(id=job_id, project_id=project_id).first()
    if job is None:
        raise ValueError("空间处理任务不存在")
    return _serialize_job(job)


def retry_spatial_job(project_id, job_id, actor="system"):
    _editable_project(project_id)
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
    _activity(
        project_id,
        "spatial_job_retried",
        {"job_id": job.id, "resource_id": resource.id},
        actor=actor,
        target={"type": "spatial_job", "id": str(job.id)},
    )
    db.session.commit()
    return _serialize_job(job)


def cancel_spatial_job(project_id, job_id, actor="system"):
    _editable_project(project_id)
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
    _activity(
        project_id,
        "spatial_job_cancel_requested",
        {"job_id": job.id},
        actor=actor,
        target={"type": "spatial_job", "id": str(job.id)},
    )
    db.session.commit()
    return _serialize_job(job)


MAX_BASEMAP_UPLOAD_BYTES = 15 * 1024 * 1024 * 1024
_BASEMAP_UPLOAD_CHUNK = 256 * 1024


def _incoming_root():
    storage_root = ensure_storage_layout()
    incoming_root = storage_root / "incoming"
    incoming_root.mkdir(parents=True, exist_ok=True)
    return incoming_root


def validate_basemap_filename(filename):
    """底图文件名权威校验（单发与分片共用单一真源）：返回 safe_name，非法抛 ValueError。"""
    original_name = str(filename or "").strip()
    safe_name = os.path.basename(original_name.replace("\\", "/"))
    ext = Path(safe_name).suffix.lower()
    if not safe_name or safe_name.startswith(".") or safe_name != original_name:
        raise ValueError("底图文件名非法")
    if ext not in ALLOWED_BASEMAP_EXTENSIONS:
        raise ValueError(
            f"不支持的底图格式: {ext or '（无扩展名）'}（{_BASEMAP_FORMAT_HINT}）"
        )
    return safe_name


def receive_basemap_upload(project_id, filename, stream):
    """底图网页上传（2026-10-31 方案 A）：浏览器直传 → 流式落盘 incoming/。

    受控存储接口归后端（AGENTS §3）：BFF 只做鉴权与流式转发，本函数是校验与
    落盘的唯一权威——扩展名白名单复用 ALLOWED_BASEMAP_EXTENSIONS、staging 命名
    复用 _is_incomplete_copy_name 契约、重名 409、15GB 业务上限（超限 413，2026-10-31 用户确认放宽：整省底图/大图集经网页上传的场景）。
    落盘完成即成为空间资源面板的底图候选。
    """
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    safe_name = validate_basemap_filename(filename)
    incoming_root = _incoming_root()
    final_path = incoming_root / safe_name
    staging_path = incoming_root / f"{safe_name}.staging"
    if final_path.exists():
        raise Conflict("incoming/ 已存在同名底图，请重命名后重试")

    received = 0
    try:
        with open(staging_path, "wb") as handle:
            while True:
                chunk = stream.read(_BASEMAP_UPLOAD_CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_BASEMAP_UPLOAD_BYTES:
                    raise RequestEntityTooLarge("底图文件超过 15GB 上限")
                handle.write(chunk)
        os.replace(staging_path, final_path)
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise
    return {
        "filename": safe_name,
        "size_bytes": received,
        "candidate": f"incoming/{safe_name}",
    }


def init_basemap_chunked_upload(project_id, payload, owner):
    """底图分片 init（2026-11-01 后续项①，突破单发 15GB 的整省场景）：
    底图预检（扩展名/重名/文件名）+ 复用 100GB 分片续传会话。

    会话暂存 static/upload/.chunks（与目标卷无关，7 天 sweep 清扫）；
    complete 阶段合并落盘 incoming/（见 complete_basemap_chunked_upload）。
    """
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    safe_name = validate_basemap_filename(payload.get("filename"))
    if (_incoming_root() / safe_name).exists():
        raise Conflict("incoming/ 已存在同名底图，请重命名后重试")
    from applications.common.utils import upload_sessions

    total_size = payload.get("total_size")
    try:
        total_size = int(total_size)
    except (TypeError, ValueError):
        raise ValueError("total_size 必须为正整数") from None
    if total_size <= 0:
        raise ValueError("total_size 必须为正整数")
    state = upload_sessions.init_session(
        upload_key=payload.get("upload_key"),
        filename=safe_name,
        total_size=total_size,
        mime="application/octet-stream",
        chunk_size=payload.get("chunk_size"),
        owner=owner,
    )
    return state


def complete_basemap_chunked_upload(project_id, session_id, filename, owner):
    """底图分片 complete：合并会话 → incoming/<safe_name>（staging 原子转正）。

    不走 upload_one_from_path（跳过 Photo 注册与 tiff 预处理）——落盘即成底图
    候选。done 幂等：complete 成功过（响应丢失/重试）直接返回上次落盘结果；
    属主校验在幂等分支之前——他人不得重放别人的完成结果（2026-11-01 审查 P1）。
    """
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    safe_name = validate_basemap_filename(filename)
    from applications.common.utils import upload_sessions

    upload_sessions.ensure_session_owner(session_id, owner)
    done = upload_sessions.session_is_done(session_id)
    if done is not None:
        # done 幂等：返回上次实际落盘的文件（而非本次请求名——改名重试会指向
        # 不存在的文件，2026-11-01 审查项）；改名属于客户端不一致，明确 409
        previous = done[0] if isinstance(done, list) and done else {}
        previous_name = previous.get("filename")
        if previous_name and previous_name != safe_name:
            raise Conflict(
                f"该上传会话已按文件名 {previous_name} 完成，与本次请求的 {safe_name} 不一致"
            )
        return {
            "filename": previous_name or safe_name,
            "size_bytes": int(previous.get("size_bytes") or 0),
            "candidate": previous.get("candidate") or f"incoming/{safe_name}",
        }
    final_path = _incoming_root() / safe_name
    if final_path.exists():
        raise Conflict("incoming/ 已存在同名底图，请重命名后重试")
    staging_path = _incoming_root() / f"{safe_name}.staging"
    try:
        from applications.common.utils.upload_sessions import assemble_session

        assembled = assemble_session(session_id, staging_path)
        size_bytes = assembled.stat().st_size
        os.replace(staging_path, final_path)
    except Exception:
        staging_path.unlink(missing_ok=True)
        raise
    upload_sessions.mark_session_done(
        session_id, [{"filename": safe_name, "size_bytes": size_bytes, "candidate": f"incoming/{safe_name}"}]
    )
    return {
        "filename": safe_name,
        "size_bytes": size_bytes,
        "candidate": f"incoming/{safe_name}",
    }


def detect_tileset_directory(directory):
    """探测甲方预切瓦片目录的 XYZ 结构与范围（2026-11-01 后续项③）。

    XYZ 契约：{z}(0-22 数字目录)/{x}(数字目录)/{y}.png；TMS 与 grid conf 包
    不支持（明确拒绝而非猜测转换）。返回
    {"min_zoom", "max_zoom", "bounds": [w, s, e, n], "tile_count} 或 None。
    """
    if not directory.is_dir():
        return None
    zoom_layers = {}
    for zoom_dir in directory.iterdir():
        if not zoom_dir.is_dir() or not zoom_dir.name.isdigit():
            continue
        zoom = int(zoom_dir.name)
        if not 0 <= zoom <= 22:
            continue
        x_dirs = [x for x in zoom_dir.iterdir() if x.is_dir() and x.name.isdigit()]
        if not x_dirs:
            continue
        y_count = 0
        x_range = [None, None]
        y_range = [None, None]
        for x_dir in x_dirs:
            x_index = int(x_dir.name)
            tiles = [t for t in x_dir.iterdir() if t.is_file() and t.suffix.lower() == ".png"]
            if not tiles:
                continue
            y_count += len(tiles)
            for tile in tiles:
                if not tile.stem.isdigit():
                    continue
                y_index = int(tile.stem)
                x_range[0] = x_index if x_range[0] is None else min(x_range[0], x_index)
                x_range[1] = x_index if x_range[1] is None else max(x_range[1], x_index)
                y_range[0] = y_index if y_range[0] is None else min(y_range[0], y_index)
                y_range[1] = y_index if y_range[1] is None else max(y_range[1], y_index)
        if y_count:
            zoom_layers[zoom] = {"x": x_range, "y": y_range, "count": y_count}
    if not zoom_layers:
        return None

    def _tile_bounds(zoom, x_index, y_index):
        n = 2 ** zoom
        west = x_index / n * 360.0 - 180.0
        east = (x_index + 1) / n * 360.0 - 180.0
        # Web Mercator 标准反算：lat = atan(sinh(π(1-2y/n)))（2026-11-01 审查 P1：
        # 误写 sin 会系统性低估纬度——26.1°N 被算成 24.5°，bounds 裁掉有效瓦片）
        north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y_index / n))))
        south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y_index + 1) / n))))
        return west, south, east, north

    top_zoom = max(zoom_layers)
    top = zoom_layers[top_zoom]
    west, south, east, north = _tile_bounds(top_zoom, top["x"][0], top["y"][0])
    for x_index in range(top["x"][0], top["x"][1] + 1):
        w, s, e, n2 = _tile_bounds(top_zoom, x_index, top["y"][0])
        west, north = min(west, w), max(north, n2)
        w, s, e, n2 = _tile_bounds(top_zoom, x_index, top["y"][1])
        west, south = min(west, w), min(south, s)
        east = max(east, e)
    return {
        "min_zoom": min(zoom_layers),
        "max_zoom": max(zoom_layers),
        "bounds": [round(west, 7), round(south, 7), round(east, 7), round(north, 7)],
        "tile_count": sum(layer["count"] for layer in zoom_layers.values()),
    }


def register_basemap_tileset(project_id, candidate, actor="system"):
    """甲方预切瓦片包直挂登记（2026-11-01 后续项③）：跳过切片直接激活。

    与 register_basemap 的差异：候选是 XYZ 瓦片目录而非单幅影像——探测结构后
    将瓦片原子拷贝到规范 tile_path，再调用 spatial_worker._activate_basemap
    激活状态机（互斥下线旧 active / .active 标记 commit 成功后才删旧 /
    保留最近 2 份历史 / activity 日志），并登记一条即时 succeeded 的 job
    供任务面板溯源。版本号取 func.max(version)+1（count 会因历史清理回退
    撞唯一约束）；归档项目经 _editable_project 拒绝（2026-11-01 审查修复，
    此前直挂重复实现激活流程导致版本/归档/回滚规则与切片通道分叉）。
    """
    import uuid as _uuid

    from applications.project_hub.spatial_worker import _activate_basemap

    project = _editable_project(project_id)
    candidate_relative = str(candidate or "").strip()
    candidate_path = resolve_storage_path(ensure_storage_layout(), candidate_relative)
    try:
        candidate_path.relative_to(_incoming_root())
    except ValueError as exc:
        raise ValueError("瓦片集必须来自 project_storage/incoming 目录") from exc
    if not candidate_path.is_dir():
        raise ValueError("瓦片集候选不存在或不是目录")
    detection = detect_tileset_directory(candidate_path)
    if detection is None:
        raise ValueError("候选目录不是可识别的 XYZ 瓦片集（需 {z}/{x}/{y}.png 结构）")
    bounds = detection["bounds"]
    # 直挂语义 = 背景图登记：不强制与矿山相交（甲方瓦片包可覆盖更大范围，
    # 与 register_basemap 的影像切片口径不同——直挂无逐矿山 ROI 推理诉求）
    # bounds 仍完整记录，供前端定位与裁剪显示。

    # 磁盘预检与 register_basemap 对齐：拷贝需要等量空间（瓦片包常达 GB 级）
    source_bytes = sum(
        item.stat().st_size for item in candidate_path.rglob("*") if item.is_file()
    )
    required_bytes = max(source_bytes, 100 * 1024 * 1024)
    disk = shutil.disk_usage(ensure_storage_layout())
    if disk.free < required_bytes:
        raise ValueError(f"磁盘空间不足，至少需要 {required_bytes} 字节可用空间")

    latest_version = (
        db.session.query(func.max(ProjectSpatialResource.version))
        .filter_by(project_id=project.id, resource_type="basemap")
        .scalar()
        or 0
    )
    resource = ProjectSpatialResource(
        project_id=project.id,
        resource_type="basemap",
        version=latest_version + 1,
        status="pending",
        source_path=candidate_relative,
        source_format="xyz_tiles",
        crs="EPSG:3857",
        bounds_json=json.dumps(bounds),
        min_zoom=detection["min_zoom"],
        max_zoom=detection["max_zoom"],
    )
    db.session.add(resource)
    db.session.flush()
    resource.tile_path = f"projects/{project_id}/tiles/{resource.id}"
    tile_dir = resolve_storage_path(ensure_storage_layout(), Path(resource.tile_path))
    # tile_path 属于本次新建的资源——目录已存在只可能来自异常残留（或记录删除
    # 后 id 复用），清掉再落位，避免 os.replace 撞非空目录（测试实测暴露）
    shutil.rmtree(tile_dir, ignore_errors=True)
    staging_dir = tile_dir.with_name(f".{tile_dir.name}.staging-{_uuid.uuid4().hex}")
    try:
        shutil.copytree(candidate_path, staging_dir)
        os.replace(staging_dir, tile_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    db.session.flush()

    job = ProjectSpatialJob(
        id=str(_uuid.uuid4()), project_id=project.id, job_type="basemap_tiles",
        status="running", stage="assembling", progress=50.0,
        resource_id=resource.id,
    )
    db.session.add(job)
    _activity(
        project.id,
        "basemap_tileset_registered",
        {"resource_id": resource.id, "candidate": candidate_relative,
         "min_zoom": detection["min_zoom"], "max_zoom": detection["max_zoom"],
         "tile_count": detection["tile_count"]},
        actor=actor,
        target={"type": "spatial_resource", "id": str(resource.id)},
    )
    # 激活/互斥下线/标记时序/历史保留/commit 全部复用切片通道的状态机（含
    # spatial_resource_activated 事件与 job 终态、heartbeat 设置）
    _activate_basemap(job, resource, ensure_storage_layout(), actor=actor)
    return {
        "resource_id": resource.id,
        "tile_path": resource.tile_path,
        "min_zoom": detection["min_zoom"],
        "max_zoom": detection["max_zoom"],
        "tile_count": detection["tile_count"],
        "bounds": bounds,
    }
