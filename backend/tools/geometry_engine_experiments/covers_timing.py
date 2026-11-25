# -*- coding: utf-8 -*-
"""量普通谓词与 prepared 谓词做 covers 的单次耗时（按图斑坐标数分档），并导出那个结论相反的图斑做夹具。
用法（一次性容器内）：python covers_timing.py <production-results.ndjson> <fixture-out.json>"""
import json
import sys
import time
import warnings

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely.geometry import shape  # noqa: E402
from shapely.prepared import prep  # noqa: E402

TOL = 1e-9
plain, prepared = [], []
mismatches = []
exported = False
count = 0
for line in open(sys.argv[1], encoding="utf-8"):
    if not line.startswith('{"result_id"'):
        continue
    result = json.loads(line)
    roi = shape(result["roi"])
    buffered = roi.buffer(TOL)
    strict_p, tolerant_p = prep(roi), prep(buffered)
    for feature in result["features"]:
        geometry = shape(feature["geometry"])
        if not geometry.is_valid:
            continue
        count += 1
        started = time.perf_counter()
        a = roi.covers(geometry) or buffered.covers(geometry)
        plain_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        b = strict_p.covers(geometry) or tolerant_p.covers(geometry)
        prepared_ms = (time.perf_counter() - started) * 1000
        plain.append((plain_ms, len(feature["geometry"]["coordinates"][0]) if feature["geometry"]["type"] == "Polygon" else 0))
        prepared.append(prepared_ms)
        if a != b:
            mismatches.append((result["result_id"], feature["fid"]))
            if not exported:
                json.dump({"result_id": result["result_id"], "feature_id": feature["fid"], "roi": result["roi"], "geometry": feature["geometry"]},
                          open(sys.argv[2], "w", encoding="utf-8"))
                exported = True
    if count > 40000:
        break


def stats(values):
    ordered = sorted(values)
    return {f"p{int(f * 100)}": round(ordered[min(len(ordered) - 1, int(f * len(ordered)))], 4) for f in (0.5, 0.9, 0.99)} | {"max": round(ordered[-1], 3)}


print(json.dumps({"features": count, "plain_ms": stats([v for v, _ in plain]), "prepared_ms": stats(prepared),
                  "original_feature_mismatches": mismatches[:5]}))
