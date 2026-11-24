import json
import logging
import os
import shutil
import uuid
from contextlib import suppress
from pathlib import Path

from shapely.geometry import mapping, shape
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
from applications.project_hub.classification_stats import (
    _effective_result_refs_by_year,
    _revision_stats_path,
    request_change_matrix_rebuild_for_result,
    write_revision_stats,
)
from applications.project_hub.geometry_engine import (
    INVALID_GEOMETRY,
    OUTSIDE_ROI,
    REJECTED,
    SIMPLIFY_TOLERANCE_DEGREES,
    GeometryProblem,
    RoiContext,
    RoiContextCache,
    evaluate,
    evaluate_parsed,
    parse_geometry,
    simplify as simplify_geometry,
    to_geojson,
    union as union_geometries,
)
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path
from applications.common.utils.utc_time import to_utc_z


LOGGER = logging.getLogger(__name__)
MAX_SAVE_BODY_BYTES = 10 * 1024 * 1024
MAX_FEATURES = 2_000
MAX_POSITIONS_PER_FEATURE = 5_000
MAX_POSITIONS_TOTAL = 100_000
# 几何接口（编辑期校验 / 简化 / 合并）的请求上限：只处理用户这次操作改动的面
MAX_UNION_GEOMETRIES = 50
MAX_GEOMETRY_REQUEST_POSITIONS = 200_000


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
    # 编辑器画矿山边界轮廓、做视野定位需要 ROI（2026-11-13 风险复盘）。越界与否不在这里判：
    # 编辑期校验走 geometry/validate，与保存共用 geometry_engine.evaluate
    payload["roi_geometry"] = _serialize_feature_collection(result.roi_geometry_json)
    # 该成果是否为其矿山当年的有效成果（2026-11-24）。概览、台账、变化矩阵、溯源
    # 一律只认有效成果；编辑一条已被取代的成果，修订只留在它自己的版本记录里。
    # 生产 3036 条可编辑成果里 2387 条属于这种情况，此前编辑器与入口列表都不提示。
    # 只读提示字段，不改变可保存性（契约允许任意 ready / ready_empty 成果保存）
    payload["effective"] = is_effective_result(result)
    return payload


def is_effective_result(result):
    """成果是否为其矿山当年的有效成果，规则即全系统唯一的
    classification_stats.select_effective_result_refs（同年取 id 最大者，
    vector_failed 在同年有非失败兄弟时让位）。vector_failed 恒为 False。"""
    if result.vector_status == "vector_failed":
        return False
    refs = _effective_result_refs_by_year(result.project_id, result.mine_fid)
    return (refs.get(result.year) or (None,))[0] == result.id


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
                "created_at": to_utc_z(item.create_time),
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
    # payload 是待序列化的对象。传已经序列化好的 JSON 文本会被再编码一层：文件顶层变成
    # 一个字符串而不是 GeoJSON 对象——库里是对的、运行时也没有代码读这些快照，功能不受影响，
    # 但文件本身打不开。2026-11-24 的 48e3c64 把保存路径错传成了 feature_collection_json，
    # 当天真实编辑测试后的存储核对才发现；这里明确拒绝，不让同类错误再悄悄通过
    if isinstance(payload, str):
        raise TypeError("写入 JSON 文件的必须是待序列化的对象，不能是已经序列化好的文本")
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
    result.feature_count = 0
    result.vector_status = "vector_failed"
    result.vector_error = error_code
    db.session.commit()
    return serialize_classification_result(result)


def _validation_error(field, reason, feature_index=None, message="分类成果保存数据不合法"):
    details = {"field": field, "reason": reason}
    if feature_index is not None:
        details["feature_index"] = feature_index
    raise ClassificationValidationError(message, details)


def roi_context(result):
    """成果矿山边界的加速上下文，按成果缓存（ROI 在矢量化时冻结，之后不变）。

    保存校验与编辑期的校验 / 简化 / 合并接口拿的是同一个上下文、同一个判定函数。
    """
    roi_json = result.roi_geometry_json
    if not roi_json:
        raise ValueError("分类成果没有矿山边界几何")
    return _ROI_CONTEXTS.get(
        (result.id, len(roi_json), hash(roi_json)),
        lambda: RoiContext(shape(json.loads(roi_json))),
    )


def _base_revision_payload(result, base_revision_no):
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
    return json.loads(revision.feature_collection_json)


def _feature_ids(payload):
    return {
        feature.get("properties", {}).get("feature_id")
        for feature in payload.get("features") or []
        if feature.get("properties", {}).get("feature_id")
    }


def _position_count(geometry):
    """一个面几何的坐标位置总数，口径同 _validate_geometry（闭合环末端位置计入）。"""
    geometry = geometry or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "MultiPolygon":
        return sum(len(ring) for rings in coordinates for ring in rings)
    return sum(len(ring) for ring in coordinates)


class _BaseCapacity:
    """基准修订的容量快照（要素数、各既有要素的坐标数、总坐标数）。

    容量上限约束的是「增长」，不是「继承」（2026-11-24）：自动矢量化按连通分量
    出要素，一个带几十个洞的大面可以有五六千个坐标，发布时又不经过保存校验。
    生产 3036 条成果里有 10 条（其中 3 条是有效成果）的单要素坐标数超过 5000，
    此前这些成果连原样保存都被 422 拒绝——与状态矩阵「ready 即可保存」矛盾。
    现在超过上限的内容只要不比基准修订里的同一个要素更大就放行；新要素、
    以及既有要素变大，仍一律受限。数值只在超限后才计算（惰性）。
    """

    def __init__(self, payload):
        payload = payload or {}
        self.feature_count = len(payload.get("features") or [])
        self._payload = payload
        self._positions_by_id = None

    def _ensure_positions(self):
        if self._positions_by_id is not None:
            return
        positions = {}
        for feature in self._payload.get("features") or []:
            feature_id = (feature.get("properties") or {}).get("feature_id")
            positions[feature_id] = positions.get(feature_id, 0) + _position_count(feature.get("geometry"))
        self._positions_by_id = positions

    def positions_of(self, feature_id):
        self._ensure_positions()
        return self._positions_by_id.get(feature_id, 0)

    @property
    def total_positions(self):
        self._ensure_positions()
        return sum(self._positions_by_id.values())


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
    base_payload = _base_revision_payload(result, base_revision_no)
    base_ids = _feature_ids(base_payload)
    base_capacity = _BaseCapacity(base_payload)
    # 三个容量上限都只约束增长：超过上限时，不比基准修订更大才放行（见 _BaseCapacity）
    if len(features) > MAX_FEATURES and len(features) > base_capacity.feature_count:
        _validation_error("feature_collection.features", "要素数量不能超过 2000")
    if result.current_revision_no != base_revision_no:
        raise ClassificationConflictError(
            "成果已被其他保存更新",
            {
                "base_revision_no": base_revision_no,
                "current_revision_no": result.current_revision_no,
            },
        )
    historic_ids = _all_historic_feature_ids(result.id)
    context = roi_context(result)
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
        try:
            parsed_geometry, position_count = parse_geometry(feature.get("geometry"))
        except GeometryProblem as problem:
            _validation_error(problem.field, problem.reason, feature_index)
        # 单要素上限：既有要素（ID 在基准修订里）只要坐标数没比基准里多就放行
        if position_count > MAX_POSITIONS_PER_FEATURE and not (
            submitted_feature_id in base_ids
            and position_count <= base_capacity.positions_of(submitted_feature_id)
        ):
            _validation_error("geometry.coordinates", "单要素坐标位置不能超过 5000", feature_index)
        total_positions += position_count
        # 总量上限同理：整集合不超过基准修订的总量即可（基准总量只在超限后才统计）
        if total_positions > MAX_POSITIONS_TOTAL and total_positions > base_capacity.total_positions:
            _validation_error("feature_collection", "总坐标位置不能超过 100000", feature_index)
        # 合法性只有 geometry_engine.evaluate 这一个判定：结构与有效性、是否完整位于 ROI。
        # ROI 容差（1e-9 度）只吸收坐标舍入与投影换算误差，规则与改造前逐字相同
        if evaluate_parsed(context, parsed_geometry).code == OUTSIDE_ROI:
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


# 编辑期几何接口：校验、简化、合并。全部只读——不写库、不写文件、不产生修订。
# 它们与保存共用 geometry_engine 的同一个判定函数，所以「编辑时提示会被拒绝」与
# 「保存时确实被拒绝」是同一件事，前端不再自带一套几何判定
_ROI_CONTEXTS = RoiContextCache()


def _geometry_request(result, payload, body_size, *, keys, minimum, maximum):
    message = "几何请求不合法"
    if body_size is not None and body_size > MAX_SAVE_BODY_BYTES:
        _validation_error("body", "请求 JSON 超过 10 MiB", message=message)
    if not isinstance(payload, dict) or "geometries" not in payload or not set(payload).issubset(keys):
        _validation_error("body", "请求体只能包含 " + " 和 ".join(sorted(keys)), message=message)
    geometries = payload["geometries"]
    if not isinstance(geometries, list) or not minimum <= len(geometries) <= maximum:
        _validation_error("geometries", f"geometries 必须是 {minimum} 到 {maximum} 个几何的数组", message=message)
    positions = 0
    for index, geometry in enumerate(geometries):
        if not isinstance(geometry, dict) or set(geometry) != {"type", "coordinates"}:
            _validation_error("geometry", "几何只能包含 type 和 coordinates", index, message=message)
        try:
            positions += _position_count(geometry)
        except TypeError:
            positions += MAX_GEOMETRY_REQUEST_POSITIONS + 1
    # 只数坐标、不校验结构：先挡住超量请求，再逐个解析
    if positions > MAX_GEOMETRY_REQUEST_POSITIONS:
        _validation_error("geometries", f"请求内坐标位置总数不能超过 {MAX_GEOMETRY_REQUEST_POSITIONS}", message=message)
    return roi_context(result), geometries


def validate_result_geometries(project_id, result_id, payload, *, body_size=None):
    """逐个几何给出与保存完全相同的合法性结论（ok，或 code + reason + 越界量级）。"""
    context, geometries = _geometry_request(
        _result_for_project(project_id, result_id), payload, body_size,
        keys={"geometries"}, minimum=1, maximum=MAX_FEATURES,
    )
    return {"items": [evaluate(context, geometry).payload() for geometry in geometries]}


def simplify_result_geometries(project_id, result_id, payload, *, body_size=None):
    """逐个几何简化。每个结果都是被 evaluate 接受的，否则报告 rejected、不给几何。"""
    context, geometries = _geometry_request(
        _result_for_project(project_id, result_id), payload, body_size,
        keys={"geometries", "tolerance"}, minimum=1, maximum=MAX_FEATURES,
    )
    tolerance = payload.get("tolerance", SIMPLIFY_TOLERANCE_DEGREES)
    try:
        items = [simplify_geometry(context, geometry, tolerance) for geometry in geometries]
    except GeometryProblem as problem:
        _validation_error(problem.field, problem.reason, message="几何请求不合法")
    return {"tolerance": tolerance, "items": items}


def union_result_geometries(project_id, result_id, payload, *, body_size=None):
    """合并 2 到 MAX_UNION_GEOMETRIES 个面；结果不被 evaluate 接受时 422 并带原因。"""
    context, geometries = _geometry_request(
        _result_for_project(project_id, result_id), payload, body_size,
        keys={"geometries"}, minimum=2, maximum=MAX_UNION_GEOMETRIES,
    )
    try:
        merged, verdict = union_geometries(context, geometries)
    except GeometryProblem as problem:
        _validation_error(problem.field, problem.reason, message="几何无法合并")
    if merged is None:
        raise ClassificationValidationError(
            "几何无法合并",
            {key: value for key, value in verdict.payload().items() if key != "ok"},
        )
    return {"geometry": to_geojson(merged)}


def _claim_current_revision(result, base_revision_no, revision_no, feature_collection_json):
    import json as _json

    try:
        collection = _json.loads(feature_collection_json or "{}")
        feature_count = len(collection.get("features") or []) if isinstance(collection, dict) else 0
    except (TypeError, ValueError):
        feature_count = 0
    return ClassificationResult.query.filter(
        ClassificationResult.id == result.id,
        ClassificationResult.project_id == result.project_id,
        ClassificationResult.current_revision_no == base_revision_no,
    ).update(
        {
            "current_feature_collection_json": feature_collection_json,
            "current_revision_no": revision_no,
            "feature_count": feature_count,
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


def _settle_snapshot_after_failure(result, revision_no, snapshot_path, revision_stats_path, snapshot_installed):
    """提交失败后处置已转正的修订快照与统计 sidecar，返回 removed / committed / unknown。

    保存的顺序是"先落盘、后提交"（文件先于 DB：提交成功而文件缺失，比反过来
    更糟），所以 commit 失败时 revisions/<n>.geojson 与 <n>.stats.json 已经就位。
    此前失败分支只删临时文件——它早已被 rename 走，于是每次提交失败都留下一个
    库里不存在的孤立快照（2026-11-24 审计可复现）。是否清理由数据库对账决定：
    - 修订行不存在 → 提交确未生效，删除本次转正的文件；
    - 修订行存在   → 提交其实成功，文件必须保留（否则 DB 指向不存在的快照）；
    - 对账本身失败（数据库不可用）→ 不动文件：孤儿文件无害（下次保存同一修订号
      会原子覆盖；统计 sidecar 按版本号寻址，孤儿不会被任何有效成果引用），
      缺失快照有害。
    调用前必须已 rollback。未转正（snapshot_installed=False）时无需处置。
    """
    if not snapshot_installed:
        return "removed"
    try:
        committed = (
            ClassificationRevision.query.filter_by(
                result_id=result.id, revision_no=revision_no
            ).first()
            is not None
        )
    except Exception:  # noqa: BLE001 - 对账失败按"不确定"处理，不动文件
        LOGGER.warning(
            "保存提交失败后无法对账修订快照，保留文件 result_id=%s revision_no=%s",
            result.id, revision_no, exc_info=True,
        )
        return "unknown"
    if committed:
        return "committed"
    for path in (snapshot_path, revision_stats_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "清理未提交修订的落盘文件失败 result_id=%s revision_no=%s",
                result.id, revision_no, exc_info=True,
            )
    return "removed"


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
    # 快照是否已从临时名转正到 revisions/<n>.geojson（转正发生在 commit 之前）
    snapshot_installed = False
    temporary_snapshot_path = None
    revision_stats_path = _revision_stats_path(result.project_id, result.id, revision_no)
    try:
        temporary_snapshot_path = _write_json_temporary(snapshot_path, feature_collection)
        if _claim_current_revision(
            result,
            base_revision_no,
            revision_no,
            feature_collection_json,
        ) != 1:
            db.session.rollback()
            raise _save_conflict(result, base_revision_no)
        # CAS 成功：本次保存是「该成果这一版本号」的唯一写者（并发保存在上面的条件
        # 更新处落败，走不到这里），此时才写修订的派生统计 sidecar——先文件、后提交
        # 数据库。写失败不阻断保存：读端在 sidecar 缺失时按当前矢量现算同一个纯函数
        try:
            write_revision_stats(result.project_id, result.id, revision_no, feature_collection)
        except OSError:
            LOGGER.warning(
                "修订派生统计写盘失败（读取时将现算）result_id=%s revision_no=%s",
                result.id, revision_no, exc_info=True,
            )
        # UPDATE 用 synchronize_session=False，同步内存对象供后续统计回写
        # （revised_revision_no 等）读取正确修订号
        result.current_revision_no = revision_no
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
        snapshot_installed = True
        db.session.commit()
        # 变化矩阵是整矿级重计算（栅格化），不在这条保存请求里同步等待：提交后交给
        # 后台队列，读端（溯源）按重算回执判断是否现值，不是就再补一次请求。
        # 逐修订的地类占比已在上方随快照落盘，保存返回时即为就绪
        try:
            request_change_matrix_rebuild_for_result(
                result.project_id, result.mine_fid, result.year, result.id
            )
        except Exception:  # noqa: BLE001 - 矩阵派生绝不影响保存结果
            LOGGER.warning(
                "审校矩阵重算请求失败 result_id=%s mine_fid=%s",
                result.id, result.mine_fid, exc_info=True,
            )
    except ClassificationConflictError:
        db.session.rollback()
        if temporary_snapshot_path is not None:
            with suppress(OSError):
                temporary_snapshot_path.unlink(missing_ok=True)
        raise
    except IntegrityError as error:
        db.session.rollback()
        _settle_snapshot_after_failure(
            result, revision_no, snapshot_path, revision_stats_path, snapshot_installed
        )
        if temporary_snapshot_path is not None:
            with suppress(OSError):
                temporary_snapshot_path.unlink(missing_ok=True)
        raise _save_conflict(result, base_revision_no) from error
    except Exception:
        db.session.rollback()
        if (
            snapshot_installed
            and _settle_snapshot_after_failure(
                result, revision_no, snapshot_path, revision_stats_path, snapshot_installed
            )
            == "committed"
        ):
            # commit 抛错但修订其实已落库（连接在应答途中断开等不确定结局）：
            # 客户端手里的基准版本已过期，按冲突提示重新加载，而不是报一个
            # 让用户重试的 500
            if temporary_snapshot_path is not None:
                with suppress(OSError):
                    temporary_snapshot_path.unlink(missing_ok=True)
            raise _save_conflict(result, base_revision_no)
        if temporary_snapshot_path is not None:
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
    result.feature_count = len(feature_collection["features"])
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
