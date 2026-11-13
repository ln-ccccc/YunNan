# -*- coding: utf-8 -*-
"""分类成果统计口径改造（2026-10-31 方案 b）。

人工在编辑器修订矢量后，按审校后矢量重算两类统计并回写发布产物：
- 地类占比：球面鞋带面积（复用 _geometry.geometry_area_m2，与 stats/质检
  同源口径）按 class_code 聚合归一化，写入 class_ratio_percent.json 的 revised
  块——推理口径 series_percent 原值保留，消费端优先 revised、无则回落。
- 变化矩阵：把各年审校后矢量（current FC，v0 auto 亦属矢量口径）rasterize 到
  该年 label.tif 网格，逐像素转移计数，覆盖更新 outputs/change_matrix/{fid}.json
  并标记 basis="revised_vector"；推理口径 CSV 原文件保留。

失败策略：统计产物非关键数据（溯源/趋势均有推理口径回落），重算失败 suppress
+ 日志，绝不阻断保存事务。
"""
import json
import logging
import os
import uuid
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.warp import transform_geom

from applications.kml_roi.change_matrix import CLASS_NAMES
from applications.project_hub._geometry import geometry_area_m2
from applications.project_hub.spatial_storage import get_storage_root, resolve_storage_path

LOGGER = logging.getLogger(__name__)

_AREA_SOURCE_REVISED = "vector_shoelace"
_AREA_SOURCE_INFERENCE = "inference_mask"


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


def resolve_ratio_series(ratio_doc):
    """占比序列的口径解析：有审校块优先返回审校序列，否则推理口径。

    返回 (series_percent, area_source, revised_revision_no)；ratio_doc 缺失时
    返回 (None, None, None)，调用方按原容错处理。
    """
    if not isinstance(ratio_doc, dict):
        return None, None, None
    revised = ratio_doc.get("revised")
    if isinstance(revised, dict) and revised.get("series_percent"):
        return (
            revised.get("series_percent"),
            revised.get("area_source") or _AREA_SOURCE_REVISED,
            revised.get("revision_no"),
        )
    return ratio_doc.get("series_percent"), _AREA_SOURCE_INFERENCE, None


def compute_vector_class_ratio(feature_collection):
    """审校后矢量的地类占比：球面鞋带 m² 按 class_code 聚合，归一化 0-100。

    无有效几何（空集合/全坏要素）返回 None，调用方跳过回写。
    """
    areas = {name: 0.0 for name in CLASS_NAMES}
    for feature in (feature_collection or {}).get("features") or []:
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties") or {}
        class_code = properties.get("class_code")
        if not isinstance(class_code, int) or not 0 <= class_code < len(CLASS_NAMES):
            continue
        areas[CLASS_NAMES[class_code]] += geometry_area_m2(feature.get("geometry"))
    total = sum(areas.values())
    if total <= 0:
        return None
    return {name: round(areas[name] / total * 100.0, 2) for name in CLASS_NAMES}


def apply_revised_class_ratio(project_id, mine_fid, year, revision_no, feature_collection):
    """保存修订后把审校占比写入发布产物的 revised 块（按年索引定位，原子写）。

    返回 True/False 供日志与补算脚本判断；json 缺失（如旧任务未生成）静默跳过。
    """
    vector_ratio = compute_vector_class_ratio(feature_collection)
    if vector_ratio is None:
        return False
    try:
        fid_dir = _inference_fid_dir(project_id, mine_fid)
        ratio_path = fid_dir / "class_ratio_percent.json"
        if not ratio_path.is_file():
            LOGGER.info(
                "审校占比跳过（推理占比文件不存在）project_id=%s mine_fid=%s", project_id, mine_fid
            )
            return False
        with open(ratio_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        years = [int(value) for value in (payload.get("years") or [])]
        try:
            index = years.index(int(year))
        except ValueError:
            LOGGER.info(
                "审校占比跳过（修订年份 %s 不在占比序列 %s）project_id=%s mine_fid=%s",
                year, years, project_id, mine_fid,
            )
            return False
        original = payload.get("series_percent") or {}
        revised_series = {}
        for name in CLASS_NAMES:
            series = [float(value) for value in (original.get(name) or [])]
            while len(series) < len(years):
                series.append(0.0)
            series[index] = float(vector_ratio[name])
            revised_series[name] = series
        payload["revised"] = {
            "revision_no": int(revision_no),
            "area_source": _AREA_SOURCE_REVISED,
            "year": int(year),
            "series_percent": revised_series,
        }
        _atomic_write_json(fid_dir, "class_ratio_percent.json", payload)
        return True
    except (OSError, ValueError, TypeError) as exc:
        LOGGER.warning(
            "审校占比回写失败 project_id=%s mine_fid=%s year=%s: %s",
            project_id, mine_fid, year, exc,
        )
        return False


def rasterize_year_vector(project_id, mine_fid, year, feature_collection, target_grid=None):
    """审校矢量栅格化：类别值 0-5、无效区 255。

    target_grid=None 时用该年自身 label.tif 网格；提供 (transform, shape, crs)
    时强制栅格化到统一网格——跨 CRS/跨分辨率年份的转移矩阵必须同网格对齐，
    否则逐像素对比在空间上不成立（2026-10-31 数值审查发现的静默错误源）。
    """
    label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year}_label.tif"
    if not label_path.is_file():
        return None
    shapes = []
    with rasterio.open(label_path) as dataset:
        grid_transform = target_grid[0] if target_grid else dataset.transform
        grid_shape = target_grid[1] if target_grid else dataset.shape
        grid_crs = target_grid[2] if target_grid else dataset.crs
        for feature in (feature_collection or {}).get("features") or []:
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
        if not shapes:
            return None
        return rasterize(
            shapes,
            out_shape=grid_shape,
            transform=grid_transform,
            fill=255,
            dtype="uint8",
        )


def compute_revised_change_matrix(project_id, mine_fid):
    """各年审校矢量栅格化后的地类转移矩阵（最近两年，行=旧年、列=新年）。

    与溯源同一取数规则：同年多成果取最新非 vector_failed；任一年缺 label.tif/
    有效矢量、或成果数 <2 年则跳过（返回 False，原矩阵文件保持推理口径）。
    """
    from applications.models.classification_result import ClassificationResult

    rows = (
        ClassificationResult.query.filter_by(project_id=project_id, mine_fid=mine_fid)
        .order_by(ClassificationResult.id.desc())
        .all()
    )
    latest_by_year = {}
    for result in rows:
        if result.year in latest_by_year:
            continue
        if result.vector_status == "vector_failed" and any(
            other.year == result.year and other.vector_status != "vector_failed"
            for other in rows
        ):
            continue
        latest_by_year[result.year] = result
    years_sorted = sorted(latest_by_year)
    if len(years_sorted) < 2:
        return False

    import json as _json

    # 统一目标网格（2026-10-31 数值审查修正）：取旧年的 label 网格，两期矢量
    # 都栅格化到同一 transform/CRS——不同来源影像的 label 网格 CRS/分辨率不同
    # （GEHI=4326 度、用户 573M=3857 米），各自网格逐像素对比在空间上不成立。
    def _grid_of(year_value):
        label_path = _inference_fid_dir(project_id, mine_fid) / f"{mine_fid}+{year_value}_label.tif"
        if not label_path.is_file():
            return None
        with rasterio.open(label_path) as dataset:
            return dataset.transform, dataset.shape, dataset.crs

    old_year_value = years_sorted[-2]
    target_grid = _grid_of(old_year_value)
    if target_grid is None:
        LOGGER.info(
            "审校矩阵跳过（旧年 %s label 缺失）project_id=%s mine_fid=%s",
            old_year_value, project_id, mine_fid,
        )
        return False

    rasters = []
    for year in years_sorted[-2:]:
        result = latest_by_year[year]
        try:
            collection = _json.loads(result.current_feature_collection_json or "null")
        except (TypeError, ValueError):
            collection = None
        raster = rasterize_year_vector(project_id, mine_fid, year, collection, target_grid=target_grid)
        if raster is None:
            LOGGER.info(
                "审校矩阵跳过（年份 %s 栅格化不可用）project_id=%s mine_fid=%s",
                year, project_id, mine_fid,
            )
            return False
        rasters.append((int(year), raster, int(result.current_revision_no or 0)))

    old_year, old_raster, old_revision = rasters[-2]
    new_year, new_raster, new_revision = rasters[-1]

    class_count = len(CLASS_NAMES)
    valid = (old_raster < class_count) & (new_raster < class_count)
    total_valid = int(valid.sum())
    if total_valid <= 0:
        return False
    pairs = (old_raster[valid].astype(np.int64), new_raster[valid].astype(np.int64))
    pixels = np.zeros((class_count, class_count), dtype=np.int64)
    np.add.at(pixels, pairs, 1)
    row_totals = pixels.sum(axis=1, keepdims=True)
    percent = np.divide(
        pixels.astype(np.float64),
        row_totals,
        out=np.zeros_like(pixels, dtype=np.float64),
        where=row_totals > 0,
    ) * 100.0

    output_root = resolve_storage_path(
        get_storage_root(), Path("projects") / str(project_id) / "outputs"
    )
    summary_dir = output_root / "change_matrix"
    summary_path = summary_dir / f"{mine_fid}.json"
    previous = {}
    if summary_path.is_file():
        try:
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    payload = {
        "fid": str(mine_fid),
        "old_year": old_year,
        "new_year": new_year,
        "headers": list(CLASS_NAMES),
        "matrix": [
            {
                "label": CLASS_NAMES[index],
                "values": [round(float(value), 1) for value in percent[index]],
            }
            for index in range(class_count)
        ],
        "images": previous.get("images") or {"old": None, "new": None},
        "has_change_matrix": True,
        "data_source": "project_inference",
        "basis": "revised_vector",
        "area_source": _AREA_SOURCE_REVISED,
        "revised_revision_no": {str(old_year): old_revision, str(new_year): new_revision},
    }
    _atomic_write_json(summary_dir, f"{mine_fid}.json", payload)
    return True
