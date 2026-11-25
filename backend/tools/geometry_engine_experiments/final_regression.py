# -*- coding: utf-8 -*-
"""定稿回归：对导出的全部生产成果做只读离线回归，结论用于验收报告。

运行方式把被测源码钉死在一个 commit 上：源码目录由 `git archive <commit> backend` 导出后只读挂载到
/app/backend，所以这里 import 到的 geometry_engine 就是该 commit 的内容（脚本会把它的 sha256 写进摘要）。

内容：
  1. 判定对账：引擎 evaluate 与改造前保存规则的原表达式逐图斑比对（必须零差异），
     并给出前端旧判定（b2 采样 / b3 booleanWithin）与引擎结论的交叉表；
  2. coverage 普查：矿山有效性、图斑间重叠、与矿山之间的缝隙、MultiPolygon 数量、公共边占比；
  3. 定稿简化：逐图斑结果与分类统计；对每个 changed 结果做不依赖引擎代码的交叉验证
     （改造前规则原表达式、叠加运算差面积、纯 Python 的边中点与 ROI 拐点检查）；
  4. 耗时：成果加载、ROI 上下文、逐图斑判定、简化、整成果合并的 P50/P95/P99。

用法（一次性容器内）：
  python final_regression.py <production-results.ndjson> <before-verdicts.ndjson> <outdir> [workers]
"""
import hashlib
import json
import math
import os
import resource
import sys
import time
import warnings
from multiprocessing import Pool

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely.geometry import Point, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402
from shapely.prepared import prep  # noqa: E402

from applications.project_hub import geometry_engine as engine  # noqa: E402

TOL = engine.NUMERIC_TOLERANCE_DEGREES
BIG = 5000
BOUNDARY_FLAG_DEGREES = 1e-7  # 「贴边界」类目的判定阈值（约 1 厘米），只用于分类统计
OVERLAP_AREA_FLOOR = 1e-14  # 平方度，约 0.01 平方米；低于它按数值噪声处理
BEFORE = {}


def geojson_rings(geometry):
    if geometry["type"] == "Polygon":
        return [[(p[0], p[1]) for p in ring] for ring in geometry["coordinates"]]
    return [[(p[0], p[1]) for p in ring] for polygon in geometry["coordinates"] for ring in polygon]


def parity_inside(x, y, rings):
    inside = False
    for ring in rings:
        if len(ring) < 2:
            continue
        x0, y0 = ring[-1]
        for x1, y1 in ring:
            if (y0 > y) != (y1 > y):
                if x0 + (y - y0) * (x1 - x0) / (y1 - y0) > x:
                    inside = not inside
            x0, y0 = x1, y1
    return inside


def ring_distance(x, y, rings):
    best = float("inf")
    for ring in rings:
        if len(ring) < 2:
            continue
        x0, y0 = ring[-1]
        for x1, y1 in ring:
            dx, dy = x1 - x0, y1 - y0
            if dx or dy:
                t = ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy)
                t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
            else:
                t = 0.0
            best = min(best, math.hypot(x - (x0 + t * dx), y - (y0 + t * dy)))
            x0, y0 = x1, y1
    return best


def inside_or_on(x, y, rings):
    return ring_distance(x, y, rings) <= TOL or parity_inside(x, y, rings)


def samples_off_roi(rings, roi_rings):
    """纯 Python：新几何每条边的 1/4、1/2、3/4 处是否都在矿山内或贴边界。返回不在的采样点数。"""
    count = 0
    for ring in rings:
        for (xa, ya), (xb, yb) in zip(ring, ring[1:]):
            for fraction in (0.25, 0.5, 0.75):
                if not inside_or_on(xa + (xb - xa) * fraction, ya + (yb - ya) * fraction, roi_rings):
                    count += 1
    return count


def roi_vertices_lost(original_rings, simplified_rings, roi_rings, bounds):
    """纯 Python：落在原图斑边界上的矿山拐点，简化后离新边界超过数值容差的个数。"""
    minx, miny, maxx, maxy = bounds
    count = 0
    for ring in roi_rings:
        for x, y in ring[:-1]:
            if not (minx - 1e-12 <= x <= maxx + 1e-12 and miny - 1e-12 <= y <= maxy + 1e-12):
                continue
            if ring_distance(x, y, original_rings) <= TOL and ring_distance(x, y, simplified_rings) > TOL:
                count += 1
    return count


def literal_save_rule(roi, tolerant_roi, geometry):
    """改造前保存接口的表达式，原样照抄，不走引擎代码。"""
    return geometry.is_valid and (roi.covers(geometry) or tolerant_roi.covers(geometry))


def process(line):
    started = time.perf_counter()
    result = json.loads(line)
    parsed_all = [shape(feature["geometry"]) for feature in result["features"]]
    load_ms = (time.perf_counter() - started) * 1000
    result_id = result["result_id"]
    roi_geojson = result["roi"]
    roi_rings = geojson_rings(roi_geojson)
    roi = shape(roi_geojson)
    started = time.perf_counter()
    try:
        context = engine.RoiContext(roi)
        context_ms = (time.perf_counter() - started) * 1000
        context_error = None
    except ValueError as error:
        context, context_ms, context_error = None, (time.perf_counter() - started) * 1000, str(error)
    tolerant_roi = roi.buffer(TOL)
    boundary_band = prep(roi.boundary.buffer(BOUNDARY_FLAG_DEGREES))
    before = BEFORE.get(result_id, {})
    rows = []
    simplify_stats = {"a": 0, "u": 0, "s": 0}
    rejected_codes = {}
    unstable = []  # 同一输入连算两次结论不同的图斑（GEOS 3.10.3 拓扑保持简化的不确定性）
    evaluate_ms, simplify_ms = [], []
    for feature, geometry_object in zip(result["features"], parsed_all):
        geometry = feature["geometry"]
        old = before.get(feature["fid"], {})
        positions = engine.position_count(geometry_object) if engine._is_valid_face(geometry_object) else sum(
            len(ring) for ring in geojson_rings(geometry))
        rings = geojson_rings(geometry)
        row = {"r": result_id, "f": feature["fid"], "n": positions, "b2": old.get("b2"), "b3": old.get("b3"),
               "mp": int(geometry["type"] == "MultiPolygon"), "mr": int(len(rings) > 1), "big": int(positions > BIG)}
        raw = literal_save_rule(roi, tolerant_roi, geometry_object)
        row["raw"] = int(raw)
        if context is None:
            row["eng"] = "?"
            rows.append(row)
            continue
        started = time.perf_counter()
        verdict = engine.evaluate(context, geometry)
        elapsed = (time.perf_counter() - started) * 1000
        evaluate_ms.append(elapsed)
        row["ms_eval"] = round(elapsed, 3)
        row["eng"] = "-" if verdict.ok else ("O" if verdict.code == engine.OUTSIDE_ROI else "I")
        row["bt"] = int(any(boundary_band.intersects(Point(x, y)) for ring in rings for x, y in ring[:-1]))
        if verdict.ok:
            started = time.perf_counter()
            outcome = engine.simplify(context, geometry)
            elapsed = (time.perf_counter() - started) * 1000
            again = engine.simplify(context, geometry)
            if again != outcome:
                unstable.append([result_id, feature["fid"], outcome["outcome"], again["outcome"]])
            simplify_ms.append(elapsed)
            row["ms_simp"] = round(elapsed, 3)
            code = {"changed": "a", "unchanged": "u", "rejected": "s"}[outcome["outcome"]]
            simplify_stats[code] += 1
            row["S"] = code
            if code == "s":
                reason = outcome["reason"]["code"]
                rejected_codes[reason] = rejected_codes.get(reason, 0) + 1
                row["scode"] = "O" if reason == engine.OUTSIDE_ROI else "I"
            elif code == "a":
                new_geometry = outcome["geometry"]
                new_object = shape(new_geometry)
                new_rings = geojson_rings(new_geometry)
                row["n2"] = engine.position_count(new_object)
                row["da"] = (new_object.area - geometry_object.area) / geometry_object.area if geometry_object.area else None
                # 交叉验证（都不走引擎的判定代码）
                row["x_literal"] = int(literal_save_rule(roi, tolerant_roi, new_object))
                try:
                    row["x_overlay"] = int(new_object.difference(roi).area == 0)
                except Exception:  # noqa: BLE001
                    row["x_overlay"] = -1
                row["x_off"] = samples_off_roi(new_rings, roi_rings)
                if row["bt"]:
                    row["x_lost"] = roi_vertices_lost(rings, new_rings, roi_rings, geometry_object.bounds)
        rows.append(row)

    summary = {"r": result_id, "project": result["project_id"], "features": len(rows), "roi_type": roi_geojson["type"],
               "roi_parts": len(roi_rings), "roi_valid": bool(roi.is_valid), "roi_area": roi.area, "pixel": result.get("pixel"),
               "ms_load": round(load_ms, 3), "ms_context": round(context_ms, 3)}
    if context_error:
        summary["context_error"] = context_error
    summary["simplify"] = simplify_stats
    summary["rejected_codes"] = rejected_codes
    summary["nondeterministic"] = unstable
    valid_objects = [geometry for geometry in parsed_all if engine._is_valid_face(geometry)]
    summary["mp"] = sum(1 for geometry in result["features"] if geometry["geometry"]["type"] == "MultiPolygon")
    started = time.perf_counter()
    overlap_pairs, overlap_area, max_overlap = 0, 0.0, 0.0
    bounds = [geometry.bounds for geometry in valid_objects]
    for i in range(len(valid_objects)):
        for j in range(i + 1, len(valid_objects)):
            if min(bounds[i][2], bounds[j][2]) - max(bounds[i][0], bounds[j][0]) <= 1e-13:
                continue
            if min(bounds[i][3], bounds[j][3]) - max(bounds[i][1], bounds[j][1]) <= 1e-13:
                continue
            try:
                area = valid_objects[i].intersection(valid_objects[j]).area
            except Exception:  # noqa: BLE001
                continue
            if area > OVERLAP_AREA_FLOOR:
                overlap_pairs += 1
                overlap_area += area
                max_overlap = max(max_overlap, area)
    summary["overlap_pairs"] = overlap_pairs
    summary["overlap_area"] = overlap_area
    summary["max_overlap_area"] = max_overlap
    union_all = unary_union(valid_objects) if valid_objects else None
    summary["ms_union_all"] = round((time.perf_counter() - started) * 1000, 3)
    if union_all is not None and not union_all.is_empty:
        try:
            summary["union_outside_area"] = union_all.difference(roi).area
            summary["gap_ratio"] = (roi.area - union_all.area) / roi.area if roi.area else None
        except Exception:  # noqa: BLE001
            pass
        total_length = sum(geometry.length for geometry in valid_objects)
        if total_length:
            summary["shared"] = round(1 - union_all.boundary.length / total_length, 6)
    return summary, rows, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def init_worker(before):
    global BEFORE
    BEFORE = before


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
    return round(ordered[index], 3)


def distribution(values):
    return {key: percentile(values, float(key[1:]) / 100) for key in ("p50", "p90", "p99")} | {"max": round(max(values), 6) if values else None}


def main():
    production, before_path, outdir = sys.argv[1:4]
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    os.makedirs(outdir, exist_ok=True)
    before = {}
    with open(before_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith('{"r"'):
                item = json.loads(line)
                before.setdefault(item["r"], {})[item["f"]] = item
    lines = [line for line in open(production, encoding="utf-8") if line.startswith('{"result_id"')]
    started = time.perf_counter()
    summaries, all_rows, maxrss = [], [], 0
    with open(os.path.join(outdir, "final-features.ndjson"), "w", encoding="utf-8") as features_out, \
            open(os.path.join(outdir, "final-results.ndjson"), "w", encoding="utf-8") as results_out, \
            Pool(workers, initializer=init_worker, initargs=(before,)) as pool:
        for index, (summary, rows, rss) in enumerate(pool.imap_unordered(process, lines, chunksize=4), 1):
            summaries.append(summary)
            all_rows.extend(rows)
            results_out.write(json.dumps(summary, separators=(",", ":")) + "\n")
            for row in rows:
                features_out.write(json.dumps(row, separators=(",", ":")) + "\n")
            maxrss = max(maxrss, rss)
            if index % 500 == 0:
                print(f"  {index}/{len(lines)} results, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    BEFORE = {}

    features = len(all_rows)
    projects = {}
    for summary in summaries:
        projects[summary["project"]] = projects.get(summary["project"], 0) + 1
    # 1. 判定对账
    parity_mismatch = sum(1 for row in all_rows if row["eng"] != "?" and (row["eng"] == "-") != bool(row["raw"]))
    cross = {}
    for row in all_rows:
        for key in ("b2", "b3"):
            if row[key] is None:
                continue
            cell = cross.setdefault(key, {})
            name = f"前端判定合法={row[key] == 1}/引擎合法={row['eng'] == '-'}"
            cell[name] = cell.get(name, 0) + 1
    # 2. coverage 普查
    gaps = [summary["gap_ratio"] for summary in summaries if summary.get("gap_ratio") is not None]
    overlaps = [summary for summary in summaries if summary["overlap_pairs"]]
    shared = [summary["shared"] for summary in summaries if "shared" in summary]
    # 3. 定稿简化
    categories = [
        ("全部", lambda row: True),
        ("普通", lambda row: not (row.get("bt") or row["mr"] or row["mp"] or row["big"])),
        ("贴边界", lambda row: row.get("bt") == 1),
        ("多环", lambda row: row["mr"] == 1),
        ("MultiPolygon", lambda row: row["mp"] == 1),
        ("超限(>5000)", lambda row: row["big"] == 1),
    ]
    simplify_by_category = {}
    for name, predicate in categories:
        subset = [row for row in all_rows if "S" in row and predicate(row)]
        changed = [row for row in subset if row["S"] == "a"]
        before_positions = sum(row["n"] for row in changed)
        area_changes = [abs(row["da"]) for row in changed if row["da"] is not None]
        touching = [row for row in changed if row.get("bt")]
        simplify_by_category[name] = {
            "count": len(subset), "changed": len(changed), "unchanged": sum(1 for row in subset if row["S"] == "u"),
            "rejected": sum(1 for row in subset if row["S"] == "s"),
            "rejected_outside": sum(1 for row in subset if row.get("scode") == "O"),
            "rejected_invalid": sum(1 for row in subset if row.get("scode") == "I"),
            "effective_rate": round(len(changed) / len(subset), 4) if subset else None,
            "positions_before": before_positions, "positions_after": sum(row["n2"] for row in changed),
            "compression_total": round(1 - sum(row["n2"] for row in changed) / before_positions, 4) if before_positions else None,
            "area_change": distribution([value for value in area_changes]),
            "corner_cut_features": sum(1 for row in touching if row.get("x_lost")),
            "corner_cuts": sum(row.get("x_lost", 0) for row in touching),
        }
    report = {
        "results": len(summaries), "features": features, "by_project": projects,
        "seconds": round(time.perf_counter() - started, 1), "worker_max_rss_mb": maxrss // 1024,
        "engine_sha256": hashlib.sha256(open(engine.__file__, "rb").read()).hexdigest(),
        "verdict_parity": {
            "engine_vs_literal_mismatch": parity_mismatch,
            "engine_illegal": sum(1 for row in all_rows if row["eng"] not in ("-", "?")),
            "engine_no_context": sum(1 for row in all_rows if row["eng"] == "?"),
            "frontend_cross": cross,
        },
        "coverage": {
            "roi_invalid_results": sum(1 for summary in summaries if not summary["roi_valid"]),
            "roi_types": {kind: sum(1 for summary in summaries if summary["roi_type"] == kind) for kind in {summary["roi_type"] for summary in summaries}},
            "roi_parts": distribution([summary["roi_parts"] for summary in summaries]),
            "multipolygon_features": sum(summary["mp"] for summary in summaries),
            "results_with_overlap": len(overlaps), "overlap_pairs": sum(summary["overlap_pairs"] for summary in overlaps),
            "overlap_area_m2": round(sum(summary["overlap_area"] for summary in overlaps) * engine.DEGREES_TO_METRES ** 2, 3),
            "max_overlap_m2": round(max([summary["max_overlap_area"] for summary in overlaps] or [0]) * engine.DEGREES_TO_METRES ** 2, 3),
            "gap_ratio": distribution(gaps),
            "results_gap_over_1pct": sum(1 for value in gaps if value > 0.01),
            "results_gap_over_10pct": sum(1 for value in gaps if value > 0.10),
            "union_outside_roi_results": sum(1 for summary in summaries if summary.get("union_outside_area", 0) > 1e-16),
            "union_outside_m2": round(sum(summary.get("union_outside_area", 0) for summary in summaries) * engine.DEGREES_TO_METRES ** 2, 3),
            "shared": distribution(shared),
        },
        "simplify": simplify_by_category,
        "geos_nondeterministic": {
            "features": sum(len(summary["nondeterministic"]) for summary in summaries),
            "examples": [item for summary in summaries for item in summary["nondeterministic"]][:8],
        },
        "cross_check": {
            "changed": sum(1 for row in all_rows if row.get("S") == "a"),
            "literal_rule_accepts": sum(1 for row in all_rows if row.get("S") == "a" and row.get("x_literal") == 1),
            "overlay_outside_zero": sum(1 for row in all_rows if row.get("S") == "a" and row.get("x_overlay") == 1),
            "overlay_errors": sum(1 for row in all_rows if row.get("x_overlay") == -1),
            "pure_python_samples_off_roi": sum(row.get("x_off", 0) for row in all_rows),
            "pure_python_corner_cuts": sum(row.get("x_lost", 0) for row in all_rows),
        },
        "timing_ms": {
            "load_per_result": distribution([summary["ms_load"] for summary in summaries]),
            "context_per_result": distribution([summary["ms_context"] for summary in summaries]),
            "union_all_per_result": distribution([summary["ms_union_all"] for summary in summaries]),
            "evaluate_per_feature": distribution([row["ms_eval"] for row in all_rows if "ms_eval" in row]),
            "simplify_per_feature": distribution([row["ms_simp"] for row in all_rows if "ms_simp" in row]),
            "evaluate_total_s": round(sum(row["ms_eval"] for row in all_rows if "ms_eval" in row) / 1000, 2),
            "simplify_total_s": round(sum(row["ms_simp"] for row in all_rows if "ms_simp" in row) / 1000, 2),
        },
    }
    open(os.path.join(outdir, "final-summary.json"), "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
