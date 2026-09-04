import re

from flask import Blueprint, request

from applications.auth.guard import ensure_logged_in
from applications.common.utils.http import fail_api, success_api
from applications.inference.routing import resolve_interpretation_scope
from applications.inference.jobs import (
    create_job,
    get_latest_worker_capability,
    get_job,
    normalize_job_request,
    request_job_cancel,
    serialize_job,
    serialize_worker_capability,
)
from applications.project_hub.inference_inputs import (
    ProjectInferenceInputError,
    resolve_project_inference_inputs,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


inference_api = Blueprint("inference_api", __name__, url_prefix="/api/inference")
_CREATE_JOB_FIELDS = {"project_id", "dataset_id", "year", "mine_fids", "device"}
_URL_VALUE = re.compile(r"(?i)\b(?:https?|ftp)://[^\s]+")
_ABSOLUTE_PATH_VALUE = re.compile(r"(?<![A-Za-z0-9+.\-])(?:[A-Za-z]:[\\/]|[\\/]{1,2})")


class InferenceRequestValidationError(ValueError):
    """Raised for an invalid browser-facing inference job request shape."""


@inference_api.before_request
def require_inference_auth():
    return ensure_logged_in()


def _request_positive_integer(payload, field_name):
    value = payload.get(field_name)
    if isinstance(value, bool):
        raise InferenceRequestValidationError(f"{field_name} 必须是正整数")
    if isinstance(value, int):
        result = value
    elif isinstance(value, str) and value.strip().isdigit():
        result = int(value.strip())
    else:
        raise InferenceRequestValidationError(f"{field_name} 必须是正整数")
    if result <= 0:
        raise InferenceRequestValidationError(f"{field_name} 必须是正整数")
    return result


def _parse_create_job_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise InferenceRequestValidationError("推理任务请求必须是 JSON 对象")
    unsupported_fields = sorted(set(payload).difference(_CREATE_JOB_FIELDS))
    if unsupported_fields:
        raise InferenceRequestValidationError(
            f"推理任务请求包含不支持字段: {', '.join(unsupported_fields)}"
        )
    result = {
        "project_id": _request_positive_integer(payload, "project_id"),
        "dataset_id": _request_positive_integer(payload, "dataset_id"),
    }
    if "year" in payload:
        year = payload["year"]
        if isinstance(year, bool) or not isinstance(year, (str, int)):
            raise InferenceRequestValidationError("year 必须是字符串或整数")
        result["year"] = year
    if "mine_fids" in payload:
        if not isinstance(payload["mine_fids"], list):
            raise InferenceRequestValidationError("mine_fids 必须是数组")
        result["mine_fids"] = payload["mine_fids"]
    if "device" in payload:
        if not isinstance(payload["device"], str):
            raise InferenceRequestValidationError("device 必须是字符串")
        result["device"] = payload["device"]
    return result


def _safe_create_error_message(error):
    message = str(error)
    if "file:" in message.casefold() or _ABSOLUTE_PATH_VALUE.search(_URL_VALUE.sub("", message)):
        return "无法创建推理任务，请检查项目影像和矿山资源状态"
    return message


@inference_api.post("/jobs")
def create_inference_job_api():
    try:
        payload = _parse_create_job_payload()
        private_inputs = resolve_project_inference_inputs(
            **payload,
            scope_resolver=resolve_interpretation_scope,
        )
        storage_root = get_storage_root()
        project_id = private_inputs["project_id"]
        project_root = resolve_storage_path(storage_root, f"projects/{project_id}")
        normalized = normalize_job_request(
            private_inputs,
            allowed_roots=[storage_root],
            allowed_output_roots=[project_root / "outputs"],
        )
        job = create_job(normalized)
    except InferenceRequestValidationError as error:
        return fail_api(str(error)), 422
    except (ProjectInferenceInputError, ValueError, FileNotFoundError) as error:
        return fail_api(_safe_create_error_message(error)), 400
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
