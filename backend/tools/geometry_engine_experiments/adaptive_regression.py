# -*- coding: utf-8 -*-
"""自适应简化容差的定稿回归（2026-11-24）：对只读导出的生产成果做离线回归，结论用于验收报告。

与上一轮 final_regression.py 的区别（都针对那次报告暴露的问题）：
  - 每个图斑只调一次 simplify。全部指标——计数、面积、对称差、贴边、重叠与覆盖、地类汇总、耗时——
    都从这一次返回的几何上算，候选几何以 WKB 落盘留档（candidates.ndjson）。GEOS 3.10.3 拓扑保持
    简化的不确定性另由 --repeat 单独度量，不混进正式指标（上一轮 137,040 与 137,042 的差异就是这么来的）；
  - 像元尺寸走生产代码：只读挂载生产存储，经 classification_results.simplify_scale 读真实 label 的头信息，
    由 PROJ 换算成米。

被测源码由运行器从 git archive 导出到仓库外、只读挂载到 /app/backend，import 到的就是该 commit 的内容。

每条成果的流程：
  1. 逐图斑简化并计时。changed 的结果做不依赖引擎判定代码的交叉验证：改造前保存规则的原表达式、
     叠加运算的 ROI 外面积、PROJ 等积投影下逐部件的精确面积变化与对称差、贴矿山边界的接触长度与最大偏离；
  2. 重复简化：对 changed 的结果再简化，最多到第 5 次，记录相对原始几何的累计面积变化与对称差；
  3. 整条成果的图斑并集：简化前后图斑之间的重叠面积、被图斑覆盖的面积。

用法（一次性容器内；/project_storage 只读挂载）：
  python adaptive_regression.py <production-results.ndjson> <label-meta.ndjson> <outdir> <all|样本 json> [workers] [--repeat]
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
from types import SimpleNamespace

from rasterio.warp import transform_geom
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.prepared import prep

sys.path.insert(0, "/app/backend")
warnings.simplefilter("ignore")

from applications.project_hub import classification_results as results_service  # noqa: E402
from applications.project_hub import geometry_engine as engine  # noqa: E402

TOL = engine.NUMERIC_TOLERANCE_DEGREES
BOUNDARY_FLAG_DEGREES = 1e-7  # 「贴边界」类目的判定阈值（约 1 厘米），只用于分类统计
BIG = 5000
REPEAT_PASSES = 5  # 含第一次，一共简化几次
LABELS = {}
REPEAT = False


def parts_of(face):
    return [face] if face.geom_type == "Polygon" else list(face.geoms)


def structure(face):
    """(部件数, 孔数)。"""
    parts = parts_of(face)
    return len(parts), sum(len(part.interiors) for part in parts)


def part_shape_change(original, candidate):
    """逐部件的对称差占比的最大值。"""
    return max(old.symmetric_difference(new).area / old.area for old, new in zip(parts_of(original), parts_of(candidate)))


def literal_save_rule(roi, tolerant_roi, geometry):
    """改造前保存接口的表达式，原样照抄，不走引擎代码。"""
    return geometry.is_valid and (roi.covers(geometry) or tolerant_roi.covers(geometry))


def equal_area_drift(original, candidate, longitude, latitude):
    """PROJ 等积投影（以图斑中心为原点）下，逐部件的面积相对变化与对称差占比的最大值。"""
    target = f"+proj=laea +lat_0={latitude:.10f} +lon_0={longitude:.10f} +datum=WGS84 +units=m +no_defs"
    left = shape(transform_geom("EPSG:4326", target, engine.to_geojson(original), precision=-1))
    right = shape(transform_geom("EPSG:4326", target, engine.to_geojson(candidate), precision=-1))
    return (
        max(abs(new.area - old.area) / old.area for old, new in zip(parts_of(left), parts_of(right))),
        part_shape_change(left, right),
    )


def distribution(values):
    if not values:
        return {"p50": None, "p90": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def at(fraction):
        return ordered[min(len(ordered) - 1, max(0, int(math.ceil(fraction * len(ordered))) - 1))]

    return {"p50": round(at(0.50), 6), "p90": round(at(0.90), 6), "p95": round(at(0.95), 6),
            "p99": round(at(0.99), 6), "max": round(ordered[-1], 6)}


def process(line):
    result = json.loads(line)
    result_id = result["result_id"]
    roi = shape(result["roi"])
    context = engine.RoiContext(roi)
    tolerant_roi = roi.buffer(TOL)
    boundary_band = roi.boundary.buffer(BOUNDARY_FLAG_DEGREES)
    boundary_prepared = prep(boundary_band)
    # 生产代码路径：读真实 label 的头信息 → PROJ 换算 → 按成果缓存。成果对象只用到这三个属性
    scale = results_service.simplify_scale(SimpleNamespace(
        id=result_id, label_path=LABELS[result_id]["label_path"],
        roi_geometry_json=json.dumps(result["roi"], ensure_ascii=False, separators=(",", ":")),
    ))
    square_metres = scale.metres_per_degree_lon * scale.metres_per_degree_lat
    pixel = scale.pixel_metres
    originals, replaced, rows, candidates = [], [], [], []
    class_totals = {}
    for index, feature in enumerate(result["features"]):
        geometry = feature["geometry"]
        parsed = shape(geometry)
        count = engine.position_count(parsed)
        part_count, hole_count = structure(parsed)
        touching = bool(boundary_prepared.intersects(parsed.boundary))
        started = time.perf_counter()
        outcome = engine.simplify(context, geometry, scale=scale)
        elapsed = (time.perf_counter() - started) * 1000
        area_m2 = parsed.area * square_metres
        pixels = area_m2 / (pixel * pixel) if pixel else None
        row = {
            "r": result_id, "i": index, "fid": feature["fid"], "cls": feature["cls"], "src": feature["source"],
            "n": count, "a": parsed.area, "p": parsed.length, "k": square_metres, "ml": scale.metres_per_degree_lat,
            "px": pixel, "bt": int(touching), "mp": int(geometry["type"] == "MultiPolygon"),
            "rings": part_count + hole_count, "big": int(count > BIG),
            "am": area_m2, "apx": pixels,  # 面积（平方米）、折合多少个像元
            # 狭窄细长：平均宽度不到 12 个像元，且不是小图斑
            "narrow": bool(pixel and pixels and pixels >= 200
                           and 2 * area_m2 / (parsed.length * math.sqrt(square_metres)) < 12 * pixel),
            "o": outcome["outcome"], "ms": round(elapsed, 4),
        }
        if REPEAT:
            again = engine.simplify(context, geometry, scale=scale)
            row["nd"] = int(again != outcome)
        total = class_totals.setdefault(feature["cls"], {"a0": 0.0, "a1": 0.0, "abs": 0.0, "features": 0})
        total["features"] += 1
        total["a0"] += parsed.area
        area_after = parsed.area
        originals.append(parsed)
        if outcome["outcome"] == engine.CHANGED:
            candidate = shape(outcome["geometry"])
            # ---- 正式指标：全部从这一个候选几何上算 ----
            initial = engine.initial_tolerance(parsed, scale)
            row.update({
                "n2": engine.position_count(candidate), "tol": outcome["tolerance"], "t0": initial,
                "lvl": int(round(math.log2(initial / outcome["tolerance"]))) if outcome["tolerance"] < initial else 0,
                "ae": (candidate.area - parsed.area) / parsed.area, "se": part_shape_change(parsed, candidate),
                # ---- 交叉验证（都不走引擎的判定代码）----
                "x_literal": int(literal_save_rule(roi, tolerant_roi, candidate)),
                "x_out": candidate.difference(roi).area * square_metres,
                "x_topo": int(structure(candidate) == (part_count, hole_count) and candidate.geom_type == parsed.geom_type),
                "x_vertices": int({
                    (x, y) for part in parts_of(candidate) for ring in [part.exterior, *part.interiors] for x, y in ring.coords
                } <= {
                    (x, y) for part in parts_of(parsed) for ring in [part.exterior, *part.interiors] for x, y in ring.coords
                }),
            })
            point = parsed.representative_point()
            row["x_area"], row["x_shape"] = equal_area_drift(parsed, candidate, point.x, point.y)
            if touching:
                row["c0"] = parsed.boundary.intersection(boundary_band).length
                row["c1"] = candidate.boundary.intersection(boundary_band).length
                row["hd"] = parsed.boundary.hausdorff_distance(candidate.boundary) * scale.metres_per_degree_lat
            # ---- 重复简化：第 2 ~ 5 次 ----
            current = outcome["geometry"]
            area_after = candidate.area
            row["rp"] = 0
            for _ in range(REPEAT_PASSES - 1):
                step = engine.simplify(context, current, scale=scale)
                if step["outcome"] != engine.CHANGED:
                    break
                current = step["geometry"]
                again = shape(current)
                area_after = again.area
                row["rp"] += 1
                row.setdefault("repeat", []).append([
                    round(abs(again.area - parsed.area) / parsed.area, 6), round(part_shape_change(parsed, again), 6),
                    engine.position_count(again),
                ])
            replaced.append(candidate)
            candidates.append((index, candidate.wkb_hex))
        else:
            replaced.append(parsed)
            if outcome["outcome"] == engine.REJECTED:
                code = outcome["reason"]["code"]
                row["code"] = {"OUTSIDE_ROI": "O", "INVALID_GEOMETRY": "I"}.get(code, code)
        total["a1"] += area_after
        total["abs"] += abs(area_after - parsed.area)
        rows.append(row)
    # 图斑之间的重叠、被图斑覆盖的面积：整条成果的图斑并集，简化前后各算一次
    started = time.perf_counter()
    union_before = unary_union(originals) if originals else None
    union_after = unary_union(replaced) if replaced else None
    summary = {
        "r": result_id, "project": result["project_id"], "features": len(rows), "pixel": pixel,
        "ms_union": round((time.perf_counter() - started) * 1000, 3),
        "overlap_before_m2": (sum(item.area for item in originals) - union_before.area) * square_metres if originals else None,
        "overlap_after_m2": (sum(item.area for item in replaced) - union_after.area) * square_metres if replaced else None,
        "covered_before_m2": union_before.area * square_metres if union_before is not None and not union_before.is_empty else 0.0,
        "covered_after_m2": union_after.area * square_metres if union_after is not None and not union_after.is_empty else 0.0,
        "classes": {str(key): {"a0": value["a0"] * square_metres, "a1": value["a1"] * square_metres,
                               "abs": value["abs"] * square_metres, "features": value["features"]}
                    for key, value in sorted(class_totals.items())},
    }
    return summary, rows, candidates, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def init_worker(labels, repeat):
    global LABELS, REPEAT
    LABELS, REPEAT = labels, repeat


def summarize(summaries, rows, nondeterministic):
    strata = [
        ("全部", lambda row: True),
        ("面积 <10 m²", lambda row: row["am"] < 10), ("面积 10–50 m²", lambda row: 10 <= row["am"] < 50),
        ("面积 50–100 m²", lambda row: 50 <= row["am"] < 100), ("面积 100–1000 m²", lambda row: 100 <= row["am"] < 1000),
        ("面积 1000–10000 m²", lambda row: 1000 <= row["am"] < 10000), ("面积 ≥10000 m²", lambda row: row["am"] >= 10000),
        ("像元 <0.1 m", lambda row: row["px"] is not None and row["px"] < 0.1),
        ("像元 0.1–0.3 m", lambda row: row["px"] is not None and 0.1 <= row["px"] < 0.3),
        ("像元 0.3–0.5 m", lambda row: row["px"] is not None and 0.3 <= row["px"] < 0.5),
        ("像元 0.5–0.7 m", lambda row: row["px"] is not None and 0.5 <= row["px"] < 0.7),
        ("像元 ≥0.7 m", lambda row: row["px"] is not None and row["px"] >= 0.7),
        ("像元未知（回退）", lambda row: row["px"] is None),
        ("贴矿山边界", lambda row: row["bt"] == 1), ("不贴边界", lambda row: row["bt"] == 0),
        ("MultiPolygon", lambda row: row["mp"] == 1), ("带孔", lambda row: row["rings"] > 1),
        ("道路（地类 3）", lambda row: row["cls"] == 3), ("狭窄细长", lambda row: row["narrow"]),
        ("超限（>5000 坐标）", lambda row: row["big"] == 1), ("人工修订来源", lambda row: row["src"] == "manual"),
    ]
    table = {}
    for name, predicate in strata:
        subset = [row for row in rows if predicate(row)]
        if not subset:
            continue
        changed = [row for row in subset if row["o"] == engine.CHANGED]
        before = sum(row["n"] for row in subset)
        table[name] = {
            "count": len(subset), "changed": len(changed),
            "unchanged": sum(1 for row in subset if row["o"] == engine.UNCHANGED),
            "rejected": sum(1 for row in subset if row["o"] == engine.REJECTED),
            "rejected_outside": sum(1 for row in subset if row.get("code") == "O"),
            "rejected_invalid": sum(1 for row in subset if row.get("code") == "I"),
            "rejected_drift": sum(1 for row in subset if row.get("code") == engine.SHAPE_DRIFT),
            "compression_all": round(1 - sum(row.get("n2", row["n"]) for row in subset) / before, 4) if before else None,
            "compression_changed": (round(1 - sum(row["n2"] for row in changed) / sum(row["n"] for row in changed), 4)
                                    if changed else None),
            "area_change": distribution([abs(row["ae"]) for row in changed]),
            "shape_change": distribution([row["se"] for row in changed]),
            "levels": [sum(1 for row in changed if row["lvl"] == level) for level in range(len(engine.SIMPLIFY_RETRY_SCALES))],
        }
    changed = [row for row in rows if row["o"] == engine.CHANGED]
    touching = [row for row in changed if row["bt"]]
    classes = {}
    for summary in summaries:
        for key, value in summary["classes"].items():
            total = classes.setdefault(key, {"a0": 0.0, "a1": 0.0, "abs": 0.0, "features": 0})
            total["a0"] += value["a0"]
            total["a1"] += value["a1"]
            total["abs"] += value["abs"]
            total["features"] += value["features"]
    # 重复简化：按「这是第几次」分开统计（记录里的第 1 项是第 2 次简化的结果，依此类推）
    repeat_by_pass = {}
    for pass_index in range(1, REPEAT_PASSES - 1):
        records = [row["repeat"][pass_index - 1] for row in changed if len(row.get("repeat", [])) >= pass_index]
        repeat_by_pass[f"pass_{pass_index + 1}"] = {
            "changed_again": len(records),
            "cumulative_area_change": distribution([item[0] for item in records]),
            "cumulative_shape_change": distribution([item[1] for item in records]),
        }
    return {
        "results": len(summaries), "features": len(rows), "positions": sum(row["n"] for row in rows),
        "by_stratum": table,
        "cross_check": {
            "changed": len(changed),
            "literal_rule_accepts": sum(1 for row in changed if row["x_literal"] == 1),
            "overlay_outside_zero": sum(1 for row in changed if row["x_out"] == 0),
            "overlay_outside_m2_max": round(max((row["x_out"] for row in changed), default=0), 6),
            "structure_preserved": sum(1 for row in changed if row["x_topo"] == 1),
            "vertices_subset": sum(1 for row in changed if row["x_vertices"] == 1),
            "exact_area_change": distribution([row["x_area"] for row in changed]),
            "exact_shape_change": distribution([row["x_shape"] for row in changed]),
            "over_threshold": sum(1 for row in changed if row["x_area"] > engine.MAX_SIMPLIFY_AREA_CHANGE + 1e-9
                                  or row["x_shape"] > engine.MAX_SIMPLIFY_SHAPE_CHANGE + 1e-9),
        },
        "boundary_contact": {
            "changed_touching": len(touching),
            "contact_length_kept": (round(sum(row["c1"] for row in touching) / sum(row["c0"] for row in touching), 4)
                                    if touching and sum(row["c0"] for row in touching) else None),
            "detachment_m": distribution([row["hd"] for row in touching]),
            "features_detached_over_10cm": sum(1 for row in touching if row["hd"] > 0.1),
        },
        "overlap_and_coverage": {
            "overlap_before_m2": round(sum(item["overlap_before_m2"] or 0 for item in summaries), 3),
            "overlap_after_m2": round(sum(item["overlap_after_m2"] or 0 for item in summaries), 3),
            "covered_before_m2": round(sum(item["covered_before_m2"] for item in summaries), 3),
            "covered_after_m2": round(sum(item["covered_after_m2"] for item in summaries), 3),
            "coverage_change_by_result_m2": distribution([
                item["covered_after_m2"] - item["covered_before_m2"] for item in summaries]),
        },
        "classes": {key: {"features": value["features"], "before_m2": round(value["a0"], 3),
                          "net_change": round((value["a1"] - value["a0"]) / value["a0"], 6) if value["a0"] else None,
                          "gross_change": round(value["abs"] / value["a0"], 6) if value["a0"] else None}
                    for key, value in sorted(classes.items())},
        "repeat_simplify": repeat_by_pass,
        "timing_ms": {
            "per_feature": distribution([row["ms"] for row in rows]),
            "changed": distribution([row["ms"] for row in changed]),
            "total_s": round(sum(row["ms"] for row in rows) / 1000, 2),
        },
        "geos_nondeterministic": nondeterministic,
    }


def main():
    global REPEAT
    production, meta_path, outdir, selection = sys.argv[1:5]
    rest = [item for item in sys.argv[5:] if not item.startswith("--")]
    workers = int(rest[0]) if rest else 3
    REPEAT = "--repeat" in sys.argv
    os.makedirs(outdir, exist_ok=True)
    labels = {}
    for line in open(meta_path, encoding="utf-8"):
        if line.startswith("{"):
            row = json.loads(line)
            labels[row["id"]] = row
    wanted = None if selection == "all" else set(json.load(open(selection)))
    started = time.perf_counter()
    summaries, all_rows, maxrss, nondeterministic = [], [], 0, 0
    with open(os.path.join(outdir, "features.ndjson"), "w", encoding="utf-8") as features_out, \
            open(os.path.join(outdir, "candidates.ndjson"), "w", encoding="utf-8") as candidates_out, \
            Pool(workers, initializer=init_worker, initargs=(labels, REPEAT)) as pool:
        lines = (line for line in open(production, encoding="utf-8") if line.startswith('{"result_id"'))
        picked = (line for line in lines if wanted is None or int(line.split(",", 1)[0].split(":")[1]) in wanted)
        for done, (summary, rows, candidates, rss) in enumerate(pool.imap_unordered(process, picked, chunksize=2), 1):
            summaries.append(summary)
            all_rows.extend(rows)
            maxrss = max(maxrss, rss)
            nondeterministic += sum(row.get("nd", 0) for row in rows)
            for row in rows:
                features_out.write(json.dumps(row, separators=(",", ":")) + "\n")
            for index, wkb in candidates:
                candidates_out.write(json.dumps({"r": summary["r"], "i": index, "wkb": wkb}, separators=(",", ":")) + "\n")
            if done % 200 == 0:
                print(f"  {done} results, {len(all_rows)} features, {time.perf_counter() - started:.0f}s",
                      file=sys.stderr, flush=True)
    report = summarize(summaries, all_rows, nondeterministic if REPEAT else None)
    report.update({
        "seconds": round(time.perf_counter() - started, 1), "worker_max_rss_mb": maxrss // 1024,
        "engine_sha256": hashlib.sha256(open(engine.__file__, "rb").read()).hexdigest(),
    })
    open(os.path.join(outdir, "summary.json"), "w", encoding="utf-8").write(json.dumps(report, ensure_ascii=False, indent=1))
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
