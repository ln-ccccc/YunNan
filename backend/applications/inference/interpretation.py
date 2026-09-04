import os
import shutil
import uuid
from pathlib import Path

from flask import current_app

from applications.inference.jobs import create_job, normalize_job_request
from applications.inference.routing import resolve_interpretation_scope
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


def resolve_uploaded_tiff(value):
    if not value:
        raise ValueError("缺少 new_tif_path")
    upload_root = Path(current_app.config["UPLOADED_PHOTOS_DEST"]).expanduser().resolve()
    tif_path = Path(value).expanduser().resolve()
    try:
        tif_path.relative_to(upload_root)
    except ValueError as exc:
        raise ValueError("TIFF 路径不在上传目录中") from exc
    if not tif_path.is_file():
        raise FileNotFoundError("TIFF 文件不存在")
    if tif_path.suffix.lower() not in {".tif", ".tiff"}:
        raise ValueError("地物分类仅支持 tif/tiff")
    return tif_path


def _parse_year(value):
    text = str(value or "").strip()
    if len(text) != 4 or not text.isdigit():
        raise ValueError("年份必须是四位整数")
    year = int(text)
    if year < 1800 or year > 2200:
        raise ValueError("年份超出有效范围")
    return year


def _publish_project_input(project_id, tif_path, year):
    storage_root = get_storage_root()
    relative_dir = Path("projects") / str(project_id) / "inputs" / "interpretation" / str(year)
    target_dir = resolve_storage_path(storage_root, relative_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / tif_path.name
    temporary = target_dir / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copy2(tif_path, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def prepare_geoview_interpretation(payload):
    source = dict(payload or {})
    tif_path = resolve_uploaded_tiff(source.get("new_tif_path") or source.get("old_tif_path"))
    scope = resolve_interpretation_scope(source.get("project_id"), tif_path)
    if scope["mode"] == "standalone":
        return {**scope, "job": None}

    project_id = scope["project_id"]
    year = _parse_year(source.get("year"))
    storage_root = get_storage_root()
    project_root = resolve_storage_path(storage_root, Path("projects") / str(project_id))
    project_input = _publish_project_input(project_id, tif_path, year)
    output_root = project_root / "outputs" / "inference"
    normalized = normalize_job_request(
        {
            **source,
            "project_id": project_id,
            "mine_resource_id": scope["mine_resource_id"],
            "mine_fids": scope["matched_fids"],
            "old_tif_path": str(project_input),
            "new_tif_path": str(project_input),
            "kml_path": scope["vector_path"],
            "output_root": str(output_root),
            "year": str(year),
        },
        allowed_roots=[project_root],
        allowed_output_roots=[project_root / "outputs"],
    )
    return {**scope, "job": create_job(normalized)}
