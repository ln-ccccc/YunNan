import csv
import json
import logging
import math
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.errors import RasterioError
from rasterio.warp import reproject


CLASS_NAMES = ["grassland", "forest", "building", "road", "bareground", "water"]
LOGGER = logging.getLogger(__name__)
MATRIX_FILES = ("change_matrix_pixels.csv", "change_matrix_percent_rownorm.csv", "change_matrix_alignment.json")


def _label_path(mask_path):
    return mask_path.with_name(mask_path.name.removesuffix("_mask.png") + "_label.tif")


def _read_labels(source):
    transform = source.transform
    coefficients = tuple(transform)[:6]
    if source.crs is None:
        raise ValueError("变化矩阵标签缺少 CRS，需重新生成地理参考标签")
    if (not all(math.isfinite(value) for value in coefficients)
            or transform.is_identity or transform.determinant == 0):
        raise ValueError("变化矩阵标签缺少有效地理变换，需重新生成地理参考标签")
    if source.count != 1 or not np.issubdtype(np.dtype(source.dtypes[0]), np.integer):
        raise ValueError("变化矩阵标签必须是单波段整数类别栅格")
    labels = source.read(1)
    valid = (source.read_masks(1) > 0) & (labels >= 0) & (labels < len(CLASS_NAMES))
    return np.where(valid, labels, 255).astype(np.uint8)


def _aligned_masks(old_mask, new_mask):
    old_label, new_label = _label_path(old_mask), _label_path(new_mask)
    if old_label.is_file() != new_label.is_file():
        raise ValueError("两期变化矩阵均须具有地理参考标签，不能仅使用一期标签")
    if old_label.is_file():
        with rasterio.open(old_label) as old_source, rasterio.open(new_label) as new_source:
            old, new = _read_labels(old_source), _read_labels(new_source)
            same_grid = (old_source.crs == new_source.crs
                         and old_source.transform == new_source.transform and old.shape == new.shape)
            if not same_grid:
                aligned = np.full(new.shape, 255, dtype=np.uint8)
                reproject(source=old, destination=aligned,
                          src_transform=old_source.transform, src_crs=old_source.crs, src_nodata=255,
                          dst_transform=new_source.transform, dst_crs=new_source.crs, dst_nodata=255,
                          resampling=Resampling.nearest)
                old = aligned
            metadata = {"mode": "georeferenced", "spatial_alignment_verified": True,
                        "resampling": "nearest", "target_grid": {
                            "crs": new_source.crs.to_string(), "transform": list(new_source.transform)[:6],
                            "width": new_source.width, "height": new_source.height}}
            return old, new, metadata
    old = cv2.imread(str(old_mask), cv2.IMREAD_UNCHANGED)
    new = cv2.imread(str(new_mask), cv2.IMREAD_UNCHANGED)
    if old is None or new is None:
        raise ValueError("无法读取变化矩阵 PNG，需检查两期掩膜文件")
    if old.ndim == 3:
        old = old[:, :, 0]
    if new.ndim == 3:
        new = new[:, :, 0]
    if old.shape != new.shape:
        raise ValueError("无地理参考的两期 PNG 尺寸不同，无法计算空间变化矩阵")
    return old, new, {"mode": "legacy_png", "spatial_alignment_verified": False,
                      "resampling": None, "target_grid": None}


def write_available_change_matrix(old_mask, new_mask, out_dir):
    """Keep classification publishable when a historical pair cannot be aligned."""
    try:
        return write_change_matrix_csv(old_mask, new_mask, out_dir)
    except (ValueError, RasterioError) as error:
        LOGGER.warning("无法生成空间变化矩阵: %s", error)
        reason = ("分类标签不可读，请检查文件并重新生成地理参考标签"
                  if isinstance(error, RasterioError) else str(error))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "change_matrix_alignment.json").write_text(json.dumps({
            "mode": "unavailable", "spatial_alignment_verified": False,
            "reason": reason, "compared_pixels": 0,
        }, ensure_ascii=False), encoding="utf-8")
        return False

def compute_class_ratio_percent(mask_path: Path) -> Optional[Dict]:
    img = cv2.imread(str(mask_path), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 3:
        img = img[:, :, 0]
    n = len(CLASS_NAMES)
    valid = (img >= 0) & (img < n)
    total = int(valid.sum())
    if total <= 0:
        return {"total": 0, "percent": {name: 0.0 for name in CLASS_NAMES}}
    vals = img[valid].astype(np.int64)
    counts = np.bincount(vals, minlength=n)[:n].astype(np.int64)
    percent = {CLASS_NAMES[i]: float(counts[i]) / float(total) * 100.0 for i in range(n)}
    return {"total": total, "percent": percent}


def write_class_ratio_json(
    *,
    fid: str,
    year_masks: List,
    out_dir: Path,
) -> bool:
    years: List[int] = []
    series = {name: [] for name in CLASS_NAMES}
    totals: List[int] = []

    for y, _, mask_path in year_masks:
        stat = compute_class_ratio_percent(Path(mask_path))
        if stat is None:
            continue
        years.append(int(y))
        totals.append(int(stat.get("total") or 0))
        pct = stat.get("percent") or {}
        for name in CLASS_NAMES:
            series[name].append(float(pct.get(name, 0.0)))

    if not years:
        return False

    payload = {
        "fid": fid,
        "class_names": CLASS_NAMES,
        "years": years,
        "totals": totals,
        "series_percent": series,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "class_ratio_percent.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return True


def write_change_matrix_csv(old_mask: Path, new_mask: Path, out_dir: Path) -> bool:
    for name in MATRIX_FILES:
        (out_dir / name).unlink(missing_ok=True)
    if not old_mask.exists() or not new_mask.exists():
        return False
    old, new, metadata = _aligned_masks(old_mask, new_mask)

    n = len(CLASS_NAMES)
    valid = (old >= 0) & (old < n) & (new >= 0) & (new < n)
    old_v = old[valid].astype(np.int64)
    new_v = new[valid].astype(np.int64)
    metadata["compared_pixels"] = int(old_v.size)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "change_matrix_alignment.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    if old_v.size == 0:
        return False

    mat = np.zeros((n, n), dtype=np.int64)
    if old_v.size > 0:
        np.add.at(mat, (old_v, new_v), 1)

    pct = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        row_sum = mat[i].sum()
        if row_sum > 0:
            pct[i] = (mat[i] / row_sum) * 100.0

    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "change_matrix_pixels.csv", "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f)
        wtr.writerow(["class"] + CLASS_NAMES)
        for i, name in enumerate(CLASS_NAMES):
            wtr.writerow([name] + mat[i].tolist())

    with open(out_dir / "change_matrix_percent_rownorm.csv", "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f)
        wtr.writerow(["class"] + CLASS_NAMES)
        for i, name in enumerate(CLASS_NAMES):
            wtr.writerow([name] + [f"{v:.2f}" for v in pct[i].tolist()])
    return True
