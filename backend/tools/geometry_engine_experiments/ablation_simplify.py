# -*- coding: utf-8 -*-
"""简化方案对照实验（A / B / C / D），全量生产成果，只读输入，隔离容器内运行。

  A  已部署方案：turf 不保拓扑 Douglas-Peucker + 采样式接受判定 + 降容差重试（结论与几何取自
     before-verdicts.ndjson，耗时取自 a-timing.ndjson，均在 Node 里计算）
  B  Shapely 原生 simplify(preserve_topology=True)，不校验、不重试（最简基线，原样采用结果）
  C  原生 simplify(preserve_topology=True) + 统一 evaluate 校验 + 降容差重试（÷4、÷16）
  D  当前实现：边界链保护（边界顶点标记 + 自由链分段 DP + 共线点清理）+ 校验 + 重试
  另附 B'、C'：把 preserve_topology 换成 False 的对应版本，仅供对照。

每个图斑按类别打标：bt 贴边界（有顶点落在矿山边界 1e-7 度内）、mr 多环（环数 > 1）、
mp MultiPolygon、big 超限（坐标位置数 > 5000）。类别之间有重叠，统计时分别看。

用法（一次性容器内）：
  python ablation_simplify.py <production-results.ndjson> <before-verdicts.ndjson> <outdir> [workers]
"""
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

from shapely.geometry import shape  # noqa: E402

from applications.project_hub import geometry_engine as engine  # noqa: E402

TOL = engine.NUMERIC_TOLERANCE_DEGREES
SIMPLIFY_TOLERANCE = engine.SIMPLIFY_TOLERANCE_DEGREES
SCALES = engine.SIMPLIFY_RETRY_SCALES
BIG = 5000
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
            continue  # 已部署方案接受过的个别结果带空环，跳过而不是让实验中断
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


def corner_cuts(original_rings, simplified_rings, roi_rings, bounds):
    """纯 Python 独立检测：落在原图斑边界上的矿山拐点，简化后离新边界超过数值容差的个数。"""
    minx, miny, maxx, maxy = bounds
    count = 0
    for ring in roi_rings:
        for x, y in ring[:-1]:
            if not (minx - 1e-12 <= x <= maxx + 1e-12 and miny - 1e-12 <= y <= maxy + 1e-12):
                continue
            if ring_distance(x, y, original_rings) <= TOL and ring_distance(x, y, simplified_rings) > TOL:
                count += 1
    return count


def code_of(verdict):
    return "-" if verdict.ok else ("O" if verdict.code == engine.OUTSIDE_ROI else "I")


def feature_row(result_id, roi, context, band, roi_rings, item):
    geometry = item["geometry"]
    original = shape(geometry)
    if not engine._is_valid_face(original):
        return None
    original_rings = geojson_rings(geometry)
    positions = engine.position_count(original)
    rings = len(original_rings)
    vertices = [(x, y) for ring in original_rings for x, y in ring[:-1]]
    touching = bool(context.near_boundary(vertices))
    area = original.area
    row = {
        "r": result_id, "f": item["fid"], "n": positions, "area": area,
        "mp": int(geometry["type"] == "MultiPolygon"), "mr": int(rings > 1), "big": int(positions > BIG), "bt": int(touching),
    }
    on_boundary = original.boundary.intersection(band).length if touching else None
    if on_boundary is not None:
        row["obl"] = on_boundary

    def describe(candidate, verdict):
        description = {"n2": engine.position_count(candidate), "a2": candidate.area, "c": code_of(verdict)}
        if verdict.code == engine.OUTSIDE_ROI:
            try:
                description["out"] = candidate.difference(roi).area
            except Exception:  # noqa: BLE001
                description["out"] = None
        if touching:
            description["obl2"] = candidate.boundary.intersection(band).length
            description["cc"] = corner_cuts(original_rings, geojson_rings(engine.to_geojson(candidate)), roi_rings, original.bounds)
        return description

    def unchanged(code):
        description = {"o": "u" if code == "-" else "s", "n2": positions, "a2": area, "c": code}
        if touching:
            description["obl2"] = on_boundary
            description["cc"] = 0
        return description

    # A：已部署方案（结论与几何来自 Node 侧的同一套函数）
    old = BEFORE.get(result_id, {}).get(item["fid"], {})
    row["A"] = unchanged("-") if old.get("oldS") != "a" or old.get("g") is None else {"o": "a"}
    if row["A"]["o"] == "a":
        candidate = shape(old["g"])
        row["A"].update(describe(candidate, engine.evaluate_parsed(context, candidate)))
        row["A"]["empty_ring"] = int(any(len(ring) == 0 for ring in geojson_rings(old["g"])))

    # B / B'：原生简化，不校验不重试（原样采用）
    for key, preserve in (("B", True), ("B2", False)):
        started = time.perf_counter()
        candidate = original.simplify(SIMPLIFY_TOLERANCE, preserve_topology=preserve)
        row[key + "_ms"] = (time.perf_counter() - started) * 1000
        verdict = engine.evaluate_parsed(context, candidate)
        row[key] = describe(candidate, verdict)
        row[key]["o"] = "a" if row[key]["n2"] != positions else "u"

    # C / C'：原生简化 + 统一校验 + 降容差重试
    for key, preserve in (("C", True), ("C2", False)):
        started = time.perf_counter()
        chosen, verdict = None, None
        for scale in SCALES:
            candidate = original.simplify(SIMPLIFY_TOLERANCE * scale, preserve_topology=preserve)
            if engine.position_count(candidate) == positions:
                verdict = "unchanged"
                break
            verdict = engine.evaluate_parsed(context, candidate)
            if verdict.ok:
                chosen = candidate
                break
        row[key + "_ms"] = (time.perf_counter() - started) * 1000
        if chosen is not None:
            row[key] = describe(chosen, verdict)
            row[key]["o"] = "a"
        else:
            row[key] = unchanged("-" if verdict == "unchanged" else code_of(verdict))

    # D：当前实现（边界链保护）
    started = time.perf_counter()
    outcome = engine.simplify(context, geometry)
    row["D_ms"] = (time.perf_counter() - started) * 1000
    if outcome["outcome"] == engine.CHANGED:
        candidate = shape(outcome["geometry"])
        row["D"] = describe(candidate, engine.evaluate_parsed(context, candidate))
        row["D"]["o"] = "a"
    else:
        reason = outcome.get("reason") or {}
        row["D"] = unchanged({"OUTSIDE_ROI": "O", "INVALID_GEOMETRY": "I"}.get(reason.get("code"), "-"))
    return row


def process(line):
    result = json.loads(line)
    result_id = result["result_id"]
    roi = shape(result["roi"])
    context = engine.RoiContext(roi)
    band = roi.boundary.buffer(TOL)
    roi_rings = geojson_rings(result["roi"])
    rows = []
    for item in result["features"]:
        try:
            row = feature_row(result_id, roi, context, band, roi_rings, item)
        except Exception as error:  # noqa: BLE001 —— 个别异常几何如实记录，不让实验中断
            row = {"r": result_id, "f": item["fid"], "err": type(error).__name__}
        if row is not None:
            rows.append(json.dumps(row, separators=(",", ":")))
    return rows, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def init_worker(before):
    global BEFORE
    BEFORE = before


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
    features = 0
    maxrss = 0
    with open(os.path.join(outdir, "ablation-features.ndjson"), "w", encoding="utf-8") as out, \
            Pool(workers, initializer=init_worker, initargs=(before,)) as pool:
        for index, (rows, rss) in enumerate(pool.imap_unordered(process, lines, chunksize=4), 1):
            out.write("\n".join(rows) + "\n")
            features += len(rows)
            maxrss = max(maxrss, rss)
            if index % 300 == 0:
                print(f"  {index}/{len(lines)} results, {features} features, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    print(json.dumps({"results": len(lines), "features": features, "seconds": round(time.perf_counter() - started, 1), "worker_max_rss_mb": maxrss // 1024}))


if __name__ == "__main__":
    main()
