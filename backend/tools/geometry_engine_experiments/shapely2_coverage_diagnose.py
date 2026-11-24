# -*- coding: utf-8 -*-
"""诊断：GEOS 的 coverage_is_valid 为什么把绝大多数生产成果判为「不是有效 coverage」。
2.1.2 的 coverage_is_valid 对整份集合返回一个布尔值；coverage_invalid_edges 逐个图斑给出不满足 coverage 要求的边。
对带无效边的图斑，看它与相邻图斑的实际关系：相交面积、共边、共边端点是不是双方的顶点（T 形接合）。"""
import json
import sys
import warnings

import numpy as np
import shapely
from shapely.geometry import shape

warnings.simplefilter("ignore")
sys.stdout.reconfigure(encoding="utf-8")
print("shapely", shapely.__version__, "geos", shapely.geos_version_string)
with open("/work/out/shapely2/shapely2-results.ndjson", encoding="utf-8") as handle:
    flagged = [json.loads(line)["r"] for line in handle if '"cov_valid":false' in line][:40]
wanted = set(flagged)
totals = {"results": 0, "geoms": 0, "with_invalid_edges": 0, "pairs": 0, "overlap_area": 0, "t_junction": 0, "shared_clean": 0, "point_touch": 0, "other": 0}
examples = 0
for line in open("/work/production-results.ndjson", encoding="utf-8"):
    if not line.startswith('{"result_id"'):
        continue
    result = json.loads(line)
    if result["result_id"] not in wanted:
        continue
    geoms = np.array([shape(feature["geometry"]) for feature in result["features"]], dtype=object)
    invalid_edges = shapely.coverage_invalid_edges(geoms)
    bad = [index for index, item in enumerate(invalid_edges) if item is not None]
    totals["results"] += 1
    totals["geoms"] += len(geoms)
    totals["with_invalid_edges"] += len(bad)
    tree = shapely.STRtree(geoms)
    if examples < 3:
        print(f"\nresult {result['result_id']} (project {result['project_id']}): features={len(geoms)} with_invalid_edges={len(bad)}")
    for index in bad[:6]:
        polygon = geoms[index]
        edges = invalid_edges[index]
        edge_length = edges.length if edges is not None else 0
        for other in tree.query(polygon, predicate="intersects"):
            if other == index:
                continue
            neighbour = geoms[other]
            inter = polygon.intersection(neighbour)
            totals["pairs"] += 1
            if inter.area > 1e-16:
                kind = "overlap_area"
            elif inter.length == 0:
                kind = "point_touch"
            else:
                lines = [inter] if inter.geom_type == "LineString" else [g for g in getattr(inter, "geoms", []) if g.geom_type == "LineString"]
                junction = False
                neighbour_vertices = shapely.points(list(neighbour.exterior.coords) + [c for ring in neighbour.interiors for c in list(ring.coords)])
                for part in lines:
                    ends = shapely.points([part.coords[0], part.coords[-1]])
                    if min(float(np.min(shapely.distance(end, neighbour_vertices))) for end in (ends[0], ends[1])) > 1e-15:
                        junction = True
                kind = "t_junction" if junction else "shared_clean"
            totals[kind] += 1
            if examples < 3:
                print(f"   geom #{index} (positions={shapely.get_num_coordinates(polygon)}, invalid_edge_length={edge_length:.3e}, boundary={polygon.length:.3e}) "
                      f"& #{other} (edges invalid={invalid_edges[other] is not None}) -> {kind} ({inter.geom_type}, area={inter.area:.2e}, length={inter.length:.3e})")
    examples += 1
print("\nTOTALS", json.dumps(totals))
