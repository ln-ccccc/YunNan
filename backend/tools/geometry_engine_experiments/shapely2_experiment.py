# -*- coding: utf-8 -*-
"""独立实验：Shapely 2.1 + GEOS 3.13（一次性容器里临时安装，不动生产依赖）。

评估两件事：
  1. coverage_simplify 对生产成果是否适用：成果是不是一个成立的 coverage；三种用法
       all        整份成果做 coverage，外边界也简化
       interior   整份成果做 coverage，只简化图斑之间的公共边（外边界即矿山边界不动）
       complement 成果 + 「矿山内没有图斑的部分」虚拟面一起做 coverage，只简化公共边
     各自的越界率、非法率、顶点压缩、面积变化、简化后 coverage 是否仍成立、耗时；
  2. 批量计算：向量化 is_valid / covers / simplify 相对 Shapely 1.8.5 逐个循环的耗时。

合法性表达式与保存规则相同：is_valid 且 (roi.covers(g) 或 roi.buffer(1e-9).covers(g))。

用法（容器内，PYTHONPATH 指向临时安装目录）：
  python shapely2_experiment.py <production-results.ndjson> <outdir> [workers]
"""
import inspect
import json
import math
import os
import sys
import time
import warnings
from multiprocessing import Pool

import numpy as np
import shapely
from shapely.geometry import shape

warnings.simplefilter("ignore")

TOL = 1e-9
SIMPLIFY_TOLERANCE = 1e-5


def percentile(values, fraction):
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))
    return round(ordered[index], 3)


def legal(roi, roi_tol, geoms):
    return shapely.is_valid(geoms) & ~shapely.is_empty(geoms) & (shapely.covers(roi, geoms) | shapely.covers(roi_tol, geoms))


def process(line):
    result = json.loads(line)
    roi = shape(result["roi"])
    roi_tol = roi.buffer(TOL)
    shapely.prepare(roi)
    shapely.prepare(roi_tol)
    geoms = np.array([shape(feature["geometry"]) for feature in result["features"]], dtype=object)
    count = len(geoms)
    if count == 0:
        return None
    before = shapely.get_num_coordinates(geoms)
    area_before = float(shapely.area(geoms).sum())
    union_before = shapely.union_all(geoms)
    out = {"r": result["result_id"], "n": count, "positions": int(before.sum()), "mp": int((shapely.get_type_id(geoms) == 6).sum()),
           "area": area_before, "overlap_area": area_before - union_before.area}
    started = time.perf_counter()
    ok = legal(roi, roi_tol, geoms)
    out["ms_batch_legal"] = (time.perf_counter() - started) * 1000
    out["illegal"] = int((~ok).sum())
    for key, kwargs in (("cov_valid", {}), ("cov_valid_edges", {"validate_edges": True})):
        try:
            out[key] = bool(np.all(shapely.coverage_is_valid(geoms, **kwargs)))
        except Exception as error:  # noqa: BLE001
            out[key] = "err:" + type(error).__name__
    complement = roi.difference(union_before)
    out["complement_ratio"] = complement.area / roi.area if roi.area else None
    modes = {"all": (geoms, True), "interior": (geoms, False)}
    if not complement.is_empty:
        modes["complement"] = (np.append(geoms, complement), False)
    parameters = inspect.signature(shapely.coverage_simplify).parameters
    for mode, (array, simplify_boundary) in modes.items():
        kwargs = {"simplify_boundary": simplify_boundary} if "simplify_boundary" in parameters else {}
        try:
            started = time.perf_counter()
            simplified = shapely.coverage_simplify(array, SIMPLIFY_TOLERANCE, **kwargs)
            elapsed = (time.perf_counter() - started) * 1000
            simplified_features = simplified[:count]
            valid = shapely.is_valid(simplified_features)
            legal_features = legal(roi, roi_tol, simplified_features)
            after = shapely.get_num_coordinates(simplified_features)
            changed = after != before
            try:
                coverage_after = bool(np.all(shapely.coverage_is_valid(simplified)))
            except Exception:  # noqa: BLE001
                coverage_after = None
            out[mode] = {
                "ms": elapsed, "changed": int(changed.sum()), "legal_changed": int((changed & legal_features).sum()),
                "outside": int((changed & valid & ~legal_features).sum()), "invalid": int((~valid).sum()),
                "positions_after": int(after.sum()), "area_after": float(shapely.area(simplified_features).sum()),
                "union_area_after": float(shapely.union_all(simplified_features).area), "cov_valid_after": coverage_after,
            }
        except Exception as error:  # noqa: BLE001
            out[mode] = {"err": type(error).__name__ + ":" + str(error)[:80]}
    started = time.perf_counter()
    shapely.simplify(geoms, SIMPLIFY_TOLERANCE, preserve_topology=True)
    out["ms_batch_simplify"] = (time.perf_counter() - started) * 1000
    return json.dumps(out, separators=(",", ":"))


def main():
    production, outdir = sys.argv[1:3]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    os.makedirs(outdir, exist_ok=True)
    info = {"shapely": shapely.__version__, "geos": shapely.geos_version_string, "python": sys.version.split()[0],
            "coverage_simplify": str(inspect.signature(shapely.coverage_simplify)),
            "coverage_is_valid": str(inspect.signature(shapely.coverage_is_valid))}
    print(json.dumps(info, ensure_ascii=False))
    lines = [line for line in open(production, encoding="utf-8") if line.startswith('{"result_id"')]
    started = time.perf_counter()
    rows = []
    with Pool(workers) as pool, open(os.path.join(outdir, "shapely2-results.ndjson"), "w", encoding="utf-8") as out:
        for index, line in enumerate(pool.imap_unordered(process, lines, chunksize=4), 1):
            if line is None:
                continue
            rows.append(json.loads(line))
            out.write(line + "\n")
            if index % 500 == 0:
                print(f"  {index}/{len(lines)} results, {time.perf_counter() - started:.0f}s", file=sys.stderr, flush=True)
    summary = dict(info)
    summary["results"] = len(rows)
    summary["features"] = sum(row["n"] for row in rows)
    summary["positions"] = sum(row["positions"] for row in rows)
    summary["illegal_original"] = sum(row["illegal"] for row in rows)
    for key in ("cov_valid", "cov_valid_edges"):
        summary[key] = {str(value): sum(1 for row in rows if row.get(key) == value) for value in set(row.get(key) for row in rows)}
    overlapping = [row["overlap_area"] for row in rows if row["overlap_area"] > 1e-14]
    summary["results_with_overlap"] = len(overlapping)
    summary["max_overlap_m2"] = max(overlapping) * 111320.0 ** 2 if overlapping else 0
    ratios = sorted(row["complement_ratio"] for row in rows if row["complement_ratio"] is not None)
    summary["complement_ratio"] = {"p50": percentile(ratios, 0.5), "p90": percentile(ratios, 0.9), "max": round(ratios[-1], 6) if ratios else None}
    for mode in ("all", "interior", "complement"):
        good = [row[mode] for row in rows if mode in row and "err" not in row[mode]]
        errors = sum(1 for row in rows if mode in row and "err" in row[mode])
        changed = sum(item["changed"] for item in good)
        summary[mode] = {
            "results": len(good), "errors": errors, "changed": changed,
            "legal_changed": sum(item["legal_changed"] for item in good),
            "outside": sum(item["outside"] for item in good), "invalid": sum(item["invalid"] for item in good),
            "outside_rate": sum(item["outside"] for item in good) / changed if changed else None,
            "invalid_rate": sum(item["invalid"] for item in good) / changed if changed else None,
            "positions_before": sum(row["positions"] for row in rows if mode in row and "err" not in row[mode]),
            "positions_after": sum(item["positions_after"] for item in good),
            "area_before": sum(row["area"] for row in rows if mode in row and "err" not in row[mode]),
            "area_after": sum(item["area_after"] for item in good),
            "cov_valid_after": {str(value): sum(1 for item in good if item["cov_valid_after"] == value) for value in set(item["cov_valid_after"] for item in good)},
            "ms_per_result": {key: percentile([item["ms"] for item in good], float(key[1:]) / 100) for key in ("p50", "p95", "p99")},
            "ms_max": round(max(item["ms"] for item in good), 1) if good else None,
        }
    summary["batch_ms_per_result"] = {
        "legal": {key: percentile([row["ms_batch_legal"] for row in rows], float(key[1:]) / 100) for key in ("p50", "p95", "p99")},
        "simplify": {key: percentile([row["ms_batch_simplify"] for row in rows], float(key[1:]) / 100) for key in ("p50", "p95", "p99")},
        "legal_total_s": round(sum(row["ms_batch_legal"] for row in rows) / 1000, 2),
        "simplify_total_s": round(sum(row["ms_batch_simplify"] for row in rows) / 1000, 2),
    }
    summary["seconds"] = round(time.perf_counter() - started, 1)
    open(os.path.join(outdir, "shapely2-summary.json"), "w", encoding="utf-8").write(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
