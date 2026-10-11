"""只对任务暂存 RGB 应用已有增强/降噪算法，原栅格与原图保持只读。"""

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import cv2
import rasterio

from applications.image_processing.CLAHE import CLAHE
from applications.image_processing.gaussian_blur import gaussian_blur
from applications.image_processing.median_blur import median_blur
from applications.image_processing.sharpen import sharpen
from applications.inference.preprocessing import normalize_preprocessing_options


LOGGER = logging.getLogger(__name__)
_STEPS = {
    2: ("clahe", CLAHE, {"clipLimit": 2, "tileGridSize": [8, 8]}),
    4: ("sharpen", sharpen, {"kernel": [[0, -1, 0], [-1, 5, -1], [0, -1, 0]]}),
    3: ("median_blur", median_blur, {"ksize": 3}),
    5: ("gaussian_blur", gaussian_blur, {"ksize": [3, 3], "sigmaX": 0, "sigmaY": 0}),
}


def preprocessing_record(prehandle, denoise):
    normalize_preprocessing_options({"prehandle": prehandle, "denoise": denoise})
    steps = [_STEPS[code] for code in (prehandle, denoise) if code]
    return {
        "prehandle": prehandle,
        "denoise": denoise,
        "order": [name for name, _, _ in steps],
        "parameters": {name: parameters for name, _, parameters in steps},
        "sources": [],
        "tiles": [],
    }


def source_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare_processed_tiles(tile_dir, output_dir, file_names, sources, *, prehandle, denoise):
    record = preprocessing_record(prehandle, denoise)
    if not record["order"]:
        return tile_dir, None
    if not file_names:
        return tile_dir, record
    output_dir.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for role, source in sources:
        source = Path(source).resolve()
        if source not in hashes:
            hashes[source] = source_sha256(source)
        record["sources"].append({"role": role, "sha256": hashes[source]})

    for name in file_names:
        original = cv2.imread(str(tile_dir / name), cv2.IMREAD_COLOR)
        if original is None:
            raise ValueError(f"预处理原图读取失败: {name}，请检查 ROI RGB 产物")
        shutil.copy2(tile_dir / name, output_dir / name)
        current_name = name
        for code in (prehandle, denoise):
            if not code:
                continue
            step_name, algorithm, _ = _STEPS[code]
            generated = algorithm(str(output_dir), str(output_dir), [current_name])
            if len(generated) != 1:
                raise ValueError(f"预处理 {step_name} 未返回产物: {name}，请检查算法输出")
            next_name = generated[0]
            processed = cv2.imread(str(output_dir / next_name), cv2.IMREAD_COLOR)
            if processed is None or processed.shape != original.shape:
                raise ValueError(f"预处理 {step_name} 产物不可读或尺寸异常: {name}，请检查 RGB 写入")
            (output_dir / current_name).unlink()
            current_name = next_name
        os.replace(output_dir / current_name, output_dir / name)
        with rasterio.open(tile_dir / f"{Path(name).stem}.tif") as crop:
            record["tiles"].append({
                "input_name": name,
                "source_rgb_sha256": hashlib.sha256(original[:, :, ::-1].copy().tobytes()).hexdigest(),
                "processed_rgb_sha256": hashlib.sha256(processed[:, :, ::-1].copy().tobytes()).hexdigest(),
                "crs": crop.crs.to_string() if crop.crs else None,
                "transform": list(crop.transform)[:6],
                "width": crop.width,
                "height": crop.height,
                "inference_width": original.shape[1],
                "inference_height": original.shape[0],
            })
    LOGGER.info("ROI 预处理记录: %s", json.dumps(record, ensure_ascii=False))
    return output_dir, record
