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
