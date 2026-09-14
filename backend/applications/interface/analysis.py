import copy
import json
import os
import uuid
from pathlib import Path
from urllib.parse import unquote

import cv2
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform_geom

from applications.common.path_global import fun_type_2, fun_type_3, fun_type_4, fun_type_5, generate_url, generate_dir, up_url
from applications.common.utils.upload import img_url_handle
from applications.extensions import db
from applications.image_processing.CLAHE import CLAHE
from applications.image_processing.gaussian_blur import gaussian_blur
from applications.image_processing.median_blur import median_blur
from applications.image_processing.resize import resize
from applications.image_processing.sharpen import sharpen
from applications.interface import semantic_segmentation as SS
from applications.kml_roi.index_sync import sync_miner_index_rows
from applications.kml_roi.kml import load_vector_features
from applications.kml_roi.raster_ops import raster_bounds_4326
from applications.kml_roi.spatial_index import filter_features_by_bounds
from applications.models.analysis import Analysis

WGS84 = "EPSG:4326"


def save_analysis(type_, pic1, retPic, pic2="", data="{}", is_hole=False, checked="0,0"):
    analysis = Analysis()
    analysis.type = type_
    analysis.before_img = pic1
    analysis.before_img1 = pic2
    analysis.after_img = retPic
    analysis.data = data
    analysis.is_hole = is_hole
    analysis.checked = checked
    db.session.add(analysis)
    db.session.commit()


def terrain_classification(model_path, data_path, out_dir, names, step1, step2, type_):
    print("地物分类 -> start")
    imgs = []
    temp_names = copy.deepcopy(names)
    for j, pair in enumerate(names):
        names[j] = img_url_handle(pair)
        imgs.append(names[j])

    resizes = resize(data_path, data_path, imgs, mode=2)
    for i in range(len(imgs)):
        imgs[i] = resizes[i]

    if step1 != 0:
        imgs = handle(step1, imgs, data_path, data_path)
    if step2 != 0:
        imgs = handle(step2, imgs, data_path, data_path)

    retPics = SS.execute(model_path, data_path, out_dir, imgs)

    for i, pair in enumerate(resizes):
        first_ = temp_names[i]
        retPic = retPics[i]
        save_analysis(type_, first_, retPic, pic2="", data="", checked=str(step1) + "," + str(step2))

    print("地物分类 -> end")


def _default_kml_path():
    return Path(__file__).resolve().parents[3] / "miner" / "yunnan.kml"


def _resolve_spectral_input(item, data_path):
    if isinstance(item, dict):
        raw_path = item.get("raw_tiff_path") or item.get("raw_tiff") or item.get("path")
        display_url = item.get("preview_src") or item.get("src") or raw_path
    else:
        raw_path = item
        display_url = item

    candidate = raw_path or display_url
    text = str(candidate or "")
    # 纵深收口：读取路径一律收敛到受控 data_path 下的 basename。任何含分隔符
    # （含反斜杠与 URL 编码解码后）的输入不得经 os.path.exists 直通读取服务器
    # 任意文件；display_url 仅作展示，原样保留。
    decoded = unquote(text.replace("\\", "/"))
    if decoded and ("/" in decoded or "%" in text):
        safe_name = decoded.rsplit("/", 1)[-1]
        if not safe_name or safe_name in (".", ".."):
            safe_name = ""
        if not safe_name:
            raise ValueError("影像路径不合法")
        return os.path.join(data_path, safe_name), safe_name, display_url
    # 纯 basename 也不做 CWD 直通（backend 目录下可能存在其他同名文件），
    # 一律收敛到受控 data_path 下再由调用方打开
    img_name = img_url_handle(text)
    return os.path.join(data_path, img_name), img_name, display_url


def _safe_mean(values):
    if values.size == 0:
        return None
    return float(np.mean(values))


def _safe_fid(value):
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def _mine_boundary_stats(
    src,
    input_path,
    index_data,
    valid_mask,
    vector_path=None,
    allowed_fids=None,
):
    if src.crs is None:
        return np.array([], dtype=np.float32), "raster_missing_crs", 0, []

    if not vector_path:
        return np.array([], dtype=np.float32), "standalone", 0, []
    resolved_vector = Path(vector_path).expanduser()
    if not resolved_vector.exists():
        return np.array([], dtype=np.float32), "vector_missing", 0, []

    features = load_vector_features(resolved_vector)
    bounds_4326 = raster_bounds_4326(Path(input_path))
    features = filter_features_by_bounds(features, bounds_4326)
    if allowed_fids is not None:
        selected = {str(fid) for fid in allowed_fids}
        features = [(feature_fid, geom) for feature_fid, geom in features if str(feature_fid) in selected]
    if not features:
        return np.array([], dtype=np.float32), "no_boundary_overlap", 0, []

    masks_by_fid = {}
    for feature_fid, geom_4326 in features:
        geom_src = transform_geom(WGS84, src.crs, geom_4326, precision=6)
        feature_mask = geometry_mask(
            [geom_src],
            out_shape=(src.height, src.width),
            transform=src.transform,
            invert=True,
        )
        if not np.any(feature_mask):
            continue
        fid_key = str(feature_fid).strip()
        if fid_key in masks_by_fid:
            masks_by_fid[fid_key] |= feature_mask
        else:
            masks_by_fid[fid_key] = feature_mask

    if not masks_by_fid:
        return np.array([], dtype=np.float32), "no_boundary_overlap", len(features), []

    union_mask = np.zeros(valid_mask.shape, dtype=bool)
    fid_stats = []
    for fid_key, feature_mask in masks_by_fid.items():
        union_mask |= feature_mask
        values = index_data[valid_mask & feature_mask]
        if values.size == 0:
            continue
        fid_stats.append(
            {
                "fid": _safe_fid(fid_key),
                "mean": float(np.mean(values)),
                "pixel_count": int(values.size),
            }
        )

    boundary_values = index_data[valid_mask & union_mask]
    if boundary_values.size == 0:
        return boundary_values, "no_valid_pixels", len(features), fid_stats
    return boundary_values, "ok", len(features), fid_stats


def spectral_index_calculation(
    data_path,
    out_dir,
    names,
    index_type,
    year,
    band_map,
    type_,
    kml_path=None,
    fid=None,
    vector_path=None,
    allowed_fids=None,
    sync_global=False,
):
    print("光谱指数计算 -> start")
    index_type = index_type.upper()
    index_map = {
        "NDVI": ("nir", "red"),
        "NDBI": ("swir", "nir"),
        "NDWI": ("green", "nir"),
        "NDSI": ("green", "swir"),
    }
    if index_type not in index_map:
        raise ValueError("不支持的指数类型")
    os.makedirs(out_dir, exist_ok=True)
    output_urls = []
    records = []
    sync_results = []
    sync_warnings = []
    first_band_name, second_band_name = index_map[index_type]

    for item in names:
        input_path, img_name, display_url = _resolve_spectral_input(item, data_path)
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"文件不存在: {img_name}")

        preview_input_url = display_url or up_url + img_name

        with rasterio.open(input_path) as src:
            band_count = src.count
            auto_map = {}
            if band_count >= 7:
                auto_map = {"green": 2, "red": 3, "nir": 4, "swir": 6}
            elif band_count >= 5:
                auto_map = {"green": 2, "red": 3, "nir": 4, "swir": 5}
            elif band_count >= 4:
                auto_map = {"green": 2, "red": 3, "nir": 4}

            merged_map = dict(auto_map)
            if isinstance(band_map, dict):
                merged_map.update(band_map)

            if first_band_name not in merged_map or second_band_name not in merged_map:
                descriptions = [d for d in src.descriptions if d]
                raise ValueError(
                    f"波段映射缺失: 需要 {first_band_name},{second_band_name}; 当前波段数={band_count}; 描述={descriptions}"
                )

            first_band_idx = int(merged_map[first_band_name])
            second_band_idx = int(merged_map[second_band_name])

            if (
                first_band_idx < 1
                or second_band_idx < 1
                or first_band_idx > band_count
                or second_band_idx > band_count
            ):
                raise ValueError(f"波段序号超出范围: {img_name} 共有 {band_count} 个波段")

            first_band = src.read(first_band_idx).astype(np.float32)
            second_band = src.read(second_band_idx).astype(np.float32)
            nodata = src.nodata

            if img_name.lower().endswith((".tif", ".tiff")):
                if band_count >= 3:
                    red_idx = int(merged_map.get("red", 3 if band_count >= 3 else 1))
                    green_idx = int(merged_map.get("green", 2 if band_count >= 2 else 1))
                    blue_idx = int(merged_map.get("blue", 1))
                    red_idx = max(1, min(red_idx, band_count))
                    green_idx = max(1, min(green_idx, band_count))
                    blue_idx = max(1, min(blue_idx, band_count))
                    preview_r = src.read(red_idx).astype(np.float32)
                    preview_g = src.read(green_idx).astype(np.float32)
                    preview_b = src.read(blue_idx).astype(np.float32)
                    preview_rgb = np.stack([preview_r, preview_g, preview_b], axis=-1)
                    low = np.percentile(preview_rgb, 2)
                    high = np.percentile(preview_rgb, 98)
                    if high > low:
                        preview_rgb = (preview_rgb - low) / (high - low)
                    preview_rgb = np.clip(preview_rgb, 0.0, 1.0)
                    preview_u8 = (preview_rgb * 255.0).astype(np.uint8)
                else:
                    preview_gray = src.read(1).astype(np.float32)
                    low = np.percentile(preview_gray, 2)
                    high = np.percentile(preview_gray, 98)
                    if high > low:
                        preview_gray = (preview_gray - low) / (high - low)
                    preview_gray = np.clip(preview_gray, 0.0, 1.0)
                    preview_u8 = (preview_gray * 255.0).astype(np.uint8)
                    preview_u8 = cv2.cvtColor(preview_u8, cv2.COLOR_GRAY2RGB)
                preview_name = "preview_{}.png".format(uuid.uuid4().hex[:12])
                preview_path = os.path.join(out_dir, preview_name)
                cv2.imwrite(preview_path, cv2.cvtColor(preview_u8, cv2.COLOR_RGB2BGR))
                preview_input_url = generate_url + preview_name

        raw_denominator = first_band + second_band
        denominator = np.where(np.abs(raw_denominator) < 1e-6, 1e-6, raw_denominator)
        index_data = (first_band - second_band) / denominator
        index_data = np.clip(index_data, -1.0, 1.0)
        normalized = ((index_data + 1.0) / 2.0 * 255.0).astype(np.uint8)
        valid_mask = np.isfinite(index_data) & (np.abs(raw_denominator) >= 1e-6)
        if nodata is not None:
            valid_mask &= (first_band != nodata) & (second_band != nodata)

        image_values = index_data[valid_mask]
        with rasterio.open(input_path) as src:
            boundary_values, boundary_status, boundary_count, fid_stats = _mine_boundary_stats(
                src,
                input_path,
                index_data,
                valid_mask,
                vector_path=vector_path or kml_path,
                allowed_fids=allowed_fids if allowed_fids is not None else ([fid] if fid else None),
            )

        if index_type == "NDVI":
            color_map = cv2.COLORMAP_SUMMER
        elif index_type == "NDWI":
            color_map = cv2.COLORMAP_OCEAN
        elif index_type == "NDBI":
            color_map = cv2.COLORMAP_HOT
        elif index_type == "NDSI":
            color_map = cv2.COLORMAP_WINTER
        else:
            color_map = cv2.COLORMAP_JET

        color_mapped = cv2.applyColorMap(normalized, color_map)
        output_name = f"spectral_{index_type.lower()}_{uuid.uuid4().hex[:12]}.png"
        output_path = os.path.join(out_dir, output_name)
        cv2.imwrite(output_path, color_mapped)

        output_url = generate_url + output_name
        if sync_global:
            sync_result = sync_miner_index_rows(index_type, year, fid_stats)
            if sync_result.get("synced"):
                sync_results.append(sync_result)
            else:
                sync_warnings.append(sync_result)
        else:
            sync_result = {"synced": False, "reason": "project_routing"}
        use_boundary = boundary_status == "ok" and boundary_values.size > 0
        result_values = boundary_values if use_boundary else image_values
        data = json.dumps(
            {
                "index_type": index_type,
                "year": str(year or ""),
                "band_map": {first_band_name: first_band_idx, second_band_name: second_band_idx},
                "min": float(np.min(index_data)),
                "max": float(np.max(index_data)),
                "mean": _safe_mean(result_values),
                "mean_scope": "mine_boundary" if use_boundary else "image",
                "image_mean": _safe_mean(image_values),
                "boundary_status": boundary_status,
                "boundary_count": boundary_count,
                "valid_pixel_count": int(result_values.size),
                "matched_fid_list": [row["fid"] for row in fid_stats],
                "fid_stats": fid_stats,
                "synced_to_miner": bool(sync_result.get("synced")),
                "sync_warning": None if sync_result.get("synced") else sync_result.get("reason"),
            }
        )
        save_analysis(type_, preview_input_url, output_url, pic2="", data=data)
        output_urls.append(output_url)
        records.append(
            {
                "url": output_url,
                "input": preview_input_url,
                "index_type": index_type,
                "year": str(year or ""),
                "matched_fid_list": [row["fid"] for row in fid_stats],
                "fid_stats": fid_stats,
                "sync": sync_result,
            }
        )

    print("光谱指数计算 -> end")
    return {
        "urls": output_urls,
        "records": records,
        "sync_results": sync_results,
        "sync_warnings": sync_warnings,
    }


def handle(fun_type, imgs, src_dir, save_dir, prefix=""):
    temps = []
    if fun_type == fun_type_2:
        temps = CLAHE(src_dir, save_dir, imgs)
    elif fun_type == fun_type_3:
        temps = median_blur(src_dir, save_dir, imgs)
    elif fun_type == fun_type_4:
        temps = sharpen(src_dir, save_dir, imgs)
    elif fun_type == fun_type_5:
        temps = gaussian_blur(src_dir, save_dir, imgs)
    return temps
