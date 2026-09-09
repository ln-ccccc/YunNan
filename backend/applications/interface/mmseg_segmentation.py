#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MMSegmentation Inference Script

此脚本在 MMSeg310 环境中运行，使用 MMSegmentation 进行语义分割推理。
支持 GeoTIFF 图像输入，保留地理坐标信息。

6类地物分类:
  0: grassland (草地) - 灰色
  1: forest (林地) - 红色
  2: building (建筑) - 绿色
  3: road (道路) - 浅绿色
  4: bareground (裸地) - 深灰色
  5: water (水体) - 青色
"""

import argparse
import json
import os
import sys
from typing import List, Optional

# 添加 backend 路径到 sys.path，确保可以导入 backend 下的模块
sys.path.append(os.path.join(os.path.dirname(__file__), "../../../"))
# 导入自定义模型以注册到 metrics/registry
# NOTE:
# Do not import backend.model.custom_models here.
# DINOv3 classes are already provided by the mmseg source tree in PYTHONPATH,
# importing custom_models again can trigger duplicate registration errors.

import cv2
import numpy as np

# 类别颜色配置 (RGB格式)
PALETTE = [
    [0, 255, 0],      # 0: grassland - Lime
    [0, 128, 0],      # 1: forest - Green
    [255, 0, 0],      # 2: building - Red
    [255, 255, 0],    # 3: road - Yellow
    [255, 0, 255],    # 4: bareground - Magenta
    [0, 191, 255],    # 5: water - DeepSkyBlue
]

CLASS_NAMES = ['grassland', 'forest', 'building', 'road', 'bareground', 'water']


def load_rs_image_with_gdal(img_path: str, to_float32: bool = True) -> Optional[np.ndarray]:
    """
    使用 GDAL 加载遥感图像，支持多波段 GeoTIFF
    
    Args:
        img_path: 遥感图像路径
        to_float32: 是否转换为 float32
    
    Returns:
        图像数组 (H, W, C)，加载失败返回 None
    """
    try:
        from osgeo import gdal
    except ImportError:
        # 如果没有 GDAL，使用 cv2 加载
        img = cv2.imread(img_path)
        if img is not None and to_float32:
            img = img.astype(np.float32)
        return img
    
    ds = gdal.Open(img_path)
    if ds is None:
        print(f"Warning: Failed to open image with GDAL: {img_path}", file=sys.stderr)
        return None
    
    # 读取并调整维度 (C, H, W) -> (H, W, C)
    img_array = np.einsum('ijk->jki', ds.ReadAsArray())
    
    if to_float32:
        img_array = img_array.astype(np.float32)
    
    ds = None  # 关闭数据集
    return img_array


def save_with_georeference(output_path: str, data: np.ndarray, reference_path: str):
    """
    保存带地理参考信息的 GeoTIFF
    
    Args:
        output_path: 输出路径
        data: 数据数组 (H, W) 或 (H, W, C)
        reference_path: 参考影像路径 (用于获取投影信息)
    """
    try:
        from osgeo import gdal, osr
        
        ref_ds = gdal.Open(reference_path)
        if ref_ds is None:
            # 无法获取参考信息，使用普通保存
            cv2.imwrite(output_path, data)
            return
        
        # 获取投影信息
        geo_transform = ref_ds.GetGeoTransform()
        projection = ref_ds.GetProjection()
        
        # 创建输出文件
        driver = gdal.GetDriverByName('GTiff')
        if len(data.shape) == 2:
            out_ds = driver.Create(output_path, data.shape[1], data.shape[0], 1, gdal.GDT_Byte)
            out_ds.GetRasterBand(1).WriteArray(data)
        else:
            out_ds = driver.Create(output_path, data.shape[1], data.shape[0], data.shape[2], gdal.GDT_Byte)
            for i in range(data.shape[2]):
                out_ds.GetRasterBand(i + 1).WriteArray(data[:, :, i])
        
        out_ds.SetGeoTransform(geo_transform)
        out_ds.SetProjection(projection)
        out_ds.FlushCache()
        out_ds = None
        ref_ds = None
        
    except ImportError:
        # 没有 GDAL，使用普通保存
        cv2.imwrite(output_path, data)


def colorize_mask(pred_mask: np.ndarray) -> np.ndarray:
    """
    将预测掩码转换为彩色可视化图像
    
    Args:
        pred_mask: 预测掩码 (H, W)，值为类别索引
    
    Returns:
        彩色图像 (H, W, 3) BGR格式
    """
    color_mask = np.zeros((pred_mask.shape[0], pred_mask.shape[1], 3), dtype=np.uint8)
    for idx, color in enumerate(PALETTE):
        color_mask[pred_mask == idx] = color[::-1]  # RGB to BGR
    return color_mask


def load_model(config_file: str, checkpoint_file: str, device: str = "cuda:0"):
    from mmseg.apis import init_model

    print(f"[MMSeg] Loading model from {checkpoint_file}", file=sys.stderr)
    model = init_model(config_file, checkpoint_file, device=device)
    print("[MMSeg] Model loaded successfully", file=sys.stderr)
    return model


def run_inference_with_model(
    model,
    input_dir: str,
    output_dir: str,
    file_names: List[str],
    opacity: float = 0.3,
    progress_callback=None,
    should_cancel=None,
    batch_size: int = 1,
    forward_fn=None,
) -> dict:
    """
    运行 MMSegmentation 推理（支持瓦片批量前向）。

    Args:
        model: 已初始化的 MMSeg 模型
        input_dir: 输入图片目录
        output_dir: 输出目录
        file_names: 待处理文件名列表
        opacity: 叠加透明度
        batch_size: 每次前向的瓦片数量；1 为逐张（原行为）。>1 时同批瓦片
            一次前向，GPU 显存占用随批线性增加，需实测显存上限。
        forward_fn: 覆盖默认 mmseg 推理调用（测试注入用）；
            签名 forward_fn(model, imgs)，imgs 为列表时返回结果列表。

    Returns:
        推理结果字典
    """
    if forward_fn is None:
        from mmseg.apis import inference_model as forward_fn

    try:
        batch_size = max(1, int(batch_size or 1))
    except (TypeError, ValueError):
        batch_size = 1

    os.makedirs(output_dir, exist_ok=True)

    results = []
    total = len(file_names)
    processed = 0

    def _progress():
        nonlocal processed
        processed += 1
        if progress_callback is not None:
            progress_callback(processed, total)

    def _render(img_array, pred_mask, filename):
        pred = pred_mask.astype(np.uint8)
        color_mask = colorize_mask(pred)
        if len(img_array.shape) == 3 and img_array.shape[2] >= 3:
            img_rgb = img_array[:, :, :3]
            if img_rgb.max() > 1:
                img_rgb = img_rgb / img_rgb.max() * 255
            img_bgr = img_rgb[:, :, ::-1].astype(np.uint8)
            overlay = cv2.addWeighted(img_bgr, opacity, color_mask, 1 - opacity, 0)
        else:
            overlay = color_mask
        base_name = os.path.splitext(filename)[0]
        out_name = f"pred_{base_name}.png"
        cv2.imwrite(os.path.join(output_dir, out_name), overlay)
        mask_name = f"mask_{base_name}.png"
        cv2.imwrite(os.path.join(output_dir, mask_name), pred)
        return {
            "input_name": filename,
            "name": out_name,
            "output_name": out_name,
            "mask_name": mask_name,
            "status": "success",
        }

    for chunk_start in range(0, total, batch_size):
        chunk = file_names[chunk_start:chunk_start + batch_size]

        # 1. 组批加载：坏瓦片单独记录错误，不进入批次
        imgs, names, failed = [], [], {}
        for filename in chunk:
            if should_cancel is not None and should_cancel():
                raise RuntimeError("INFERENCE_CANCELLED")
            img_array = load_rs_image_with_gdal(os.path.join(input_dir, filename), to_float32=True)
            if img_array is None:
                failed[filename] = {
                    "input_name": filename,
                    "name": filename,
                    "status": "error",
                    "error": "Failed to load image",
                }
            else:
                imgs.append(img_array)
                names.append(filename)

        # 2. 批量前向；整批失败时降级为逐张重试，坏瓦片单独报错不拖累好瓦片
        det_outputs = None
        if imgs:
            try:
                det = forward_fn(model, imgs)
                det_list = list(det) if isinstance(det, (list, tuple)) else [det]
                if len(det_list) != len(imgs):
                    raise RuntimeError(f"批量前向返回数量不符: {len(det_list)} != {len(imgs)}")
                det_outputs = det_list
            except Exception as exc:
                print(f"[MMSeg] 批量前向失败（{len(imgs)} 张），降级为逐张重试: {exc}", file=sys.stderr)
                det_outputs = []
                for name, img in zip(names, imgs):
                    if should_cancel is not None and should_cancel():
                        raise RuntimeError("INFERENCE_CANCELLED")
                    try:
                        det_outputs.append(forward_fn(model, img))
                    except Exception as single_exc:
                        det_outputs.append({
                            "input_name": name,
                            "name": name,
                            "status": "error",
                            "error": str(single_exc),
                        })

        # 3. 统一后处理：按原始顺序对齐（加载失败的直接落错误条目）
        img_by_name = dict(zip(names, imgs))
        for filename in chunk:
            if should_cancel is not None and should_cancel():
                raise RuntimeError("INFERENCE_CANCELLED")
            if filename in failed:
                results.append(failed[filename])
                print(f"[MMSeg] Error processing {filename}: Failed to load image", file=sys.stderr)
                _progress()
                continue
            det = det_outputs[names.index(filename)]
            if isinstance(det, dict) and det.get("status") == "error":
                results.append(det)
                print(f"[MMSeg] Error processing {filename}: {det.get('error')}", file=sys.stderr)
                _progress()
                continue
            try:
                pred_mask = det.pred_sem_seg.data[0].cpu().numpy()
                rendered = _render(img_by_name[filename], pred_mask, filename)
                results.append(rendered)
                print(f"[MMSeg] Processed: {filename} -> {rendered['name']}", file=sys.stderr)
            except Exception as e:
                results.append({
                    "input_name": filename,
                    "name": filename,
                    "status": "error",
                    "error": str(e),
                })
                print(f"[MMSeg] Error processing {filename}: {e}", file=sys.stderr)
            _progress()

    return {
        "status": "completed",
        "total": len(file_names),
        "success": sum(1 for r in results if r.get("status") == "success"),
        "results": results,
    }


def run_inference(
    config_file: str,
    checkpoint_file: str,
    input_dir: str,
    output_dir: str,
    file_names: List[str],
    device: str = "cuda:0",
    opacity: float = 0.3,
) -> dict:
    model = load_model(config_file, checkpoint_file, device=device)
    return run_inference_with_model(
        model,
        input_dir=input_dir,
        output_dir=output_dir,
        file_names=file_names,
        opacity=opacity,
    )


def main():
    parser = argparse.ArgumentParser(description="MMSegmentation Inference")
    parser.add_argument("--config", required=True, help="Model config file path")
    parser.add_argument("--checkpoint", required=True, help="Model checkpoint file path")
    parser.add_argument("--input_dir", required=True, help="Input directory")
    parser.add_argument("--output_dir", required=True, help="Output directory")
    parser.add_argument("--file_names", required=True, help="Comma-separated file names")
    parser.add_argument("--device", default="cuda:0", help="Device (cuda:0 or cpu)")
    parser.add_argument("--opacity", type=float, default=0.3, help="Overlay opacity")
    
    args = parser.parse_args()
    
    file_names = [f.strip() for f in args.file_names.split(",") if f.strip()]
    
    result = run_inference(
        config_file=args.config,
        checkpoint_file=args.checkpoint,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        file_names=file_names,
        device=args.device,
        opacity=args.opacity
    )
    
    # 输出 JSON 结果到 stdout
    print(json.dumps(result))


if __name__ == "__main__":
    main()
