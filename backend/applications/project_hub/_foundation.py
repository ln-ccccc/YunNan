# -*- coding: utf-8 -*-
"""项目域共享基础工具（H3 拆分，2026-10-01）：service.py god-module 的无内部
横向依赖段下沉——JSON/时间序列化、storage key、活动写入、目录解析与原子写、
校验与清理回滚。exports/snapshots/overview/service 均从这里取用，
本模块不得 import 项目域其它业务模块（防止循环）。"""
import hashlib
import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path, PureWindowsPath

from applications.common.utils.utc_time import to_utc_z
from applications.extensions import db
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectBackupRecord,
    ProjectExportRecord,
)
from applications.project_hub.project_storage import (
    ProjectStorageValidationError,
    get_storage_root,
    resolve_project_record_root,
    resolve_storage_path,
)
from applications.project_hub.spatial_storage import SUPPORTED_INCOMING_RASTER_FORMATS

LOGGER = logging.getLogger(__name__)



def _log_storage_cleanup_failure(area, project_id, record_id, stage, exc):
    LOGGER.warning(
        "项目存储清理失败 area=%s project_id=%s record_id=%s stage=%s",
        area,
        project_id,
        record_id,
        stage,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

def _rollback_after_storage_failure(area, project_id, record_id, stage):
    try:
        db.session.rollback()
    except Exception as rollback_exc:
        _log_storage_cleanup_failure(area, project_id, record_id, stage, rollback_exc)

def _json_dump(value):
    return json.dumps(value or {}, ensure_ascii=False)

def _json_load(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default

def _parse_iso_datetime(value):
    """快照 manifest 里的 ISO 时间串回 datetime；非法/缺失返回 None。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None

def _validate_incoming_tiff_storage_key(value):
    storage_key = str(value or "").strip()
    path = Path(storage_key)
    windows_path = PureWindowsPath(storage_key)
    if (
        not storage_key
        or ".." in path.parts
        or ".." in windows_path.parts
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectStorageValidationError("storage_key 必须是 incoming/ 下的相对 TIFF 文件")
    storage_root = get_storage_root()
    try:
        source_path = resolve_storage_path(storage_root, storage_key)
        incoming_root = resolve_storage_path(storage_root, "incoming")
        source_path.relative_to(incoming_root)
    except ValueError as exc:
        raise ProjectStorageValidationError("storage_key 必须位于 incoming/ 目录") from exc
    source_format = source_path.suffix.lower().lstrip(".")
    if source_format not in SUPPORTED_INCOMING_RASTER_FORMATS:
        raise ProjectStorageValidationError(
            "storage_key 必须是受支持的栅格格式（tif/tiff/img/jp2）"
        )
    if not source_path.is_file():
        raise ProjectStorageValidationError("storage_key 指向的文件不存在")
    return source_path.relative_to(storage_root).as_posix(), source_format

def _write_xlsx(path, project, project_id=None, record_id=None):
    from applications.project_hub.ledger import build_project_ledger_workbook

    path.write_bytes(build_project_ledger_workbook(project).read())

def _storage_key(*parts):
    return Path(*parts).as_posix()

def _utc_timestamp(value):
    return to_utc_z(value)

def _append_activity(
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
    row = ProjectActivityLog(
        project_id=project_id,
        event_type=event_type,
        actor=actor or "system",
        payload_json=_json_dump(event_payload),
    )
    db.session.add(row)
    return row

def _get_project_or_404(project_id):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if not project:
        raise ValueError(f"项目不存在: {project_id}")
    return project

def _write_artifact_atomic(target_path, writer, *args, project_id=None, record_id=None):
    temporary_path = target_path.parent / f".{target_path.name}.{uuid.uuid4().hex}.tmp"
    try:
        writer(temporary_path, *args)
        os.replace(temporary_path, target_path)
    except Exception:
        if temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as cleanup_exc:
                _log_storage_cleanup_failure(
                    "exports",
                    project_id,
                    record_id,
                    "artifact_temp_cleanup",
                    cleanup_exc,
                )
        raise

def _export_directory(project_id, export_id):
    return resolve_project_record_root(project_id, "exports", export_id, create=False)

def _snapshot_directory(project_id, backup_id):
    return resolve_project_record_root(project_id, "snapshots", backup_id, create=False)

def _cleanup_storage_directory(project_id, area, record_id):
    try:
        if area == "exports":
            path_obj = _export_directory(project_id, record_id)
        else:
            path_obj = _snapshot_directory(project_id, record_id)
        shutil.rmtree(path_obj)
    except (OSError, ProjectStorageValidationError) as cleanup_exc:
        _log_storage_cleanup_failure(
            area,
            project_id,
            record_id,
            "record_directory_cleanup",
            cleanup_exc,
        )
        return

def _mark_export_failed(project_id, record_id):
    try:
        db.session.remove()
        record = ProjectExportRecord.query.get(record_id)
        if record is not None:
            record.status = "failed"
            db.session.commit()
    except Exception as update_exc:
        _rollback_after_storage_failure(
            "exports",
            project_id,
            record_id,
            "mark_failed_rollback",
        )
        _log_storage_cleanup_failure(
            "exports",
            project_id,
            record_id,
            "mark_failed_status",
            update_exc,
        )

def _mark_backup_failed(project_id, backup_id):
    try:
        db.session.remove()
        backup = ProjectBackupRecord.query.get(backup_id)
        if backup is not None:
            backup.status = "failed"
            backup.restorable = False
            db.session.commit()
    except Exception as update_exc:
        _rollback_after_storage_failure(
            "snapshots",
            project_id,
            backup_id,
            "mark_failed_rollback",
        )
        _log_storage_cleanup_failure(
            "snapshots",
            project_id,
            backup_id,
            "mark_failed_status",
            update_exc,
        )

def _file_checksum(path_obj):
    checksum = hashlib.sha256()
    with open(path_obj, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()
