# -*- coding: utf-8 -*-
"""定稿回归补充：被引擎接受的简化结果，用叠加运算（difference）算出的 ROI 外面积有多大。
evaluate 用的是拓扑谓词（covers，带 1e-9 度数值容差）；叠加运算是 GEOS 里另一条代码路径，
这里量出「谓词说合法、叠加说有 ROI 外面积」的那部分到底有多大。
用法（一次性容器内）：python final_overlay_measure.py <production-results.ndjson> <out.json> [workers]"""
import json
import math
import os
import sys
import time
import warnings
from multiprocessing import Pool

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely.geometry import shape  # noqa: E402

from applications.project_hub import geometry_engine as engine  # noqa: E402


def process(line):
    result = json.loads(line)
    roi = shape(result["roi"])
    try:
        context = engine.RoiContext(roi)
    except ValueError:
        return []
    values = []
    for feature in result["features"]:
        outcome = engine.simplify(context, feature["geometry"])
        if outcome["outcome"] != engine.CHANGED:
            continue
        candidate = shape(outcome["geometry"])
        try:
            outside = candidate.difference(roi)
        except Exception:  # noqa: BLE001
            continue
        values.append(outside.area)
    return values


def main():
    production, output = sys.argv[1:3]
    workers = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    lines = [line for line in open(production, encoding="utf-8") if line.startswith('{"result_id"')]
    started = time.perf_counter()
    areas = []
    with Pool(workers) as pool:
        for values in pool.imap_unordered(process, lines, chunksize=4):
            areas.extend(values)
    positive = sorted(value for value in areas if value > 0)
    square = engine.DEGREES_TO_METRES ** 2

    def percentile(fraction):
        return positive[min(len(positive) - 1, int(math.ceil(fraction * len(positive))))] if positive else None

    report = {
        "changed": len(areas), "overlay_outside_nonzero": len(positive),
        "share": round(len(positive) / len(areas), 4) if areas else None,
        "outside_m2": {"p50": percentile(0.5) * square, "p99": percentile(0.99) * square, "max": positive[-1] * square if positive else None},
        "total_m2": round(sum(positive) * square, 6),
        "numeric_tolerance_band_m2_reference": round(2 * engine.NUMERIC_TOLERANCE_DEGREES * 1e-3 * engine.DEGREES_TO_METRES ** 2, 9),
        "seconds": round(time.perf_counter() - started, 1),
    }
    for key in ("p50", "p99", "max"):
        report["outside_m2"][key] = report["outside_m2"][key] and f"{report['outside_m2'][key]:.3e}"
    open(output, "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
