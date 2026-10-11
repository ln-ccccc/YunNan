import os
from pathlib import Path, PureWindowsPath


def get_storage_root():
    return Path(os.getenv("PROJECT_STORAGE_ROOT", "/project_storage")).expanduser().resolve()


def resolve_storage_path(storage_root, relative_path):
    raw = Path(str(relative_path or ""))
    if raw.is_absolute():
        raise ValueError("空间资源必须使用相对路径")
    root = Path(storage_root).expanduser().resolve()
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("空间资源路径越界") from exc
    return candidate


def ensure_storage_layout(storage_root=None):
    root = Path(storage_root or get_storage_root()).resolve()
    (root / "incoming").mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(parents=True, exist_ok=True)
    return root

# GDAL 可读的单文件栅格格式；ENVI 等需配套 header 的多文件格式暂不开放
SUPPORTED_INCOMING_RASTER_FORMATS = {"tif", "tiff", "img", "jp2"}


def normalize_dataset_storage_key(storage_root, value, project_id, *, allow_legacy=False):
    """规范化受控索引；历史绝对路径只可映射到 incoming 或当前项目。"""
    text = str(value or "").strip()
    raw = Path(text)
    windows = PureWindowsPath(text)
    if not text or ".." in raw.parts or ".." in windows.parts:
        raise ValueError("数据集路径不合法")
    root = Path(storage_root).resolve()
    if raw.is_absolute():
        if not allow_legacy:
            raise ValueError("数据集必须使用相对路径")
        candidate = raw.resolve()
    elif windows.drive or windows.is_absolute():
        raise ValueError("数据集必须使用相对路径")
    else:
        candidate = resolve_storage_path(root, text.replace("\\", "/"))
    try:
        key = candidate.relative_to(root).as_posix()
    except ValueError:
        raise ValueError("数据集路径不在本项目存储内，已拒绝") from None
    if not (key.startswith("incoming/") or key.startswith(f"projects/{project_id}/")):
        raise ValueError("数据集路径不在本项目存储内，已拒绝")
    return key


def resolve_project_imagery_path(storage_root, value, project_id, *, allow_legacy=False):
    key = normalize_dataset_storage_key(
        storage_root, value, project_id, allow_legacy=allow_legacy
    )
    if not (key.startswith("incoming/") or key.startswith(f"projects/{project_id}/inputs/imagery/")):
        raise ValueError("影像必须位于 incoming 或本项目 inputs/imagery 目录")
    return resolve_storage_path(storage_root, key)


def resolve_basemap_source_path(storage_root, resource):
    """解析底图的受控原栅格；地图瓦片目录不能作为推理输入。"""
    import rasterio
    from rasterio.errors import RasterioError

    key = normalize_dataset_storage_key(storage_root, resource.source_path, resource.project_id)
    prefixes = (
        "incoming/",
        f"projects/{resource.project_id}/basemap/",
        f"projects/{resource.project_id}/basemaps/",
    )
    if not key.startswith(prefixes):
        raise ValueError("底图原影像不在受控来源目录，请重新登记底图")
    path = resolve_storage_path(storage_root, key)
    if (
        path.suffix.lower().lstrip(".") not in SUPPORTED_INCOMING_RASTER_FORMATS
        or str(resource.source_format or "").lower() not in SUPPORTED_INCOMING_RASTER_FORMATS
        or not path.is_file()
    ):
        raise ValueError("底图原影像不存在或格式不支持，请重新登记底图")
    try:
        with rasterio.open(path) as src:
            if src.width <= 0 or src.height <= 0 or src.count <= 0 or src.crs is None:
                raise ValueError("底图原影像缺少有效尺寸或坐标系，请重新登记底图")
    except (OSError, RasterioError) as exc:
        raise ValueError("底图原影像不可读，请检查源文件并重新登记底图") from exc
    return path
