# -*- coding: utf-8 -*-
"""S3 影像裁剪与切片契约测试：候选清单/多边形裁剪地理参考/矿山边界+外扩/
固定像素与固定面积网格/边片丢弃/上限防护/数据集登记回读。"""
import base64
import io
import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectDataset
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.imagery_processing import (
    ImageryProcessingError,
    _pixel_area_m2,
)
from applications.project_hub.inference_inputs import (
    ProjectInferenceInputError,
    _resolve_registered_tif,
)
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


def _make_crs_raster(path: Path, width, height, transform, crs):
    """按给定仿射与 CRS 构造小栅格（问题9 像元面积测试用）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 3, "dtype": "uint8",
        "crs": crs, "transform": transform,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(rng.integers(20, 235, size=(3, height, width), dtype=np.uint8))
    return path


def _web_mercator_origin(width, height, pixel_m, center_lon, center_lat):
    """中心点落在 (center_lon, center_lat) 的 3857 仿射 origin（左上角）。"""
    from rasterio.warp import transform as warp_transform

    mx, my = warp_transform("EPSG:4326", "EPSG:3857", [center_lon], [center_lat])
    return mx[0] - width * pixel_m / 2.0, my[0] + height * pixel_m / 2.0


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
        # 相对 storage 根的 key：新契约下浏览器只允许提交 dataset_id 或该形态（AGENTS §6）
        self.source_key = f"projects/{self.project_id}/inputs/imagery/source_full.tif"
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

    def test_candidates_never_expose_server_paths(self):
        data = self._candidates().get_json()["data"]
        for item in data["items"]:
            self.assertNotIn("file_path", item)
            if item.get("dataset_id") is None:
                self.assertTrue(str(item.get("storage_key", "")).startswith("projects/"), item)

    def test_absolute_path_source_is_rejected(self):
        # AGENTS §6：浏览器不得提交服务器物理路径；旧实现曾接受项目内绝对路径
        response = self._clip({
            "source": str(self.source),
            "geometry": {"type": "Polygon", "coordinates": [[[100.1, 25.95], [100.2, 25.95], [100.2, 25.99], [100.1, 25.99], [100.1, 25.95]]]},
            "buffer_meters": 0,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("不合法", response.get_json()["msg"])

    def test_cross_project_storage_key_is_rejected(self):
        # AGENTS §9 沙箱归属：source key 必须落在当前项目前缀下（审查 P3-3）
        response = self._clip({
            "source": "projects/999999/inputs/imagery/source_full.tif",
            "geometry": {"type": "Polygon", "coordinates": [[[100.1, 25.95], [100.2, 25.95], [100.2, 25.99], [100.1, 25.99], [100.1, 25.95]]]},
            "buffer_meters": 0,
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("不合法", response.get_json()["msg"])

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
        response = self._clip({"source": self.source_key, "geometry": geometry, "buffer_meters": 0})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        self.assertNotIn("file_path", data)
        out_path = self.storage_root / data["storage_key"]
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
        response = self._clip({"source": self.source_key, "mine_fid": 42, "buffer_meters": 200})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        # 外扩后窗口严格大于矿山 bbox（0.1°≈11km 宽，200m 外扩两侧各加）
        with rasterio.open(self.source) as src:
            mine_width_px = 0.1 / src.transform.a
        self.assertGreater(data["width"], mine_width_px)

    def test_clip_rejects_invalid_geometry_and_no_overlap(self):
        bad = self._clip({"source": self.source_key, "geometry": {"type": "Point", "coordinates": [1, 2]}})
        self.assertEqual(bad.status_code, 400)
        far = self._clip({
            "source": self.source_key,
            "geometry": {"type": "Polygon", "coordinates": [[[1.0, 1.0], [1.1, 1.0], [1.1, 1.1], [1.0, 1.1], [1.0, 1.0]]]},
        })
        self.assertEqual(far.status_code, 400)
        self.assertIn("无有效重叠", far.get_json()["msg"])

    def test_clip_rejects_unknown_mine(self):
        response = self._clip({"source": self.source_key, "mine_fid": 999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("不在项目边界", response.get_json()["msg"])


class TestImagerySlice(ImageryProcessingBase):
    def test_grid_pixels_slices_register_datasets_and_drop_small_edges(self):
        # 960×640 按 500px 网格 → 2×2 全满片（960<2*500 边缘列 460≥阈值保留）
        response = self._slice({"source": self.source_key, "mode": "grid_pixels", "tile_pixels": 500})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        self.assertEqual(data["grid"], {"cols": 2, "rows": 2, "tile_w": 500, "tile_h": 500})
        self.assertEqual(data["created"], 4)
        self.assertEqual(len(data["items"]), 4)
        # 每片可独立打开且带地理参考
        first = data["items"][0]
        self.assertNotIn("file_path", first)
        with rasterio.open(self.storage_root / first["storage_key"]) as tile:
            self.assertEqual(tile.crs.to_string(), "EPSG:4326")
            self.assertEqual(tile.width, first["width"])
        datasets = ProjectDataset.query.filter_by(project_id=self.project_id, dataset_kind="imagery").all()
        self.assertGreaterEqual(len(datasets), 4)

    def test_grid_area_computes_tile_side_from_pixel_size(self):
        # pixel 0.0005° ≈ 55.66m → 55.66²≈3098 m²/px；1e6 m² → side≈180px
        # 像元 ~3098m² → side≈114px → 网格 9×6=54 片 < 64 上限
        response = self._slice({"source": self.source_key, "mode": "grid_area", "tile_area_m2": 40_000_000})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        grid = response.get_json()["data"]["grid"]
        # 像元面积 ~3098m²/px；4e7 m² 期望边 ~114px，容差 ±30%
        self.assertTrue(80 <= grid["tile_w"] <= 148, f"tile_w={grid['tile_w']} grid={grid}")

    def test_slice_limit_rejected_when_grid_exceeds(self):
        response = self._slice({"source": self.source_key, "mode": "grid_pixels", "tile_pixels": 64})
        self.assertEqual(response.status_code, 400)
        self.assertIn("超过上限", response.get_json()["msg"])

    def test_slice_rejects_invalid_mode_and_pixels(self):
        for body in (
            {"source": self.source_key, "mode": "grid_pixels", "tile_pixels": 8},
            {"source": self.source_key, "mode": "grid_pixels", "tile_pixels": 99999},
            {"source": self.source_key, "mode": "grid_area", "tile_area_m2": -1},
            {"source": self.source_key, "mode": "unknown"},
            {"source": "", "mode": "grid_pixels", "tile_pixels": 512},
        ):
            response = self._slice(body)
            self.assertEqual(response.status_code, 400, body)


class TestFilePathContract(ImageryProcessingBase):
    """契约 B（2026-10-24 审查 P1-1）：ProjectDataset.file_path 唯一规范形态
    是 storage 根相对键——clip/slice 曾写绝对路径，而推理输入校验只认受控相对
    键，产物永远无法推理；legacy 绝对路径仅根内双形态兼容读。"""

    def _clip_by_polygon(self, source):
        geometry = {
            "type": "Polygon",
            "coordinates": [[[100.1, 25.95], [100.2, 25.95], [100.2, 25.99], [100.1, 25.99], [100.1, 25.95]]],
        }
        return self._clip({"source": source, "geometry": geometry, "buffer_meters": 0})

    def test_clip_registers_relative_key_passing_inference_input_check(self):
        response = self._clip_by_polygon(self.source_key)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        dataset = db.session.get(ProjectDataset, response.get_json()["data"]["dataset_id"])
        key = dataset.file_path
        self.assertFalse(Path(key).is_absolute())
        self.assertTrue(key.startswith(f"projects/{self.project_id}/inputs/imagery/"))
        # P1-1 核心断言：裁剪产物直接通过推理输入校验（修前此处必抛错）
        tif = _resolve_registered_tif(self.storage_root, key, self.project_id)
        self.assertTrue(tif.is_file())

    def test_slice_registers_relative_keys(self):
        response = self._slice({"source": self.source_key, "mode": "grid_pixels", "tile_pixels": 512})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        items = response.get_json()["data"]["items"]
        self.assertTrue(items)
        for item in items:
            dataset = db.session.get(ProjectDataset, item["dataset_id"])
            self.assertFalse(Path(dataset.file_path).is_absolute())
            _resolve_registered_tif(self.storage_root, dataset.file_path, self.project_id)

    def test_inference_input_check_rejects_foreign_project_keys(self):
        with self.assertRaises(ProjectInferenceInputError):
            _resolve_registered_tif(
                self.storage_root,
                f"projects/{self.project_id + 1}/inputs/imagery/x.tif",
                self.project_id,
            )

    def test_inference_input_check_still_accepts_incoming_keys(self):
        incoming = _make_raster(self.storage_root / "incoming" / "upload.tif", width=64, height=64)
        tif = _resolve_registered_tif(self.storage_root, "incoming/upload.tif", self.project_id)
        self.assertEqual(tif, incoming.resolve())

    def test_legacy_absolute_dataset_path_resolves_inside_root(self):
        # 迁移前存量行（绝对路径且根内）：按 dataset_id 裁剪仍可用（双形态兼容读）
        dataset = ProjectDataset(
            project_id=self.project_id, dataset_kind="imagery",
            display_name="legacy绝对路径", file_path=str(self.source), source_format="tif",
        )
        db.session.add(dataset)
        db.session.commit()
        response = self._clip_by_polygon(str(dataset.id))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def test_legacy_absolute_dataset_path_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = _make_raster(Path(tmp) / "outside.tif", width=64, height=64)
            dataset = ProjectDataset(
                project_id=self.project_id, dataset_kind="imagery",
                display_name="根外绝对路径", file_path=str(outside), source_format="tif",
            )
            db.session.add(dataset)
            db.session.commit()
            response = self._clip_by_polygon(str(dataset.id))
            self.assertEqual(response.status_code, 400)
            self.assertIn("不合法", response.get_json()["msg"])


class TestNodataMaskPreservation(ImageryProcessingBase):
    """2026-10-24 审查 P1-4：clip/slice 曾 pop 掉 nodata 声明且不写掩膜带，
    源影像的黑边/补零区在产物中与有效像元不可区分，推理/预览当真实地物处理。"""

    def _clip_left_quarter(self, key):
        # 覆盖影像左带（含 nodata/掩膜区）的裁剪窗口
        geometry = {
            "type": "Polygon",
            "coordinates": [[[100.0, 25.9], [100.12, 25.9], [100.12, 25.99], [100.0, 25.99], [100.0, 25.9]]],
        }
        return self._clip({"source": key, "geometry": geometry, "buffer_meters": 0})

    def test_clip_preserves_nodata_declaration_and_valid_domain(self):
        nodata_tif = self.inputs_dir / "nodata_edge.tif"
        nodata_tif.parent.mkdir(parents=True, exist_ok=True)
        rng = np.random.default_rng(7)
        profile = {
            "driver": "GTiff", "height": 640, "width": 960, "count": 3, "dtype": "uint8",
            "crs": "EPSG:4326", "nodata": 0,
            "transform": rasterio.transform.from_origin(100.0, 26.0, 0.0005, 0.0005),
        }
        data = rng.integers(20, 235, size=(3, 640, 960), dtype=np.uint8)
        data[:, :, :240] = 0  # 左 1/4 为 nodata 黑边
        with rasterio.open(nodata_tif, "w", **profile) as dst:
            dst.write(data)

        response = self._clip_left_quarter(f"projects/{self.project_id}/inputs/imagery/nodata_edge.tif")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        out = self.storage_root / response.get_json()["data"]["storage_key"]
        with rasterio.open(out) as clipped:
            self.assertEqual(clipped.nodata, 0, "nodata 声明必须随产物保留")
            pixels = clipped.read()
            valid = np.all(clipped.read_masks() > 0, axis=0)
            invalid_pixels = np.all(pixels == 0, axis=0)
            self.assertTrue(invalid_pixels.any(), "测试影像应含 nodata 区")
            # 有效域逐像元一致：全 0 黑边必须判无效，其余必须判有效
            self.assertFalse(valid[invalid_pixels].any())
            self.assertTrue(valid[~invalid_pixels].all())

    def test_slice_preserves_mask_band_without_nodata_declaration(self):
        # 无 nodata 声明、仅内部掩膜带的源：掩膜同样必须透传到切片
        masked_tif = self.inputs_dir / "masked_edge.tif"
        _make_raster(masked_tif)
        with rasterio.open(masked_tif, "r+") as src:
            mask = np.full((src.height, src.width), 255, dtype=np.uint8)
            mask[:, : src.width // 4] = 0
            src.write_mask(mask)

        response = self._slice({
            "source": f"projects/{self.project_id}/inputs/imagery/masked_edge.tif",
            "mode": "grid_pixels", "tile_pixels": 256,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        invalid_tile_seen = False
        for item in response.get_json()["data"]["items"]:
            with rasterio.open(self.storage_root / item["storage_key"]) as tile:
                valid = np.all(tile.read_masks() > 0, axis=0)
                if (~valid).any():
                    invalid_tile_seen = True
                    # 无效带在源左 1/4：该片的第 0 列必须整列无效
                    self.assertFalse(valid[:, 0].any())
        self.assertTrue(invalid_tile_seen, "左带切片应携带无效掩膜（修前掩膜带丢失）")


class TestImageryPreview(ImageryProcessingBase):
    """影像上手绘底图（2026-10-24 审查 P1-8）：降采样 PNG + 4326 bounds + 有效域 alpha。"""

    def _preview(self, source=None, **params):
        query = {"source": source or self.source_key}
        query.update(params)
        return self.client.get(
            f"/api/projects/{self.project_id}/imagery/preview", query_string=query
        )

    def test_preview_returns_downsampled_png_with_geographic_bounds(self):
        response = self._preview(max_size=300)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()["data"]
        self.assertNotIn("file_path", data)
        bounds = data["bounds"]
        # 源 960×640 @0.0005°：west=100.0 north=26.0 east=100.48 south=25.68
        self.assertAlmostEqual(bounds["west"], 100.0, delta=1e-4)
        self.assertAlmostEqual(bounds["north"], 26.0, delta=1e-4)
        self.assertAlmostEqual(bounds["east"], 100.48, delta=1e-3)
        self.assertAlmostEqual(bounds["south"], 25.68, delta=1e-3)
        self.assertEqual((data["width"], data["height"]), (300, 200))
        self.assertEqual((data["source_width"], data["source_height"]), (960, 640))
        image = Image.open(io.BytesIO(base64.b64decode(data["png_base64"])))
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.size, (300, 200))

    def test_preview_clamps_max_size_and_never_upsamples(self):
        clamped = self._preview(max_size=50).get_json()["data"]
        self.assertEqual(clamped["width"], 256, "max_size 下限 256")
        passthrough = self._preview(max_size=10000).get_json()["data"]
        self.assertEqual(
            (passthrough["width"], passthrough["height"]), (960, 640), "小于上限的影像不得被放大"
        )

    def test_preview_rejects_absolute_and_cross_project_source(self):
        self.assertEqual(self._preview(source=str(self.source)).status_code, 400)
        self.assertEqual(
            self._preview(source="projects/999999/inputs/imagery/source_full.tif").status_code, 400
        )

    def test_preview_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.get(
            f"/api/projects/{self.project_id}/imagery/preview",
            query_string={"source": self.source_key},
        )
        self.assertEqual(response.status_code, 401)

    def test_preview_marks_masked_pixels_transparent(self):
        # P1-4 口径：nodata 区不得伪装成真实地物——预览透传为 alpha=0
        masked_key = f"projects/{self.project_id}/inputs/imagery/source_masked.tif"
        masked_path = self.storage_root / masked_key
        profile = {
            "driver": "GTiff", "height": 200, "width": 200, "count": 3, "dtype": "uint8",
            "crs": "EPSG:4326", "nodata": 0,
            "transform": rasterio.transform.from_origin(101.0, 27.0, 0.0005, 0.0005),
        }
        with rasterio.open(masked_path, "w", **profile) as dst:
            block = np.zeros((3, 200, 200), dtype=np.uint8)
            block[:, :, :100] = 120  # 左半有效，右半保持 nodata=0
            dst.write(block)

        data = self._preview(source=masked_key).get_json()["data"]
        image = Image.open(io.BytesIO(base64.b64decode(data["png_base64"])))
        alpha = np.array(image.split()[-1])
        self.assertTrue((alpha[:, :100] == 255).all(), "有效区不透明")
        self.assertTrue((alpha[:, 100:] == 0).all(), "nodata 区必须透明")


class TestPixelAreaM2(unittest.TestCase):
    """像元真实面积换算（2026-11-14 审查问题9）。

    旧实现把 EPSG:3857 以米计的 bounds 纵坐标直接当纬度参与余弦计算——
    北纬 25°、10m 像元算出 ~18 m²（真实应为 100·cos²(25°)≈82.14 m²）；
    无 CRS 时仍猜测面积，与调用方"缺少有效地理参考应拒绝"的预期冲突。
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def _area(self, name, transform, crs):
        path = _make_crs_raster(self.dir / name, 4, 4, transform, crs)
        with rasterio.open(path) as src:
            return _pixel_area_m2(src)

    def test_web_mercator_uses_true_latitude(self):
        # 中心 (100°E, 25°N)，10m 像元：真实面积 = 100·cos²(25°) ≈ 82.14 m²
        x0, y1 = _web_mercator_origin(4, 4, 10.0, 100.0, 25.0)
        area = self._area(
            "mercator.tif",
            rasterio.transform.from_origin(x0, y1, 10.0, 10.0),
            "EPSG:3857",
        )
        expected = 100.0 * math.cos(math.radians(25.0)) ** 2
        self.assertAlmostEqual(area, expected, places=2, msg=f"area={area} expected≈{expected}")

    def test_geographic_degrees_formula_unchanged(self):
        # 4326 中心 25°N、0.001° 像元：(0.001·111320)²·cos(25°)
        area = self._area(
            "geo.tif",
            rasterio.transform.from_origin(100.0, 25.002, 0.001, 0.001),
            "EPSG:4326",
        )
        expected = (0.001 * 111320.0) ** 2 * math.cos(math.radians(25.0))
        self.assertAlmostEqual(area, expected, places=2)

    def test_metric_projection_is_direct(self):
        # UTM 47N 10m 像元：投影米制单位直接 100 m²
        area = self._area(
            "utm.tif",
            rasterio.transform.from_origin(500000.0, 2760000.0, 10.0, 10.0),
            "EPSG:32647",
        )
        self.assertAlmostEqual(area, 100.0, places=6)

    def test_missing_crs_returns_none(self):
        area = self._area(
            "nocrs.tif",
            rasterio.transform.from_origin(100.0, 26.0, 0.001, 0.001),
            None,
        )
        self.assertIsNone(area)

    def test_rotated_affine_uses_determinant(self):
        # 旋转/剪切像元面积 = |a·e − b·d|：10/-10 主轴 + 3/3 剪切 → 109 m²（CRS 单位）
        from affine import Affine
        from rasterio.warp import transform as warp_transform

        mx, my = warp_transform("EPSG:4326", "EPSG:3857", [100.0], [25.0])
        transform = Affine(10.0, 3.0, mx[0] - 26.0, 3.0, -10.0, my[0] + 14.0)
        area = self._area("rotated.tif", transform, "EPSG:3857")
        expected = 109.0 * math.cos(math.radians(25.0)) ** 2
        self.assertAlmostEqual(area, expected, places=2)

    def test_non_metre_projection_rejected(self):
        # 非米制投影（NAD83 / North Carolina ftUS）不得静默按数值当米
        with self.assertRaises(ImageryProcessingError) as ctx:
            self._area(
                "foot.tif",
                rasterio.transform.from_origin(2000000.0, 500000.0, 10.0, 10.0),
                "EPSG:2264",
            )
        self.assertIn("非米制", str(ctx.exception))


class TestGridAreaCRSHandling(ImageryProcessingBase):
    # 屏蔽继承的父类用例（fixture 已改写，只跑本类用例）
    def test_timeline_normalizes_project_data_export_and_snapshot_activities(self):
        self.skipTest("parent-case not applicable")

    def test_xlsx_export_builds_project_ledger_workbook(self):
        self.skipTest("parent-case not applicable")

    def test_xlsx_export_tolerates_vector_failed_result_with_null_collection(self):
        self.skipTest("parent-case not applicable")

    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable")

    def test_grid_area_on_web_mercator_uses_true_pixel_area(self):
        # 3857 10m 像元 @25°N 真实 ≈82.14 m²/px；面积目标 = 像元面积×400
        # → side = 20px。旧实现把米当纬度算出 ~18 m²/px → side≈46px（错误）
        x0, y1 = _web_mercator_origin(200, 200, 10.0, 100.0, 25.0)
        source = _make_crs_raster(
            self.inputs_dir / "source_3857.tif",
            200, 200,
            rasterio.transform.from_origin(x0, y1, 10.0, 10.0),
            "EPSG:3857",
        )
        key = f"projects/{self.project_id}/inputs/imagery/{source.name}"
        pixel_area = 100.0 * math.cos(math.radians(25.0)) ** 2
        response = self._slice({
            "source": key, "mode": "grid_area",
            "tile_area_m2": pixel_area * 400, "limit": 128,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        grid = response.get_json()["data"]["grid"]
        self.assertEqual(grid["tile_w"], 20)
        self.assertEqual(grid["tile_h"], 20)

    def test_grid_area_without_crs_rejected(self):
        _make_crs_raster(
            self.inputs_dir / "source_nocrs.tif",
            200, 200,
            rasterio.transform.from_origin(100.0, 26.0, 0.001, 0.001),
            None,
        )
        key = f"projects/{self.project_id}/inputs/imagery/source_nocrs.tif"
        response = self._slice({"source": key, "mode": "grid_area", "tile_area_m2": 40_000})
        self.assertEqual(response.status_code, 400)
        self.assertIn("缺少有效地理参考", response.get_json()["msg"])


if __name__ == "__main__":
    unittest.main()
