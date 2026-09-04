import os
from pathlib import Path


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
