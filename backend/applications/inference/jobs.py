"""推理任务请求、持久化和状态更新。"""

import datetime
import json
import uuid
from pathlib import Path


def _is_within(path_obj, root_obj):
    try:
        path_obj.relative_to(root_obj)
        return True
    except ValueError:
        return False


def _resolve_existing_input(value, allowed_roots, field_name):
    if not value:
        raise ValueError(f"缺少 {field_name}")
    path_obj = Path(value).expanduser().resolve()
    if not path_obj.exists():
        raise FileNotFoundError(f"{field_name} 不存在: {path_obj}")
    roots = [Path(root).expanduser().resolve() for root in allowed_roots]
    if not any(_is_within(path_obj, root) for root in roots):
        raise ValueError(f"{field_name} 不在允许目录中: {path_obj}")
    return str(path_obj)


def _normalize_requested_device(value):
    requested = str(value or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"
    if requested in {"auto", "cuda"} or requested.startswith("cuda:"):
        return "auto"
    raise ValueError(f"不支持的推理设备: {value}")


def normalize_job_request(payload, *, allowed_roots, allowed_output_roots=None):
    source = dict(payload or {})
    old_tif_path = _resolve_existing_input(source.get("old_tif_path"), allowed_roots, "old_tif_path")
    new_tif_path = _resolve_existing_input(
        source.get("new_tif_path") or old_tif_path,
        allowed_roots,
        "new_tif_path",
    )
    result = {
        "old_tif_path": old_tif_path,
        "new_tif_path": new_tif_path,
        "requested_device": _normalize_requested_device(source.get("device") or source.get("requested_device")),
        "limit": max(0, int(source.get("limit") or 0)),
        "year": str(source.get("year") or ""),
        "old_year": str(source.get("old_year") or ""),
        "new_year": str(source.get("new_year") or ""),
    }
    if source.get("project_id") not in (None, ""):
        result["project_id"] = int(source["project_id"])
    if source.get("mine_resource_id") not in (None, ""):
        result["mine_resource_id"] = int(source["mine_resource_id"])
    if source.get("mine_fids") is not None:
        try:
            mine_fids = sorted({int(fid) for fid in source.get("mine_fids") or []})
        except (TypeError, ValueError) as exc:
            raise ValueError("mine_fids 必须是正整数列表") from exc
        if any(fid <= 0 for fid in mine_fids):
            raise ValueError("mine_fids 必须是正整数")
        result["mine_fids"] = mine_fids
    if source.get("kml_path"):
        result["kml_path"] = _resolve_existing_input(source["kml_path"], allowed_roots, "kml_path")
    if source.get("output_root"):
        output_path = Path(source["output_root"]).expanduser().resolve()
        output_roots = [
            Path(root).expanduser().resolve()
            for root in (allowed_output_roots if allowed_output_roots is not None else allowed_roots)
        ]
        if not any(_is_within(output_path, root) for root in output_roots):
            raise ValueError(f"output_root 不在允许目录中: {output_path}")
        result["output_root"] = str(output_path)
    return result


def _database_dependencies():
    from applications.extensions import db
    from applications.models.inference_job import InferenceJob

    return db, InferenceJob


def publish_worker_capability(worker_id, resolution):
    from applications.extensions import db
    from applications.models.inference_job import InferenceWorkerState

    state = InferenceWorkerState.query.filter_by(worker_id=str(worker_id)).first()
    if state is None:
        state = InferenceWorkerState(worker_id=str(worker_id))
        db.session.add(state)
    state.requested_device = resolution.requested
    state.effective_device = resolution.effective
    state.fallback_reason = resolution.fallback_reason
    state.warnings_json = json.dumps(list(resolution.warnings), ensure_ascii=False)
    state.gpu_name = getattr(resolution, "gpu_name", None)
    state.compute_capability = getattr(resolution, "compute_capability", None)
    state.update_time = datetime.datetime.now()
    db.session.commit()
    return state


def get_latest_worker_capability():
    from applications.models.inference_job import InferenceWorkerState

    return InferenceWorkerState.query.order_by(InferenceWorkerState.update_time.desc()).first()


def serialize_worker_capability(state):
    if state is None:
        return {
            "worker_status": "unavailable",
            "requested_device": None,
            "effective_device": None,
            "fallback_reason": None,
            "warnings": ["推理 Worker 尚未报告运行时能力"],
            "gpu_name": None,
            "compute_capability": None,
            "updated_at": None,
        }
    warnings = _loads(state.warnings_json, [])
    worker_status = "ready"
    if state.update_time and (datetime.datetime.now() - state.update_time).total_seconds() > 90:
        worker_status = "stale"
        warnings.append("推理 Worker 心跳已超过 90 秒未更新")
    return {
        "worker_status": worker_status,
        "worker_id": state.worker_id,
        "requested_device": state.requested_device,
        "effective_device": state.effective_device,
        "fallback_reason": state.fallback_reason,
        "warnings": warnings,
        "gpu_name": state.gpu_name,
        "compute_capability": state.compute_capability,
        "updated_at": state.update_time.isoformat() if state.update_time else None,
    }


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def serialize_job(job):
    return {
        "id": job.id,
        "project_id": job.project_id,
        "status": job.status,
        "requested_device": job.requested_device,
        "effective_device": job.effective_device,
        "fallback_reason": job.fallback_reason,
        "warnings": _loads(job.warnings_json, []),
        "progress": {"current": job.progress_current or 0, "total": job.progress_total or 0},
        "request": _loads(job.request_payload_json, {}),
        "result": _loads(job.result_json, None),
        "error": None if not job.error_code and not job.error_message else {
            "code": job.error_code,
            "message": job.error_message,
        },
        "cancel_requested": bool(job.cancel_requested),
        "create_time": job.create_time.isoformat() if job.create_time else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def create_job(request_payload):
    db, InferenceJob = _database_dependencies()
    job = InferenceJob(
        id=str(uuid.uuid4()),
        project_id=request_payload.get("project_id"),
        status="queued",
        requested_device=request_payload.get("requested_device", "auto"),
        request_payload_json=json.dumps(request_payload, ensure_ascii=False),
    )
    db.session.add(job)
    db.session.commit()
    return job


def get_job(job_id):
    _, InferenceJob = _database_dependencies()
    return InferenceJob.query.filter_by(id=str(job_id)).first()


def request_job_cancel(job):
    db, _ = _database_dependencies()
    now = datetime.datetime.now()
    if job.status == "queued":
        job.status = "cancelled"
        job.cancel_requested = True
        job.finished_at = now
    elif job.status == "running":
        job.cancel_requested = True
    db.session.commit()
    return job


def claim_next_job(worker_id):
    db, InferenceJob = _database_dependencies()
    while True:
        candidate = InferenceJob.query.filter_by(status="queued").order_by(InferenceJob.create_time.asc()).first()
        if candidate is None:
            return None
        now = datetime.datetime.now()
        changed = InferenceJob.query.filter_by(id=candidate.id, status="queued").update(
            {
                "status": "running",
                "worker_id": str(worker_id),
                "started_at": now,
                "update_time": now,
            },
            synchronize_session=False,
        )
        db.session.commit()
        if changed:
            return InferenceJob.query.filter_by(id=candidate.id).first()


def recover_abandoned_jobs(worker_host, current_worker_id):
    """将同一容器中上一个 Worker 进程遗留的运行任务置为可诊断失败。"""
    db, InferenceJob = _database_dependencies()
    now = datetime.datetime.now()
    changed = InferenceJob.query.filter(
        InferenceJob.status == "running",
        InferenceJob.worker_id.like(f"{worker_host}:%"),
        InferenceJob.worker_id != str(current_worker_id),
    ).update(
        {
            "status": "failed",
            "error_code": "WORKER_RESTARTED",
            "error_message": "推理 Worker 进程已重启，原运行任务无法安全续跑",
            "finished_at": now,
            "update_time": now,
        },
        synchronize_session=False,
    )
    db.session.commit()
    return changed


def update_job_progress(job, current, total):
    db, _ = _database_dependencies()
    job.progress_current = max(0, int(current or 0))
    job.progress_total = max(0, int(total or 0))
    db.session.commit()


def refresh_job(job):
    db, _ = _database_dependencies()
    db.session.refresh(job)
    return job


def set_job_workdir(job, work_dir):
    db, _ = _database_dependencies()
    job.work_dir = str(Path(work_dir).resolve())
    db.session.commit()


def finish_job(
    job,
    *,
    status,
    result=None,
    effective_device=None,
    fallback_reason=None,
    warnings=(),
    error_code=None,
    error_message=None,
):
    from applications.inference.status import validate_job_transition

    db, _ = _database_dependencies()
    validate_job_transition(job.status, status)
    job.status = status
    job.result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
    job.effective_device = effective_device
    job.fallback_reason = fallback_reason
    job.warnings_json = json.dumps(list(warnings), ensure_ascii=False)
    job.error_code = error_code
    job.error_message = error_message
    job.finished_at = datetime.datetime.now()
    db.session.commit()
    return job
