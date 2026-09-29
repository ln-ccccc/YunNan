# -*- coding: utf-8 -*-
"""解译成果质量复核（优化建议三.3）：批次质检统计 + 疑似误判线索筛查。

口径与边界：
- 只做结构性质检（地类构成/细碎图斑线索），不承诺识别准确率——精度评估需要
  地面真值，模型迭代不在本期范围（AGENTS §9）。"疑似清单"是人工复核线索，
  非误判判定。
- 面积口径与项目地图一致（project_map 面积链）：矢量属性 area→TBTYMJ_1→
  TBTYMJ（官方图斑面积别名，m²）优先，真值非数字逐级回落；无属性时按
  WGS84 球面几何近似计算兜底（Chamberlain & Duquette 公式，与 JS 生态
  turf.area 同源，正反均忽略地球扁率，相对误差 ~0.1% 量级）。
  不读 binding.area_snapshot（量纲随源数据，历史项目曾误映射平方度）。
- 复核对象优先 current（人工修订后）矢量，为空回落 auto，两者皆空
  （vector_failed 等）返回零值报告而非报错。
"""
import math
from datetime import datetime, timezone

from applications.common.utils.utc_time import to_utc_z

DEFAULT_SMALL_AREA_THRESHOLD_M2 = 100.0
MAX_SMALL_AREA_THRESHOLD_M2 = 1_000_000.0
MAX_SUSPECTS = 200

_SPHERE_RADIUS_M = 6371008.8

_AREA_PROPERTY_ALIASES = ("area", "TBTYMJ_1", "TBTYMJ")


def ring_area_m2(ring):
    """单环球面面积（m²，带符号：逆时针为正）。重复首尾点与单点环按 0 处理。"""
    if not ring or len(ring) < 3:
        return 0.0
    total = 0.0
    prev_lon = None
    prev_lat = None
    first_lon = None
    first_lat = None
    for point in ring:
        try:
            lon = math.radians(float(point[0]))
            lat = math.radians(float(point[1]))
        except (TypeError, ValueError, IndexError):
            return 0.0
        if prev_lon is not None:
            total += (lon - prev_lon) * (2.0 + math.sin(prev_lat) + math.sin(lat))
        else:
            first_lon, first_lat = lon, lat
        prev_lon, prev_lat = lon, lat
    if prev_lon is not None and first_lon is not None:
        total += (first_lon - prev_lon) * (2.0 + math.sin(prev_lat) + math.sin(first_lat))
    return total * _SPHERE_RADIUS_M * _SPHERE_RADIUS_M / 2.0


def geometry_area_m2(geometry):
    """GeoJSON Polygon/MultiPolygon 绝对面积（m²，外环减内环，环方向不敏感）。"""
    if not isinstance(geometry, dict):
        return 0.0
    geom_type = geometry.get("type")
    if geom_type == "Polygon":
        rings = [geometry.get("coordinates") or []]
    elif geom_type == "MultiPolygon":
        rings = geometry.get("coordinates") or []
    else:
        return 0.0
    total = 0.0
    for polygon in rings:
        if not polygon:
            continue
        exterior = abs(ring_area_m2(polygon[0]))
        holes = sum(abs(ring_area_m2(ring)) for ring in polygon[1:])
        total += max(exterior - holes, 0.0)
    return total


def _ring_centroid(ring):
    """外环顶点均值作为定位质心（复核线索用途，非严格形心）。
    GeoJSON 环首尾重复闭合点只计一次，避免闭合点加权放大末端。"""
    points = [p for p in (ring or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
    if len(points) > 1 and points[0][0] == points[-1][0] and points[0][1] == points[-1][1]:
        points = points[:-1]
    if not points:
        return None
    lon = sum(float(p[0]) for p in points) / len(points)
    lat = sum(float(p[1]) for p in points) / len(points)
    return [round(lon, 6), round(lat, 6)]


def feature_area_m2(properties, geometry):
    """面积链：属性 area/TBTYMJ_1/TBTYMJ 逐个尝试数值解析，全部无效回落几何计算。"""
    for alias in _AREA_PROPERTY_ALIASES:
        candidate = (properties or {}).get(alias)
        if candidate in (None, ""):
            continue
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return geometry_area_m2(geometry)


def _feature_class(properties):
    return (properties or {}).get("class_code"), (properties or {}).get("class_name")


def _feature_centroid(geometry):
    if not isinstance(geometry, dict):
        return None
    if geometry.get("type") == "Polygon":
        return _ring_centroid((geometry.get("coordinates") or [[]])[0])
    if geometry.get("type") == "MultiPolygon":
        polygons = geometry.get("coordinates") or []
        if polygons and polygons[0]:
            return _ring_centroid(polygons[0][0])
    return None


def _validate_threshold(raw_value):
    if raw_value in (None, ""):
        return DEFAULT_SMALL_AREA_THRESHOLD_M2
    try:
        threshold = float(raw_value)
    except (TypeError, ValueError):
        raise ValueError("small_area_threshold_m2 必须是数字")
    if not 0 < threshold <= MAX_SMALL_AREA_THRESHOLD_M2:
        raise ValueError(f"small_area_threshold_m2 须在 0~{int(MAX_SMALL_AREA_THRESHOLD_M2)} 之间")
    return threshold


def build_result_quality_review(project_id, result_id, small_area_threshold_m2=None):
    """单条成果的质量复核报告：地类构成统计 + 细碎图斑（疑似误判）线索清单。"""
    threshold = _validate_threshold(small_area_threshold_m2)

    from applications.project_hub.classification_results import get_classification_result

    summary = get_classification_result(project_id, result_id)
    current = summary.get("current_feature_collection")
    auto = summary.get("auto_feature_collection")
    current_usable = isinstance(current, dict) and bool(current.get("features"))
    auto_usable = isinstance(auto, dict) and bool(auto.get("features"))
    collection = current if current_usable else (auto if auto_usable else None)
    source = "current" if current_usable else ("auto" if auto_usable else "none")

    features = (collection or {}).get("features") or []
    class_stats = {}
    total_area = 0.0
    suspects = []
    for index, feature in enumerate(features):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        area = feature_area_m2(properties, geometry)
        total_area += area
        class_code, class_name = _feature_class(properties)
        key = (class_code, class_name)
        stat = class_stats.setdefault(key, {
            "class_code": class_code,
            "class_name": class_name,
            "count": 0,
            "area_m2": 0.0,
        })
        stat["count"] += 1
        stat["area_m2"] += area
        if area < threshold:
            suspects.append({
                "feature_index": index,
                "class_code": class_code,
                "class_name": class_name,
                "area_m2": round(area, 2),
                "centroid": _feature_centroid(geometry),
            })

    suspects.sort(key=lambda item: (item["area_m2"], item["feature_index"]))
    class_rows = sorted(
        class_stats.values(),
        key=lambda row: (-row["area_m2"], -(row["count"])),
    )
    for row in class_rows:
        row["area_m2"] = round(row["area_m2"], 2)
        row["area_percent"] = round(row["area_m2"] / total_area * 100, 2) if total_area > 0 else None

    small_count = len(suspects)
    return {
        "result_id": summary.get("result_id"),
        "project_id": summary.get("project_id"),
        "mine_fid": summary.get("mine_fid"),
        "year": summary.get("year"),
        "vector_status": summary.get("vector_status"),
        "vector_source": source,
        "feature_count": len(features),
        "class_kind_count": len(class_rows),
        "total_area_m2": round(total_area, 2),
        "small_area_threshold_m2": threshold,
        "small_feature_count": small_count,
        "small_feature_ratio_percent": round(small_count / len(features) * 100, 2) if features else 0.0,
        "class_stats": class_rows,
        # 疑似误判线索：细碎图斑按面积升序，最多 200 条；定位与修订走编辑器
        "suspects": suspects[:MAX_SUSPECTS],
        "suspects_truncated": small_count > MAX_SUSPECTS,
        "reviewed_at": to_utc_z(datetime.now(timezone.utc)),
    }
