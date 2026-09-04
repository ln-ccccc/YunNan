import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np
import rasterio
from affine import Affine
from rasterio.transform import from_origin

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.kml_roi.raster_ops import (
    draw_polygon_boundary_on_prediction,
    label_transform_for_shape,
    write_label_geotiff,
)
from applications.kml_roi.tiles import cleanup_output_dir, distribute_outputs
from applications.project_hub.inference_results import _prepare_fid_publication


class TestClassificationVectorization(unittest.TestCase):
    ROI = {
        "type": "Polygon",
        "coordinates": [
            [[100.0, 0.0], [140.0, 0.0], [140.0, 40.0], [100.0, 40.0], [100.0, 0.0]]
        ],
    }

    @staticmethod
    def _write_crop_tif(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        profile = {
            "driver": "GTiff",
            "height": 4,
            "width": 8,
            "count": 3,
            "dtype": "uint8",
            "crs": "EPSG:4326",
            "transform": from_origin(100.0, 40.0, 10.0, 10.0),
        }
        with rasterio.open(path, "w", **profile) as dataset:
            dataset.write(np.full((3, 4, 8), 120, dtype=np.uint8))

    @staticmethod
    def _write_png(path: Path, value: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), value):
            raise AssertionError(f"无法写入测试 PNG: {path}")

    def test_label_transform_preserves_crop_bounds_at_label_resolution(self):
        crop_transform = from_origin(100.0, 40.0, 10.0, 10.0)

        label_transform = label_transform_for_shape(crop_transform, 8, 4, (2, 4))

        self.assertEqual(label_transform, from_origin(100.0, 40.0, 20.0, 20.0))
        self.assertEqual(
            rasterio.transform.array_bounds(2, 4, label_transform),
            rasterio.transform.array_bounds(4, 8, crop_transform),
        )

    def test_write_label_geotiff_preserves_metadata_and_sets_roi_exterior_to_nodata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_tif = root / "crop.tif"
            label_tif = root / "label.tif"
            self._write_crop_tif(crop_tif)
            labels = np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8)

            self.assertTrue(write_label_geotiff(labels, crop_tif, self.ROI, label_tif))

            with rasterio.open(crop_tif) as crop, rasterio.open(label_tif) as label:
                self.assertEqual(label.count, 1)
                self.assertEqual(label.dtypes, ("uint8",))
                self.assertEqual(label.nodata, 255)
                self.assertEqual(label.compression, rasterio.enums.Compression.lzw)
                self.assertEqual(label.crs, crop.crs)
                self.assertEqual(label.bounds, crop.bounds)
                self.assertEqual(label.transform, from_origin(100.0, 40.0, 20.0, 20.0))
                np.testing.assert_array_equal(
                    label.read(1),
                    np.asarray([[0, 1, 255, 255], [4, 5, 255, 255]], dtype=np.uint8),
                )

    def test_write_label_geotiff_projects_wgs84_roi_to_crop_crs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_tif = root / "projected-crop.tif"
            label_tif = root / "projected-label.tif"
            profile = {
                "driver": "GTiff",
                "height": 4,
                "width": 4,
                "count": 1,
                "dtype": "uint8",
                "crs": "EPSG:3857",
                "transform": from_origin(0.0, 4000.0, 1000.0, 1000.0),
            }
            with rasterio.open(crop_tif, "w", **profile) as crop:
                crop.write(np.full((1, 4, 4), 120, dtype=np.uint8))
            roi_4326 = {
                "type": "Polygon",
                "coordinates": [
                    [[0.0, 0.0], [0.02, 0.0], [0.02, 0.02], [0.0, 0.02], [0.0, 0.0]]
                ],
            }

            self.assertTrue(write_label_geotiff(np.zeros((4, 4), dtype=np.uint8), crop_tif, roi_4326, label_tif))

            with rasterio.open(label_tif) as label:
                np.testing.assert_array_equal(
                    label.read(1),
                    np.asarray(
                        [[255, 255, 255, 255], [255, 255, 255, 255], [0, 0, 255, 255], [0, 0, 255, 255]],
                        dtype=np.uint8,
                    ),
                )

    def test_write_label_geotiff_rejects_crop_without_crs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_tif = root / "missing-crs.tif"
            with rasterio.open(
                crop_tif,
                "w",
                driver="GTiff",
                height=2,
                width=2,
                count=1,
                dtype="uint8",
                transform=from_origin(100.0, 200.0, 10.0, 10.0),
            ) as crop:
                crop.write(np.ones((1, 2, 2), dtype=np.uint8))

            with self.assertRaisesRegex(RuntimeError, "缺少 CRS"):
                write_label_geotiff(
                    np.zeros((2, 2), dtype=np.uint8),
                    crop_tif,
                    self.ROI,
                    root / "missing-crs-label.tif",
                )

    def test_write_label_geotiff_rejects_identity_crop_transform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_tif = root / "identity-transform.tif"
            with rasterio.open(
                crop_tif,
                "w",
                driver="GTiff",
                height=2,
                width=2,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=Affine.identity(),
            ) as crop:
                crop.write(np.ones((1, 2, 2), dtype=np.uint8))

            with self.assertRaisesRegex(RuntimeError, "地理变换"):
                write_label_geotiff(
                    np.zeros((2, 2), dtype=np.uint8),
                    crop_tif,
                    self.ROI,
                    root / "identity-transform-label.tif",
                )

    def test_write_label_geotiff_rejects_nonidentity_singular_crop_transform(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            crop_tif = root / "singular-transform.tif"
            with rasterio.open(
                crop_tif,
                "w",
                driver="GTiff",
                height=2,
                width=2,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=Affine(1.0, 0.0, 100.0, 0.0, 0.0, 40.0),
            ) as crop:
                crop.write(np.ones((1, 2, 2), dtype=np.uint8))

            with self.assertRaisesRegex(RuntimeError, "地理变换"):
                write_label_geotiff(
                    np.zeros((2, 2), dtype=np.uint8),
                    crop_tif,
                    self.ROI,
                    root / "singular-transform-label.tif",
                )

    def test_distribute_outputs_writes_label_without_changing_existing_preview_contract(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mmseg_dir = root / "mmseg"
            tile_dir = root / "tiles"
            output_root = root / "outputs"
            expected_dir = root / "expected"
            src_base = "101+2024_tile"
            dst_base = "101+2024"
            raw_prediction = np.full((2, 4, 3), 50, dtype=np.uint8)
            raw_mask = np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8)
            tile_image = np.full((2, 4, 3), 120, dtype=np.uint8)
            crop_tif = tile_dir / f"{src_base}.tif"
            self._write_crop_tif(crop_tif)
            self._write_png(mmseg_dir / f"pred_{src_base}.png", raw_prediction)
            self._write_png(mmseg_dir / f"mask_{src_base}.png", raw_mask)
            self._write_png(tile_dir / f"{src_base}.png", tile_image)
            raw_prediction_bytes = (mmseg_dir / f"pred_{src_base}.png").read_bytes()
            raw_mask_bytes = (mmseg_dir / f"mask_{src_base}.png").read_bytes()

            expected_image = expected_dir / f"{dst_base}.png"
            expected_mask = expected_dir / f"{dst_base}_mask.png"
            self.assertTrue(
                draw_polygon_boundary_on_prediction(
                    pred_image_path=mmseg_dir / f"pred_{src_base}.png",
                    pred_mask_path=mmseg_dir / f"mask_{src_base}.png",
                    tile_image_path=tile_dir / f"{src_base}.png",
                    raster_path=crop_tif,
                    geom_4326=self.ROI,
                    out_image_path=expected_image,
                    out_mask_path=expected_mask,
                )
            )

            summary = distribute_outputs(
                ["101"],
                mmseg_dir,
                output_root,
                {
                    "101": [
                        {
                            "src_base": src_base,
                            "dst_base": dst_base,
                            "raster_path": str(crop_tif),
                            "geom": self.ROI,
                            "already_cropped": True,
                        }
                    ]
                },
                tile_dir=tile_dir,
            )

            fid_dir = output_root / "101"
            self.assertEqual(summary, {"written_fids": 1, "written_fid_list": ["101"], "missing_fids": []})
            self.assertEqual((fid_dir / f"{dst_base}.png").read_bytes(), expected_image.read_bytes())
            self.assertEqual((fid_dir / f"{dst_base}_mask.png").read_bytes(), expected_mask.read_bytes())
            self.assertEqual(
                cv2.imread(str(fid_dir / f"{dst_base}_mask.png"), cv2.IMREAD_UNCHANGED).tolist(),
                raw_mask.tolist(),
            )
            self.assertEqual(
                (mmseg_dir / f"pred_{src_base}.png").read_bytes(), raw_prediction_bytes
            )
            self.assertEqual((mmseg_dir / f"mask_{src_base}.png").read_bytes(), raw_mask_bytes)
            with rasterio.open(fid_dir / f"{dst_base}_label.tif") as label:
                self.assertEqual(label.count, 1)
                self.assertEqual(label.dtypes, ("uint8",))
                self.assertEqual(label.nodata, 255)
                np.testing.assert_array_equal(
                    label.read(1),
                    np.asarray([[0, 1, 255, 255], [4, 5, 255, 255]], dtype=np.uint8),
                )

    def test_distribute_outputs_keeps_png_outputs_when_label_crop_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mmseg_dir = root / "mmseg"
            tile_dir = root / "tiles"
            output_root = root / "outputs"
            src_base = "101+2024_tile"
            dst_base = "101+2024"
            self._write_png(mmseg_dir / f"pred_{src_base}.png", np.full((2, 4, 3), 50, dtype=np.uint8))
            self._write_png(mmseg_dir / f"mask_{src_base}.png", np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8))
            self._write_png(tile_dir / f"{src_base}.png", np.full((2, 4, 3), 120, dtype=np.uint8))

            try:
                with self.assertLogs("applications.kml_roi.tiles", level="WARNING") as logs:
                    summary = distribute_outputs(
                        ["101"],
                        mmseg_dir,
                        output_root,
                        {
                            "101": [
                                {
                                    "src_base": src_base,
                                    "dst_base": dst_base,
                                    "geom": self.ROI,
                                    "already_cropped": True,
                                }
                            ]
                        },
                        tile_dir=tile_dir,
                    )
            except Exception as error:
                self.fail(f"标签副产物失败不应中断 PNG 结果分发: {error}")

            fid_dir = output_root / "101"
            self.assertEqual(summary, {"written_fids": 1, "written_fid_list": ["101"], "missing_fids": []})
            self.assertTrue((fid_dir / f"{dst_base}.png").is_file())
            self.assertTrue((fid_dir / f"{dst_base}_mask.png").is_file())
            self.assertFalse((fid_dir / f"{dst_base}_label.tif").exists())
            self.assertIn("fid=101", "\n".join(logs.output))
            self.assertIn(f"src={src_base}", "\n".join(logs.output))
            self.assertIn("error=", "\n".join(logs.output))

    def test_distribute_outputs_keeps_png_outputs_when_label_georeference_is_invalid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mmseg_dir = root / "mmseg"
            tile_dir = root / "tiles"
            output_root = root / "outputs"
            src_base = "101+2024_tile"
            dst_base = "101+2024"
            crop_tif = tile_dir / f"{src_base}.tif"
            tile_dir.mkdir(parents=True)
            with rasterio.open(
                crop_tif,
                "w",
                driver="GTiff",
                height=4,
                width=8,
                count=1,
                dtype="uint8",
                crs="EPSG:4326",
                transform=Affine.identity(),
            ) as crop:
                crop.write(np.full((1, 4, 8), 120, dtype=np.uint8))
            self._write_png(mmseg_dir / f"pred_{src_base}.png", np.full((2, 4, 3), 50, dtype=np.uint8))
            self._write_png(mmseg_dir / f"mask_{src_base}.png", np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8))
            self._write_png(tile_dir / f"{src_base}.png", np.full((2, 4, 3), 120, dtype=np.uint8))
            stale_label = output_root / "101" / f"{dst_base}_label.tif"
            stale_label.parent.mkdir(parents=True)
            stale_label.write_bytes(b"stale-label")

            with self.assertLogs("applications.kml_roi.tiles", level="WARNING") as logs:
                summary = distribute_outputs(
                    ["101"],
                    mmseg_dir,
                    output_root,
                    {
                        "101": [
                            {
                                "src_base": src_base,
                                "dst_base": dst_base,
                                "geom": self.ROI,
                                "already_cropped": True,
                            }
                        ]
                    },
                    tile_dir=tile_dir,
                )

            fid_dir = output_root / "101"
            self.assertEqual(summary, {"written_fids": 1, "written_fid_list": ["101"], "missing_fids": []})
            self.assertTrue((fid_dir / f"{dst_base}.png").is_file())
            self.assertTrue((fid_dir / f"{dst_base}_mask.png").is_file())
            self.assertFalse((fid_dir / f"{dst_base}_label.tif").exists())
            self.assertIn("fid=101", "\n".join(logs.output))
            self.assertIn(f"src={src_base}", "\n".join(logs.output))
            self.assertIn("error=", "\n".join(logs.output))

    def test_cleanup_output_dir_keeps_latest_year_label_and_removes_old_year_label(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fid_dir = Path(temp_dir) / "101"
            fid_dir.mkdir()
            for year in (2022, 2024):
                for suffix in (".png", "_mask.png", "_src.png", "_label.tif"):
                    (fid_dir / f"101+{year}{suffix}").touch()
            (fid_dir / "change_matrix_pixels.csv").touch()
            (fid_dir / "orphan.txt").touch()

            cleanup_output_dir("101", fid_dir, keep_last_years=1)

            self.assertTrue((fid_dir / "101+2024_label.tif").is_file())
            self.assertFalse((fid_dir / "101+2022_label.tif").exists())
            self.assertFalse((fid_dir / "101+2022_mask.png").exists())
            self.assertFalse((fid_dir / "orphan.txt").exists())

    def test_project_publication_includes_label_geotiff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stage_dir = root / "stage" / "101"
            target_dir = root / "published" / "101"
            stage_dir.mkdir(parents=True)
            self._write_png(stage_dir / "101+2024.png", np.full((2, 4, 3), 120, dtype=np.uint8))
            self._write_png(
                stage_dir / "101+2024_mask.png",
                np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8),
            )
            self._write_crop_tif(stage_dir / "101+2024_label.tif")

            publication_dir, _, copied = _prepare_fid_publication(1, 101, stage_dir, target_dir)

            try:
                self.assertIn("101+2024_label.tif", copied)
                self.assertTrue((publication_dir / "101+2024_label.tif").is_file())
            finally:
                shutil.rmtree(publication_dir, ignore_errors=True)

    def test_project_publication_drops_stale_label_when_current_stage_has_none(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stage_dir = root / "stage" / "101"
            target_dir = root / "published" / "101"
            stage_dir.mkdir(parents=True)
            target_dir.mkdir(parents=True)
            self._write_png(stage_dir / "101+2024.png", np.full((2, 4, 3), 120, dtype=np.uint8))
            self._write_png(
                stage_dir / "101+2024_mask.png",
                np.asarray([[0, 1, 2, 3], [4, 5, 0, 1]], dtype=np.uint8),
            )
            (target_dir / "101+2024_label.tif").write_bytes(b"stale-label")

            publication_dir, _, copied = _prepare_fid_publication(1, 101, stage_dir, target_dir)

            try:
                self.assertIn("101+2024.png", copied)
                self.assertFalse((publication_dir / "101+2024_label.tif").exists())
            finally:
                shutil.rmtree(publication_dir, ignore_errors=True)

    def test_project_publication_rejects_non_label_geotiff(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stage_dir = root / "stage" / "101"
            target_dir = root / "published" / "101"
            stage_dir.mkdir(parents=True)
            self._write_png(stage_dir / "101+2024.png", np.full((2, 4, 3), 120, dtype=np.uint8))
            (stage_dir / "101+2024.tif").write_bytes(b"not-a-label")

            publication_dir, _, copied = _prepare_fid_publication(1, 101, stage_dir, target_dir)

            try:
                self.assertNotIn("101+2024.tif", copied)
                self.assertFalse((publication_dir / "101+2024.tif").exists())
            finally:
                shutil.rmtree(publication_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
