# -*- coding: utf-8 -*-
"""分类统计口径改造（2026-10-31 方案 b）单元测试：纯函数与文件型逻辑。

集成链（保存修订 → 回写 → 溯源/趋势消费）见 test_project_api.py。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from applications.project_hub.classification_stats import (
    apply_revised_class_ratio,
    compute_vector_class_ratio,
    rasterize_year_vector,
    resolve_ratio_series,
)


def _polygon(coordinates):
    return {"type": "Polygon", "coordinates": coordinates}


def _square(lon, lat, side=0.001):
    return _polygon([[
        [lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat],
    ]])


def _feature(class_code, geometry):
    return {"type": "Feature", "properties": {"class_code": class_code}, "geometry": geometry}


class ComputeVectorClassRatioTests(unittest.TestCase):
    def test_two_polygons_are_area_weighted(self):
        # 同边长两类各一块：面积各半
        fc = {"type": "FeatureCollection", "features": [
            _feature(0, _square(100.0, 26.0)),
            _feature(1, _square(100.001, 26.0)),
        ]}
        ratio = compute_vector_class_ratio(fc)
        self.assertAlmostEqual(ratio["grassland"], 50.0, places=1)
        self.assertAlmostEqual(ratio["forest"], 50.0, places=1)
        self.assertAlmostEqual(sum(ratio.values()), 100.0, places=0)

    def test_hole_and_multipolygon_are_handled(self):
        # 带洞面（建筑）：洞占外环四分之一面积，洞不应计入；MultiPolygon（林地）两面合并
        outer = _square(100.0, 26.0, 0.002)  # 外环 4 单位
        hole = _square(100.0005, 26.0005, 0.001)["coordinates"][0]  # 洞 1 单位
        with_hole = _polygon([outer["coordinates"][0], hole])  # 净 3 单位
        multi = {"type": "MultiPolygon", "coordinates": [
            _square(100.01, 26.0)["coordinates"],
            _square(100.011, 26.0)["coordinates"],
        ]}  # 2 单位
        fc = {"type": "FeatureCollection", "features": [
            _feature(2, with_hole),
            _feature(2, with_hole),  # 环方向相反的重复面也应按绝对面积累加
            _feature(1, multi),
        ]}
        ratio = compute_vector_class_ratio(fc)
        self.assertAlmostEqual(ratio["building"], 75.0, places=1)  # 6/(6+2)
        self.assertAlmostEqual(ratio["forest"], 25.0, places=1)
        self.assertAlmostEqual(sum(ratio.values()), 100.0, places=0)

    def test_empty_or_invalid_returns_none(self):
        self.assertIsNone(compute_vector_class_ratio({"type": "FeatureCollection", "features": []}))
        self.assertIsNone(compute_vector_class_ratio({"type": "FeatureCollection", "features": [
            _feature(9, _square(100.0, 26.0)),  # class_code 越界被忽略
        ]}))
        self.assertIsNone(compute_vector_class_ratio(None))


class ResolveRatioSeriesTests(unittest.TestCase):
    def test_revised_block_takes_priority(self):
        doc = {
            "years": [2023, 2024],
            "series_percent": {"grassland": [10.0, 20.0]},
            "revised": {
                "revision_no": 3,
                "area_source": "vector_shoelace",
                "series_percent": {"grassland": [10.0, 55.5]},
            },
        }
        series, source, revision = resolve_ratio_series(doc)
        self.assertEqual(series["grassland"], [10.0, 55.5])
        self.assertEqual(source, "vector_shoelace")
        self.assertEqual(revision, 3)

    def test_falls_back_to_inference_series(self):
        series, source, revision = resolve_ratio_series({
            "years": [2024], "series_percent": {"grassland": [30.0]},
        })
        self.assertEqual(series["grassland"], [30.0])
        self.assertEqual(source, "inference_mask")
        self.assertIsNone(revision)
        self.assertEqual(resolve_ratio_series(None), (None, None, None))


class ApplyRevisedClassRatioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage)
        self.fid_dir = self.storage / "projects" / "10" / "outputs" / "inference" / "713"
        self.fid_dir.mkdir(parents=True)
        (self.fid_dir / "class_ratio_percent.json").write_text(json.dumps({
            "fid": "713",
            "class_names": ["grassland", "forest", "building", "road", "bareground", "water"],
            "years": [2023, 2024],
            "totals": [100, 200],
            "series_percent": {
                "grassland": [30.0, 40.0], "forest": [20.0, 20.0], "building": [10.0, 10.0],
                "road": [5.0, 5.0], "bareground": [15.0, 15.0], "water": [20.0, 10.0],
            },
        }, ensure_ascii=False), encoding="utf-8")

    def test_revised_block_written_at_year_index(self):
        fc = {"type": "FeatureCollection", "features": [
            _feature(0, _square(100.0, 26.0)),
            _feature(0, _square(100.001, 26.0)),
        ]}
        self.assertTrue(apply_revised_class_ratio(10, 713, 2024, 5, fc))
        payload = json.loads((self.fid_dir / "class_ratio_percent.json").read_text(encoding="utf-8"))
        revised = payload["revised"]
        self.assertEqual(revised["revision_no"], 5)
        self.assertEqual(revised["area_source"], "vector_shoelace")
        # 2024 索引位被审校值替换，2023 位保持推理原值
        self.assertAlmostEqual(revised["series_percent"]["grassland"][1], 100.0, places=1)
        self.assertAlmostEqual(revised["series_percent"]["grassland"][0], 30.0, places=1)
        # 推理口径原值保留
        self.assertAlmostEqual(payload["series_percent"]["grassland"][1], 40.0, places=1)

    def test_year_not_in_series_and_missing_file_return_false(self):
        fc = {"type": "FeatureCollection", "features": [_feature(0, _square(100.0, 26.0))]}
        self.assertFalse(apply_revised_class_ratio(10, 713, 2019, 1, fc))
        (self.fid_dir / "class_ratio_percent.json").unlink()
        self.assertFalse(apply_revised_class_ratio(10, 713, 2024, 1, fc))


class RasterizeYearVectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage)
        self.fid_dir = self.storage / "projects" / "10" / "outputs" / "inference" / "714"
        self.fid_dir.mkdir(parents=True)
        # 10x10 label：左半水体(5)、右半裸地(4)、边角 nodata(255)；CRS 直接用 4326
        data = np.full((10, 10), 255, dtype=np.uint8)
        data[:, :5] = 5
        data[:, 5:] = 4
        self.transform = from_origin(100.0, 26.0, 0.001, 0.001)
        with rasterio.open(
            self.fid_dir / "714+2024_label.tif", "w", driver="GTiff",
            height=10, width=10, count=1, dtype="uint8",
            crs="EPSG:4326", transform=self.transform, nodata=255,
        ) as dataset:
            dataset.write(data, 1)

    def test_features_are_rasterized_to_label_grid(self):
        # 覆盖左半的面 → 水体(5)；覆盖右上角的面 → 裸地(4)
        fc = {"type": "FeatureCollection", "features": [
            _feature(5, _polygon([[[100.0, 26.0], [100.004, 26.0], [100.004, 25.996], [100.0, 25.996], [100.0, 26.0]]])),
            _feature(4, _polygon([[[100.008, 26.0], [100.01, 26.0], [100.01, 25.998], [100.008, 25.998], [100.008, 26.0]]])),
        ]}
        raster = rasterize_year_vector(10, 714, 2024, fc)
        self.assertIsNotNone(raster)
        self.assertEqual(raster[0, 0], 5)
        self.assertEqual(raster[0, 9], 4)
        self.assertEqual(raster.shape, (10, 10))

    def test_missing_label_or_empty_collection_returns_none(self):
        fc = {"type": "FeatureCollection", "features": [_feature(0, _square(100.0, 26.0))]}
        self.assertIsNone(rasterize_year_vector(10, 714, 2019, fc))
        empty = {"type": "FeatureCollection", "features": []}
        self.assertIsNone(rasterize_year_vector(10, 714, 2024, empty))


class RasterizeUnifiedGridTests(unittest.TestCase):
    """跨 CRS 年份的统一网格（2026-10-31 数值审查）：2018 期 label 为 4326 度
    网格、2024 期为 3857 米网格——两期矢量必须栅格化到同一 transform/CRS 才能
    逐像素对比，否则转移矩阵静默产出空间不对应的错误数据。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.storage = Path(self.temp.name)
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage)
        self.fid_dir = self.storage / "projects" / "10" / "outputs" / "inference" / "713"
        self.fid_dir.mkdir(parents=True)
        # 2023 label：4326 度网格（8x8，左半草地 0 右半裸地 4）
        data = np.full((8, 8), 255, dtype=np.uint8)
        data[:, :4] = 0
        data[:, 4:] = 4
        with rasterio.open(
            self.fid_dir / "713+2023_label.tif", "w", driver="GTiff",
            height=8, width=8, count=1, dtype="uint8",
            crs="EPSG:4326", transform=from_origin(100.0, 26.0, 0.001, 0.001), nodata=255,
        ) as dataset:
            dataset.write(data, 1)
        # 2024 label：3857 米网格——用 warp.transform 把 2023 网格的同一地理
        # 范围精确转到 3857，保证两期网格空间对齐（这正是统一网格的语义）
        from rasterio.warp import transform as warp_transform
        west, east = 100.0, 100.008
        south, north = 25.992, 26.0
        mx, my = warp_transform("EPSG:4326", "EPSG:3857", [west, east], [south, north])
        with rasterio.open(
            self.fid_dir / "713+2024_label.tif", "w", driver="GTiff",
            height=8, width=8, count=1, dtype="uint8",
            crs="EPSG:3857",
            transform=from_origin(min(mx), max(my), (max(mx) - min(mx)) / 8, (max(my) - min(my)) / 8),
            nodata=255,
        ) as dataset:
            dataset.write(data, 1)

    def test_target_grid_aligns_cross_crs_years(self):
        # 2023 矢量（4326）：左半草地；2024 矢量（4326 描述的同一地理位置）：右半裸地
        # 统一到 2024 的 3857 网格后：左半应是 0（无矢量覆盖处 255? 不——栅格化 fill=255，
        # 但草地面覆盖左半 → 左半 0），右半 4
        fc_2023 = {"type": "FeatureCollection", "features": [
            _feature(0, _polygon([[[100.0, 26.0], [100.004, 26.0], [100.004, 25.992], [100.0, 25.992], [100.0, 26.0]]])),
        ]}
        with rasterio.open(self.fid_dir / "713+2023_label.tif") as dataset:
            grid_2023 = (dataset.transform, dataset.shape, dataset.crs)
        # 2023 label 自身网格（4326）：栅格化 2023 矢量
        raster_2023 = rasterize_year_vector(10, 713, 2023, fc_2023, target_grid=grid_2023)
        self.assertIsNotNone(raster_2023)
        self.assertEqual(int(raster_2023[0, 0]), 0, "草地应落在左上角（4326 网格）")
        self.assertEqual(int(raster_2023[0, 7]), 255, "4326 矢量未覆盖右下角")

    def test_4326_vector_rasterized_onto_3857_grid(self):
        # 4326 描述的面（右半裸地）栅格化到 3857 网格：同地理位置必须落对位置
        fc = {"type": "FeatureCollection", "features": [
            _feature(4, _polygon([[[100.004, 26.0], [100.008, 26.0], [100.008, 25.992], [100.004, 25.992], [100.004, 26.0]]])),
        ]}
        with rasterio.open(self.fid_dir / "713+2024_label.tif") as dataset:
            grid_2024 = (dataset.transform, dataset.shape, dataset.crs)
        raster = rasterize_year_vector(10, 713, 2024, fc, target_grid=grid_2024)
        self.assertIsNotNone(raster)
        # 右半（经度 100.004-100.008 ≈ 网格列 4-7）应为裸地 4
        self.assertEqual(int(raster[0, 6]), 4)
        self.assertEqual(int(raster[0, 0]), 255, "左半未被该面覆盖")


if __name__ == "__main__":
    unittest.main()
