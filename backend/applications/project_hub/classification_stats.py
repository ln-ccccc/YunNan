# -*- coding: utf-8 -*-
"""分类成果派生统计（2026-10-31 方案 b；2026-11-24 按派生数据自身的性质重构）。

人工修订矢量之后需要两类派生量。两者性质不同，存放位置与一致性保障也不同。

一、逐修订的地类占比 / 绝对面积——便宜（毫秒级），只依赖「一个修订」
    保存修订时与修订快照同批写出
        outputs/classification-results/<result_id>/revisions/<版本号>.stats.json
    （先文件、后提交数据库，与 revisions/<版本号>.geojson 同一顺序、同一失败处置）。
    文件名即来源 (result_id, revision_no)，内容不可变：
      - 没有共享写方，不存在「后一年份冲掉前一年份」；
      - 不需要合并、失效或锁；
      - 推理发布只重建 outputs/inference/，碰不到它。
    读端口径：修订过的年份（修订号 > 0）读修订自己的统计；未修订年份读推理占比
    class_ratio_percent.json（整文件由推理发布重建，是纯推理产物，保存不再改写它）。
    sidecar 缺失（本次改造之前保存的历史修订）时按当前矢量现算——与写 sidecar 是
    同一个纯函数，结果同源。

    历史注记：此前审校占比写进 class_ratio_percent.json 的 revised 块，一份文件两个
    主人（保存与推理发布），要靠行锁 + 按年合并 + 写时校验 + 发布后恢复互相补位，
    读端仍无法判断值是不是现值。旧文件里残留的 revised 块不再被任何读端采信。

二、整矿的变化矩阵——昂贵（栅格化），依赖「一座矿的两期有效成果」
    outputs/change_matrix/<fid>.json 仍是缓存文件，写入规则：
        同一数据库快照内读「有效成果 + 两期矢量」→ 释放连接 → 分块计算
        → 提交前复核有效成果未变 → 原子替换 → 写重算回执
    正确性靠「提交前复核」而不是锁：谁来算、算几次都幂等；复核发现期间又有新修订
    就丢弃本次结果重算。重算不在保存请求里同步等待（大矿山栅格化可达分钟级），而是
    提交到进程内单线程后台队列；读端（溯源）按重算回执判断矩阵是否现值，不是就再补
    一次请求——进程重启丢掉的任务由下一次读取自愈。

「同矿山同年多成果取哪条」只有 select_effective_result_refs 一个实现，溯源、矩阵、
趋势、补算全部共用——规则只要出现第二份，统计就对不上成果。
"""
import hashlib
import json
import logging
import os
import queue
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from flask import current_app
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from applications.kml_roi.change_matrix import CLASS_NAMES
from applications.project_hub._geometry import geometry_area_m2
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)

REVISION_STATS_SCHEMA = 1
REVISION_STATS_FILENAME = "revisions/{}.stats.json"
_AREA_SOURCE_REVISED = "vector_shoelace"
_AREA_SOURCE_INFERENCE = "inference_mask"

# 变化矩阵相对「当前有效成果」的状态（溯源 DTO 的 change_matrix_state）
MATRIX_STATE_INFERENCE = "inference"          # 无人工修订：推理矩阵即正确口径
MATRIX_STATE_REVISED = "revised"              # 已按当前有效矢量重算
MATRIX_STATE_REVISED_EMPTY = "revised_empty"  # 有一期图斑为空：无地类转移数据
MATRIX_STATE_UNAVAILABLE = "unavailable"      # 结构性无法计算（缺 label 等）
MATRIX_STATE_PENDING = "pending"              # 需要重算：后台队列会处理，读端稍后刷新
_MATRIX_STATE_TEXT = {
    MATRIX_STATE_PENDING: "正在按人工修订重算变化矩阵，稍后自动更新",
    MATRIX_STATE_REVISED_EMPTY: "有一期图斑为空，没有地类转移数据",
    MATRIX_STATE_UNAVAILABLE: "变化矩阵无法按审校矢量计算",
}


def _inference_fid_dir(project_id, mine_fid):
    return resolve_storage_path(
        get_storage_root(),
        Path("projects") / str(project_id) / "outputs" / "inference" / str(mine_fid),
    )


def _atomic_write_json(target_dir, filename, payload):
    target_dir.mkdir(parents=True, exist_ok=True)
    temporary = target_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, target_dir / filename)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_file(path):
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_feature_collection(value):
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(value or "null")
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


# ---------------------------------------------------------------------------
# 一、逐修订的地类占比 / 绝对面积
# ---------------------------------------------------------------------------

def compute_vector_class_areas(feature_collection):
    """审校后矢量各类别的绝对面积（球面鞋带 m²，按 class_code 聚合）。

    占比与绝对面积同源。趋势里的地类面积必须用这里的绝对值：占比是「图斑内部
    份额」，拿份额 × 整矿面积会把「只剩一个小水体面」算成整矿皆水。
    """
    areas = {name: 0.0 for name in CLASS_NAMES}
    for feature in (_parse_feature_collection(feature_collection) or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        class_code = properties.get("class_code")
        if not isinstance(class_code, int) or not 0 <= class_code < len(CLASS_NAMES):
            continue
        areas[CLASS_NAMES[class_code]] += geometry_area_m2(feature.get("geometry"))
    return areas


def compute_vector_class_ratio(feature_collection):
    """审校后矢量的地类占比：球面鞋带 m² 按 class_code 聚合，归一化 0-100。

    无有效几何（空集合/全坏要素）返回 None，调用方按「显式空」处理。
    """
    areas = compute_vector_class_areas(feature_collection)
    total = sum(areas.values())
    if total <= 0:
        return None
    return {name: round(areas[name] / total * 100.0, 2) for name in CLASS_NAMES}


def compute_revision_stats(feature_collection):
    """一个修订的派生统计（纯函数）：占比、绝对面积、图斑数。

    显式清空（features=[]）时占比为全 0——人工删光图斑是真实状态，不能回落成别的值。
    """
    collection = _parse_feature_collection(feature_collection) or {}
    features = collection.get("features") or []
    areas = compute_vector_class_areas(collection)
    total = sum(areas.values())
    return {
        "percent": (
            {name: round(areas[name] / total * 100.0, 2) for name in CLASS_NAMES}
            if total > 0
            else {name: 0.0 for name in CLASS_NAMES}
        ),
        "area_m2": {name: round(value, 2) for name, value in areas.items()},
        "feature_count": len(features),
    }


def _revision_stats_path(project_id, result_id, revision_no):
    return resolve_storage_path(
        get_storage_root(),
        Path("projects") / str(project_id) / "outputs" / "classification-results"
        / str(result_id) / REVISION_STATS_FILENAME.format(int(revision_no)),
    )


def write_revision_stats(project_id, result_id, revision_no, feature_collection):
    """把一个修订的派生统计写成 sidecar（原子替换）。返回落盘路径。

    只对「这一条成果的这一个版本」负责：同一个修订号重写得到同样的内容，不同修订号
    互不相干，所以没有任何需要串行化的共享状态。
    """
    payload = compute_revision_stats(feature_collection)
    payload.update({
        "schema": REVISION_STATS_SCHEMA,
        "result_id": int(result_id),
        "revision_no": int(revision_no),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    })
    path = _revision_stats_path(project_id, result_id, revision_no)
    _atomic_write_json(path.parent, path.name, payload)
    return path


def read_revision_stats(project_id, result_id, revision_no):
    """读一个修订的派生统计；不存在、损坏或身份不符（文件名与内容对不上）返回 None。"""
    payload = _read_json_file(_revision_stats_path(project_id, result_id, revision_no))
    if payload is None:
        return None
    if payload.get("result_id") != int(result_id) or payload.get("revision_no") != int(revision_no):
        return None
    if not isinstance(payload.get("percent"), dict) or not isinstance(payload.get("area_m2"), dict):
        return None
    return payload


def _stats_matches_result(payload, feature_count):
    """sidecar 与成果行是否对得上：图斑数一致。

    正常流程下同一版本号只有一个写者（保存的条件更新保证了这一点），sidecar 不可能
    对不上。这里是一道兜底：极端情况下遗留的同版本号孤儿文件（提交结局未知 + 之后
    某次保存的 sidecar 又恰好写失败）内容不同，图斑数大概率不同，宁可多现算一次。"""
    if feature_count is None:
        return True
    return payload.get("feature_count") == int(feature_count)


def resolve_result_stats(project_id, result):
    """一条成果「当前修订」的派生统计：sidecar 优先，缺失或对不上则按当前矢量现算。

    调用方传入的 result 需要有 id / current_revision_no；回退现算时还会读
    current_feature_collection_json。只读，不写文件（GET 路径保持只读）。
    """
    revision_no = int(getattr(result, "current_revision_no", 0) or 0)
    if revision_no > 0:
        stored = read_revision_stats(project_id, result.id, revision_no)
        if stored is not None and _stats_matches_result(stored, getattr(result, "feature_count", None)):
            return stored
    payload = compute_revision_stats(getattr(result, "current_feature_collection_json", None))
    payload.update({
        "schema": REVISION_STATS_SCHEMA, "result_id": int(result.id), "revision_no": revision_no,
    })
    return payload


# ---------------------------------------------------------------------------
# 「同矿山同年哪条成果有效」的唯一规则
# ---------------------------------------------------------------------------

def select_effective_result_refs(rows):
    """同年多成果的选择规则（纯函数）。

    rows 为按 id 降序排列的 (result_id, year, vector_status, revision_no) 四元组；
    返回 {year: (result_id, revision_no)}：同年取 id 最大者，但 vector_failed 的成果
    在同一年存在非 failed 兄弟时让位（一次失败不得抹掉既有好成果）。
    """
    refs_by_year = {}
    for result_id, year, vector_status, revision_no in rows:
        if year in refs_by_year:
            continue
        if vector_status == "vector_failed" and any(
            other_year == year and other_status != "vector_failed"
            for _other_id, other_year, other_status, _other_revision in rows
        ):
            continue
        refs_by_year[year] = (result_id, revision_no)
    return refs_by_year


def _effective_result_refs_by_year(project_id, mine_fid):
    """一座矿山的有效成果引用，只取四个标量列（不把矢量大字段拉进内存）。"""
    from applications.models.classification_result import ClassificationResult

    rows = (
        ClassificationResult.query.filter_by(project_id=project_id, mine_fid=mine_fid)
        .order_by(ClassificationResult.id.desc())
        .with_entities(
            ClassificationResult.id,
            ClassificationResult.year,
            ClassificationResult.vector_status,
            ClassificationResult.current_revision_no,
        )
        .all()
    )
    return select_effective_result_refs([tuple(row) for row in rows])


def effective_result_refs_by_project(project_id):
    """项目内全部矿山的有效成果引用（趋势等整项目口径用，一次五列轻量查询）。

    返回 {mine_fid: {year: (result_id, revision_no)}}。整行加载会连带三个 MEDIUMTEXT
    大字段（项目 10 近 3000 条成果共 1.4GB），这里只取判定所需的列。
    """
    from applications.models.classification_result import ClassificationResult

    rows = (
        ClassificationResult.query.filter_by(project_id=project_id)
        .order_by(ClassificationResult.id.desc())
        .with_entities(
            ClassificationResult.id,
            ClassificationResult.mine_fid,
            ClassificationResult.year,
            ClassificationResult.vector_status,
            ClassificationResult.current_revision_no,
        )
        .all()
    )
    grouped = {}
    for result_id, mine_fid, year, vector_status, revision_no in rows:
        grouped.setdefault(mine_fid, []).append((result_id, year, vector_status, revision_no))
    return {
        mine_fid: select_effective_result_refs(items)
        for mine_fid, items in grouped.items()
    }


def load_revision_stats_by_ref(project_id, ref):
    """按有效成果引用 (result_id, revision_no) 取该修订的派生统计。

    sidecar 优先（只补一次主键查询取图斑数做一致性兜底）；历史修订没有 sidecar 时
    只加载这一条成果的矢量现算，不整表加载。
    """
    from sqlalchemy.orm import load_only

    from applications.models.classification_result import ClassificationResult

    result_id, revision_no = int(ref[0]), int(ref[1] or 0)
    stored = read_revision_stats(project_id, result_id, revision_no)
    columns = [
        ClassificationResult.id,
        ClassificationResult.current_revision_no,
        ClassificationResult.feature_count,
    ]
    if stored is None:
        columns.append(ClassificationResult.current_feature_collection_json)
    row = (
        ClassificationResult.query.options(load_only(*columns))
        .filter_by(id=result_id)
        .first()
    )
    if row is None or int(row.current_revision_no or 0) != revision_no:
        return None
    if stored is not None and _stats_matches_result(stored, row.feature_count):
        return stored
    return resolve_result_stats(project_id, row)


# ---------------------------------------------------------------------------
# 二、整矿变化矩阵：分块累积（内存有界）+ 快照复核 + 后台重算
# ---------------------------------------------------------------------------

def _prepare_year_shapes(feature_collection, grid_crs):
    """审校矢量形状准备：类别码过滤 + 几何变换到网格 CRS（EPSG:4326 入）。

    返回 rasterize 输入 shapes 列表；无有效形状返回 None（该期矢量为空）。
    """
    shapes = []
    for feature in (_parse_feature_collection(feature_collection) or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        class_code = properties.get("class_code")
        geometry = feature.get("geometry")
        if not isinstance(class_code, int) or not 0 <= class_code < len(CLASS_NAMES):
            continue
        if not isinstance(geometry, dict):
            continue
        shapes.append((transform_geom("EPSG:4326", grid_crs, geometry), class_code))
    return shapes or None


def rasterize_year_vector(project_id, mine_fid, year, feature_collection, target_grid=None):
    """审校矢量栅格化（整幅）：类别值 0-5、无效区 255。小网格 / 调试入口。

    target_grid=None 时用该年自身 label.tif 网格；提供 (transform, shape, crs) 时
    强制栅格化到统一网格——跨 CRS/跨分辨率年份的转移矩阵必须同网格对齐，否则逐像素
    对比在空间上不成立。主链路走 _accumulate_review_matrix 的分块实现，内存有界。
    """
    label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year}_label.tif"
    if not label_path.is_file():
        return None
    with rasterio.open(label_path) as dataset:
        grid_transform = target_grid[0] if target_grid else dataset.transform
        grid_shape = target_grid[1] if target_grid else dataset.shape
        grid_crs = target_grid[2] if target_grid else dataset.crs
    shapes = _prepare_year_shapes(feature_collection, grid_crs)
    if shapes is None:
        return None
    return rasterize(
        shapes, out_shape=grid_shape, transform=grid_transform, fill=255, dtype="uint8",
    )


_REVIEW_MATRIX_BLOCK_BUDGET_BYTES = 256 * 1024 * 1024
# 配对子分块：块内配对按行子分块累计，索引数组字节 = 子分块行数 × W × 16（两根 int64），
# 按 64MB 预算反推子分块行数——极宽网格（W=10⁶）自动降到 4 行，配对内存恒有界
_REVIEW_MATRIX_PAIR_SUBCHUNK_BYTES = 64 * 1024 * 1024


def _matrix_block_rows(grid_width, block_bytes=None):
    """审校矩阵分块行数。

    块内栅格化阶段真实存活：两期 uint8 块 2B/px + 有效掩膜 bool 1B/px + rasterize
    每形状临时 uint8 1B/px = 4B/px。预算取名义值一半，给 GDAL 块缓存/几何变换临时/
    矢量对象留余量。行数另受像素总数上限约束（与列窗口联动）。"""
    budget = (_REVIEW_MATRIX_BLOCK_BUDGET_BYTES if block_bytes is None else block_bytes) // 2
    row_bytes = max(1, int(grid_width)) * 4
    return max(1, budget // row_bytes)


def _matrix_window_shape(grid_shape, block_rows=None):
    """审校矩阵二维窗口尺寸（行×列均受限），窗口像素数 ≤ 预算一半。"""
    grid_h, grid_w = int(grid_shape[0]), int(grid_shape[1])
    rows = block_rows if block_rows is not None else _matrix_block_rows(grid_w)
    block_bytes = _REVIEW_MATRIX_BLOCK_BUDGET_BYTES // 2
    cols = max(1, min(grid_w, max(1, block_bytes // (4 * max(1, rows)))))
    return max(1, min(rows, max(1, grid_h))), cols


def _accumulate_review_matrix(old_shapes, new_shapes, grid_transform, grid_shape, class_count, block_rows=None):
    """分块累积审校转移矩阵：行为旧年、列为新年，像素有效当且仅当两期类别码均在
    0..class_count-1（fill=255 无效区不计入）。任意网格单请求内存有界。
    返回 (pixels, total_valid)。"""
    grid_h, grid_w = int(grid_shape[0]), int(grid_shape[1])
    if block_rows is None:
        block_rows = _matrix_block_rows(grid_w)
    block_rows, block_cols = _matrix_window_shape(grid_shape, block_rows)
    pixels = np.zeros((class_count, class_count), dtype=np.int64)
    for row_offset in range(0, grid_h, block_rows):
        rows = min(block_rows, grid_h - row_offset)
        for col_offset in range(0, grid_w, block_cols):
            cols = min(block_cols, grid_w - col_offset)
            # 块变换：整幅 transform 平移 (col_offset, row_offset)，窗口内像素与整幅
            # 栅格化逐元素一致（逐像素几何判定，无跨窗口状态）
            window_transform = grid_transform * Affine.translation(col_offset, row_offset)
            window_shape = (rows, cols)
            old_block = rasterize(
                old_shapes, out_shape=window_shape, transform=window_transform,
                fill=255, dtype="uint8",
            )
            new_block = rasterize(
                new_shapes, out_shape=window_shape, transform=window_transform,
                fill=255, dtype="uint8",
            )
            pair_sub_rows = max(1, _REVIEW_MATRIX_PAIR_SUBCHUNK_BYTES // (max(1, cols) * 16))
            for sub_offset in range(0, rows, pair_sub_rows):
                sub_rows = min(pair_sub_rows, rows - sub_offset)
                old_sub = old_block[sub_offset:sub_offset + sub_rows]
                new_sub = new_block[sub_offset:sub_offset + sub_rows]
                valid = (old_sub < class_count) & (new_sub < class_count)
                if valid.any():
                    np.add.at(
                        pixels,
                        (old_sub[valid].astype(np.int64), new_sub[valid].astype(np.int64)),
                        1,
                    )
            del old_block, new_block
    return pixels, int(pixels.sum())


def _change_matrix_path(project_id, mine_fid):
    return (
        resolve_storage_path(get_storage_root(), Path("projects") / str(project_id) / "outputs")
        / "change_matrix"
        / f"{mine_fid}.json"
    )


def _rebuild_marker_path(project_id, mine_fid):
    """重算回执：记录「最近一次重算针对哪一组有效成果、结果如何、写出的矩阵长什么样」。

    读端据此判断矩阵是不是现值，不必加载矢量、也不必解析矩阵去猜。回执在矩阵文件
    之后写出：中途崩溃则回执偏旧，被判为待重算，而重算幂等。"""
    return _change_matrix_path(project_id, mine_fid).with_name(f".{mine_fid}.rebuild.json")


def _refs_to_json(refs):
    return {
        str(year): [int(result_id), int(revision_no or 0)]
        for year, (result_id, revision_no) in refs.items()
    }


def _sha256_of(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _write_rebuild_marker(project_id, mine_fid, refs, years, outcome, reason=None, has_matrix=False):
    payload = {
        "refs": _refs_to_json(refs),
        "years": [int(year) for year in years],
        "outcome": outcome,
        "reason": reason,
        # 矩阵文件内容摘要：推理发布会整文件覆盖矩阵，摘要对不上即判为待重算
        "matrix_sha256": _sha256_of(_change_matrix_path(project_id, mine_fid)) if has_matrix else None,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    marker_path = _rebuild_marker_path(project_id, mine_fid)
    _atomic_write_json(marker_path.parent, marker_path.name, payload)


def _invalidate_revised_matrix_if_stale(project_id, mine_fid, reason, refs):
    """当前矢量内容已无法构成矩阵时，若现有矩阵仍标记为审校口径，则显式失效：
    旧审校矩阵不得伪装成当前结果。推理口径矩阵来自 label 产物、仍然有效，不受影响。"""
    summary_path = _change_matrix_path(project_id, mine_fid)
    previous = _read_json_file(summary_path) or {}
    if previous.get("basis") != "revised_vector":
        return
    payload = {
        "fid": str(mine_fid),
        "old_year": previous.get("old_year"),
        "new_year": previous.get("new_year"),
        "headers": list(CLASS_NAMES),
        "matrix": [],
        "images": previous.get("images") or {"old": None, "new": None},
        "has_change_matrix": False,
        "data_source": "project_inference",
        "basis": "revised_vector_invalid",
        "invalid_reason": reason,
        "revised_revision_no": {
            str(year): int(refs[year][1] or 0)
            for year in (previous.get("old_year"), previous.get("new_year"))
            if year in refs
        },
    }
    _atomic_write_json(summary_path.parent, summary_path.name, payload)


def _matrix_years(refs):
    """变化矩阵涉及的两个年份：有效成果里最近的两期（不足两期时有多少算多少）。"""
    years = sorted(refs)
    return tuple(years[-2:])


def _has_manual_revision_in_matrix_years(refs):
    """矩阵涉及的年份里是否有人工修订。

    这是"要不要产审校矩阵"的唯一判据，写端与读端必须同用：只有历史年份（不在最近
    两期里）有修订时，矩阵与人工修订无关，推理矩阵就是正确口径。
    """
    return any(int(refs[year][1] or 0) > 0 for year in _matrix_years(refs) if year in refs)


def _rebuild_change_matrix_once(project_id, mine_fid):
    """一次完整的矩阵重算，返回 written / invalidated / skipped / superseded。

    资源槽位（容器级跨进程文件锁）罩住全程，重计算不并发占内存；正确性由最后的
    提交前复核保证，与槽位无关。"""
    from applications.extensions import db
    from applications.models.classification_result import ClassificationResult
    from applications.project_hub.imagery_processing import _recompute_budget_slot

    matrix_path = _change_matrix_path(project_id, mine_fid)
    with _recompute_budget_slot():
        # ① 同一数据库快照内读有效成果与两期矢量，读完即释放连接（重计算期间不占连接）
        refs = _effective_result_refs_by_year(project_id, mine_fid)
        years_sorted = sorted(refs)
        if len(years_sorted) < 2:
            db.session.commit()
            _write_rebuild_marker(project_id, mine_fid, refs, years_sorted, "skipped", "不足两期成果")
            return "skipped"
        if not _has_manual_revision_in_matrix_years(refs):
            # 最近两期有效成果都没有人工修订：推理矩阵就是正确口径，不产审校矩阵
            # （保存一条已被更新成果取代的历史成果会走到这里）
            db.session.commit()
            return "skipped"
        old_year, new_year = years_sorted[-2], years_sorted[-1]
        label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{old_year}_label.tif"
        if not label_path.is_file():
            db.session.commit()
            _write_rebuild_marker(
                project_id, mine_fid, refs, (old_year, new_year), "skipped", f"{old_year} 年 label 缺失"
            )
            return "skipped"
        with rasterio.open(label_path) as dataset:
            target_grid = (dataset.transform, dataset.shape, dataset.crs)
        prepared = []
        try:
            for year in (old_year, new_year):
                result_id, revision_no = refs[year]
                row = ClassificationResult.query.filter_by(id=result_id).first()
                if row is None or int(row.current_revision_no or 0) != int(revision_no or 0):
                    return "superseded"
                shapes = _prepare_year_shapes(row.current_feature_collection_json, target_grid[2])
                if shapes is None:
                    _invalidate_revised_matrix_if_stale(
                        project_id, mine_fid, f"empty_vector_year_{year}", refs
                    )
                    _write_rebuild_marker(
                        project_id, mine_fid, refs, (old_year, new_year), "invalidated",
                        f"{year} 年图斑为空", has_matrix=matrix_path.is_file(),
                    )
                    return "invalidated"
                prepared.append((int(year), shapes, int(revision_no or 0)))
        finally:
            db.session.commit()

        # ② 分块计算（无数据库参与）
        old_year, old_shapes, old_revision = prepared[-2]
        new_year, new_shapes, new_revision = prepared[-1]
        class_count = len(CLASS_NAMES)
        pixels, total_valid = _accumulate_review_matrix(
            old_shapes, new_shapes, target_grid[0], target_grid[1], class_count
        )
        if total_valid <= 0:
            _invalidate_revised_matrix_if_stale(project_id, mine_fid, "no_valid_overlap", refs)
            _write_rebuild_marker(
                project_id, mine_fid, refs, (old_year, new_year), "invalidated", "两期矢量无有效重叠",
                has_matrix=matrix_path.is_file(),
            )
            return "invalidated"

        # ③ 提交前复核：先结束读事务（MySQL RR 下同一事务内重读仍是旧快照），再取最新
        #    有效成果；期间有新修订/新成果落地就丢弃本次结果，由对方触发的那次重算负责
        db.session.commit()
        unchanged = _effective_result_refs_by_year(project_id, mine_fid) == refs
        db.session.commit()
        if not unchanged:
            LOGGER.info(
                "审校矩阵重算期间成果已更新，丢弃本次结果 project_id=%s mine_fid=%s",
                project_id, mine_fid,
            )
            return "superseded"

        # ④ 原子替换矩阵，再写回执
        previous = _read_json_file(matrix_path) or {}
        row_totals = pixels.sum(axis=1, keepdims=True)
        percent = np.divide(
            pixels.astype(np.float64), row_totals,
            out=np.zeros_like(pixels, dtype=np.float64), where=row_totals > 0,
        ) * 100.0
        payload = {
            "fid": str(mine_fid),
            "old_year": old_year,
            "new_year": new_year,
            "headers": list(CLASS_NAMES),
            "matrix": [
                {"label": CLASS_NAMES[index], "values": [round(float(value), 1) for value in percent[index]]}
                for index in range(class_count)
            ],
            "images": previous.get("images") or {"old": None, "new": None},
            "has_change_matrix": True,
            "data_source": "project_inference",
            "basis": "revised_vector",
            "area_source": _AREA_SOURCE_REVISED,
            "revised_revision_no": {str(old_year): old_revision, str(new_year): new_revision},
        }
        _atomic_write_json(matrix_path.parent, matrix_path.name, payload)
        _write_rebuild_marker(project_id, mine_fid, refs, (old_year, new_year), "written", has_matrix=True)
        return "written"


def compute_revised_change_matrix(project_id, mine_fid, max_attempts=3):
    """按当前有效矢量重算一座矿山的审校变化矩阵（幂等）。

    返回 True 表示写出了审校矩阵。复核发现期间成果已更新时会重试（最多 max_attempts）；
    结构性无法计算（不足两期 / 缺 label / 图斑为空）如实返回 False 并留下回执。
    """
    for _attempt in range(max(1, int(max_attempts))):
        try:
            outcome = _rebuild_change_matrix_once(project_id, mine_fid)
        except Exception:  # noqa: BLE001 - 统计派生绝不影响调用方（保存/发布）的主结果
            LOGGER.warning(
                "审校矩阵重算失败 project_id=%s mine_fid=%s", project_id, mine_fid, exc_info=True
            )
            return False
        if outcome != "superseded":
            return outcome == "written"
    LOGGER.warning(
        "审校矩阵重算连续 %s 次被更新后的成果取代，放弃 project_id=%s mine_fid=%s（下次读取会再触发）",
        max_attempts, project_id, mine_fid,
    )
    return False


def evaluate_change_matrix_state(project_id, mine_fid, refs):
    """矩阵相对「当前有效成果」的状态与给用户看的一句话说明。

    refs 为该矿山的有效成果引用 {year: (result_id, revision_no)}。判定只读两个小文件
    （回执 + 矩阵），不加载矢量。
    """
    if len(refs) >= 2 and not _has_manual_revision_in_matrix_years(refs):
        return MATRIX_STATE_INFERENCE, None
    if not any(int(revision_no or 0) > 0 for _result_id, revision_no in refs.values()):
        return MATRIX_STATE_INFERENCE, None
    marker = _read_json_file(_rebuild_marker_path(project_id, mine_fid)) or {}
    if marker.get("refs") != _refs_to_json(refs):
        return MATRIX_STATE_PENDING, _MATRIX_STATE_TEXT[MATRIX_STATE_PENDING]
    if marker.get("matrix_sha256"):
        if _sha256_of(_change_matrix_path(project_id, mine_fid)) != marker.get("matrix_sha256"):
            return MATRIX_STATE_PENDING, _MATRIX_STATE_TEXT[MATRIX_STATE_PENDING]
    outcome = marker.get("outcome")
    if outcome == "written":
        return MATRIX_STATE_REVISED, None
    if outcome == "invalidated":
        return MATRIX_STATE_REVISED_EMPTY, _MATRIX_STATE_TEXT[MATRIX_STATE_REVISED_EMPTY]
    if outcome == "skipped":
        reason = marker.get("reason")
        return MATRIX_STATE_UNAVAILABLE, (
            f"{_MATRIX_STATE_TEXT[MATRIX_STATE_UNAVAILABLE]}（{reason}）" if reason
            else _MATRIX_STATE_TEXT[MATRIX_STATE_UNAVAILABLE]
        )
    return MATRIX_STATE_PENDING, _MATRIX_STATE_TEXT[MATRIX_STATE_PENDING]


class _RebuildQueue:
    """进程内单线程后台重算队列。

    - 守护线程：进程退出即结束，不阻塞 gunicorn worker 回收；
    - 按 (project_id, mine_fid) 合并：同一座矿排队期间再来请求只算一次——正在跑的
      那次若发现成果已变会自己重试，不会漏；
    - 异常隔离：单座矿失败只记日志，线程继续服务；
    - 队列里只有整数键，不持有矢量对象；线程在自己的应用上下文里开会话，随上下文回收。
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._queued = set()
        self._lock = threading.Lock()
        self._thread = None

    def submit(self, app, project_id, mine_fid):
        key = (int(project_id), int(mine_fid))
        with self._lock:
            if key in self._queued:
                return False
            self._queued.add(key)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name="change-matrix-rebuild", daemon=True
                )
                self._thread.start()
        self._queue.put((app, key))
        return True

    @property
    def queued(self):
        with self._lock:
            return set(self._queued)

    def _run(self):
        from applications.extensions import db

        while True:
            item = self._queue.get()
            if item is None:
                return
            app, key = item
            try:
                with app.app_context():
                    try:
                        compute_revised_change_matrix(*key)
                    finally:
                        db.session.remove()
            except Exception:  # noqa: BLE001 - 后台线程不得被单个任务杀死
                LOGGER.exception("审校矩阵后台重算异常 project_id=%s mine_fid=%s", *key)
            finally:
                with self._lock:
                    self._queued.discard(key)


_REBUILD_QUEUE = _RebuildQueue()


def request_change_matrix_rebuild_for_result(project_id, mine_fid, year, result_id, background=None):
    """一条成果保存修订后请求重算其矿山的矩阵；该成果不是当年有效成果时不请求。

    同年已有更新成果时，较旧成果的修订不参与任何按矿山聚合的视图（溯源/矩阵/
    趋势只认有效成果），重算只会白跑一遍。返回是否提交了请求。
    """
    refs = _effective_result_refs_by_year(project_id, mine_fid)
    if refs.get(year, (None, None))[0] != int(result_id):
        LOGGER.info(
            "成果 %s 不是 %s 年的有效成果，其修订不触发审校矩阵重算 mine_fid=%s",
            result_id, year, mine_fid,
        )
        return False
    request_change_matrix_rebuild(project_id, mine_fid, background=background)
    return True


def request_change_matrix_rebuild(project_id, mine_fid, background=None):
    """请求重算一座矿山的审校变化矩阵（幂等，可合并重复请求）。

    默认：有应用上下文且不在 TESTING 时提交到后台队列——保存请求不等待栅格化；
    测试环境或没有应用上下文（推理 worker 里的发布本来就是后台任务）时同步执行，
    行为确定。显式传 background=False 可在任何环境强制同步。
    """
    if background is None:
        try:
            background = bool(current_app) and not current_app.config.get("TESTING")
        except RuntimeError:  # 没有应用上下文
            background = False
    if not background:
        return compute_revised_change_matrix(project_id, mine_fid)
    _REBUILD_QUEUE.submit(current_app._get_current_object(), project_id, mine_fid)
    return None
