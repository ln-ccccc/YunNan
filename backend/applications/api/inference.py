from pathlib import Path

from flask import Blueprint, current_app, request

from applications.auth.guard import ensure_logged_in
from applications.common.utils.http import fail_api, success_api
from applications.inference.jobs import (
    create_job,
    get_latest_worker_capability,
    get_job,
    normalize_job_request,
    request_job_cancel,
    serialize_job,
    serialize_worker_capability,
)
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


inference_api = Blueprint("inference_api", __name__, url_prefix="/api/inference")
repo_root = Path(__file__).resolve().parents[3]
default_kml_path = repo_root / "miner" / "yunnan.kml"
default_output_root = repo_root / "miner" / "change_matrix_outputs"


@inference_api.before_request
def require_inference_auth():
    return ensure_logged_in()


def _configured_roots(config_name, defaults):
    configured = current_app.config.get(config_name)
    if not configured:
        return [Path(item).resolve() for item in defaults]
    return [Path(item.strip()).expanduser().resolve() for item in str(configured).split(",") if item.strip()]


@inference_api.post("/jobs")
def create_inference_job_api():
    payload = dict(request.get_json(silent=True) or {})
    try:
        if payload.get("project_id") in (None, ""):
            raise ValueError("解译任务必须提交 project_id")
        project_id = int(payload.get("project_id"))
        project = Project.query.filter_by(id=project_id, deleted_at=None).first()
        if project is None:
            raise ValueError("项目不存在")
        mine_resource = (
            ProjectSpatialResource.query.filter_by(
                project_id=project_id,
                resource_type="mine_vector",
                status="active",
            )
            .order_by(ProjectSpatialResource.version.desc())
            .first()
        )
        if mine_resource is None or not mine_resource.normalized_path:
            raise ValueError("项目尚未激活矿山资源")
        storage_root = get_storage_root()
        project_root = resolve_storage_path(storage_root, f"projects/{project_id}")
        payload["project_id"] = project_id
        payload["mine_resource_id"] = mine_resource.id
        payload["kml_path"] = str(resolve_storage_path(storage_root, mine_resource.normalized_path))
        payload["output_root"] = str(project_root / "outputs" / "inference")
        requested_fids = payload.get("mine_fids") or ([] if payload.get("fid") in (None, "") else [payload.get("fid")])
        bound_fids = {row.mine_fid for row in project.mines}
        if any(int(fid) not in bound_fids for fid in requested_fids):
            raise ValueError("输入矿山不属于当前项目")
        project_imagery = [
            Path(dataset.file_path).expanduser().resolve()
            for dataset in project.datasets
            if dataset.dataset_kind == "imagery" and Path(dataset.file_path).expanduser().is_file()
        ]
        normalized = normalize_job_request(
            payload,
            allowed_roots=[project_root, *project_imagery],
            allowed_output_roots=[project_root / "outputs"],
        )
        job = create_job(normalized)
    except (ValueError, FileNotFoundError) as error:
        return fail_api(str(error)), 400
    return success_api(data=serialize_job(job)), 201


@inference_api.get("/jobs/<job_id>")
def get_inference_job_api(job_id):
    job = get_job(job_id)
    if job is None:
        return fail_api("推理任务不存在"), 404
    return success_api(data=serialize_job(job))


@inference_api.post("/jobs/<job_id>/cancel")
def cancel_inference_job_api(job_id):
    job = get_job(job_id)
    if job is None:
        return fail_api("推理任务不存在"), 404
    return success_api(data=serialize_job(request_job_cancel(job)))


@inference_api.get("/capabilities")
def inference_capabilities_api():
    return success_api(data=serialize_worker_capability(get_latest_worker_capability()))
