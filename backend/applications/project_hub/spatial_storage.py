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


def resolve_stored_file(storage_root, stored_path):
    """数据集/记录类存储键（ProjectDataset.file_path 等）的统一双形态解析
    （2026-10-24 契约 B）：唯一规范形态是 storage 根相对键；legacy 绝对路径
    仅在位于当前 storage 根内时兼容接受。越界/穿越/非法输入抛 ValueError，
    由调用方决定对客户端的错误语义。"""
    text = str(stored_path or "").strip()
    if not text:
        raise ValueError("存储路径为空")
    try:
        raw = Path(text).expanduser()
    except RuntimeError as exc:
        # pathlib 对 '~0' 等畸形家目录前缀抛 RuntimeError（Python 3.10），
        # 按本函数契约统一转为 ValueError
        raise ValueError("存储路径非法") from exc
    windows_path = PureWindowsPath(text)
    if (
        ".." in raw.parts
        or ".." in windows_path.parts
        or ((windows_path.is_absolute() or windows_path.drive) and not raw.is_absolute())
    ):
        raise ValueError("存储路径越界")
    if raw.is_absolute():
        try:
            raw = raw.resolve().relative_to(Path(storage_root).expanduser().resolve())
        except ValueError as exc:
            raise ValueError("存储路径越界") from exc
    return resolve_storage_path(storage_root, raw)


def ensure_storage_layout(storage_root=None):
    root = Path(storage_root or get_storage_root()).resolve()
    (root / "incoming").mkdir(parents=True, exist_ok=True)
    (root / "projects").mkdir(parents=True, exist_ok=True)
    return root

# GDAL 可读的单文件栅格格式；ENVI 等需配套 header 的多文件格式暂不开放
SUPPORTED_INCOMING_RASTER_FORMATS = {"tif", "tiff", "img", "jp2"}
