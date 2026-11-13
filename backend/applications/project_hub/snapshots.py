# -*- coding: utf-8 -*-
"""项目配置快照域（H3 拆分，2026-10-01）：快照创建/清单/恢复与跨环境导入。
契约不变——service 层 re-export 保持既有 import 兼容。快照是元数据+索引打包，
不是完整备份（AGENTS §6 命名约束）。"""
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path, PureWindowsPath

from applications.extensions import db
from applications.models.inference_job import InferenceJob
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectBackupRecord,
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.schemas.project import (
    ProjectBackupRecordSchema,
    ProjectMineBindingSchema,
)
from applications.project_hub._foundation import (
    _append_activity,
    _cleanup_storage_directory,
    _get_project_or_404,
    _json_dump,
    _json_load,
    _log_storage_cleanup_failure,
    _mark_backup_failed,
    _rollback_after_storage_failure,
    _snapshot_directory,
    _storage_key,
    _utc_timestamp,
    _write_artifact_atomic,
)
from applications.project_hub.project_storage import (
    ProjectStorageValidationError,
    resolve_project_record_file,
    snapshot_root,
    write_json_atomic,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)


def _relative_storage_key(raw):
    """manifest 对外只输出 storage 相对键（契约 B）：legacy 绝对路径在根内
    则收敛为相对键（消除内部物理路径随快照下载外泄），根外异常值原样保留
    由迁移脚本报告处置。"""
    text = str(raw or "").strip()
    if not text or not Path(text).expanduser().is_absolute():
        return raw
    try:
        return Path(text).expanduser().resolve().relative_to(get_storage_root()).as_posix()
    except ValueError:
        return raw


def _serialize_internal_dataset(dataset):
    return {
        "id": dataset.id,
        "dataset_kind": dataset.dataset_kind,
        "display_name": dataset.display_name,
        "file_path": _relative_storage_key(dataset.file_path),
        "source_format": dataset.source_format,
        "mine_fid": dataset.mine_fid,
        "year_start": dataset.year_start,
        "year_end": dataset.year_end,
        "slice_config_json": _json_load(dataset.slice_config_json, {}),
    }

def _serialize_internal_export(record):
    return {
        "id": record.id,
        "format": record.format,
        "file_path": _relative_storage_key(record.file_path),
        "status": record.status,
        "request_params": _json_load(record.request_params_json, {}),
    }

def _serialize_internal_activity(activity):
    return {
        "id": activity.id,
        "event_type": activity.event_type,
        "actor": activity.actor,
        "payload": _json_load(activity.payload_json, {}),
        # 审计时间线的主数据：恢复时回写原始时间，避免整段历史被压缩到恢复时刻
        "created_at": activity.create_time.isoformat() if activity.create_time else None,
    }

def _project_manifest(project, backup_id):
    return {
        "snapshot_version": 1,
        "project_id": project.id,
        "backup_id": backup_id,
        "summary": {
            "name": project.name,
            "region": project.region,
            "manager": project.manager,
            "remark": project.remark,
            "status": project.status,
            "monitor_start_year": project.monitor_start_year,
            "monitor_end_year": project.monitor_end_year,
        },
        "mines": ProjectMineBindingSchema(many=True).dump(project.mines),
        "datasets": [_serialize_internal_dataset(dataset) for dataset in project.datasets],
        "exports": [_serialize_internal_export(record) for record in project.exports],
        "activities": [_serialize_internal_activity(activity) for activity in project.activities],
    }

def resolve_project_backup_manifest(project_id, backup_id):
    """配置快照清单下载解析：校验记录归属与完成态，返回 (record, 落盘路径)。"""
    record = ProjectBackupRecord.query.get(backup_id)
    if record is None or record.project_id != project_id:
        raise ProjectStorageValidationError("项目配置快照不存在")
    if record.status != "completed":
        raise ValueError("快照尚未完成，无法下载")
    path = resolve_project_record_file(
        project_id, "snapshots", backup_id, "manifest.json", require_exists=True
    )
    return record, path

def create_backup(project_id, payload, actor="system"):
    if not isinstance(payload, dict):
        raise ProjectStorageValidationError("项目配置快照请求格式不合法")
    if "output_dir" in payload:
        raise ProjectStorageValidationError("不支持指定服务端输出目录，请移除 output_dir")
    if set(payload).difference({"scope"}):
        raise ProjectStorageValidationError("项目配置快照请求包含不支持字段")
    project = _get_project_or_404(project_id)
    scope = str(payload.get("scope") or "metadata_index").strip() or "metadata_index"
    if scope != "metadata_index":
        raise ProjectStorageValidationError("当前仅支持 metadata_index 项目配置快照")
    backup = ProjectBackupRecord(
        project_id=project.id,
        scope=scope,
        manifest_path="",
        status="pending",
        restorable=False,
    )
    project_identifier = project.id
    backup_id = None
    try:
        db.session.add(backup)
        db.session.flush()
        backup_id = backup.id
        manifest_key = _storage_key("projects", str(project_identifier), "snapshots", str(backup_id), "manifest.json")
        backup.manifest_path = manifest_key
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "snapshots",
            project_identifier,
            backup_id,
            "initial_pending_rollback",
        )
        raise
    try:
        snapshot_root(project_identifier, backup_id)
        manifest_path = resolve_project_record_file(
            project_identifier,
            "snapshots",
            backup_id,
            "manifest.json",
        )
        write_json_atomic(
            manifest_path,
            _project_manifest(project, backup_id),
            area="snapshots",
            project_id=project_identifier,
            record_id=backup_id,
        )
        backup.status = "completed"
        backup.restorable = True
        _append_activity(
            project_identifier,
            "backup_created",
            {"backup_id": backup_id, "scope": scope},
            actor=actor,
            target={"type": "snapshot", "id": str(backup_id)},
        )
        db.session.commit()
    except Exception:
        _rollback_after_storage_failure(
            "snapshots",
            project_identifier,
            backup_id,
            "final_snapshot_rollback",
        )
        _cleanup_storage_directory(project_identifier, "snapshots", backup_id)
        _mark_backup_failed(project_identifier, backup_id)
        raise
    return ProjectBackupRecordSchema().dump(backup)

def list_backups(project_id):
    project = _get_project_or_404(project_id)
    return {"items": ProjectBackupRecordSchema(many=True).dump(project.backups), "count": len(project.backups)}

def _snapshot_manifest_path(project_id, backup_id, manifest_key):
    raw_key = str(manifest_key or "").strip()
    expected_key = _storage_key(
        "projects",
        str(project_id),
        "snapshots",
        str(backup_id),
        "manifest.json",
    )
    windows_path = PureWindowsPath(raw_key)
    if (
        not raw_key
        or raw_key != expected_key
        or Path(raw_key).is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
    ):
        raise ProjectStorageValidationError("配置快照路径不合法")
    return resolve_project_record_file(
        project_id,
        "snapshots",
        backup_id,
        "manifest.json",
        require_exists=True,
    )

def _validate_backup_manifest(manifest, project_id, backup_id):
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_version") != 1
        or isinstance(manifest.get("project_id"), bool)
        or not isinstance(manifest.get("project_id"), int)
        or manifest.get("project_id") != project_id
        or isinstance(manifest.get("backup_id"), bool)
        or not isinstance(manifest.get("backup_id"), int)
        or manifest.get("backup_id") != backup_id
        or not isinstance(manifest.get("summary"), dict)
    ):
        raise ProjectStorageValidationError("配置快照内容不合法")
    required_fields = {
        "mines": ("mine_fid",),
        "datasets": ("dataset_kind", "display_name", "file_path"),
        "exports": ("format",),
        "activities": ("event_type",),
    }
    for section, fields in required_fields.items():
        items = manifest.get(section)
        if not isinstance(items, list):
            raise ProjectStorageValidationError("配置快照内容不合法")
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in fields):
                raise ProjectStorageValidationError("配置快照内容不合法")
    return manifest

def _validate_import_manifest(manifest):
    """导入校验：结构同 _validate_backup_manifest，但不要求 project_id/backup_id 匹配
    （跨环境迁移时 ID 必然不同）。"""
    if (
        not isinstance(manifest, dict)
        or manifest.get("snapshot_version") != 1
        or not isinstance(manifest.get("summary"), dict)
    ):
        raise ProjectStorageValidationError("导入的配置快照内容不合法")
    required_fields = {
        "mines": ("mine_fid",),
        "datasets": ("dataset_kind", "display_name", "file_path"),
        "exports": ("format",),
        "activities": ("event_type",),
    }
    for section, fields in required_fields.items():
        items = manifest.get(section)
        if not isinstance(items, list):
            raise ProjectStorageValidationError("导入的配置快照内容不合法")
        for item in items:
            if not isinstance(item, dict) or any(field not in item for field in fields):
                raise ProjectStorageValidationError("导入的配置快照内容不合法")
    return manifest

def _dataset_ids_referenced_by_jobs():
    """任务载荷引用的数据集 ID 集合（引用保护，与数据集删除接口同口径）。"""
    referenced = set()
    jobs = InferenceJob.query.filter(
        InferenceJob.request_payload_json.like('%"dataset_id"%')
    ).all()
    for job in jobs:
        payload = _json_load(job.request_payload_json, {})
        dataset_id = (payload or {}).get("dataset_id")
        if isinstance(dataset_id, int) and not isinstance(dataset_id, bool):
            referenced.add(dataset_id)
    return referenced

def import_backup_manifest(project_id, manifest, actor="system"):
    """跨环境快照导入（M2 计划 §3）：上传 manifest JSON 应用到目标项目。

    与 restore_backup 的差异：ID 不匹配场景合法（只校验结构）；不覆盖目标项目
    的活动时间线（导入记一条 project_imported 事件，原 activities 仅存档不重放）；
    mines 覆盖式重建；datasets 重建为新身份并显式记录 源ID→新ID 映射（问题3：
    旧环境的引用不得视为新环境有效引用，映射随事件与导入存档可查），被任务引用
    的目标登记不随导入删除（引用保护）；exports 忽略（跨环境路径无意义）。
    """
    from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

    manifest = _validate_import_manifest(manifest)
    project = _get_project_or_404(project_id)
    try:
        summary = manifest["summary"]
        project.name = summary.get("name") or project.name
        project.region = summary.get("region")
        project.manager = summary.get("manager")
        project.remark = summary.get("remark")
        project.monitor_start_year = summary.get("monitor_start_year")
        project.monitor_end_year = summary.get("monitor_end_year")

        # 引用保护（与数据集删除接口同口径）：目标项目被任务引用的登记不随导入删除
        referenced_dataset_ids = _dataset_ids_referenced_by_jobs()
        retained_dataset_ids = []
        for row in ProjectDataset.query.filter_by(project_id=project.id).all():
            if row.id in referenced_dataset_ids:
                retained_dataset_ids.append(row.id)
            else:
                db.session.delete(row)
        ProjectMineBinding.query.filter_by(project_id=project.id).delete()
        db.session.flush()

        for item in manifest["mines"]:
            db.session.add(
                ProjectMineBinding(
                    project_id=project.id,
                    mine_fid=item["mine_fid"],
                    mine_name_snapshot=item.get("mine_name_snapshot"),
                    city_snapshot=item.get("city_snapshot"),
                    area_snapshot=item.get("area_snapshot"),
                    status_snapshot=item.get("status_snapshot"),
                    sort_order=item.get("sort_order") or 0,
                )
            )
        # 导入数据集的 file_path 统一收敛为目标项目内相对键（2026-10-24 契约 B）：
        # 跨环境导入时 incoming/ 是源环境全局暂存区、projects/<他项目id>/ 是源项目
        # 沙箱，都不可原样落地——一律重定位到本项目 inputs/imagery/，物理文件由
        # 运维另行搬运；绝对/穿越路径仍然拒绝（2026-09-22 收官审查 S1 的防线保留）

        def _safe_dataset_path(raw):
            text = str(raw or "").strip()
            if not text:
                raise ProjectStorageValidationError("导入的数据集缺少路径")
            candidate = Path(text).expanduser()
            if candidate.is_absolute():
                # legacy 形态：仅接受源环境 storage 根内的绝对路径，先收敛为相对键
                try:
                    candidate = Path(candidate.resolve().relative_to(get_storage_root()).as_posix())
                except ValueError:
                    raise ProjectStorageValidationError(
                        "导入的数据集路径不在本项目存储内，已拒绝"
                    ) from None
            if ".." in candidate.parts:
                raise ProjectStorageValidationError("导入的数据集路径不合法")
            parts = candidate.parts
            if parts[:1] == ("incoming",):
                rebased = Path("projects") / str(project.id) / "inputs" / "imagery" / candidate.name
            elif parts[:1] == ("projects",) and len(parts) >= 2:
                # 他项目或本项目旧键：剥掉源项目 id 段，落回目标项目根下
                rebased = Path("projects") / str(project.id) / Path(*parts[2:])
            else:
                raise ProjectStorageValidationError(
                    "导入的数据集路径不在本项目存储内，已拒绝"
                )
            try:
                resolve_storage_path(get_storage_root(), rebased)
            except ValueError:
                raise ProjectStorageValidationError("导入的数据集路径不合法") from None
            return rebased.as_posix()

        dataset_id_map = {}
        for item in manifest["datasets"]:
            row = ProjectDataset(
                project_id=project.id,
                dataset_kind=item["dataset_kind"],
                display_name=item["display_name"],
                file_path=_safe_dataset_path(item["file_path"]),
                source_format=item.get("source_format"),
                mine_fid=item.get("mine_fid"),
                year_start=item.get("year_start"),
                year_end=item.get("year_end"),
                slice_config_json=_json_dump(item.get("slice_config_json")),
            )
            db.session.add(row)
            db.session.flush()
            source_id = item.get("id")
            if isinstance(source_id, int) and not isinstance(source_id, bool):
                dataset_id_map[str(source_id)] = row.id

        # 导入的 manifest 原样存档（追溯导入来源），活动只记一条事件
        storage_root = get_storage_root()
        import_dir = resolve_storage_path(
            storage_root, Path("projects") / str(project.id) / "imports"
        )
        import_dir.mkdir(parents=True, exist_ok=True)
        import_path = import_dir / f"manifest_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
        write_json_atomic(import_path, {
            **manifest,
            "import_result": {
                "target_project_id": project.id,
                "imported_at": datetime.now().isoformat(),
                "dataset_id_map": dataset_id_map,
                "retained_dataset_ids": sorted(retained_dataset_ids),
            },
        })

        _append_activity(
            project.id,
            "project_imported",
            {
                "source_project_id": manifest.get("project_id"),
                "source_backup_id": manifest.get("backup_id"),
                "mines": len(manifest["mines"]),
                "datasets": len(manifest["datasets"]),
                "dataset_id_map": dataset_id_map,
                "retained_dataset_ids": sorted(retained_dataset_ids),
            },
            actor=actor,
            target={"type": "project", "id": str(project.id)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    # overview 装配在 service 层；延迟 import 避免循环（H3 拆分边界）
    from applications.project_hub.service import get_project_overview

    return get_project_overview(project.id)

def _restore_reconcile_mines(project, manifest_mines):
    """矿山绑定按稳定身份对齐：先按快照 id，再按 mine_fid 兜底，未列出的删除。"""
    live_bindings = {binding.id: binding for binding in project.mines}
    by_fid = {binding.mine_fid: binding for binding in project.mines}
    matched_ids = set()
    for item in manifest_mines:
        binding = None
        raw_id = item.get("id")
        if isinstance(raw_id, int) and not isinstance(raw_id, bool):
            candidate = live_bindings.get(raw_id)
            if candidate is not None:
                binding = candidate
        if binding is None:
            binding = by_fid.get(item["mine_fid"])
        if binding is None:
            binding = ProjectMineBinding(project_id=project.id)
            db.session.add(binding)
            by_fid[item["mine_fid"]] = binding
        binding.mine_fid = item["mine_fid"]
        binding.mine_name_snapshot = item.get("mine_name_snapshot")
        binding.city_snapshot = item.get("city_snapshot")
        binding.area_snapshot = item.get("area_snapshot")
        binding.status_snapshot = item.get("status_snapshot")
        binding.sort_order = item.get("sort_order") or 0
        if binding.id is not None:
            matched_ids.add(binding.id)
    for binding in live_bindings.values():
        if binding.id not in matched_ids:
            db.session.delete(binding)
    return len(manifest_mines)

def _restore_reconcile_datasets(project, manifest_datasets):
    """数据集按快照身份原位恢复；快照后被删的按原身份重建；快照后新增且未被
    任务引用的登记移除，被引用的保留（引用保护，与删除接口同口径）。"""
    referenced_dataset_ids = _dataset_ids_referenced_by_jobs()
    live_ids = {
        row_id
        for (row_id,) in db.session.query(ProjectDataset.id).filter_by(
            project_id=project.id
        )
    }
    restored_ids = set()
    for item in manifest_datasets:
        dataset_id = item["id"]
        dataset = db.session.get(ProjectDataset, dataset_id)
        if dataset is not None and dataset.project_id != project.id:
            raise ProjectStorageValidationError(
                "配置快照数据集身份与其他项目冲突，已拒绝恢复"
            )
        if dataset is None:
            dataset = ProjectDataset(id=dataset_id, project_id=project.id)
            db.session.add(dataset)
        dataset.dataset_kind = item["dataset_kind"]
        dataset.display_name = item["display_name"]
        dataset.file_path = item["file_path"]
        dataset.source_format = item.get("source_format")
        dataset.mine_fid = item.get("mine_fid")
        dataset.year_start = item.get("year_start")
        dataset.year_end = item.get("year_end")
        dataset.slice_config_json = _json_dump(item.get("slice_config_json"))
        restored_ids.add(dataset_id)
    retained_referenced = []
    for dataset_id in sorted(live_ids - restored_ids):
        if dataset_id in referenced_dataset_ids:
            retained_referenced.append(dataset_id)
        else:
            db.session.delete(db.session.get(ProjectDataset, dataset_id))
    return restored_ids, retained_referenced

def restore_backup(project_id, backup_id, actor="system"):
    """同项目配置快照恢复：按记录身份原位更新，不删表重建（问题3——重建会改变
    自增 ID，任务载荷引用与 exports/<id>/ 物理目录全部断链）。快照后新增且未被
    任务引用的数据集登记按恢复语义移除，被引用的保留；导出记录与活动时间线是
    不可变历史，恢复只追加 backup_restored 事件（问题4）。"""
    project = _get_project_or_404(project_id)
    backup = ProjectBackupRecord.query.filter_by(id=backup_id, project_id=project.id).first()
    if not backup:
        raise ValueError(f"备份不存在: {backup_id}")
    if backup.status != "completed" or not backup.restorable:
        raise ProjectStorageValidationError("配置快照不可恢复")
    try:
        manifest_path = _snapshot_manifest_path(project.id, backup.id, backup.manifest_path)
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = _validate_backup_manifest(json.load(handle), project.id, backup.id)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectStorageValidationError("配置快照内容不合法") from exc

    try:
        summary = manifest["summary"]
        project.name = summary.get("name") or project.name
        project.region = summary.get("region")
        project.manager = summary.get("manager")
        project.remark = summary.get("remark")
        project.status = "active"
        project.monitor_start_year = summary.get("monitor_start_year")
        project.monitor_end_year = summary.get("monitor_end_year")
        project.deleted_at = None

        mine_count = _restore_reconcile_mines(project, manifest["mines"])

        # 旧版快照未记录数据集身份：禁止猜测关联，跳过该段（限制恢复范围）
        dataset_items = manifest["datasets"]
        has_dataset_identity = all(
            isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool)
            for item in dataset_items
        )
        skipped_sections = []
        restored_dataset_count = 0
        retained_referenced = []
        if has_dataset_identity:
            restored_ids, retained_referenced = _restore_reconcile_datasets(
                project, dataset_items
            )
            restored_dataset_count = len(restored_ids)
        elif dataset_items:
            skipped_sections.append("datasets")

        # 导出历史是不可变记录：重建会使 exports/<id>/ 物理目录与下载引用断链，
        # 恢复一律不触碰 ProjectExportRecord；审计只追加：快照内活动本就留在
        # 库中，不删除也不重放

        _append_activity(
            project.id,
            "backup_restored",
            {
                "backup_id": backup.id,
                "restored": {"mines": mine_count, "datasets": restored_dataset_count},
                "retained_referenced_datasets": sorted(retained_referenced),
                "skipped_sections": skipped_sections,
            },
            actor=actor,
            target={"type": "snapshot", "id": str(backup.id)},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    # overview 装配在 service 层；延迟 import 避免循环（H3 拆分边界）
    from applications.project_hub.service import get_project_overview

    return get_project_overview(project.id)
