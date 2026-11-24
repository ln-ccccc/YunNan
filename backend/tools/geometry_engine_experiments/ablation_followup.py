# -*- coding: utf-8 -*-
"""对照实验补充分析（只读输入，隔离容器内运行）：

  1. ROI 边界保持——稳健口径：原图斑里「两端都落在矿山边界上」的边视为贴边边；对它的中点量到简化后边界的
     距离，不超过数值容差就算这条边还贴着。只用 GEOS 的 distance，不用线与细带求交（那种做法在 1e-9 量级上数值不稳）。
  2. 面积变化按图斑大小分档（A / C / D），解释「中位 15%~40%」是不是真实情况。
  3. 顶点压缩的配对比较：只看 C 与 D 都简化成功的图斑，避免「各自成功集合不同」造成的选择偏差。
  4. A 的 unchanged / skipped 拆分。

用法：python ablation_followup.py <production-results.ndjson> <before-verdicts.ndjson> <out.json> [workers]
"""
import json
import math
import os
import sys
import time
import warnings
from multiprocessing import Pool

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely.geometry import Point, shape  # noqa: E402

from applications.project_hub import geometry_engine as engine  # noqa: E402

TOL = engine.NUMERIC_TOLERANCE_DEGREES
SIMPLIFY_TOLERANCE = engine.SIMPLIFY_TOLERANCE_DEGREES
SCALES = engine.SIMPLIFY_RETRY_SCALES
BINS = ((0, 100), (100, 1000), (1000, 10000), (10000, float("inf")))
BEFORE = {}
MAX_SAMPLES = 50


def bin_of(area_m2):
    for index, (low, high) in enumerate(BINS):
        if low <= area_m2 < high:
            return index
    return len(BINS) - 1


def native_with_retry(roi, tolerant_roi, original):
    for scale in SCALES:
        candidate = original.simplify(SIMPLIFY_TOLERANCE * scale, preserve_topology=True)
        if engine.position_count(candidate) == engine.position_count(original):
            return None
        if candidate.is_valid and (roi.covers(candidate) or tolerant_roi.covers(candidate)):
            return candidate
    return None


def new_accumulator():
    return {"changed": 0, "sum_n": 0, "sum_n2": 0, "kept_len": 0.0, "sample_len": 0.0, "area_change": [[] for _ in BINS]}


def process(line):
    result = json.loads(line)
    roi = shape(result["roi"])
    roi_context = engine.RoiContext(roi)
    tolerant_roi = roi.buffer(TOL)
    before = BEFORE.get(result["result_id"], {})
    out = {key: {"all": new_accumulator(), "bt": new_accumulator()} for key in ("A", "C", "D")}
    paired = []
    a_outcomes = {"a": 0, "u": 0, "s": 0}
    for feature in result["features"]:
        original = shape(feature["geometry"])
        if not engine._is_valid_face(original):
            continue
        geometry = feature["geometry"]
        positions = engine.position_count(original)
        vertices = [(x, y) for part in engine._rings(original) for ring in part for x, y in list(ring.coords)[:-1]]
        near = roi_context.near_boundary(vertices)
        # 贴边边：环上相邻两个顶点都在矿山边界上
        edges = []
        offset = 0
        for part in engine._rings(original):
            for ring in part:
                count = len(ring.coords) - 1
                ring_vertices = list(ring.coords)[:-1]
                for index in range(count):
                    a, b = offset + index, offset + (index + 1) % count
                    if a in near and b in near:
                        pa, pb = ring_vertices[index], ring_vertices[(index + 1) % count]
                        edges.append((((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2), math.hypot(pb[0] - pa[0], pb[1] - pa[1])))
                offset += count
        touching = bool(edges)
        sample = edges[:: max(1, len(edges) // MAX_SAMPLES)]
        sample_length = sum(length for _, length in sample)
        size_bin = bin_of(original.area * engine.DEGREES_TO_METRES ** 2)

        old = before.get(feature["fid"], {})
        candidates = {}
        if old.get("oldS") == "a" and old.get("g") is not None:
            try:
                candidates["A"] = shape(old["g"])
            except Exception:  # noqa: BLE001
                candidates["A"] = None
            a_outcomes["a"] += 1
        else:
            a_outcomes[old.get("oldS", "s")] += 1
        candidates["C"] = native_with_retry(roi, tolerant_roi, original)
        outcome = engine.simplify(roi_context, geometry)
        candidates["D"] = shape(outcome["geometry"]) if outcome["outcome"] == engine.CHANGED else None
        if candidates["C"] is not None and candidates["D"] is not None:
            paired.append((positions, engine.position_count(candidates["C"]), engine.position_count(candidates["D"])))
        for key, candidate in candidates.items():
            if candidate is None:
                continue
            for scope in ("all", "bt"):
                if scope == "bt" and not touching:
                    continue
                accumulator = out[key][scope]
                accumulator["changed"] += 1
                accumulator["sum_n"] += positions
                accumulator["sum_n2"] += engine.position_count(candidate)
                if original.area > 0:
                    accumulator["area_change"][size_bin].append(abs(candidate.area - original.area) / original.area)
                if touching and sample_length > 0:
                    accumulator["sample_len"] += sample_length
                    for midpoint, length in sample:
                        if candidate.boundary.distance(Point(*midpoint)) <= TOL:
                            accumulator["kept_len"] += length
    return out, paired, a_outcomes


def init_worker(before):
    global BEFORE
    BEFORE = before


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(math.ceil(fraction * len(ordered))) - 1)]


def main():
    production, before_path, output = sys.argv[1:4]
    workers = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    before = {}
    with open(before_path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith('{"r"'):
                item = json.loads(line)
                if item.get("oldS") == "a":
                    before.setdefault(item["r"], {})[item["f"]] = item
    lines = [line for line in open(production, encoding="utf-8") if line.startswith('{"result_id"')]
    started = time.perf_counter()
    merged = {key: {scope: new_accumulator() for scope in ("all", "bt")} for key in ("A", "C", "D")}
    paired_all, a_outcomes = [], {"a": 0, "u": 0, "s": 0}
    with Pool(workers, initializer=init_worker, initargs=(before,)) as pool:
        for index, (out, paired, outcomes) in enumerate(pool.imap_unordered(process, lines, chunksize=4), 1):
            for key in merged:
                for scope in ("all", "bt"):
                    target, source = merged[key][scope], out[key][scope]
                    for field in ("changed", "sum_n", "sum_n2", "kept_len", "sample_len"):
                        target[field] += source[field]
                    for bin_index in range(len(BINS)):
                        target["area_change"][bin_index].extend(source["area_change"][bin_index])
            paired_all.extend(paired)
            for key in a_outcomes:
                a_outcomes[key] += outcomes[key]
            if index % 500 == 0:
                print(f"  {index}/{len(lines)}, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    BEFORE = {}
    report = {"seconds": round(time.perf_counter() - started, 1), "A_outcomes": a_outcomes, "size_bins_m2": [f"{low}-{high}" for low, high in BINS]}
    for key in ("A", "C", "D"):
        report[key] = {}
        for scope in ("all", "bt"):
            accumulator = merged[key][scope]
            report[key][scope] = {
                "changed": accumulator["changed"],
                "compression_total": round(1 - accumulator["sum_n2"] / accumulator["sum_n"], 4) if accumulator["sum_n"] else None,
                "boundary_length_kept": round(accumulator["kept_len"] / accumulator["sample_len"], 4) if accumulator["sample_len"] else None,
                "boundary_sample_length_m": round(accumulator["sample_len"] * engine.DEGREES_TO_METRES, 1),
                "area_change_by_size": {
                    f"{BINS[index][0]}-{BINS[index][1]}": {
                        "count": len(values),
                        "median": round(percentile(values, 0.5), 4), "p95": round(percentile(values, 0.95), 4),
                        "share_over_10pct": round(sum(1 for value in values if value > 0.1) / len(values), 4) if values else None,
                    } for index, values in enumerate(accumulator["area_change"])
                },
            }
    if paired_all:
        report["paired_C_D"] = {
            "features": len(paired_all),
            "compression_C": round(1 - sum(c for _, c, _ in paired_all) / sum(n for n, _, _ in paired_all), 4),
            "compression_D": round(1 - sum(d for _, _, d in paired_all) / sum(n for n, _, _ in paired_all), 4),
        }
    open(output, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
