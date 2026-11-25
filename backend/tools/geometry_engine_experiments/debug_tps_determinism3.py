# -*- coding: utf-8 -*-
"""GEOS 3.10.3 简化输出是否依赖输入对象的内存地址：每次用新建的等价对象调用，统计不同输出的个数。
并在全量数据上统计有多少图斑会出现「同一输入、不同输出」。
用法（一次性容器内）：python debug_tps_determinism3.py <out/edge-feature.json> [production-results.ndjson] [最多检查多少条成果]"""
import hashlib
import json
import sys
import warnings

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from shapely import wkb  # noqa: E402
from shapely.geometry import shape  # noqa: E402


def digest(geometry):
    return hashlib.sha256(wkb.dumps(geometry)).hexdigest()[:12]


case = json.load(open(sys.argv[1], encoding="utf-8"))
for preserve in (True, False):
    seen = {}
    for _ in range(200):
        key = digest(shape(case["geometry"]).simplify(1e-5, preserve_topology=preserve))
        seen[key] = seen.get(key, 0) + 1
    print(f"fresh object each call, preserve_topology={preserve}: distinct = {len(seen)} {seen}")

if len(sys.argv) > 3:
    limit = int(sys.argv[3])
    unstable = {"True": 0, "False": 0}
    checked = 0
    results = 0
    for line in open(sys.argv[2], encoding="utf-8"):
        if not line.startswith('{"result_id"'):
            continue
        results += 1
        if results > limit:
            break
        result = json.loads(line)
        for feature in result["features"]:
            checked += 1
            geometry = feature["geometry"]
            for preserve in (True, False):
                outputs = {digest(shape(geometry).simplify(1e-5, preserve_topology=preserve)) for _ in range(3)}
                if len(outputs) > 1:
                    unstable[str(preserve)] += 1
    print(f"checked {checked} features in {min(results, limit)} results: unstable TPS={unstable['True']} DP={unstable['False']}")
