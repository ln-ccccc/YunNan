"""影像裁剪与切片（S3，2026-09-22）。

- clip：按 GeoJSON 多边形（EPSG:4326）或矿山边界 + 外扩（米）窗口裁剪，
  产物落 projects/<id>/inputs/imagery/ 并登记 ProjectDataset；
- slice：按固定像素 / 固定面积（m²）网格实体切片，逐片登记数据集，
  过小边片丢弃并报告，总量上限防误操作；
- candidates：可操作影像清单（数据集 imagery + 推理输入）。

口径：外扩距离（米）按影像中心纬度换算为度，再换算像素窗口外扩。
"""
import logging
import math
import uuid
from datetime import datetime
from pathlib import Path

import rasterio
from rasterio.warp import transform_geom
from rasterio.windows import Window, from_bounds

from applications.extensions import db
from applications.models.project import Project, ProjectDataset
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)

# 切片默认与硬上限（防误操作产生数万小文件）
DEFAULT_SLICE_LIMIT = 64
MAX_SLICE_LIMIT = 256
# 边片不足标准尺寸该比例时丢弃（太小无分析价值）
MIN_TILE_RATIO = 0.2

_METERS_PER_DEG_LAT = 111320.0


class ImageryProcessingError(ValueError):
    """面向客户端的裁剪/切片错误（400 语义）。"""


def _project_or_404(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ImageryProcessingError(f"项目不存在: {project_id}")
    return project


def _resolve_source(project, source_key):
    """数据源解析：优先按 dataset_id（正整数），否则按相对 storage_key。

    越界/非法路径一律 400（ImageryProcessingError），不回显服务器物理路径
    （收官审查 P2-1：裸 relative_to 的 ValueError 原文曾被 200 回显）。
    """
    text = str(source_key or "").strip()
    if text.isascii() and text.isdigit():
        dataset = ProjectDataset.query.filter_by(id=int(text), project_id=project.id).first()
        if dataset is None:
            raise ImageryProcessingError(f"数据集不存在: {text}")
        if not dataset.file_path:
            raise ImageryProcessingError("数据集未登记文件路径")
        path = Path(dataset.file_path).expanduser().resolve()
    else:
        if not text:
            raise ImageryProcessingError("缺少影像来源")
        storage_root = get_storage_root()
        project_root = resolve_storage_path(storage_root, Path("projects") / str(project.id))
        path = None
        try:
            path = resolve_storage_path(storage_root, Path(text))
        except ValueError:
            candidate = (project_root / text).resolve()
            try:
                candidate.relative_to(project_root)
                path = candidate
            except ValueError:
                raise ImageryProcessingError("影像来源路径不合法") from None
        try:
            path.relative_to(storage_root)
        except ValueError:
            raise ImageryProcessingError("影像来源路径不合法") from None
    if not path.is_file():
        raise ImageryProcessingError("影像文件不存在")
    return path


def list_imagery_candidates(project_id):
    project = _project_or_404(project_id)
    items = []
    for dataset in ProjectDataset.query.filter_by(project_id=project.id, dataset_kind="imagery"):
        path = Path(dataset.file_path).expanduser().resolve() if dataset.file_path else None
        summary = _raster_summary(path)
        items.append({
            "dataset_id": dataset.id,
            "display_name": dataset.display_name,
            "file_path": dataset.file_path,
            "year": dataset.year_start,
            **(summary or {"width": None, "height": None, "count": None, "crs": None, "size_bytes": None}),
        })
    # 项目影像输入目录（裁剪/切片产物与登记影像）：与 interpretation 输入同级
    for sub in ("imagery", "interpretation"):
        _append_input_candidates(items, project.id, sub)
    return {"items": items, "count": len(items)}


def _append_input_candidates(items, project_id, subdir):
    """扫描 inputs/<subdir>/ 下各年份目录的栅格文件（.hdr 伴生跳过）。

    imagery 子目录（裁剪/切片产物）无年份分层，文件直接平铺——两种布局都认。
    """
    inputs_root = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project_id) / "inputs" / subdir
    )
    if not inputs_root.is_dir():
        return
    # 布局 A：年份子目录（interpretation）
    year_dirs = [d for d in inputs_root.iterdir() if d.is_dir() and d.name.isdigit()]
    if year_dirs:
        for year_dir in sorted(year_dirs, reverse=True):
            for raster in sorted(year_dir.iterdir()):
                _append_one_input(items, raster, int(year_dir.name))
        return
    # 布局 B：平铺（imagery 裁剪/切片产物）
    for raster in sorted(inputs_root.iterdir()):
        if raster.is_file():
            _append_one_input(items, raster, None)


def _append_one_input(items, raster, year):
    if raster.suffix.lstrip(".").lower() in ("hdr",):
        return
    summary = _raster_summary(raster)
    if summary is None:
        return
    items.append({
        "dataset_id": None,
        "display_name": raster.name,
        # 相对 storage 根的 key（不向浏览器下发服务器绝对路径，AGENTS §6）
        "storage_key": _storage_key(raster),
        "year": year,
        **summary,
    })


def _storage_key(path):
    try:
        return Path(path).resolve().relative_to(get_storage_root()).as_posix()
    except ValueError:
        return None


def _raster_summary(path):
    if path is None or not path.is_file():
        return None
    try:
        with rasterio.open(path) as src:
            return {
                "width": src.width,
                "height": src.height,
                "count": src.count,
                "crs": str(src.crs) if src.crs else None,
                "size_bytes": path.stat().st_size,
            }
    except Exception:
        return None


def clip_imagery(project_id, payload):
    """按多边形/矿山边界 + 外扩（米）裁剪。返回新数据集摘要。"""
    project = _project_or_404(project_id)
    source_path = _resolve_source(project, payload.get("source"))
    geometry = payload.get("geometry")
    mine_fid = payload.get("mine_fid")
    buffer_meters = float(payload.get("buffer_meters") or 0)
    if buffer_meters < 0 or buffer_meters > 5000:
        raise ImageryProcessingError("外扩距离须在 0~5000 米之间")

    with rasterio.open(source_path) as src:
        if src.crs is None:
            raise ImageryProcessingError("影像缺少坐标系，无法按地理范围裁剪")
        if geometry is None and mine_fid is not None:
            geometry = _mine_geometry(project, mine_fid)
        if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
            raise ImageryProcessingError("裁剪范围必须是 Polygon/MultiPolygon GeoJSON")

        geom_src = transform_geom("EPSG:4326", src.crs, geometry, precision=6)
        minx, miny, maxx, maxy = _geom_bounds(geom_src)
        if minx >= maxx or miny >= maxy:
            raise ImageryProcessingError("裁剪范围无效（零面积）")

        # 外扩按 src.crs 单位换算：地理系（度）按中心纬度米→度；投影系（米）直接加
        # （收官审查 C2：投影系曾按度加导致外扩静默失效）
        if src.crs.is_geographic:
            center_lat = math.radians((src.bounds.top + src.bounds.bottom) / 2.0)
            buffer_x = buffer_meters / (_METERS_PER_DEG_LAT * max(math.cos(center_lat), 1e-6))
            buffer_y = buffer_meters / _METERS_PER_DEG_LAT
        else:
            buffer_x = buffer_meters
            buffer_y = buffer_meters
        minx -= buffer_x
        maxx += buffer_x
        miny -= buffer_y
        maxy += buffer_y

        try:
            window = from_bounds(minx, miny, maxx, maxy, src.transform)
            window = window.intersection(Window(0, 0, src.width, src.height)).round_offsets().round_lengths()
        except Exception:
            raise ImageryProcessingError("裁剪范围与影像无有效重叠") from None
        _guard_window_bytes(src, window)
        if window.width < 8 or window.height < 8:
            raise ImageryProcessingError("裁剪范围与影像无有效重叠（结果小于 8×8 像素）")

        data = src.read(window=window)
        out_transform = src.window_transform(window)
        out_profile = src.profile.copy()

    target_dir = _imagery_output_dir(project.id)
    out_name = f"clip_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.tif"
    out_path = target_dir / out_name
    out_profile.update(
        driver="GTiff", height=int(window.height), width=int(window.width),
        transform=out_transform, compress="lzw",
    )
    out_profile.pop("nodata", None)
    with rasterio.open(out_path, "w", **out_profile) as dst:
        dst.write(data)

    dataset = ProjectDataset(
        project_id=project.id,
        dataset_kind="imagery",
        display_name=f"裁剪 {payload.get('display_name') or source_path.name} (+{buffer_meters:g}m)",
        file_path=str(out_path),
        source_format="tif",
        slice_config_json='{"origin":"clip"}',
    )
    db.session.add(dataset)
    db.session.commit()
    return {
        "dataset_id": dataset.id,
        "display_name": dataset.display_name,
        "file_path": str(out_path),
        "width": int(window.width),
        "height": int(window.height),
        "buffer_meters": buffer_meters,
    }


def slice_imagery(project_id, payload):
    """按固定像素/固定面积网格实体切片，逐片登记数据集。"""
    project = _project_or_404(project_id)
    source_path = _resolve_source(project, payload.get("source"))
    mode = str(payload.get("mode") or "").strip()
    tile_pixels = payload.get("tile_pixels")
    tile_area_m2 = payload.get("tile_area_m2")
    limit = max(1, min(int(payload.get("limit") or DEFAULT_SLICE_LIMIT), MAX_SLICE_LIMIT))

    with rasterio.open(source_path) as src:
        if mode == "grid_pixels":
            tile_w = int(tile_pixels or 0)
            tile_h = tile_w
            if tile_w < 64 or tile_w > 32768:
                raise ImageryProcessingError("切片像素须在 64~32768 之间")
        elif mode == "grid_area":
            area = float(tile_area_m2 or 0)
            if area <= 0:
                raise ImageryProcessingError("固定面积必须大于 0")
            pixel_area_m2 = _pixel_area_m2(src)
            if pixel_area_m2 is None or pixel_area_m2 <= 0:
                raise ImageryProcessingError("影像缺少有效地理参考，无法按面积切片")
            side = max(1, int(round(math.sqrt(area / pixel_area_m2))))
            tile_w, tile_h = side, side
        else:
            raise ImageryProcessingError("mode 必须是 grid_pixels 或 grid_area")

        cols = math.ceil(src.width / tile_w)
        rows = math.ceil(src.height / tile_h)
        total = cols * rows
        if total > limit:
            raise ImageryProcessingError(
                f"按当前参数将产生 {total} 片，超过上限 {limit}；请增大切片尺寸或提高上限（≤{MAX_SLICE_LIMIT}）"
            )

        out_profile = src.profile.copy()
        out_profile.update(driver="GTiff", compress="lzw")
        out_profile.pop("nodata", None)

        target_dir = _imagery_output_dir(project.id)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        items = []
        skipped = []
        for row in range(rows):
            for col in range(cols):
                window = Window(col * tile_w, row * tile_h, tile_w, tile_h)
                window = window.intersection(Window(0, 0, src.width, src.height))
                if window.width < max(8, tile_w * MIN_TILE_RATIO) or window.height < max(8, tile_h * MIN_TILE_RATIO):
                    skipped.append({"row": row, "col": col, "reason": "边片过小"})
                    continue
                _guard_window_bytes(src, window)
                data = src.read(window=window)
                transform = src.window_transform(window)
                out_name = f"slice_{stamp}_{uuid.uuid4().hex[:6]}_r{row}c{col}.tif"
                out_path = target_dir / out_name
                profile = dict(out_profile, height=int(window.height), width=int(window.width), transform=transform)
                with rasterio.open(out_path, "w", **profile) as dst:
                    dst.write(data)
                dataset = ProjectDataset(
                    project_id=project.id,
                    dataset_kind="imagery",
                    display_name=f"切片 r{row}c{col} {window.width}×{window.height}",
                    file_path=str(out_path),
                    source_format="tif",
                    slice_config_json='{"origin":"slice"}',
                )
                db.session.add(dataset)
                db.session.flush()
                items.append({
                    "dataset_id": dataset.id,
                    "row": row,
                    "col": col,
                    "width": int(window.width),
                    "height": int(window.height),
                    "file_path": str(out_path),
                })
        db.session.commit()
    return {
        "grid": {"cols": cols, "rows": rows, "tile_w": tile_w, "tile_h": tile_h},
        "created": len(items),
        "skipped": skipped,
        "items": items,
    }


# 单窗读入字节上限（2GB）：clip/slice 都不允许一次把超大窗口整读进内存
MAX_WINDOW_BYTES = 2 * 1024 ** 3


def _guard_window_bytes(src, window):
    estimated = int(window.width) * int(window.height) * max(src.count, 1) * _dtype_size(src.dtypes[0] if src.dtypes else None)
    if estimated > MAX_WINDOW_BYTES:
        raise ImageryProcessingError(
            f"单窗 {int(window.width)}×{int(window.height)}×{src.count} 波段超出内存上限（约 2GB），请缩小范围或切片尺寸"
        )


def _dtype_size(dtype_name):
    import numpy as np

    try:
        return int(np.dtype(dtype_name).itemsize)
    except (TypeError, ValueError):
        return 8


def _imagery_output_dir(project_id):
    target = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project_id) / "inputs" / "imagery"
    )
    target.mkdir(parents=True, exist_ok=True)
    return target


def _pixel_area_m2(src):
    center_lat = math.radians((src.bounds.top + src.bounds.bottom) / 2.0)
    return abs(src.transform.a) * abs(src.transform.e) * (_METERS_PER_DEG_LAT ** 2) * math.cos(center_lat)


def _mine_geometry(project, mine_fid):
    from applications.kml_roi.kml import load_vector_features
    from applications.models.project_spatial import ProjectSpatialResource

    resource = (
        ProjectSpatialResource.query.filter_by(
            project_id=project.id, resource_type="mine_vector", status="active"
        )
        .order_by(ProjectSpatialResource.version.desc())
        .first()
    )
    if resource is None or not resource.normalized_path:
        raise ImageryProcessingError("项目尚未激活矿山资源，无法按矿山边界裁剪")
    vector_path = resolve_storage_path(get_storage_root(), resource.normalized_path)
    for fid, geometry in load_vector_features(vector_path):
        if str(fid) == str(mine_fid):
            return geometry
    raise ImageryProcessingError(f"矿山 {mine_fid} 不在项目边界数据中")


def _geom_bounds(geometry):
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    xs, ys = [], []

    def walk(ring):
        for point in ring:
            if isinstance(point[0], list):
                walk(point)
            else:
                xs.append(float(point[0]))
                ys.append(float(point[1]))

    if gtype == "Polygon":
        for ring in coords:
            walk(ring)
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                walk(ring)
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)
