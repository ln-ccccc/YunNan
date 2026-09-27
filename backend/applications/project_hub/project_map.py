import json
from pathlib import Path

from applications.models.inference_job import InferenceJob
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_service import sanitize_public_geojson_value
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
    return sanitize_public_geojson_value(payload)


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


def _aggregate_project_indices(project_id, fids):
    """项目级光谱指数聚合：逐矿 indices 产物求均值/趋势分布（M5 后数据完善）。

    数据源是 outputs/indices/<fid>.json（来自 NDVI/NDBI 等 xlsx 的逐矿序列）；
    unavailable 的矿山按"缺失"计数，不进均值。返回 ndviStats 与趋势分布。
    """
    summary_years = {}
    means = []
    trend_counter = {"upward": 0, "downward": 0, "stable": 0, "no_data": 0}
    available = 0
    for fid in fids:
        try:
            payload = _read_project_output(project_id, "indices", f"{int(fid)}.json")
        except (ValueError, TypeError):
            continue
        ndvi = payload.get("ndvi") or {}
        if not ndvi.get("available"):
            trend_counter["no_data"] += 1
            continue
        available += 1
        try:
            means.append(float(ndvi.get("mean")))
        except (TypeError, ValueError):
            pass
        mk = str(ndvi.get("mk_trend") or "no_data")
        if mk in trend_counter:
            trend_counter[mk] += 1
        for point in ndvi.get("data") or []:
            year = point.get("year")
            value = point.get("value")
            if year is None or value is None:
                continue
            summary_years.setdefault(year, []).append(float(value))

    yearly = [
        {
            "year": year,
            "mean": round(sum(values) / len(values), 4),
            "sample_count": len(values),
        }
        for year, values in sorted(summary_years.items(), key=lambda item: item[0])
    ]
    overall_mean = round(sum(means) / len(means), 4) if means else 0
    first = yearly[0]["mean"] if yearly else 0
    last = yearly[-1]["mean"] if len(yearly) >= 2 else 0
    return {
        "ndviStats": {
            "mean": overall_mean,
            "trend": round(last - first, 4),
            "available_mine_count": available,
            "mk_trend_counter": trend_counter,
            "yearly": yearly,
        },
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
        # 面积取值优先级：矢量属性 area/TBTYMJ_1/TBTYMJ（官方图斑面积别名，m²，
        # 与 spatial_service 字段别名表口径一致）优先；
        # binding.area_snapshot 是导入映射的原始值，量纲随源数据（历史项目曾误映射
        # 到 SHAPE_Area 平方度），仅作无属性时的兜底，避免污染 m² 口径汇总。
        # 候选逐个尝试解析：真值非数字（如 "待定"）时回落下一来源，而非整矿丢面积。
        area_candidates = (
            properties.get("area"),
            properties.get("TBTYMJ_1"),
            properties.get("TBTYMJ"),
            binding.area_snapshot if binding else None,
        )
        numeric_area = None
        for candidate in area_candidates:
            if candidate in (None, ""):
                continue
            try:
                numeric_area = float(candidate)
                break
            except (TypeError, ValueError):
                continue
        if numeric_area is not None:
            total_area += numeric_area
            if numeric_area < 1_000_000:
                small_mines += 1
            elif numeric_area <= 2_000_000:
                medium_mines += 1
            else:
                large_mines += 1
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
        **_aggregate_project_indices(project_id, binding_by_fid.keys()),
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


def _payload_dict(raw):
    try:
        payload = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def list_project_original_imagery(project_id, fid, item_limit=50):
    """矿山维度列出地物分类推理所用的原始影像（溯源，2026-09-22）。

    数据源是 InferenceJob.request_payload_json——归一化后的 new_tif_path 指向
    项目 inputs 目录中的拷贝；按 (输入路径, 年份) 去重。路径只做服务端解析、
    存储根包含性检查与存在性标注，物理路径不出现在返回 DTO 里。
    """
    try:
        fid_value = int(fid)
    except (TypeError, ValueError) as exc:
        raise ValueError("FID 必须是整数") from exc
    project = _project(project_id)
    if not any(binding.mine_fid == fid_value for binding in project.mines):
        raise ValueError("矿山不属于当前项目")

    items = []
    seen = set()
    # 列表与下载同门槛：只认本项目 inputs 根内的记录（legacy 迁移任务可能指向
    # storage 其他位置——列表放行而下载 404 会造成可点不可达，2026-09-22 审查 P2）
    input_root = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project.id) / "inputs"
    )
    jobs = (
        InferenceJob.query.filter_by(project_id=project.id)
        .order_by(InferenceJob.create_time.desc())
        .limit(400)
    )
    for job in jobs:
        payload = _payload_dict(job.request_payload_json)
        mine_fids = {str(entry) for entry in (payload.get("mine_fids") or [])}
        if str(fid_value) not in mine_fids:
            continue
        input_text = str(payload.get("new_tif_path") or payload.get("old_tif_path") or "")
        if not input_text:
            continue
        try:
            input_path = Path(input_text).expanduser().resolve()
            input_path.relative_to(input_root)
        except (ValueError, OSError):
            continue
        year_text = str(payload.get("year") or "").strip()
        key = (str(input_path), year_text)
        if key in seen:
            continue
        seen.add(key)
        size_bytes = None
        file_exists = False
        try:
            # stat 与 is_file 之间的清理竞态不放大成整列表 404（审查 P3）
            if input_path.is_file():
                size_bytes = input_path.stat().st_size
                file_exists = True
        except OSError:
            size_bytes, file_exists = None, False
        year_value = int(year_text) if year_text.isdigit() and len(year_text) == 4 else None
        items.append({
            "job_id": job.id,
            "year": year_value,
            "filename": input_path.name,
            "size_bytes": size_bytes,
            "file_exists": file_exists,
            "job_status": job.status,
            "created_at": job.create_time.isoformat() if job.create_time else None,
        })
        if len(items) >= item_limit:
            break
    return {"fid": fid_value, "items": items}


def resolve_project_original_imagery_download(project_id, job_id):
    """溯源下载：按任务记录解析输入影像，并强制位于本项目 inputs 根内。"""
    project = _project(project_id)
    job = InferenceJob.query.filter_by(id=str(job_id or ""), project_id=project.id).first()
    if job is None:
        raise FileNotFoundError("推理任务不存在")
    payload = _payload_dict(job.request_payload_json)
    input_text = str(payload.get("new_tif_path") or payload.get("old_tif_path") or "")
    if not input_text:
        raise FileNotFoundError("任务未记录原始影像")
    input_root = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project.id) / "inputs"
    )
    path = Path(input_text).expanduser().resolve()
    try:
        path.relative_to(input_root)
    except ValueError:
        raise FileNotFoundError("原始影像不在项目输入目录中") from None
    if not path.is_file():
        raise FileNotFoundError("原始影像文件不存在")
    return path
