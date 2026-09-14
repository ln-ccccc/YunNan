import numbers
from typing import Dict, List, Optional, Tuple

try:
    from shapely.geometry import box as _shapely_box
    from shapely.strtree import STRtree as _STRtree
except Exception:
    _shapely_box = None
    _STRtree = None


def geom_bbox_4326(geom_4326: Dict) -> Optional[Tuple[float, float, float, float]]:
    gtype = (geom_4326.get("type") or "").strip()
    coords = geom_4326.get("coordinates")
    if not coords:
        return None

    xs: List[float] = []
    ys: List[float] = []

    def push_ring(ring):
        for pt in ring or []:
            if not pt or len(pt) < 2:
                continue
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))

    if gtype == "Polygon":
        for ring in coords:
            push_ring(ring)
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                push_ring(ring)
    else:
        return None

    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def filter_features_by_bounds(
    features: List[Tuple[str, Dict]],
    bounds_4326: Tuple[float, float, float, float],
) -> List[Tuple[str, Dict]]:
    if not features:
        return features

    if _shapely_box is None or _STRtree is None:
        q_minx, q_miny, q_maxx, q_maxy = bounds_4326
        out = []
        for fid, geom in features:
            bb = geom_bbox_4326(geom)
            if not bb:
                continue
            minx, miny, maxx, maxy = bb
            if maxx < q_minx or q_maxx < minx or maxy < q_miny or q_maxy < miny:
                continue
            out.append((fid, geom))
        return out

    boxes = []
    idx_to_feature = []
    for fid, geom in features:
        bb = geom_bbox_4326(geom)
        if not bb:
            continue
        boxes.append(_shapely_box(*bb))
        idx_to_feature.append((fid, geom))

    if not boxes:
        return features

    tree = _STRtree(boxes)
    q = _shapely_box(*bounds_4326)
    candidates = tree.query(q)

    # 命中的候选统一收集为索引集合，再按输入顺序输出：
    # 1) STRtree.query 的返回顺序不构成契约，按输入顺序输出与无 shapely 的
    #    回退路径行为一致（回退路径天然保持输入顺序）；
    # 2) 不按 fid 去重——同 fid 的多个 Placemark 是同一场地的多个图斑，
    #    全部保留交给 tiles 层做 variant 区分（历史行为在此静默丢弃第二个图斑）。
    hit_indices = set()
    if len(candidates) > 0 and isinstance(candidates[0], numbers.Integral):
        hit_indices = {int(i) for i in candidates}
    else:
        geom_id_to_index = {id(g): i for i, g in enumerate(boxes)}
        hit_indices = {
            geom_id_to_index[id(g)] for g in candidates if id(g) in geom_id_to_index
        }

    return [idx_to_feature[i] for i in sorted(hit_indices)]
