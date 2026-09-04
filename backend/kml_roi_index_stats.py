#!/usr/bin/env python3
"""Compute per-FID spectral index means within KML polygons on a GeoTIFF."""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
import rasterio
from rasterio.mask import mask
from rasterio.warp import transform_geom

from applications.kml_roi.kml import load_kml_features
from applications.kml_roi.raster_ops import raster_bounds_4326
from applications.kml_roi.spatial_index import filter_features_by_bounds

WGS84 = "EPSG:4326"
INDEX_BANDS = {
    "ndvi": ("nir", "red"),
    "ndbi": ("swir", "nir"),
    "ndwi": ("green", "nir"),
    "ndsi": ("green", "swir"),
}


def _parse_index_types(raw: str) -> List[str]:
    items = [s.strip().lower() for s in str(raw or "").split(",") if s.strip()]
    if not items:
        return ["ndvi"]
    out: List[str] = []
    for item in items:
        if item not in INDEX_BANDS:
            raise ValueError(f"Unsupported index type: {item}")
        if item not in out:
            out.append(item)
    return out


def _normalize_user_band_map(raw: Optional[str]) -> Dict[str, int]:
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("band_map must be a JSON object")
    out: Dict[str, int] = {}
    for k, v in data.items():
        out[str(k).strip().lower()] = int(v)
    return out


def _default_band_map(band_count: int) -> Dict[str, int]:
    if band_count >= 7:
        return {"green": 2, "red": 3, "nir": 4, "swir": 6}
    if band_count >= 5:
        return {"green": 2, "red": 3, "nir": 4, "swir": 5}
    if band_count >= 4:
        return {"green": 2, "red": 3, "nir": 4}
    return {}


def _resolve_band_map(src: rasterio.DatasetReader, user_band_map: Dict[str, int], index_types: List[str]) -> Dict[str, int]:
    merged = _default_band_map(src.count)
    merged.update(user_band_map or {})

    need_keys: Set[str] = set()
    for idx_type in index_types:
        a, b = INDEX_BANDS[idx_type]
        need_keys.add(a)
        need_keys.add(b)

    for key in sorted(need_keys):
        if key not in merged:
            raise ValueError(f"Missing band mapping for '{key}', raster has {src.count} bands")
        band_idx = int(merged[key])
        if band_idx < 1 or band_idx > src.count:
            raise ValueError(f"Band index out of range for '{key}': {band_idx}, raster has {src.count} bands")
        merged[key] = band_idx
    return merged


def _parse_fid_filter(raw: str) -> Optional[Set[str]]:
    text = str(raw or "").strip()
    if not text:
        return None
    out = {s.strip() for s in text.split(",") if s.strip()}
    return out or None


def _masked_index_sum_count(a: np.ma.MaskedArray, b: np.ma.MaskedArray) -> Optional[Dict[str, float]]:
    a = np.ma.array(a, dtype=np.float32)
    b = np.ma.array(b, dtype=np.float32)
    denom = a + b
    safe = np.ma.masked_where(np.abs(denom) < 1e-6, denom)
    idx = (a - b) / safe
    idx = np.ma.clip(idx, -1.0, 1.0)
    values = idx.compressed()
    if values.size == 0:
        return None
    valid = values[np.isfinite(values)]
    if valid.size == 0:
        return None
    return {"sum": float(valid.sum()), "count": int(valid.size)}


def run_index_stats(
    *,
    tif_path: Path,
    kml_path: Path,
    index_types: List[str],
    band_map: Dict[str, int],
    limit: int = 0,
    fids: Optional[Set[str]] = None,
) -> Dict:
    features = load_kml_features(kml_path)
    bounds_4326 = raster_bounds_4326(tif_path)
    features = filter_features_by_bounds(features, bounds_4326)
    if fids is not None:
        features = [(fid, geom) for fid, geom in features if str(fid) in fids]
    if limit > 0:
        features = features[:limit]

    acc: Dict[str, Dict[str, Dict[str, float]]] = {idx: {} for idx in index_types}

    with rasterio.open(tif_path) as src:
        if src.crs is None:
            raise RuntimeError(f"Missing CRS for raster: {tif_path}")

        resolved_map = _resolve_band_map(src, band_map, index_types)
        needed_bands = sorted({resolved_map[k] for idx in index_types for k in INDEX_BANDS[idx]})

        for fid, geom_4326 in features:
            try:
                geom_src = transform_geom(WGS84, src.crs, geom_4326, precision=6)
                out_img, _ = mask(src, [geom_src], crop=True, filled=False, indexes=needed_bands)
            except Exception:
                continue

            if out_img.size == 0:
                continue

            band_values = {band_idx: out_img[pos] for pos, band_idx in enumerate(needed_bands)}
            fid_str = str(fid)

            for idx_type in index_types:
                a_key, b_key = INDEX_BANDS[idx_type]
                stats = _masked_index_sum_count(
                    band_values[resolved_map[a_key]],
                    band_values[resolved_map[b_key]],
                )
                if not stats:
                    continue
                bucket = acc[idx_type].setdefault(fid_str, {"sum": 0.0, "count": 0})
                bucket["sum"] += stats["sum"]
                bucket["count"] += int(stats["count"])

    stats_by_index: Dict[str, List[Dict]] = {}
    all_fids: Set[str] = set()
    for idx_type, fid_map in acc.items():
        rows: List[Dict] = []
        for fid_str, item in fid_map.items():
            cnt = int(item["count"])
            if cnt <= 0:
                continue
            mean_val = float(item["sum"] / cnt)
            rows.append({"fid": int(fid_str) if fid_str.isdigit() else fid_str, "mean": mean_val, "pixel_count": cnt})
            all_fids.add(fid_str)
        rows.sort(key=lambda x: str(x["fid"]))
        stats_by_index[idx_type] = rows

    matched_list = sorted(all_fids, key=lambda x: int(x) if str(x).isdigit() else x)
    return {
        "status": "completed",
        "tif_path": str(tif_path),
        "kml_path": str(kml_path),
        "index_types": index_types,
        "total_features": len(features),
        "matched_fids": len(matched_list),
        "matched_fid_list": [int(x) if str(x).isdigit() else x for x in matched_list],
        "stats_by_index": stats_by_index,
    }


def main() -> int:
    backend_root = Path(__file__).resolve().parent
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    parser = argparse.ArgumentParser(description="Compute KML polygon index means by FID")
    parser.add_argument("--tif", required=True, help="Input GeoTIFF path")
    parser.add_argument("--kml", required=True, help="KML path containing mine polygons with FID")
    parser.add_argument("--index_types", default="ndvi", help="Comma-separated index types: ndvi,ndbi,ndwi,ndsi")
    parser.add_argument("--band_map", default="", help='Optional JSON string, e.g. {"nir":4,"red":3,"green":2,"swir":5}')
    parser.add_argument("--limit", type=int, default=0, help="Process first N polygons")
    parser.add_argument("--fids", default="", help="Optional comma-separated FID filter")
    args = parser.parse_args()

    tif_path = Path(args.tif).expanduser().resolve()
    kml_path = Path(args.kml).expanduser().resolve()
    if not tif_path.exists():
        raise FileNotFoundError(f"tif not found: {tif_path}")
    if not kml_path.exists():
        raise FileNotFoundError(f"kml not found: {kml_path}")

    index_types = _parse_index_types(args.index_types)
    user_band_map = _normalize_user_band_map(args.band_map)
    fid_filter = _parse_fid_filter(args.fids)

    summary = run_index_stats(
        tif_path=tif_path,
        kml_path=kml_path,
        index_types=index_types,
        band_map=user_band_map,
        limit=int(args.limit or 0),
        fids=fid_filter,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
