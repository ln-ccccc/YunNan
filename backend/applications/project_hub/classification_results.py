import json
import logging
import math
import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from sqlalchemy.exc import IntegrityError

from applications.extensions import db
from applications.kml_roi.kml import load_vector_features
from applications.kml_roi.vectorization.classification import CLASS_DEFINITIONS, vectorize_label_geotiff
from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
    ClassificationRevision,
)
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path


LOGGER = logging.getLogger(__name__)
MAX_SAVE_BODY_BYTES = 10 * 1024 * 1024
MAX_FEATURES = 2_000
MAX_POSITIONS_PER_FEATURE = 5_000
MAX_POSITIONS_TOTAL = 100_000


class ClassificationValidationError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


class ClassificationConflictError(ValueError):
    def __init__(self, message, details):
        super().__init__(message)
        self.details = details


def _serialize_feature_collection(value):
    if not value:
        return None
    return json.loads(value)


def serialize_classification_result(result):
    return {
        "result_id": result.id,
        "project_id": result.project_id,
        "mine_fid": result.mine_fid,
        "year": result.year,
        "inference_job_id": result.inference_job_id,
        "model_id": result.model_id,
        "mine_resource_id": result.mine_resource_id,
        "mine_resource_version": result.mine_resource_version,
        "vector_status": result.vector_status,
        "vector_error": result.vector_error,
        "auto_feature_collection": _serialize_feature_collection(result.auto_feature_collection_json),
        "current_feature_collection": _serialize_feature_collection(result.current_feature_collection_json),
        "current_revision_no": result.current_revision_no,
    }


def _result_for_project(project_id, result_id):
    result = ClassificationResult.query.filter_by(id=int(result_id), project_id=int(project_id)).first()
    if result is None:
        raise ValueError("分类成果不存在")
    return result


def _class_table():
    return [
        {"class_code": class_code, **definition}
        for class_code, definition in sorted(CLASS_DEFINITIONS.items())
    ]


def get_classification_result(project_id, result_id):
    result = _result_for_project(project_id, result_id)
    from applications.project_hub.project_map import get_project_map_manifest

    payload = serialize_classification_result(result)
    payload["classes"] = _class_table()
    payload["map_manifest"] = get_project_map_manifest(int(project_id))
    return payload


def list_classification_revisions(project_id, result_id):
    result = _result_for_project(project_id, result_id)
    revisions = ClassificationRevision.query.filter_by(result_id=result.id).order_by(
        ClassificationRevision.revision_no.asc()
    ).all()
    return {
        "result_id": result.id,
        "current_revision_no": result.current_revision_no,
        "revisions": [
            {
                "revision_no": item.revision_no,
                "source": item.source,
                "author": item.author,
                "created_at": item.create_time.isoformat() if item.create_time else None,
                "feature_count": item.feature_count,
            }
            for item in revisions
        ],
    }


def _load_roi_geometry(project_id, mine_resource_id, mine_fid):
    project = Project.query.filter_by(id=project_id, deleted_at=None).first()
    if project is None:
        raise ValueError("项目不存在")
    resource = ProjectSpatialResource.query.filter_by(
        id=mine_resource_id,
        project_id=project_id,
        resource_type="mine_vector",
    ).first()
    if resource is None or not resource.normalized_path:
        raise ValueError("推理使用的矿山资源不可用")
    vector_path = resolve_storage_path(get_storage_root(), resource.normalized_path)
    if not vector_path.is_file():
        raise ValueError("推理使用的矿山资源文件不存在")
    for raw_fid, geometry in load_vector_features(vector_path):
        try:
            if int(raw_fid) == mine_fid:
                return resource, geometry
        except (TypeError, ValueError):
            continue
    raise ValueError("推理使用的矿山资源不含该矿山")


def _snapshot_paths(project_id, result_id):
    relative_dir = Path("projects") / str(project_id) / "outputs" / "classification-results" / str(result_id)
    return (
        relative_dir,
        relative_dir / "label.tif",
        relative_dir / "auto.geojson",
    )


def _write_json_temporary(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{uuid.uuid4().hex}.geojson.tmp"
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return temporary


def _write_json_atomic(path, payload):
    temporary = _write_json_temporary(path, payload)
    try:
        os.replace(temporary, path)
    except Exception:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise


def _mark_vector_failed(result, *, error_code, snapshot_dir=None):
    if snapshot_dir is not None:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
    result.label_path = None
    result.auto_geojson_path = None
    result.auto_feature_collection_json = None
    result.current_feature_collection_json = None
    result.current_revision_no = None
    result.vector_status = "vector_failed"
    result.vector_error = error_code
    db.session.commit()
    return serialize_classification_result(result)


def _validation_error(field, reason, feature_index=None):
    details = {"field": field, "reason": reason}
    if feature_index is not None:
        details["feature_index"] = feature_index
    raise ClassificationValidationError("分类成果保存数据不合法", details)


def _validate_position(position, *, field, feature_index):
    if not isinstance(position, list) or len(position) != 2:
        _validation_error(field, "坐标位置必须恰有二维 [longitude, latitude]", feature_index)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in position):
        _validation_error(field, "坐标位置必须是数值", feature_index)
    longitude, latitude = position
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        _validation_error(field, "坐标位置必须是有限数值", feature_index)
    if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
        _validation_error(field, "坐标超出 EPSG:4326 范围", feature_index)
    return 1


def _validate_ring(ring, *, field, feature_index):
    if not isinstance(ring, list) or len(ring) < 4:
        _validation_error(field, "面环至少需要四个闭合坐标位置", feature_index)
    count = sum(
        _validate_position(position, field=field, feature_index=feature_index)
        for position in ring
    )
    if ring[0] != ring[-1]:
        _validation_error(field, "面环必须显式闭合", feature_index)
    return count


def _validate_geometry(geometry, *, feature_index):
    if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates"}:
        _validation_error("geometry", "几何只能包含 type 和 coordinates", feature_index)
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            _validation_error("geometry.coordinates", "Polygon 必须包含至少一个面环", feature_index)
        position_count = sum(
            _validate_ring(ring, field="geometry.coordinates", feature_index=feature_index)
            for ring in coordinates
        )
    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            _validation_error("geometry.coordinates", "MultiPolygon 必须包含至少一个面", feature_index)
        position_count = 0
        for polygon in coordinates:
            if not isinstance(polygon, list) or not polygon:
                _validation_error("geometry.coordinates", "MultiPolygon 中的面不能为空", feature_index)
            position_count += sum(
                _validate_ring(ring, field="geometry.coordinates", feature_index=feature_index)
                for ring in polygon
            )
    else:
        _validation_error("geometry.type", "仅支持 Polygon 或 MultiPolygon", feature_index)

    try:
        parsed = shape(geometry)
    except Exception:
        _validation_error("geometry", "几何无法解析", feature_index)
    if parsed.is_empty or not parsed.is_valid or not isinstance(parsed, (Polygon, MultiPolygon)):
        _validation_error("geometry", "几何必须是非空有效面", feature_index)
    return parsed, position_count


def _base_feature_ids(result, base_revision_no):
    revision = ClassificationRevision.query.filter_by(
        result_id=result.id,
        revision_no=base_revision_no,
    ).first()
    if revision is None:
        raise ClassificationConflictError(
            "基准版本不存在",
            {
                "base_revision_no": base_revision_no,
                "current_revision_no": result.current_revision_no,
            },
        )
    payload = json.loads(revision.feature_collection_json)
    return {
        feature.get("properties", {}).get("feature_id")
        for feature in payload.get("features") or []
        if feature.get("properties", {}).get("feature_id")
    }


def _all_historic_feature_ids(result_id):
    identifiers = set()
    revisions = ClassificationRevision.query.filter_by(result_id=result_id).all()
    for revision in revisions:
        try:
            payload = json.loads(revision.feature_collection_json)
        except (TypeError, ValueError):
            continue
        for feature in payload.get("features") or []:
            feature_id = (feature.get("properties") or {}).get("feature_id")
            if feature_id:
                identifiers.add(feature_id)
    return identifiers


def _new_manual_feature_id(result_id, historic_ids):
    while True:
        candidate = f"manual-{result_id}-{uuid.uuid4().hex}"
        if candidate not in historic_ids:
            return candidate


def _validate_save_payload(result, payload, *, body_size):
    if body_size is not None and body_size > MAX_SAVE_BODY_BYTES:
        _validation_error("body", "请求 JSON 超过 10 MiB")
    if not isinstance(payload, dict) or set(payload) != {"base_revision_no", "feature_collection"}:
        _validation_error("body", "保存请求只能包含 base_revision_no 和 feature_collection")
    base_revision_no = payload.get("base_revision_no")
    if isinstance(base_revision_no, bool) or not isinstance(base_revision_no, int):
        _validation_error("base_revision_no", "必须是非负安全整数")
    if base_revision_no < 0 or base_revision_no > (2 ** 53 - 1):
        _validation_error("base_revision_no", "必须是非负安全整数")
    feature_collection = payload.get("feature_collection")
    if not isinstance(feature_collection, dict) or set(feature_collection) != {"type", "features"}:
        _validation_error("feature_collection", "必须是受限 FeatureCollection")
    if feature_collection.get("type") != "FeatureCollection" or not isinstance(feature_collection.get("features"), list):
        _validation_error("feature_collection", "必须是 FeatureCollection")
    features = feature_collection["features"]
    if len(features) > MAX_FEATURES:
        _validation_error("feature_collection.features", "要素数量不能超过 2000")
    if result.current_revision_no != base_revision_no:
        raise ClassificationConflictError(
            "成果已被其他保存更新",
            {
                "base_revision_no": base_revision_no,
                "current_revision_no": result.current_revision_no,
            },
        )
    base_ids = _base_feature_ids(result, base_revision_no)
    historic_ids = _all_historic_feature_ids(result.id)
    roi = shape(json.loads(result.roi_geometry_json))
    normalized_features = []
    seen_ids = set()
    submitted_ids = set()
    total_positions = 0
    target_revision_no = base_revision_no + 1
    for feature_index, feature in enumerate(features):
        if not isinstance(feature, dict) or set(feature) != {"type", "properties", "geometry"}:
            _validation_error("feature", "Feature 只能包含 type、properties 和 geometry", feature_index)
        if feature.get("type") != "Feature":
            _validation_error("feature.type", "必须为 Feature", feature_index)
        properties = feature.get("properties")
        if not isinstance(properties, dict) or not set(properties).issubset({"class_code", "feature_id"}):
            _validation_error("feature.properties", "仅允许 class_code 和 feature_id", feature_index)
        class_code = properties.get("class_code")
        if isinstance(class_code, bool) or not isinstance(class_code, int) or class_code not in CLASS_DEFINITIONS:
            _validation_error("feature.properties.class_code", "必须是 0 到 5 的整数", feature_index)
        submitted_feature_id = properties.get("feature_id")
        if submitted_feature_id is not None:
            if not isinstance(submitted_feature_id, str):
                _validation_error("feature.properties.feature_id", "必须是字符串", feature_index)
            if submitted_feature_id:
                if submitted_feature_id in submitted_ids:
                    _validation_error("feature.properties.feature_id", "请求内 feature_id 必须唯一", feature_index)
                submitted_ids.add(submitted_feature_id)
        parsed_geometry, position_count = _validate_geometry(feature.get("geometry"), feature_index=feature_index)
        if position_count > MAX_POSITIONS_PER_FEATURE:
            _validation_error("geometry.coordinates", "单要素坐标位置不能超过 5000", feature_index)
        total_positions += position_count
        if total_positions > MAX_POSITIONS_TOTAL:
            _validation_error("feature_collection", "总坐标位置不能超过 100000", feature_index)
        if not roi.covers(parsed_geometry):
            _validation_error("geometry", "几何必须完全位于成果 ROI 内", feature_index)
        feature_id = submitted_feature_id
        if feature_id is None:
            feature_id = _new_manual_feature_id(result.id, historic_ids | seen_ids)
        elif not feature_id:
            _validation_error(
                "feature.properties.feature_id",
                "新要素必须省略 feature_id 或使用 client- 前缀 ID",
                feature_index,
            )
        elif feature_id not in base_ids:
            if not feature_id.startswith("client-"):
                _validation_error("feature.properties.feature_id", "既有 ID 不属于基准版本", feature_index)
            feature_id = _new_manual_feature_id(result.id, historic_ids | seen_ids)
        if feature_id in seen_ids:
            _validation_error("feature.properties.feature_id", "请求内 feature_id 必须唯一", feature_index)
        seen_ids.add(feature_id)
        definition = CLASS_DEFINITIONS[class_code]
        normalized_features.append(
            {
                "type": "Feature",
                "properties": {
                    "feature_id": feature_id,
                    "class_code": class_code,
                    "class_name": definition["class_name"],
                    "source": "manual",
                    "result_id": result.id,
                    "revision_no": target_revision_no,
                },
                "geometry": mapping(parsed_geometry),
            }
        )
    return base_revision_no, {"type": "FeatureCollection", "features": normalized_features}


def _claim_current_revision(result, base_revision_no, revision_no, feature_collection_json):
    return ClassificationResult.query.filter(
        ClassificationResult.id == result.id,
        ClassificationResult.project_id == result.project_id,
        ClassificationResult.current_revision_no == base_revision_no,
    ).update(
        {
            "current_feature_collection_json": feature_collection_json,
            "current_revision_no": revision_no,
        },
        synchronize_session=False,
    )


def _save_conflict(result, base_revision_no):
    db.session.expire_all()
    current_revision_no = db.session.query(ClassificationResult.current_revision_no).filter_by(
        id=result.id,
        project_id=result.project_id,
    ).scalar()
    return ClassificationConflictError(
        "成果已被其他保存更新",
        {
            "base_revision_no": base_revision_no,
            "current_revision_no": current_revision_no,
        },
    )


def save_classification_revision(project_id, result_id, payload, *, actor, body_size=None):
    result = _result_for_project(project_id, result_id)
    if result.vector_status == "vector_failed":
        raise ClassificationConflictError(
            "分类成果自动矢量化失败，不能保存",
            {
                "error": "vector_failed",
                "vector_status": "vector_failed",
                "save_allowed": False,
                "reason": result.vector_error or "vectorization_failed",
            },
        )
    base_revision_no, feature_collection = _validate_save_payload(result, payload, body_size=body_size)
    revision_no = base_revision_no + 1
    snapshot_relative_path = (
        Path("projects")
        / str(result.project_id)
        / "outputs"
        / "classification-results"
        / str(result.id)
        / "revisions"
        / f"{revision_no}.geojson"
    )
    snapshot_path = resolve_storage_path(get_storage_root(), snapshot_relative_path)
    feature_collection_json = json.dumps(feature_collection, ensure_ascii=False)
    temporary_snapshot_path = _write_json_temporary(snapshot_path, feature_collection)
    try:
        if _claim_current_revision(
            result,
            base_revision_no,
            revision_no,
            feature_collection_json,
        ) != 1:
            db.session.rollback()
            raise _save_conflict(result, base_revision_no)
        db.session.add(
            ClassificationRevision(
                result_id=result.id,
                revision_no=revision_no,
                source="manual",
                author=str(actor),
                feature_count=len(feature_collection["features"]),
                snapshot_path=snapshot_relative_path.as_posix(),
                feature_collection_json=feature_collection_json,
            )
        )
        db.session.add(
            ClassificationEditAudit(
                result_id=result.id,
                base_revision_no=base_revision_no,
                revision_no=revision_no,
                actor=str(actor),
                action="manual_save",
                request_source="geoview",
                details_json=json.dumps({"feature_count": len(feature_collection["features"])}),
            )
        )
        db.session.flush()
        os.replace(temporary_snapshot_path, snapshot_path)
        db.session.commit()
    except ClassificationConflictError:
        db.session.rollback()
        with suppress(OSError):
            temporary_snapshot_path.unlink(missing_ok=True)
        raise
    except IntegrityError as error:
        db.session.rollback()
        with suppress(OSError):
            temporary_snapshot_path.unlink(missing_ok=True)
        raise _save_conflict(result, base_revision_no) from error
    except Exception:
        db.session.rollback()
        with suppress(OSError):
            temporary_snapshot_path.unlink(missing_ok=True)
        raise
    return {
        "result_id": result.id,
        "revision_no": revision_no,
        "current_revision_no": revision_no,
        "feature_collection": feature_collection,
    }


def export_current_classification_result(project_id, result_id):
    result = _result_for_project(project_id, result_id)
    if result.vector_status == "vector_failed" or not result.current_feature_collection_json:
        raise ClassificationConflictError(
            "分类成果自动矢量化失败，不能导出",
            {
                "error": "vector_failed",
                "vector_status": "vector_failed",
                "save_allowed": False,
                "reason": result.vector_error or "vectorization_failed",
            },
        )
    return result, json.loads(result.current_feature_collection_json)


def publish_classification_result(
    *,
    project_id,
    fid,
    year,
    inference_job_id,
    mine_resource_id,
    model_id="cc-ln/CUGRS",
    label_path,
):
    """Persist one immutable auto-vector snapshot without changing PNG inference success."""
    project_id = int(project_id)
    fid = int(fid)
    year = int(year)
    mine_resource_id = int(mine_resource_id)
    job_id = str(inference_job_id or "").strip()
    model_id = str(model_id or "").strip()
    if not job_id:
        raise ValueError("缺少推理任务标识")
    if not model_id:
        raise ValueError("缺少模型标识")

    existing = ClassificationResult.query.filter_by(
        project_id=project_id,
        mine_fid=fid,
        year=year,
        inference_job_id=job_id,
    ).first()
    if existing is not None:
        return serialize_classification_result(existing)

    result = ClassificationResult(
        project_id=project_id,
        mine_fid=fid,
        year=year,
        inference_job_id=job_id,
        model_id=model_id,
        mine_resource_id=mine_resource_id,
        vector_status="vector_failed",
    )
    db.session.add(result)
    db.session.flush()
    snapshot_relative_dir, label_relative_path, auto_relative_path = _snapshot_paths(project_id, result.id)
    storage_root = get_storage_root()
    snapshot_dir = resolve_storage_path(storage_root, snapshot_relative_dir)

    try:
        mine_resource, roi_geometry = _load_roi_geometry(project_id, mine_resource_id, fid)
    except Exception:
        LOGGER.warning("分类成果缺少可复核 ROI: project_id=%s fid=%s", project_id, fid, exc_info=True)
        return _mark_vector_failed(result, error_code="roi_unavailable")

    result.mine_resource_version = mine_resource.version
    result.roi_geometry_json = json.dumps(roi_geometry, ensure_ascii=False)
    source_label = Path(label_path).expanduser().resolve()
    if not source_label.is_file():
        return _mark_vector_failed(result, error_code="label_missing")

    try:
        feature_collection = vectorize_label_geotiff(source_label, roi_geometry, result_id=result.id)
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        label_target = resolve_storage_path(storage_root, label_relative_path)
        auto_target = resolve_storage_path(storage_root, auto_relative_path)
        shutil.copy2(source_label, label_target)
        _write_json_atomic(auto_target, feature_collection)
    except Exception:
        LOGGER.warning(
            "分类成果自动矢量化失败: project_id=%s fid=%s result_id=%s",
            project_id,
            fid,
            result.id,
            exc_info=True,
        )
        return _mark_vector_failed(
            result,
            error_code="vectorization_failed",
            snapshot_dir=snapshot_dir,
        )

    feature_collection_json = json.dumps(feature_collection, ensure_ascii=False)
    result.label_path = label_relative_path.as_posix()
    result.auto_geojson_path = auto_relative_path.as_posix()
    result.auto_feature_collection_json = feature_collection_json
    result.current_feature_collection_json = feature_collection_json
    result.current_revision_no = 0
    result.vector_status = "ready" if feature_collection["features"] else "ready_empty"
    result.vector_error = None
    db.session.add(
        ClassificationRevision(
            result_id=result.id,
            revision_no=0,
            source="auto",
            author="system",
            feature_count=len(feature_collection["features"]),
            snapshot_path=auto_relative_path.as_posix(),
            feature_collection_json=feature_collection_json,
        )
    )
    db.session.add(
        ClassificationEditAudit(
            result_id=result.id,
            base_revision_no=None,
            revision_no=0,
            actor="system",
            action="baseline_created",
            request_source="system_publish",
            details_json=json.dumps({"feature_count": len(feature_collection["features"])}),
        )
    )
    db.session.commit()
    return serialize_classification_result(result)
