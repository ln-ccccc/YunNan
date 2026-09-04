import csv
import json
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import numpy as np


CLASS_NAMES = ["grassland", "forest", "building", "road", "bareground", "water"]

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
    if not old_mask.exists() or not new_mask.exists():
        return False

    old = cv2.imread(str(old_mask), cv2.IMREAD_UNCHANGED)
    new = cv2.imread(str(new_mask), cv2.IMREAD_UNCHANGED)
    if old is None or new is None:
        return False
    if old.ndim == 3:
        old = old[:, :, 0]
    if new.ndim == 3:
        new = new[:, :, 0]

    h = min(old.shape[0], new.shape[0])
    w = min(old.shape[1], new.shape[1])
    old = old[:h, :w]
    new = new[:h, :w]

    n = len(CLASS_NAMES)
    valid = (old >= 0) & (old < n) & (new >= 0) & (new < n)
    old_v = old[valid].astype(np.int64)
    new_v = new[valid].astype(np.int64)

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
