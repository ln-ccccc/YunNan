"""超大 ROI 裁剪上限 + 黑边（无有效信息像元）掩膜的单元测试。

对应方案：docs/refactor/2026-09-22-large-imagery-nodata-plan.md
- crop_bbox_from_raster：bbox 超过 MAX_CROP_EDGE 降采样读且地理参考保持
- compute_tile_valid_mask：nodata 命中 / 近黑（max≤2）/ 掩膜带 0 三类无效
- prepare_tiles：整块黑边 ROI 跳过；部分黑边落 _valid.png
- distribute_outputs：mask 255 / pred 置黑 / label.tif 255 三处落点
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.kml_roi.raster_ops import (
    MAX_CROP_EDGE,
    compute_tile_valid_mask,
    crop_bbox_from_raster,
)
from applications.kml_roi.tiles import distribute_outputs, prepare_tiles


ROI_FULL = {
    "type": "Polygon",
    "coordinates": [
        [[100.0, 0.0], [140.0, 0.0], [140.0, 40.0], [100.0, 40.0], [100.0, 0.0]]
    ],
}


def _write_rgb_tif(path: Path, array_hwc: np.ndarray, *, transform, nodata=None) -> None:
    """按 [H, W, 3] uint8 数组写 3 波段 GeoTIFF（EPSG:4326）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = array_hwc.shape[:2]
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.transpose(array_hwc, (2, 0, 1)))


class TestCropBboxCap(unittest.TestCase):
    def test_huge_bbox_is_capped_and_georef_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            width, height = 6000, 4000
            # 栅格铺满 ROI（100~140, 0~40）：x 方向 40/6000，y 方向 40/4000
            pixel_x, pixel_y = 40.0 / width, 40.0 / height
            array = np.full((height, width, 3), 120, dtype=np.uint8)
            source = root / "huge.tif"
            _write_rgb_tif(source, array, transform=from_origin(100.0, 40.0, pixel_x, pixel_y))

            cropped = root / "crop.tif"
            self.assertTrue(crop_bbox_from_raster(source, ROI_FULL, cropped))

            with rasterio.open(source) as src, rasterio.open(cropped) as out:
                self.assertLessEqual(max(out.width, out.height), MAX_CROP_EDGE)
                # 地理范围在 1.5 个降采样像元内保持一致（transform 缩放正确性）
                scale = max(out.width, out.height) / max(src.width, src.height)
                tolerance = max(pixel_x, pixel_y) * max(src.width, src.height) * scale * 1.5
                for a, b in zip(src.bounds, out.bounds):
                    self.assertAlmostEqual(a, b, delta=tolerance)
                self.assertEqual(out.crs, src.crs)
                # 降采样读仍取到有效像元（非全黑/全空）
                data = out.read(1)
                self.assertGreater(float(data.mean()), 0.0)

    def test_small_bbox_keeps_native_resolution(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            array = np.full((4, 8, 3), 120, dtype=np.uint8)
            source = root / "small.tif"
            _write_rgb_tif(source, array, transform=from_origin(100.0, 40.0, 5.0, 10.0))

            roi_right_half = {
                "type": "Polygon",
                "coordinates": [
                    [[120.0, 10.0], [140.0, 10.0], [140.0, 40.0], [120.0, 40.0], [120.0, 10.0]]
                ],
            }
            cropped = root / "crop.tif"
            self.assertTrue(crop_bbox_from_raster(source, roi_right_half, cropped))
            with rasterio.open(cropped) as out:
                # 小 ROI 不触发上限：全分辨率裁剪（右半 4 列 × y∈[10,40] 对应的上 3 行）
                self.assertEqual((out.width, out.height), (4, 3))


class TestComputeTileValidMask(unittest.TestCase):
    def test_nodata_metadata_and_near_black_are_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            array = np.full((4, 4, 3), 120, dtype=np.uint8)
            array[:, :2] = 0          # 左半：nodata 命中
            array[0, 3] = 1           # 右上一个近黑像元（≠nodata 但 max≤2）
            tif = root / "mixed.tif"
            _write_rgb_tif(tif, array, transform=from_origin(100.0, 40.0, 1.0, 1.0), nodata=0)

            mask = compute_tile_valid_mask(tif, (4, 4))
            self.assertIsNotNone(mask)
            self.assertFalse(mask[:, :2].any(), "nodata 列应为无效")
            self.assertTrue(mask[:, 2].all(), "纯有效列应为有效")
            self.assertTrue(mask[1:, 3].all(), "有效列其余行应为有效")
            self.assertFalse(mask[0, 3], "近黑像元（max≤2）应为无效")

    def test_read_failure_returns_none_fallback_all_valid(self):
        self.assertIsNone(compute_tile_valid_mask(Path("nonexistent.tif"), (4, 4)))


class TestPrepareTilesBlackEdge(unittest.TestCase):
    @staticmethod
    def _features(fid="101"):
        return [(fid, ROI_FULL)]

    def test_all_black_roi_is_skipped_entirely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            black = np.zeros((64, 64, 3), dtype=np.uint8)
            old_tif = root / "old.tif"
            _write_rgb_tif(old_tif, black, transform=from_origin(100.0, 40.0, 10.0 / 64, 10.0 / 64), nodata=0)

            matched, file_names, _ = prepare_tiles(old_tif, old_tif, self._features(), root / "tiles", year="2024")

            self.assertEqual(matched, [])
            self.assertEqual(file_names, [])

    def test_partial_black_roi_queues_tile_and_writes_valid_png(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            array = np.full((64, 64, 3), 120, dtype=np.uint8)
            array[:, :32] = 0  # 左半黑边
            old_tif = root / "old.tif"
            _write_rgb_tif(old_tif, array, transform=from_origin(100.0, 40.0, 10.0 / 64, 10.0 / 64), nodata=0)

            matched, file_names, variants = prepare_tiles(
                old_tif, old_tif, self._features(), root / "tiles", year="2024"
            )

            self.assertEqual(matched, ["101"])
            self.assertEqual(len(file_names), 1)
            valid_png = root / "tiles" / "101+2024_tile_valid.png"
            self.assertTrue(valid_png.exists())
            valid = cv2.imread(str(valid_png), cv2.IMREAD_UNCHANGED)
            self.assertEqual(set(np.unique(valid).tolist()), {0, 255})
            self.assertAlmostEqual(float((valid > 127).mean()), 0.5, delta=0.05)
            self.assertTrue(variants["101"])


class TestDistributeOutputsMasking(unittest.TestCase):
    def test_products_mask_invalid_pixels(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tile_dir = root / "tiles"
            mmseg_dir = root / "mmseg_out"
            output_root = root / "outputs"
            tile_dir.mkdir(parents=True)
            mmseg_dir.mkdir(parents=True)

            src_base = "101+2024_tile"
            # 裁剪栅格：左半 nodata 黑边、右半有效（4×8，与既有 fixture 同构）
            array = np.full((4, 8, 3), 120, dtype=np.uint8)
            array[:, :4] = 0
            crop_tif = tile_dir / f"{src_base}.tif"
            _write_rgb_tif(crop_tif, array, transform=from_origin(100.0, 40.0, 5.0, 10.0), nodata=0)

            valid_mask = compute_tile_valid_mask(crop_tif, (32, 32))
            self.assertIsNotNone(valid_mask)
            self.assertFalse(valid_mask[:, :16].any())
            cv2.imwrite(str(tile_dir / f"{src_base}_valid.png"), (valid_mask.astype(np.uint8)) * 255)
            cv2.imwrite(str(tile_dir / f"{src_base}.png"), np.full((32, 32, 3), 120, dtype=np.uint8))

            # 伪造模型产物：类别 1 覆盖全图（含黑边——正是要被掩掉的假预测）
            cv2.imwrite(str(mmseg_dir / f"pred_{src_base}.png"), np.full((32, 32, 3), (0, 128, 0), dtype=np.uint8))
            cv2.imwrite(str(mmseg_dir / f"mask_{src_base}.png"), np.ones((32, 32), dtype=np.uint8))

            variants = {
                "101": [
                    {
                        "dst_base": "101+2024",
                        "src_base": src_base,
                        "raster_path": crop_tif,
                        "geom": ROI_FULL,
                        "already_cropped": True,
                    }
                ]
            }
            summary = distribute_outputs(
                ["101"], mmseg_dir, output_root, variants, tile_dir=tile_dir, staging_root=root / "publish"
            )
            self.assertEqual(summary["written_fids"], 1)

            fid_dir = output_root / "101"
            out_mask = cv2.imread(str(fid_dir / "101+2024_mask.png"), cv2.IMREAD_UNCHANGED)
            left, right = out_mask[:, :16], out_mask[:, 16:]
            self.assertTrue((left == 255).all(), "黑边区类别应置 255（nodata 约定）")
            self.assertTrue((right == 1).all(), "有效区保留模型类别")

            out_img = cv2.imread(str(fid_dir / "101+2024.png"), cv2.IMREAD_COLOR)
            # 罗列边界线沿图像边缘绘制（既有 UX），断言避开边缘 4px：
            # 黑边内部置黑、有效内部保留预测颜色
            self.assertTrue((out_img[8:24, 4:12] == 0).all(), "黑边区展示应置黑")
            self.assertTrue(
                (out_img[8:24, 20:28] != 0).any(axis=2).all(), "有效区保留预测颜色"
            )

            with rasterio.open(fid_dir / "101+2024_label.tif") as label:
                labels = label.read(1)
                self.assertEqual(label.nodata, 255)
                self.assertTrue((labels[:, : labels.shape[1] // 2] == 255).all(), "标签 GeoTIFF 黑边区应为 255")
                self.assertTrue((labels[:, labels.shape[1] // 2 :] == 1).all())


if __name__ == "__main__":
    unittest.main()
