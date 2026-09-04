import json
import logging
import os
import stat
import tempfile
from pathlib import Path, PureWindowsPath

from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


LOGGER = logging.getLogger(__name__)


class ProjectStorageValidationError(ValueError):
    pass


_PROJECT_RECORD_AREAS = {"exports", "snapshots"}
_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400


def _log_cleanup_failure(stage, exc, area=None, project_id=None, record_id=None):
    LOGGER.warning(
        "项目存储清理失败 area=%s project_id=%s record_id=%s stage=%s",
        area,
        project_id,
        record_id,
        stage,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _positive_id(value, field_name):
    if isinstance(value, bool):
        raise ProjectStorageValidationError(f"{field_name} 必须是正整数")
    try:
        identifier = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectStorageValidationError(f"{field_name} 必须是正整数") from exc
    if identifier <= 0:
        raise ProjectStorageValidationError(f"{field_name} 必须是正整数")
    return identifier


def _project_relative_path(project_id, *parts):
    return Path("projects") / str(_positive_id(project_id, "project_id")) / Path(*parts)


def project_root(project_id):
    return resolve_storage_path(get_storage_root(), _project_relative_path(project_id))


def _inspect_directory_component(path):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ProjectStorageValidationError("项目受控目录无法安全访问") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ProjectStorageValidationError("项目受控目录不允许使用链接")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ProjectStorageValidationError("项目受控路径必须为目录")
    return True


def resolve_project_record_root(project_id, area, record_id, create=False):
    if area not in _PROJECT_RECORD_AREAS:
        raise ProjectStorageValidationError("项目受控目录类型不合法")
    identifier = _positive_id(project_id, "project_id")
    record_field = "export_id" if area == "exports" else "snapshot_id"
    record_identifier = _positive_id(record_id, record_field)
    storage_root = get_storage_root()
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ProjectStorageValidationError("项目存储根目录无法访问") from exc

    current_path = storage_root
    for component in ("projects", str(identifier), area, str(record_identifier)):
        next_path = current_path / component
        if not _inspect_directory_component(next_path):
            if not create:
                raise ProjectStorageValidationError("项目受控目录不存在")
            try:
                next_path.mkdir()
            except OSError as exc:
                raise ProjectStorageValidationError("项目受控目录无法创建") from exc
            _inspect_directory_component(next_path)
        current_path = next_path
    return current_path


def export_root(project_id, export_id):
    return resolve_project_record_root(project_id, "exports", export_id, create=True)


def snapshot_root(project_id, snapshot_id):
    return resolve_project_record_root(project_id, "snapshots", snapshot_id, create=True)


def _validate_record_filename(filename):
    raw_filename = str(filename or "").strip()
    path = Path(raw_filename)
    windows_path = PureWindowsPath(raw_filename)
    if (
        not raw_filename
        or len(path.parts) != 1
        or len(windows_path.parts) != 1
        or path.name != raw_filename
        or windows_path.name != raw_filename
        or path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectStorageValidationError("项目受控文件名不合法")
    return raw_filename


def _inspect_regular_file(path, require_exists):
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        if require_exists:
            raise ProjectStorageValidationError("项目受控文件不存在")
        return False
    except OSError as exc:
        raise ProjectStorageValidationError("项目受控文件无法安全访问") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or getattr(metadata, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ProjectStorageValidationError("项目受控文件不允许使用链接")
    if not stat.S_ISREG(metadata.st_mode):
        raise ProjectStorageValidationError("项目受控路径必须为普通文件")
    return True


def resolve_project_record_file(project_id, area, record_id, filename, require_exists=False):
    parent_path = resolve_project_record_root(project_id, area, record_id, create=False)
    target_path = parent_path / _validate_record_filename(filename)
    _inspect_regular_file(target_path, require_exists)
    return target_path


def _resolve_storage_file(path):
    storage_root = get_storage_root()
    raw_path = Path(path).expanduser()
    windows_path = PureWindowsPath(str(path or ""))
    if (
        ".." in raw_path.parts
        or ".." in windows_path.parts
        or ((windows_path.is_absolute() or windows_path.drive) and not raw_path.is_absolute())
    ):
        raise ProjectStorageValidationError("项目存储路径越界")
    if raw_path.is_absolute():
        try:
            relative_path = raw_path.relative_to(storage_root)
        except ValueError as exc:
            raise ProjectStorageValidationError("项目存储路径越界") from exc
    else:
        relative_path = raw_path
    return storage_root / relative_path


def _ensure_safe_storage_parent(target_path):
    storage_root = get_storage_root()
    try:
        relative_parent = target_path.parent.relative_to(storage_root)
    except ValueError as exc:
        raise ProjectStorageValidationError("项目存储路径越界") from exc
    current_path = storage_root
    for component in relative_parent.parts:
        current_path = current_path / component
        if not _inspect_directory_component(current_path):
            try:
                current_path.mkdir()
            except OSError as exc:
                raise ProjectStorageValidationError("项目存储目录无法创建") from exc
            _inspect_directory_component(current_path)
    return current_path


def write_json_atomic(path, payload, *, area=None, project_id=None, record_id=None):
    target_path = _resolve_storage_file(path)
    parent_path = _ensure_safe_storage_parent(target_path)
    _inspect_regular_file(target_path, require_exists=False)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent_path,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target_path)
    except Exception:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError as cleanup_exc:
                _log_cleanup_failure(
                    "write_json_atomic_temp_cleanup",
                    cleanup_exc,
                    area=area,
                    project_id=project_id,
                    record_id=record_id,
                )
        raise
