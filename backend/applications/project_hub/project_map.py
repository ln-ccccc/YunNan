import json
from pathlib import Path

from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_state import serialize_project_spatial_state
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


def _project(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")
    return project


def _active_resource(project_id, resource_type):
    return (
        ProjectSpatialResource.query.filter_by(
            project_id=project_id,
            resource_type=resource_type,
            status="active",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .first()
    )


def _has_available_basemap_tiles(resource):
    if resource is None or not resource.tile_path:
        return False
    try:
        tile_dir = resolve_storage_path(get_storage_root(), resource.tile_path)
        if not tile_dir.is_dir() or not (tile_dir / ".active").is_file():
            return False
        return next(tile_dir.rglob("*.png"), None) is not None
    except (OSError, ValueError):
        return False


def get_project_geojson(project_id):
    _project(project_id)
    resource = _active_resource(project_id, "mine_vector")
    if resource is None or not resource.normalized_path:
        raise ValueError("暂无项目数据：未配置矿山资源")
    path = resolve_storage_path(get_storage_root(), resource.normalized_path)
    if not path.is_file():
        raise ValueError("暂无项目数据：矿山资源文件不存在")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("项目矿山数据无法读取") from exc
    if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
        raise ValueError("项目矿山数据格式错误")
    return payload


def get_project_map_manifest(project_id):
    project = _project(project_id)
    state = serialize_project_spatial_state(project)
    mines = _active_resource(project_id, "mine_vector")
    basemap = _active_resource(project_id, "basemap")
    tile_url = None
    api_tile_url = None
    if _has_available_basemap_tiles(basemap):
        tile_url = f"/tiles/projects/{project_id}/{basemap.id}/{{z}}/{{x}}/{{y}}.png"
        api_tile_url = f"/api/projects/{project_id}/map-resources/{basemap.id}/tiles/{{z}}/{{x}}/{{y}}.png"
    return {
        "project_id": project_id,
        **state,
        "bounds": json.loads((basemap or mines).bounds_json or "null") if (basemap or mines) else None,
        "mine_resource_id": mines.id if mines else None,
        "basemap_resource_id": basemap.id if basemap else None,
        "tile_url": tile_url,
        "api_tile_url": api_tile_url,
        "min_zoom": basemap.min_zoom if basemap else None,
        "max_zoom": basemap.max_zoom if basemap else None,
        "tile_scheme": "xyz" if basemap else None,
    }


def get_project_stats(project_id):
    geojson = get_project_geojson(project_id)
    features = geojson["features"]
    project = _project(project_id)
    binding_by_fid = {row.mine_fid: row for row in project.mines}
    total_area = 0.0
    small_mines = 0
    medium_mines = 0
    large_mines = 0
    treated = 0
    untreated = 0
    mining_methods = {}
    restoration_methods = {}
    land_types = {}
    for feature in features:
        properties = feature.get("properties") or {}
        fid = properties.get("FID_1", properties.get("FID", properties.get("OBJECTID", properties.get("id"))))
        try:
            binding = binding_by_fid.get(int(fid))
        except (TypeError, ValueError):
            binding = None
        area = (binding.area_snapshot if binding else None) or properties.get("TBTYMJ") or properties.get("area") or 0
        try:
            numeric_area = float(area)
            total_area += numeric_area
            if numeric_area < 1_000_000:
                small_mines += 1
            elif numeric_area <= 2_000_000:
                medium_mines += 1
            else:
                large_mines += 1
        except (TypeError, ValueError):
            pass
        status = str((binding.status_snapshot if binding else None) or properties.get("HFZLQK") or properties.get("status") or "")
        if any(word in status for word in ("已", "治理", "恢复", "复垦")) and "未" not in status:
            treated += 1
        else:
            untreated += 1
        method = str(properties.get("KCFS") or "未知").strip()
        mining_methods[method] = mining_methods.get(method, 0) + 1
        restoration = str(properties.get("NXFFS") or "未知").strip()
        restoration_methods[restoration] = restoration_methods.get(restoration, 0) + 1
        land = str(properties.get("NXFFX") or "未知").strip()
        land_types[land] = land_types.get(land, 0) + 1
    return {
        "mineTotal": len(features),
        "mineAreaTotal": total_area,
        "treatedCount": treated,
        "untreatedCount": untreated,
        "areaStats": {"small": small_mines, "medium": medium_mines, "large": large_mines},
        "miningMethodList": [{"name": key, "value": value} for key, value in mining_methods.items()],
        "restorationMethodList": [{"name": key, "count": value} for key, value in restoration_methods.items()],
        "landTypeList": [{"name": key, "value": value} for key, value in land_types.items()],
        "closingYearList": [],
        "ndviStats": {"mean": 0, "trend": 0},
        "changeAreaStats": {
            "total_changed_km2": 0,
            "valid_mine_count": 0,
            "missing_mine_count": len(features),
            "coverage_ratio": 0,
        },
        "mineChangeAreaList": [],
    }


def search_project_mines(project_id, query):
    text = str(query or "").strip().casefold()
    if not text:
        raise ValueError("缺少搜索条件 q")
    for feature in get_project_geojson(project_id)["features"]:
        properties = feature.get("properties") or {}
        fid = properties.get("FID_1", properties.get("FID", properties.get("OBJECTID", properties.get("id", ""))))
        name = properties.get("mine_name") or properties.get("name") or ""
        if str(fid).casefold() == text or text in str(name).casefold():
            return feature
    raise ValueError("项目中未找到该矿山")


def _read_project_output(project_id, category, filename):
    _project(project_id)
    relative = Path("projects") / str(project_id) / "outputs" / category / filename
    path = resolve_storage_path(get_storage_root(), relative)
    if not path.is_file():
        return {"available": False, "message": "暂无项目数据"}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("项目输出数据无法读取") from exc


def get_project_mine_indices(project_id, fid):
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")
    return _read_project_output(project_id, "indices", f"{fid_value}.json")


def get_project_trend_report(project_id, fid):
    if fid in (None, ""):
        return _read_project_output(project_id, "trend_reports", "summary.json")
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")
    return _read_project_output(project_id, "trend_reports", f"{fid_value}.json")


def get_project_change_matrix(project_id, fid):
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")
    payload = _read_project_output(project_id, "change_matrix", f"{fid_value}.json")
    if payload.get("available") is False:
        return {
            "fid": fid_value,
            "headers": [],
            "matrix": [],
            "images": {"old": None, "new": None},
            "has_change_matrix": False,
            "data_source": "none",
            "message": "暂无项目数据",
        }
    return payload
