# -*- coding: utf-8 -*-
"""分类成果派生统计（2026-11-24 重构）单元测试：纯函数与文件型逻辑。

- 逐修订的地类占比/面积：compute_revision_stats 与 revisions/<版本号>.stats.json
  sidecar（按成果+版本号寻址、不可变、身份不符不采信）；
- 有效成果选择规则 select_effective_result_refs；
- 变化矩阵现值判定 evaluate_change_matrix_state（只读回执 + 矩阵）；
- 栅格化与统一网格（跨 CRS）。
集成链（保存修订 → sidecar/矩阵 → 溯源/趋势消费）见 test_classification_derived_stats.py。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import rasterio
from rasterio.transform import from_origin

from applications.kml_roi.change_matrix import CLASS_NAMES
from applications.project_hub.classification_stats import (
    MATRIX_STATE_PENDING,
    MATRIX_STATE_REVISED,
    MATRIX_STATE_REVISED_EMPTY,
    MATRIX_STATE_UNAVAILABLE,
    _change_matrix_path,
    _revision_stats_path,
    _write_rebuild_marker,
    compute_revision_stats,
    compute_vector_class_ratio,
    evaluate_change_matrix_state,
    rasterize_year_vector,
    read_revision_stats,
    resolve_result_stats,
    select_effective_result_refs,
    write_revision_stats,
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


class ComputeRevisionStatsTests(unittest.TestCase):
    def test_empty_collection_is_explicit_zero_not_a_fallback(self):
        # 人工删光图斑是真实状态：占比全 0，不能回落成推理值或别的年份的值
        stats = compute_revision_stats({"type": "FeatureCollection", "features": []})
        self.assertEqual(stats["percent"], {name: 0.0 for name in CLASS_NAMES})
        self.assertEqual(stats["area_m2"], {name: 0.0 for name in CLASS_NAMES})
        self.assertEqual(stats["feature_count"], 0)

    def test_areas_are_absolute_square_metres(self):
        stats = compute_revision_stats({"type": "FeatureCollection", "features": [
            _feature(5, _square(100.0, 26.0)),
        ]})
        self.assertAlmostEqual(stats["percent"]["water"], 100.0, places=1)
        # 0.001° 边长 ≈ 111m → 量级 1.2e4 m²（球面鞋带）
        self.assertTrue(1.0e4 < stats["area_m2"]["water"] < 1.5e4)

    def test_accepts_stored_json_string(self):
        fc = {"type": "FeatureCollection", "features": [_feature(0, _square(100.0, 26.0))]}
        self.assertEqual(compute_revision_stats(json.dumps(fc)), compute_revision_stats(fc))


class RevisionStatsSidecarTests(unittest.TestCase):
    # 修订派生统计 sidecar：按 (result_id, revision_no) 寻址、内容不可变

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp.name
        self.addCleanup(self._restore)

    def _restore(self):
        if self.previous is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous

    @staticmethod
    def _fc(class_code, count=2):
        return {"type": "FeatureCollection", "features": [
            _feature(class_code, _square(100.0 + index * 0.002, 26.0)) for index in range(count)
        ]}

    def test_write_then_read_roundtrip_carries_identity(self):
        path = write_revision_stats(10, 55, 3, self._fc(2))
        self.assertEqual(path.parent.name, "revisions")
        self.assertEqual(path.name, "3.stats.json")
        stored = read_revision_stats(10, 55, 3)
        self.assertEqual((stored["result_id"], stored["revision_no"]), (55, 3))
        self.assertEqual(stored["feature_count"], 2)
        self.assertAlmostEqual(stored["percent"]["building"], 100.0, places=1)
        self.assertFalse(list(path.parent.glob("*.tmp")), "临时文件不得残留")

    def test_is_addressed_by_result_and_revision(self):
        write_revision_stats(10, 55, 3, self._fc(2))
        self.assertIsNone(read_revision_stats(10, 55, 4))
        self.assertIsNone(read_revision_stats(10, 56, 3))
        self.assertIsNone(read_revision_stats(11, 55, 3))

    def test_tampered_or_corrupt_file_is_rejected(self):
        path = write_revision_stats(10, 55, 3, self._fc(2))
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["revision_no"] = 9
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertIsNone(read_revision_stats(10, 55, 3), "文件名与内容身份不符不得采信")
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_revision_stats(10, 55, 3))

    def test_rewriting_one_revision_never_touches_another(self):
        # 跨年份互冲的根因防护：两个年份的统计各是各的文件，重写一个不动另一个
        write_revision_stats(10, 55, 1, self._fc(0))
        second = write_revision_stats(10, 56, 2, self._fc(1))
        before = second.read_bytes()
        write_revision_stats(10, 55, 1, self._fc(2))
        self.assertEqual(second.read_bytes(), before)
        self.assertAlmostEqual(read_revision_stats(10, 55, 1)["percent"]["building"], 100.0, places=1)
        self.assertAlmostEqual(read_revision_stats(10, 56, 2)["percent"]["forest"], 100.0, places=1)

    def test_resolve_recomputes_when_sidecar_is_missing(self):
        result = SimpleNamespace(
            id=55, current_revision_no=2, feature_count=2,
            current_feature_collection_json=json.dumps(self._fc(3)),
        )
        stats = resolve_result_stats(10, result)
        self.assertAlmostEqual(stats["percent"]["road"], 100.0, places=1)
        self.assertNotIn("computed_at", stats, "现算结果不带落盘时间")
        self.assertFalse(_revision_stats_path(10, 55, 2).exists(), "GET 路径只读：现算不得写文件")

    def test_resolve_prefers_sidecar_once_written(self):
        result = SimpleNamespace(
            id=55, current_revision_no=2, feature_count=2,
            current_feature_collection_json=json.dumps(self._fc(3)),
        )
        write_revision_stats(10, 55, 2, self._fc(3))
        self.assertIn("computed_at", resolve_result_stats(10, result))

    def test_resolve_recomputes_when_feature_count_disagrees(self):
        write_revision_stats(10, 55, 2, self._fc(3, count=2))
        result = SimpleNamespace(
            id=55, current_revision_no=2, feature_count=7,
            current_feature_collection_json=json.dumps(self._fc(3, count=7)),
        )
        self.assertEqual(resolve_result_stats(10, result)["feature_count"], 7)


class SelectEffectiveResultRefsTests(unittest.TestCase):
    # 「同矿山同年哪条成果有效」只有这一份规则——溯源、矩阵、趋势、补算共用

    def test_latest_result_wins_per_year(self):
        refs = select_effective_result_refs([
            (12, 2024, "ready", 0), (11, 2024, "ready", 2), (10, 2023, "ready", 1),
        ])
        self.assertEqual(refs, {2024: (12, 0), 2023: (10, 1)})

    def test_vector_failed_yields_to_non_failed_sibling(self):
        refs = select_effective_result_refs([
            (12, 2024, "vector_failed", 0), (11, 2024, "ready", 1),
        ])
        self.assertEqual(refs, {2024: (11, 1)})

    def test_vector_failed_is_used_when_it_is_the_only_one(self):
        refs = select_effective_result_refs([(12, 2024, "vector_failed", 0)])
        self.assertEqual(refs, {2024: (12, 0)})


class EvaluateChangeMatrixStateTests(unittest.TestCase):
    # 矩阵现值判定只读回执与矩阵两个小文件；覆盖正常、待重算与三种终态

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.previous = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp.name
        self.addCleanup(self._restore)
        self.refs = {2023: (10, 0), 2024: (11, 2)}
        self.matrix_path = _change_matrix_path(10, 713)
        self.matrix_path.parent.mkdir(parents=True)

    def _restore(self):
        if self.previous is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous

    def _write_matrix(self, basis):
        self.matrix_path.write_text(json.dumps({"basis": basis, "matrix": []}), encoding="utf-8")

    def test_without_manual_revision_the_inference_matrix_is_the_right_basis(self):
        self.assertEqual(
            evaluate_change_matrix_state(10, 713, {2023: (10, 0), 2024: (11, 0)}),
            ("inference", None),
        )

    def test_manual_revision_outside_the_matrix_years_is_inference(self):
        # 修订只落在 2020（不在最近两期里）：矩阵与人工修订无关，推理矩阵即正确口径；
        # 否则会永远判"待重算"，每次读取都白触发一遍
        refs = {2020: (9, 3), 2023: (10, 0), 2024: (11, 0)}
        self.assertEqual(evaluate_change_matrix_state(10, 713, refs), ("inference", None))

    def test_manual_revision_without_receipt_is_pending(self):
        state, note = evaluate_change_matrix_state(10, 713, self.refs)
        self.assertEqual(state, MATRIX_STATE_PENDING)
        self.assertIn("重算", note)

    def test_receipt_for_another_set_of_results_is_pending(self):
        self._write_matrix("revised_vector")
        _write_rebuild_marker(
            10, 713, {2023: (10, 0), 2024: (11, 1)}, (2023, 2024), "written", has_matrix=True
        )
        self.assertEqual(evaluate_change_matrix_state(10, 713, self.refs)[0], MATRIX_STATE_PENDING)

    def test_written_matrix_matching_receipt_is_revised(self):
        self._write_matrix("revised_vector")
        _write_rebuild_marker(10, 713, self.refs, (2023, 2024), "written", has_matrix=True)
        self.assertEqual(
            evaluate_change_matrix_state(10, 713, self.refs), (MATRIX_STATE_REVISED, None)
        )

    def test_matrix_overwritten_by_publish_is_pending_again(self):
        # 推理发布整文件覆盖矩阵（内容变了但回执还在）必须判为待重算，
        # 不能把推理矩阵当成审校矩阵
        self._write_matrix("revised_vector")
        _write_rebuild_marker(10, 713, self.refs, (2023, 2024), "written", has_matrix=True)
        self._write_matrix("inference")
        self.assertEqual(
            evaluate_change_matrix_state(10, 713, self.refs)[0], MATRIX_STATE_PENDING
        )

    def test_invalidated_and_skipped_are_terminal_states_with_notes(self):
        self._write_matrix("revised_vector_invalid")
        _write_rebuild_marker(
            10, 713, self.refs, (2023, 2024), "invalidated", "2024 年图斑为空", has_matrix=True
        )
        state, note = evaluate_change_matrix_state(10, 713, self.refs)
        self.assertEqual(state, MATRIX_STATE_REVISED_EMPTY)
        self.assertIn("图斑为空", note)
        _write_rebuild_marker(10, 713, self.refs, (2023,), "skipped", "不足两期成果")
        state, note = evaluate_change_matrix_state(10, 713, self.refs)
        self.assertEqual(state, MATRIX_STATE_UNAVAILABLE)
        self.assertIn("不足两期成果", note)


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
