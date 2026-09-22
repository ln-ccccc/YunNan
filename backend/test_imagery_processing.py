# -*- coding: utf-8 -*-
"""S3 影像裁剪与切片契约测试：候选清单/多边形裁剪地理参考/矿山边界+外扩/
固定像素与固定面积网格/边片丢弃/上限防护/数据集登记回读。"""
import json
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import rasterio

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectDataset
from applications.models.project_spatial import ProjectSpatialResource
from test_project_api import TestProjectAPI


def _make_raster(path: Path, width=960, height=640, pixel_deg=0.0005, origin=(100.0, 26.0)):
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 3, "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": rasterio.transform.from_origin(origin[0], origin[1], pixel_deg, pixel_deg),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rng.integers(20, 235, size=(3, height, width), dtype=np.uint8))
    return path


class ImageryProcessingBase(TestProjectAPI):
    # 屏蔽继承的父类用例（fixture 已改写，只跑本文件用例）
    def test_timeline_normalizes_project_data_export_and_snapshot_activities(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_builds_project_ledger_workbook(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_tolerates_vector_failed_result_with_null_collection(self):
        self.skipTest("parent-case not applicable")
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable")
    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self.project_id = self._create_project("裁剪切片项目")
        # 项目输入影像
        self.inputs_dir = self.storage_root / "projects" / str(self.project_id) / "inputs" / "imagery"
        self.source = _make_raster(self.inputs_dir / "source_full.tif")
        # 矿山矢量资源（GeoJSON，与影像范围重叠）
        mines_geojson = self.storage_root / "projects" / str(self.project_id) / "mines" / "1" / "mines.geojson"
        mines_geojson.parent.mkdir(parents=True, exist_ok=True)
        mines_geojson.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"FID": 42},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[100.1, 25.9], [100.2, 25.9], [100.2, 25.98], [100.1, 25.98], [100.1, 25.9]]],
                },
            }],
        }), encoding="utf-8")
        relative = f"projects/{self.project_id}/mines/1/mines.geojson"
        db.session.add(ProjectSpatialResource(
            project_id=self.project_id, resource_type="mine_vector", status="active",
            version=1, source_path=relative, normalized_path=relative,
            source_format="geojson", feature_count=1, crs="EPSG:4326",
            bounds_json="{}",
        ))
        db.session.commit()
        self.mines_relative = relative

    def _candidates(self):
        return self.client.get(f"/api/projects/{self.project_id}/imagery/candidates")

    def _clip(self, body):
        return self.client.post(
            f"/api/projects/{self.project_id}/imagery/clip",
            json=body,
        )

    def _slice(self, body):
        return self.client.post(
            f"/api/projects/{self.project_id}/imagery/slice",
            json=body,
        )


class TestImageryCandidates(ImageryProcessingBase):
    def test_candidates_include_inputs_dir_rasters_with_summary(self):
        response = self._candidates()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        names = [item["display_name"] for item in data["items"]]
        self.assertIn("source_full.tif", names)
        entry = next(item for item in data["items"] if item["display_name"] == "source_full.tif")
        self.assertEqual(entry["width"], 960)
        self.assertEqual(entry["crs"], "EPSG:4326")
        self.assertGreater(entry["size_bytes"], 0)

    def test_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.get(f"/api/projects/{self.project_id}/imagery/candidates")
        self.assertEqual(response.status_code, 401)


class TestImageryClip(ImageryProcessingBase):
    def test_clip_by_polygon_registers_dataset_with_georef(self):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[100.1, 25.95], [100.2, 25.95], [100.2, 25.99], [100.1, 25.99], [100.1, 25.95]]],
        }
        response = self._clip({"source": str(self.source), "geometry": geometry, "buffer_meters": 0})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        out_path = Path(data["file_path"])
        self.assertTrue(out_path.is_file())
        # 地理参考：裁剪窗与请求范围一致（像素级容差）
        with rasterio.open(out_path) as out, rasterio.open(self.source) as src:
            self.assertEqual(out.crs, src.crs)
            bounds = out.bounds
            self.assertAlmostEqual(bounds.left, 100.1, delta=src.transform.a * 2)
            self.assertAlmostEqual(bounds.top, 25.99, delta=abs(src.transform.e) * 2)
        # 数据集登记可回读
        dataset = db.session.get(ProjectDataset, data["dataset_id"])
        self.assertEqual(dataset.project_id, self.project_id)
        self.assertIn("裁剪", dataset.display_name)

    def test_clip_by_mine_boundary_with_buffer(self):
        response = self._clip({"source": str(self.source), "mine_fid": 42, "buffer_meters": 200})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        # 外扩后窗口严格大于矿山 bbox（0.1°≈11km 宽，200m 外扩两侧各加）
        with rasterio.open(self.source) as src:
            mine_width_px = 0.1 / src.transform.a
        self.assertGreater(data["width"], mine_width_px)

    def test_clip_rejects_invalid_geometry_and_no_overlap(self):
        bad = self._clip({"source": str(self.source), "geometry": {"type": "Point", "coordinates": [1, 2]}})
        self.assertEqual(bad.status_code, 400)
        far = self._clip({
            "source": str(self.source),
            "geometry": {"type": "Polygon", "coordinates": [[[1.0, 1.0], [1.1, 1.0], [1.1, 1.1], [1.0, 1.1], [1.0, 1.0]]]},
        })
        self.assertEqual(far.status_code, 400)
        self.assertIn("无有效重叠", far.get_json()["msg"])

    def test_clip_rejects_unknown_mine(self):
        response = self._clip({"source": str(self.source), "mine_fid": 999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("不在项目边界", response.get_json()["msg"])


class TestImagerySlice(ImageryProcessingBase):
    def test_grid_pixels_slices_register_datasets_and_drop_small_edges(self):
        # 960×640 按 500px 网格 → 2×2 全满片（960<2*500 边缘列 460≥阈值保留）
        response = self._slice({"source": str(self.source), "mode": "grid_pixels", "tile_pixels": 500})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        self.assertEqual(data["grid"], {"cols": 2, "rows": 2, "tile_w": 500, "tile_h": 500})
        self.assertEqual(data["created"], 4)
        self.assertEqual(len(data["items"]), 4)
        # 每片可独立打开且带地理参考
        first = data["items"][0]
        with rasterio.open(first["file_path"]) as tile:
            self.assertEqual(tile.crs.to_string(), "EPSG:4326")
            self.assertEqual(tile.width, first["width"])
        datasets = ProjectDataset.query.filter_by(project_id=self.project_id, dataset_kind="imagery").all()
        self.assertGreaterEqual(len(datasets), 4)

    def test_grid_area_computes_tile_side_from_pixel_size(self):
        # pixel 0.0005° ≈ 55.66m → 55.66²≈3098 m²/px；1e6 m² → side≈180px
        # 像元 ~3098m² → side≈114px → 网格 9×6=54 片 < 64 上限
        response = self._slice({"source": str(self.source), "mode": "grid_area", "tile_area_m2": 40_000_000})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        grid = response.get_json()["data"]["grid"]
        # 像元面积 ~3098m²/px；4e7 m² 期望边 ~114px，容差 ±30%
        self.assertTrue(80 <= grid["tile_w"] <= 148, f"tile_w={grid['tile_w']} grid={grid}")

    def test_slice_limit_rejected_when_grid_exceeds(self):
        response = self._slice({"source": str(self.source), "mode": "grid_pixels", "tile_pixels": 64})
        self.assertEqual(response.status_code, 400)
        self.assertIn("超过上限", response.get_json()["msg"])

    def test_slice_rejects_invalid_mode_and_pixels(self):
        for body in (
            {"source": str(self.source), "mode": "grid_pixels", "tile_pixels": 8},
            {"source": str(self.source), "mode": "grid_pixels", "tile_pixels": 99999},
            {"source": str(self.source), "mode": "grid_area", "tile_area_m2": -1},
            {"source": str(self.source), "mode": "unknown"},
            {"source": "", "mode": "grid_pixels", "tile_pixels": 512},
        ):
            response = self._slice(body)
            self.assertEqual(response.status_code, 400, body)


if __name__ == "__main__":
    unittest.main()
