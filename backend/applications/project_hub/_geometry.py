# -*- coding: utf-8 -*-
"""纯 GeoJSON 几何计算（2026-11-14 审查问题28 依赖环拆解）。

零项目依赖的球面面积实现（Chamberlain & Duquette 公式，与 JS 生态 turf.area
同源，正反均忽略地球扁率，相对误差 ~0.1% 量级）。原位于 quality_review——
classification_stats 只需要面积函数却被迫连入 quality_review→
classification_results 依赖环；拆出后 quality_review 与 classification_stats
共同取源于此处，quality_review 保留同名 re-export 兼容既有导入。
"""
import math

_SPHERE_RADIUS_M = 6371008.8


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
