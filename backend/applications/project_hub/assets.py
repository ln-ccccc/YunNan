import json
import math
from datetime import timezone
from pathlib import Path

from applications.models.classification_result import (
    ClassificationResult,
    ClassificationRevision,
)
from applications.models.project import (
    ProjectBackupRecord,
    ProjectDataset,
    ProjectExportRecord,
)
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_storage import (
    SUPPORTED_INCOMING_RASTER_FORMATS,
    get_storage_root,
    resolve_storage_path,
)

VALID_ASSET_TYPES = (
    "mine_boundary",
    "basemap",
    "imagery",
    "inference_result",
    "vector_revision",
    "report",
    "export",
    "backup_snapshot",
)
VALID_ASSET_STATUSES = (
    "registered",
    "processing",
    "ready",
    "failed",
    "superseded",
)
SPATIAL_STATUS_MAP = {
    "pending": "registered",
    "processing": "processing",
    "active": "ready",
    "failed": "failed",
    "retained": "superseded",
}

_ASSET_TYPE_ORDER = {asset_type: index for index, asset_type in enumerate(VALID_ASSET_TYPES)}
_DOWNLOADABLE_TYPES = {"export", "report"}
_PREVIEWABLE_TYPES = {
    "mine_boundary",
    "basemap",
    "imagery",
    "inference_result",
    "vector_revision",
    "report",
    "backup_snapshot",
}


class ProjectAssetFilterError(ValueError):
    pass


def _timestamp(value):
    if value is None:
        return None
    if hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _public_format(value, fallback="unknown"):
    text = str(value or "").strip().lower().lstrip(".")
    if not text or "/" in text or "\\" in text:
        return fallback
    return text


def _public_name(value, fallback):
    text = str(value or "").strip()
    if not text or "/" in text.replace("\\", "/"):
        return fallback
    return text


def _public_crs(value):
    text = str(value or "").strip()
    if not text or "/" in text.replace("\\", "/"):
        return None
    return text


def _spatial_metadata(resource):
    try:
        bbox = json.loads(resource.bounds_json or "null")
    except (TypeError, ValueError, json.JSONDecodeError):
        bbox = None
    if (
        not isinstance(bbox, list)
        or len(bbox) != 4
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(value)
            for value in bbox
        )
        or bbox[0] > bbox[2]
        or bbox[1] > bbox[3]
    ):
        bbox = None
    return {"crs": _public_crs(resource.crs), "bbox": bbox}


def _valid_year(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        year = value
    elif isinstance(value, str) and value.strip().isdigit():
        year = int(value.strip())
    else:
        return None
    return year if 1 <= year <= 9999 else None


def _temporal_metadata(year_start, year_end):
    start_year = _valid_year(year_start)
    end_year = _valid_year(year_end)
    if start_year is None and end_year is None:
        return None
    if start_year is None:
        start_year = end_year
    if end_year is None:
        end_year = start_year
    if start_year > end_year:
        return None
    return {
        "start": f"{start_year:04d}-01-01",
        "end": f"{end_year:04d}-12-31",
    }


def _capabilities(asset_type, asset_status):
    preview = asset_type in _PREVIEWABLE_TYPES and (
        asset_status == "ready"
        or (asset_type == "basemap" and asset_status == "superseded")
    )
    return {
        "preview": preview,
        "download": asset_status == "ready" and asset_type in _DOWNLOADABLE_TYPES,
        "activate": asset_status == "ready" and asset_type == "basemap",
        "retry": asset_status == "failed",
    }


def _safe_error_message(value, fallback):
    text = str(value or "").strip()
    normalized = text.replace("\\", "/")
    if not text or "/" in normalized or get_storage_root().as_posix() in normalized:
        return fallback
    return text


def _error(asset_status, code, message):
    if asset_status == "failed":
        return {
            "code": code or "ASSET_FAILED",
            "message": _safe_error_message(message, "资产处理失败"),
        }
    return {"code": None, "message": None}


def _asset(
    *,
    asset_id,
    source_type,
    source_id,
    asset_type,
    name,
    asset_format,
    asset_status,
    version,
    created_at,
    updated_at,
    spatial=None,
    temporal=None,
    provenance_source,
    parent_asset_ids=None,
    error_code=None,
    error_message=None,
    raw_status=None,
):
    provenance = {
        "source": provenance_source,
        "parent_asset_ids": parent_asset_ids or [],
    }
    if raw_status is not None:
        provenance["raw_status"] = raw_status
    return {
        "id": asset_id,
        "source_type": source_type,
        "source_id": int(source_id),
        "asset_type": asset_type,
        "name": name,
        "format": asset_format,
        "status": asset_status,
        "version": int(version or 1),
        "created_at": _timestamp(created_at),
        "updated_at": _timestamp(updated_at),
        "spatial": spatial,
        "temporal": temporal,
        "provenance": provenance,
        "error": _error(asset_status, error_code, error_message),
        "capabilities": _capabilities(asset_type, asset_status),
    }


def _relative_existing_path(storage_root, value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        candidate = resolve_storage_path(storage_root, raw_value)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _configured_result_id(dataset):
    try:
        payload = json.loads(dataset.slice_config_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("classification_result_id")
    if isinstance(value, bool):
        return None
    try:
        result_id = int(value)
    except (TypeError, ValueError):
        return None
    return result_id if result_id > 0 else None


def _dataset_matches_result(dataset, result_identities):
    if isinstance(dataset.mine_fid, bool):
        return False
    return (dataset.mine_fid, _valid_year(dataset.year_start)) in result_identities


def _dataset_asset(dataset, storage_root, result_ids, result_identities):
    dataset_kind = str(dataset.dataset_kind or "").strip().lower()
    source_format = _public_format(dataset.source_format)
    candidate = _relative_existing_path(storage_root, dataset.file_path)
    suffix = Path(str(dataset.file_path or "")).suffix.lower().lstrip(".")
    temporal = _temporal_metadata(dataset.year_start, dataset.year_end)

    if dataset_kind == "imagery":
        is_ready = (
            candidate is not None
            and suffix in SUPPORTED_INCOMING_RASTER_FORMATS
            and source_format in SUPPORTED_INCOMING_RASTER_FORMATS
        )
        asset_status = "ready" if is_ready else "registered"
        return _asset(
            asset_id=f"imagery:{dataset.id}",
            source_type="dataset",
            source_id=dataset.id,
            asset_type="imagery",
            name=_public_name(dataset.display_name, f"遥感影像 {dataset.id}"),
            asset_format=source_format,
            asset_status=asset_status,
            version=1,
            created_at=dataset.create_time,
            updated_at=dataset.update_time,
            temporal=temporal,
            provenance_source="dataset_registration",
        )

    if dataset_kind == "report":
        asset_status = "ready" if candidate is not None else "registered"
        return _asset(
            asset_id=f"report:{dataset.id}",
            source_type="project_report",
            source_id=dataset.id,
            asset_type="report",
            name=_public_name(dataset.display_name, f"项目报告 {dataset.id}"),
            asset_format=source_format,
            asset_status=asset_status,
            version=1,
            created_at=dataset.create_time,
            updated_at=dataset.update_time,
            temporal=temporal,
            provenance_source="dataset_registration",
        )

    if dataset_kind != "inference_result":
        return None
    if (
        _configured_result_id(dataset) in result_ids
        or _dataset_matches_result(dataset, result_identities)
    ):
        return None
    asset_status = "ready" if candidate is not None else "registered"
    return _asset(
        asset_id=f"inference_dataset:{dataset.id}",
        source_type="dataset_inference_result",
        source_id=dataset.id,
        asset_type="inference_result",
        name=_public_name(dataset.display_name, f"推理成果 {dataset.id}"),
        asset_format=source_format,
        asset_status=asset_status,
        version=1,
        created_at=dataset.create_time,
        updated_at=dataset.update_time,
        temporal=temporal,
        provenance_source="dataset_registration",
    )


def _record_status(status):
    raw_status = str(status or "").strip().lower()
    if raw_status == "completed":
        return "ready"
    if raw_status == "failed":
        return "failed"
    if raw_status in {"pending", "processing", "queued", "running"}:
        return "processing"
    return "registered"


def _validate_filter(value, valid_values, error_message):
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized not in valid_values:
        raise ProjectAssetFilterError(error_message)
    return normalized


def _result_parent_asset_ids(result, datasets):
    parent_asset_ids = []
    for dataset in datasets:
        if str(dataset.dataset_kind or "").strip().lower() != "imagery":
            continue
        start_year = _valid_year(dataset.year_start)
        end_year = _valid_year(dataset.year_end)
        if start_year is None and end_year is None:
            continue
        if start_year is None:
            start_year = end_year
        if end_year is None:
            end_year = start_year
        if dataset.mine_fid == result.mine_fid and start_year <= result.year <= end_year:
            parent_asset_ids.append(f"imagery:{dataset.id}")
    if result.mine_resource_id is not None:
        parent_asset_ids.append(f"mine_boundary:{result.mine_resource_id}")
    return parent_asset_ids


def list_project_assets(project, asset_type=None, status=None):
    asset_type = _validate_filter(asset_type, VALID_ASSET_TYPES, "资产类型不合法")
    status = _validate_filter(status, VALID_ASSET_STATUSES, "资产状态不合法")
    project_id = int(project.id)
    storage_root = get_storage_root()
    assets = []

    spatial_resources = ProjectSpatialResource.query.filter_by(project_id=project_id).all()
    for resource in spatial_resources:
        source_type = str(resource.resource_type or "").strip().lower()
        if source_type == "mine_vector":
            public_type = "mine_boundary"
        elif source_type == "basemap":
            public_type = "basemap"
        else:
            continue
        raw_status = str(resource.status or "").lower()
        public_status = SPATIAL_STATUS_MAP.get(raw_status, "registered")
        assets.append(
            _asset(
                asset_id=f"{public_type}:{resource.id}",
                source_type="spatial_resource",
                source_id=resource.id,
                asset_type=public_type,
                name="矿山边界" if public_type == "mine_boundary" else f"项目底图 {resource.id}",
                asset_format=_public_format(resource.source_format),
                asset_status=public_status,
                version=resource.version,
                created_at=resource.create_time,
                updated_at=resource.update_time,
                spatial=_spatial_metadata(resource),
                provenance_source="spatial_registration",
                raw_status=raw_status if raw_status in SPATIAL_STATUS_MAP else "pending",
                error_code="SPATIAL_RESOURCE_FAILED",
                error_message=resource.error_message,
            )
        )

    results = ClassificationResult.query.filter_by(project_id=project_id).all()
    result_identities = {(result.mine_fid, result.year) for result in results}
    result_ids = {result.id for result in results}
    datasets = ProjectDataset.query.filter_by(project_id=project_id).all()
    for dataset in datasets:
        asset = _dataset_asset(dataset, storage_root, result_ids, result_identities)
        if asset is not None:
            assets.append(asset)

    for result in results:
        vector_status = str(result.vector_status or "").lower()
        if vector_status in {"ready", "ready_empty"}:
            public_status = "ready"
        elif vector_status == "vector_failed":
            public_status = "failed"
        else:
            public_status = "processing"
        assets.append(
            _asset(
                asset_id=f"inference_result:{result.id}",
                source_type="classification_result",
                source_id=result.id,
                asset_type="inference_result",
                name=f"地物分类成果 {result.id}",
                asset_format="classification_dir",
                asset_status=public_status,
                version=result.current_revision_no or 1,
                created_at=result.create_time,
                updated_at=result.update_time,
                temporal=_temporal_metadata(result.year, result.year),
                provenance_source="model_inference",
                parent_asset_ids=_result_parent_asset_ids(result, datasets),
                error_code="CLASSIFICATION_RESULT_FAILED",
                error_message=result.vector_error,
            )
        )

    revisions = (
        ClassificationRevision.query.join(
            ClassificationResult,
            ClassificationRevision.result_id == ClassificationResult.id,
        )
        .filter(ClassificationResult.project_id == project_id)
        .all()
    )
    for revision in revisions:
        assets.append(
            _asset(
                asset_id=f"vector_revision:{revision.id}",
                source_type="classification_revision",
                source_id=revision.id,
                asset_type="vector_revision",
                name=f"分类矢量修订 {revision.revision_no}",
                asset_format="geojson",
                asset_status="ready",
                version=revision.revision_no,
                created_at=revision.create_time,
                updated_at=revision.create_time,
                provenance_source="classification_revision",
                parent_asset_ids=[f"inference_result:{revision.result_id}"],
            )
        )

    for export in ProjectExportRecord.query.filter_by(project_id=project_id).all():
        public_status = _record_status(export.status)
        assets.append(
            _asset(
                asset_id=f"export:{export.id}",
                source_type="project_export",
                source_id=export.id,
                asset_type="export",
                name=f"项目导出 {export.id}",
                asset_format=_public_format(export.format),
                asset_status=public_status,
                version=1,
                created_at=export.create_time,
                updated_at=export.update_time,
                provenance_source="project_export",
                error_code="EXPORT_FAILED",
                error_message="项目导出失败",
            )
        )

    for backup in ProjectBackupRecord.query.filter_by(project_id=project_id).all():
        public_status = _record_status(backup.status)
        assets.append(
            _asset(
                asset_id=f"backup_snapshot:{backup.id}",
                source_type="project_backup",
                source_id=backup.id,
                asset_type="backup_snapshot",
                name=f"项目配置快照 {backup.id}",
                asset_format="json",
                asset_status=public_status,
                version=1,
                created_at=backup.create_time,
                updated_at=backup.update_time,
                provenance_source="project_backup",
                error_code="BACKUP_SNAPSHOT_FAILED",
                error_message="项目配置快照失败",
            )
        )

    if asset_type is not None:
        assets = [asset for asset in assets if asset["asset_type"] == asset_type]
    if status is not None:
        assets = [asset for asset in assets if asset["status"] == status]
    return sorted(
        assets,
        key=lambda asset: (
            _ASSET_TYPE_ORDER[asset["asset_type"]],
            asset["source_id"],
            asset["id"],
        ),
    )


__all__ = [
    "ProjectAssetFilterError",
    "SPATIAL_STATUS_MAP",
    "VALID_ASSET_STATUSES",
    "VALID_ASSET_TYPES",
    "list_project_assets",
]
