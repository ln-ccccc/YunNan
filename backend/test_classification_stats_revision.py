# -*- coding: utf-8 -*-
"""审校统计逐年份合并与空成果显式空状态（2026-11-14 审查问题7）单元测试。

覆盖纯文件级逻辑：
- 连续修订两个年份，前一次的审校值不得被后一次冲回推理口径；
- 空成果写入显式空状态（该年全 0 + per_year empty 标记），不得保留旧占比；
- 旧格式 revised 块（无 per_year）在下次写入时自愈补建。
带 DB 的版本保护（问题8）见 test_project_api.TestClassificationStatsRevisionProtection。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from applications.project_hub.classification_stats import apply_revised_class_ratio


def _polygon(coordinates):
    return {"type": "Polygon", "coordinates": coordinates}


def _square(lon, lat, side=0.001):
    return _polygon([[
        [lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat],
    ]])


def _feature(class_code, geometry):
    return {"type": "Feature", "properties": {"class_code": class_code}, "geometry": geometry}


def _all_class_fc(class_code):
    return {"type": "FeatureCollection", "features": [
        _feature(class_code, _square(100.0, 26.0)),
        _feature(class_code, _square(100.01, 26.0)),
    ]}


class ApplyRevisedRatioMergeTests(unittest.TestCase):
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

    def _read_revised(self):
        payload = json.loads((self.fid_dir / "class_ratio_percent.json").read_text(encoding="utf-8"))
        return payload, payload["revised"]

    def test_second_year_revision_keeps_first_year_value(self):
        # 问题7：先审 2023（全草地），再审 2024（全建筑）——2023 的审校值不得被
        # 第二次回写冲回推理口径 30%
        self.assertTrue(apply_revised_class_ratio(10, 713, 2023, 1, _all_class_fc(0)))
        self.assertTrue(apply_revised_class_ratio(10, 713, 2024, 2, _all_class_fc(2)))
        _, revised = self._read_revised()
        self.assertAlmostEqual(revised["series_percent"]["grassland"][0], 100.0, places=1)
        self.assertAlmostEqual(revised["series_percent"]["building"][1], 100.0, places=1)
        # 逐年份记录同时存在
        self.assertEqual(revised["per_year"]["2023"]["revision_no"], 1)
        self.assertEqual(revised["per_year"]["2024"]["revision_no"], 2)

    def test_empty_collection_writes_explicit_empty_state(self):
        # 问题7：删除全部图斑保存为空集合 → 必须写入显式空状态，不得保留旧占比
        self.assertTrue(apply_revised_class_ratio(10, 713, 2024, 1, _all_class_fc(2)))
        empty_fc = {"type": "FeatureCollection", "features": []}
        self.assertTrue(apply_revised_class_ratio(10, 713, 2024, 2, empty_fc))
        payload, revised = self._read_revised()
        for name in ("grassland", "forest", "building", "road", "bareground", "water"):
            self.assertEqual(revised["series_percent"][name][1], 0.0)
        # 2023 位保持推理原值
        self.assertAlmostEqual(revised["series_percent"]["grassland"][0], 30.0, places=1)
        self.assertTrue(revised["per_year"]["2024"]["empty"])
        self.assertIsNone(revised["per_year"]["2024"]["percent"])
        # 推理口径原值不受影响
        self.assertAlmostEqual(payload["series_percent"]["grassland"][1], 40.0, places=1)

    def test_empty_state_survives_subsequent_other_year_revision(self):
        # 2024 清空后再审 2023：2024 的空状态不得回退成推理口径
        self.assertTrue(apply_revised_class_ratio(10, 713, 2024, 1, {"type": "FeatureCollection", "features": []}))
        self.assertTrue(apply_revised_class_ratio(10, 713, 2023, 3, _all_class_fc(0)))
        _, revised = self._read_revised()
        self.assertEqual(revised["series_percent"]["building"][1], 0.0)
        self.assertAlmostEqual(revised["series_percent"]["grassland"][0], 100.0, places=1)

    def test_legacy_revised_block_without_per_year_is_healed(self):
        # 旧格式（无 per_year）已写入 2024 审校值 → 再审 2023 时旧值自愈保留
        payload = json.loads((self.fid_dir / "class_ratio_percent.json").read_text(encoding="utf-8"))
        payload["revised"] = {
            "revision_no": 4,
            "area_source": "vector_shoelace",
            "year": 2024,
            "series_percent": {
                "grassland": [30.0, 0.0], "forest": [20.0, 0.0], "building": [10.0, 100.0],
                "road": [5.0, 0.0], "bareground": [15.0, 0.0], "water": [20.0, 0.0],
            },
        }
        (self.fid_dir / "class_ratio_percent.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        self.assertTrue(apply_revised_class_ratio(10, 713, 2023, 5, _all_class_fc(0)))
        _, revised = self._read_revised()
        # 旧 2024 建筑审校值保留，2023 新审校值生效
        self.assertAlmostEqual(revised["series_percent"]["building"][1], 100.0, places=1)
        self.assertAlmostEqual(revised["series_percent"]["grassland"][0], 100.0, places=1)
        self.assertEqual(revised["per_year"]["2024"]["revision_no"], 4)
        self.assertEqual(revised["per_year"]["2023"]["revision_no"], 5)


if __name__ == "__main__":
    unittest.main()
