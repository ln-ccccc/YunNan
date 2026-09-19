import datetime
import json
import logging
import math
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from applications.extensions import db
from applications.models.project import ProjectActivityLog
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource

logger = logging.getLogger(__name__)

# 激活新底图时保留的历史底图数量（不含当前激活的那一张）
RETAINED_BASEMAP_KEEP = 2
from applications.project_hub.spatial_storage import ensure_storage_layout, resolve_storage_path


WEB_MERCATOR_MAX_LATITUDE = 85.05112878


def _estimate_xyz_tile_count(bounds, min_zoom, max_zoom):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return 0
    min_lon, min_lat, max_lon, max_lat = (float(value) for value in bounds)
    min_lon = max(-180.0, min(180.0, min_lon))
    max_lon = max(-180.0, min(180.0, max_lon))
    min_lat = max(-WEB_MERCATOR_MAX_LATITUDE, min(WEB_MERCATOR_MAX_LATITUDE, min_lat))
    max_lat = max(-WEB_MERCATOR_MAX_LATITUDE, min(WEB_MERCATOR_MAX_LATITUDE, max_lat))

    def tile_x(longitude, zoom):
        count = 1 << zoom
        return min(count - 1, max(0, int(math.floor((longitude + 180.0) / 360.0 * count))))

    def tile_y(latitude, zoom):
        count = 1 << zoom
        radians = math.radians(latitude)
        value = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * count
        return min(count - 1, max(0, int(math.floor(value))))

    total = 0
    for zoom in range(int(min_zoom), int(max_zoom) + 1):
        x_min, x_max = sorted((tile_x(min_lon, zoom), tile_x(max_lon, zoom)))
        y_min, y_max = sorted((tile_y(min_lat, zoom), tile_y(max_lat, zoom)))
        total += (x_max - x_min + 1) * (y_max - y_min + 1)
    return total


def recover_running_jobs():
    running = ProjectSpatialJob.query.filter_by(status="running").all()
    recovered = 0
    for job in running:
        resource = ProjectSpatialResource.query.get(job.resource_id)
        if job.cancel_requested:
            job.status = "cancelled"
            job.stage = "cancelled"
            if resource is not None and resource.status != "active":
                resource.status = "retained"
        else:
            job.status = "queued"
            job.stage = "queued"
            job.worker_id = None
            if resource is not None:
                resource.status = "pending"
        recovered += 1
    db.session.commit()
    return recovered


def claim_next_job(worker_id):
    while True:
        candidate = (
            ProjectSpatialJob.query.filter_by(status="queued")
            .order_by(ProjectSpatialJob.create_time.asc())
            .first()
        )
        if candidate is None:
            # End the read transaction so MySQL REPEATABLE READ can see jobs
            # inserted after the worker started.
            db.session.rollback()
            return None
        now = datetime.datetime.now()
        changed = ProjectSpatialJob.query.filter_by(id=candidate.id, status="queued").update(
            {
                "status": "running",
                "stage": "preparing",
                "progress": 1.0,
                "worker_id": worker_id,
                "heartbeat_at": now,
                "update_time": now,
            },
            synchronize_session=False,
        )
        db.session.commit()
        if changed:
            job = ProjectSpatialJob.query.get(candidate.id)
            resource = ProjectSpatialResource.query.get(job.resource_id)
            if resource is not None:
                resource.status = "processing"
                db.session.commit()
            return job


def _activity(project_id, event_type, payload):
    import json

    db.session.add(
        ProjectActivityLog(
            project_id=project_id,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=False),
        )
    )


def _atomic_copy2(source, target):
    """复制到同目录临时文件后原子替换（甲方建议 2026-09-03）：
    GB 级复制中断不会在目标位置留半文件，重试总是得到完整副本。"""
    temporary = target.with_name(f".{target.name}.copying")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _copy_source_to_project(resource, storage_root):
    source = resolve_storage_path(storage_root, resource.source_path)
    if not source.is_file():
        raise FileNotFoundError(f"底图源文件不存在: {resource.source_path}")
    target_dir = storage_root / "projects" / str(resource.project_id) / "basemaps" / str(resource.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    ready_marker = target.with_name(f"{target.name}.ready")
    if source.resolve() != target.resolve():
        # 曾有失败模式：中断的复制留下半文件，target.exists() 即跳过导致切片读坏文件。
        # 现改为完整副本校验（.ready 标记或字节数一致），不一致则原子重复制
        already_copied = target.exists() and (
            ready_marker.is_file() or target.stat().st_size == source.stat().st_size
        )
        if not already_copied:
            _atomic_copy2(source, target)
            # 完成标记：记录字节数，供续跑时免重复制快速校验
            ready_marker.write_text(str(target.stat().st_size), encoding="utf-8")
        possible_sidecars = [
            Path(f"{source}.ovr"),
            source.with_suffix(".tfw"),
            source.with_suffix(".prj"),
            source.with_suffix(".enp"),
            Path(f"{source}.enp"),
            Path(f"{source}.aux.xml"),
            source.with_suffix(".aux.xml"),
        ]
        for sidecar in possible_sidecars:
            if not sidecar.is_file():
                continue
            sidecar_target = target_dir / sidecar.name
            if sidecar_target.exists() and sidecar_target.stat().st_size == sidecar.stat().st_size:
                continue
            _atomic_copy2(sidecar, sidecar_target)
    resource.source_path = target.relative_to(storage_root).as_posix()
    db.session.commit()
    return target


def _terminate(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _run_gdal2tiles(job, resource, source, tile_dir, poll_seconds):
    command = [
        "gdal2tiles.py",
        "--xyz",
        "--resume",
        "--webviewer=none",
        f"--processes={max(1, int(os.getenv('SPATIAL_TILE_PROCESSES', '4')))}",
        f"--zoom={resource.min_zoom}-{resource.max_zoom}",
        str(source),
        str(tile_dir),
    ]
    environment = os.environ.copy()
    environment.setdefault("GDAL_DATA", "/opt/conda/envs/MMSeg310/share/gdal")
    environment.setdefault("PROJ_LIB", "/opt/conda/envs/MMSeg310/share/proj")
    environment.setdefault("PROJ_DATA", environment["PROJ_LIB"])
    try:
        expected_tiles = _estimate_xyz_tile_count(
            json.loads(resource.bounds_json or "[]"), resource.min_zoom, resource.max_zoom
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        expected_tiles = 0
    next_progress_check = 0.0
    process = subprocess.Popen(command, env=environment)
    while process.poll() is None:
        time.sleep(poll_seconds)
        db.session.expire(job)
        if job.cancel_requested:
            _terminate(process)
            raise InterruptedError("任务已取消")
        job.heartbeat_at = datetime.datetime.now()
        now = time.monotonic()
        if expected_tiles and now >= next_progress_check:
            completed_tiles = sum(1 for _ in tile_dir.rglob("*.png"))
            job.progress = min(95.0, max(10.0, 10.0 + 85.0 * completed_tiles / expected_tiles))
            next_progress_check = now + 10.0
        db.session.commit()
    if process.returncode:
        raise RuntimeError(f"gdal2tiles 失败，退出码 {process.returncode}")


def _activate_basemap(job, resource, storage_root):
    previous_active = ProjectSpatialResource.query.filter_by(
        project_id=resource.project_id,
        resource_type="basemap",
        status="active",
    ).all()
    for previous in previous_active:
        previous.status = "retained"
    resource.status = "active"
    resource.error_message = None
    job.status = "succeeded"
    job.stage = "completed"
    job.progress = 100.0
    job.heartbeat_at = datetime.datetime.now()
    retained = (
        ProjectSpatialResource.query.filter_by(
            project_id=resource.project_id,
            resource_type="basemap",
            status="retained",
        )
        .order_by(ProjectSpatialResource.version.desc())
        .all()
    )
    obsolete_dirs = []
    previous_markers = []
    for previous in previous_active:
        if previous.tile_path:
            previous_markers.append(resolve_storage_path(storage_root, previous.tile_path) / ".active")
    # 历史底图保护：只清理超出保留数量的最旧资源，保留最近 2 个历史底图。
    # 原逻辑 retained[1:] 会在每次激活时把更早的历史底图连库记录、原图与瓦片
    # 一起永久删除——生产上 10GB 级原图被静默清除是事故（2026-09-09 上线前实测）。
    for obsolete in retained[RETAINED_BASEMAP_KEEP:]:
        obsolete_dirs.extend(
            [
                storage_root / "projects" / str(obsolete.project_id) / "basemaps" / str(obsolete.id),
                storage_root / "projects" / str(obsolete.project_id) / "tiles" / str(obsolete.id),
            ]
        )
        _activity(
            resource.project_id,
            "spatial_resource_removed",
            {"resource_id": obsolete.id, "resource_type": "basemap", "version": obsolete.version},
        )
        db.session.delete(obsolete)
    _activity(
        resource.project_id,
        "spatial_resource_activated",
        {"resource_id": resource.id, "resource_type": "basemap", "version": resource.version},
    )
    active_marker = resolve_storage_path(storage_root, resource.tile_path) / ".active"
    active_marker.write_text(str(resource.id), encoding="ascii")
    try:
        db.session.commit()
    except Exception:
        active_marker.unlink(missing_ok=True)
        raise
    for marker in previous_markers:
        marker.unlink(missing_ok=True)
    for obsolete_dir in obsolete_dirs:
        shutil.rmtree(obsolete_dir, ignore_errors=True)


def process_job(job, poll_seconds=1.0):
    resource = ProjectSpatialResource.query.filter_by(id=job.resource_id, project_id=job.project_id).first()
    if resource is None:
        raise ValueError("任务对应的空间资源不存在")
    storage_root = ensure_storage_layout()
    try:
        source = _copy_source_to_project(resource, storage_root)
        tile_dir = resolve_storage_path(storage_root, resource.tile_path)
        tile_dir.mkdir(parents=True, exist_ok=True)
        job.stage = "tiling"
        job.progress = 10.0
        db.session.commit()
        _run_gdal2tiles(job, resource, source, tile_dir, poll_seconds)
        if not any(tile_dir.rglob("*.png")):
            raise RuntimeError("切片完成但未生成 PNG 瓦片")
        _activate_basemap(job, resource, storage_root)
    except InterruptedError as exc:
        job.status = "cancelled"
        job.stage = "cancelled"
        job.error_message = str(exc)
        resource.status = "retained"
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job = ProjectSpatialJob.query.get(job.id)
        resource = ProjectSpatialResource.query.get(job.resource_id)
        job.status = "failed"
        job.stage = "failed"
        job.error_message = str(exc)
        resource.status = "failed"
        resource.error_message = str(exc)
        db.session.commit()


class SpatialWorker:
    def __init__(self, worker_id=None, poll_seconds=2.0):
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.poll_seconds = poll_seconds

    def run_forever(self, should_stop):
        recover_running_jobs()
        while not should_stop():
            job = claim_next_job(self.worker_id)
            if job is None:
                time.sleep(self.poll_seconds)
                continue
            try:
                process_job(job, poll_seconds=min(1.0, self.poll_seconds))
            except Exception:
                # 常驻 worker 不得被单个任务杀死（与推理 worker 同一韧性约定）：
                # 记完整堆栈、尽力把任务落为 failed 终态后继续服务
                _isolate_failed_job(job)


def _isolate_failed_job(job):
    logger.exception("空间任务 %s 处理出现未捕获异常，已隔离该任务", getattr(job, "id", "?"))
    try:
        db.session.rollback()
        fresh = ProjectSpatialJob.query.get(job.id)
        if fresh is None or fresh.status in ("succeeded", "failed", "cancelled"):
            return
        now = datetime.datetime.now()
        fresh.status = "failed"
        fresh.stage = "failed"
        fresh.error_message = "任务在切片流程之外抛出未捕获异常，已被 worker 隔离"
        fresh.update_time = now
        resource = ProjectSpatialResource.query.get(fresh.resource_id)
        if resource is not None and resource.status == "processing":
            resource.status = "failed"
            resource.error_message = fresh.error_message
        db.session.commit()
    except Exception:
        logger.exception("隔离空间任务 %s 时写入终态失败", getattr(job, "id", "?"))
        db.session.rollback()
