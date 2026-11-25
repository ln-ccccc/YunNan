# -*- coding: utf-8 -*-
"""量 C 方案「把图斑从矿山边界上拉开」的幅度（抽样）：原图斑贴边边的中点到简化后边界的距离分布。
对照已部署方案 A。用法：python ablation_detachment.py <production> <before> <sample-feature-count>"""
import json
import math
import sys
import warnings

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely.geometry import Point, shape  # noqa: E402

from applications.project_hub import geometry_engine as engine  # noqa: E402

production, before_path, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
TOL, SIMPLIFY_TOLERANCE = engine.NUMERIC_TOLERANCE_DEGREES, engine.SIMPLIFY_TOLERANCE_DEGREES
before = {}
for line in open(before_path, encoding="utf-8"):
    if line.startswith('{"r"'):
        item = json.loads(line)
        if item.get("oldS") == "a":
            before.setdefault(item["r"], {})[item["f"]] = item
distances = {"A": [], "C": []}
areas = {"A": [], "C": []}
count = 0
for line in open(production, encoding="utf-8"):
    if not line.startswith('{"result_id"'):
        continue
    result = json.loads(line)
    roi = shape(result["roi"])
    band = roi.boundary.buffer(1e-7)
    tolerant_roi = roi.buffer(TOL)
    for feature in result["features"]:
        original = shape(feature["geometry"])
        if not engine._is_valid_face(original):
            continue
        edges = []
        for part in engine._rings(original):
            for ring in part:
                points = list(ring.coords)
                for pa, pb in zip(points, points[1:]):
                    if band.intersects(Point(*pa)) and band.intersects(Point(*pb)):
                        edges.append(((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, math.hypot(pb[0] - pa[0], pb[1] - pa[1])))
        if not edges:
            continue
        candidates = {}
        old = before.get(result["result_id"], {}).get(feature["fid"])
        if old:
            candidates["A"] = shape(old["g"])
        for scale in engine.SIMPLIFY_RETRY_SCALES:
            candidate = original.simplify(SIMPLIFY_TOLERANCE * scale, preserve_topology=True)
            if engine.position_count(candidate) == engine.position_count(original):
                break
            if candidate.is_valid and (roi.covers(candidate) or tolerant_roi.covers(candidate)):
                candidates["C"] = candidate
                break
        sample = edges[:: max(1, len(edges) // 20)]
        for key, candidate in candidates.items():
            boundary = candidate.boundary
            for x, y, length in sample:
                distances[key].append((boundary.distance(Point(x, y)), length))
            areas[key].append((abs(candidate.area - original.area) / original.area, original.area))
        count += 1
        if count >= limit:
            break
    if count >= limit:
        break


def summary(values):
    total = sum(length for _, length in values)
    ordered = sorted(values)
    def share(upper):
        return sum(length for distance, length in values if distance <= upper) / total
    cumulative, result = 0.0, {}
    for fraction in (0.5, 0.9, 0.99):
        target = total * fraction
        cumulative = 0.0
        for distance, length in ordered:
            cumulative += length
            if cumulative >= target:
                result[f"p{int(fraction * 100)}_m"] = round(distance * engine.DEGREES_TO_METRES, 4)
                break
    result["max_m"] = round(ordered[-1][0] * engine.DEGREES_TO_METRES, 4)
    result["share_still_attached"] = round(share(TOL), 4)
    result["share_within_1cm"] = round(share(1e-7), 4)
    result["share_within_10cm"] = round(share(1e-6), 4)
    result["share_within_1m"] = round(share(1e-5), 4)
    return result


print(json.dumps({"boundary_touching_features": count,
                  "detachment_length_weighted": {key: summary(values) for key, values in distances.items()}}, indent=1))
