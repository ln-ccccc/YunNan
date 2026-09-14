"""同 fid 多 Placemark 的数据正确性测试（2026-09-15 审计批次 Y2-1）。

历史缺陷链（三处叠加导致同 fid 第二个图斑被静默丢弃）：
1. spatial_index.filter_features_by_bounds 的 STRtree 路径按 fid 去重，
   无 shapely 回退路径却不去重——两条路径行为矛盾；
2. tiles.prepare_tiles 同 fid 第二个 feature 覆盖同名瓦片产物，
   且 variants_by_fid[fid] 被整组覆盖；
3. kml_merge.merge_kml_increment 的 base 内同 fid 多 Placemark 只记最后一个，
   替换后其余同 fid 残留，合并结果出现重复 fid。

硬性约束：fid 唯一（云南主链路 565 矿山）时行为与历史完全一致，
既有 test_kml_roi_pipeline / test_spectral_indices 中的相关用例即为其回归门。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.kml_roi.kml import load_kml_features
from applications.kml_roi.kml_merge import merge_kml_increment
from applications.kml_roi.spatial_index import filter_features_by_bounds
from applications.kml_roi.tiles import prepare_tiles


def _polygon(lon0, lat0, lon1, lat1):
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon0, lat0],
                [lon1, lat0],
                [lon1, lat1],
                [lon0, lat1],
                [lon0, lat0],
            ]
        ],
    }


def _placemark_xml(fid, lon0, lat0, lon1, lat1):
    return f"""<Placemark><name>{fid}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>
{lon0},{lat0},0 {lon1},{lat0},0 {lon1},{lat1},0 {lon0},{lat1},0 {lon0},{lat0},0
</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"""


def _kml_document(placemarks_xml):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f"{placemarks_xml}</Document></kml>"
    )


class FilterFeaturesByBoundsDuplicateFidTests(unittest.TestCase):
    """Y2-1a：同 fid 多 Placemark 不得被空间过滤丢弃，两条路径行为一致。"""

    BOUNDS = (102.0, 25.0, 102.2, 25.18)

    def _features(self):
        return [
            ("23", _polygon(102.02, 25.02, 102.08, 25.08)),  # 相交，第 1 个图斑
            ("23", _polygon(102.12, 25.10, 102.18, 25.16)),  # 相交，同 fid 第 2 个图斑
            ("24", _polygon(102.02, 25.02, 102.08, 25.08)),  # 相交，其他 fid
            ("25", _polygon(102.30, 25.00, 102.40, 25.10)),  # 不相交
            ("26", _polygon(0.0, 0.0, 0.1, 0.1)),  # 不相交
        ]

    def test_same_fid_placemarks_are_all_kept(self):
        out = filter_features_by_bounds(self._features(), self.BOUNDS)
        self.assertEqual([fid for fid, _ in out], ["23", "23", "24"])

    def test_strtree_and_fallback_paths_agree(self):
        strtree_out = filter_features_by_bounds(self._features(), self.BOUNDS)
        with patch("applications.kml_roi.spatial_index._shapely_box", None), patch(
            "applications.kml_roi.spatial_index._STRtree", None
        ):
            fallback_out = filter_features_by_bounds(self._features(), self.BOUNDS)
        self.assertEqual(strtree_out, fallback_out)

    def test_unique_fid_behavior_unchanged(self):
        features = [
            ("23", _polygon(102.02, 25.02, 102.08, 25.08)),
            ("24", _polygon(102.12, 25.10, 102.18, 25.16)),
            ("25", _polygon(102.30, 25.00, 102.40, 25.10)),
        ]
        out = filter_features_by_bounds(features, self.BOUNDS)
        self.assertEqual([fid for fid, _ in out], ["23", "24"])


class PrepareTilesDuplicateFidTests(unittest.TestCase):
    """Y2-1b：同 fid 多 variant 的瓦片产物必须可区分且不互相覆盖。"""

    def test_same_fid_variants_get_distinct_names(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            features = [
                ("23", _polygon(102.02, 25.02, 102.08, 25.08)),
                ("23", _polygon(102.12, 25.10, 102.18, 25.16)),
            ]
            with patch(
                "applications.kml_roi.tiles.crop_bbox_from_raster", return_value=True
            ), patch("applications.kml_roi.tiles.tif_to_png", return_value=True):
                matched, names, variants = prepare_tiles(
                    root / "old.tif",
                    root / "new.tif",
                    features,
                    root / "tiles",
                    year="2026",
                )

        self.assertEqual(matched, ["23"])
        self.assertEqual(names, ["23+2026_tile.png", "23+2026_v2_tile.png"])
        self.assertEqual(len(variants["23"]), 2)
        self.assertEqual(
            sorted(spec["dst_base"] for spec in variants["23"]),
            ["23+2026", "23+2026_v2"],
        )
        self.assertEqual(
            [spec["geom"] for spec in variants["23"]],
            [features[0][1], features[1][1]],
        )

    def test_unique_fid_naming_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            features = [
                ("23", _polygon(102.02, 25.02, 102.08, 25.08)),
                ("24", _polygon(102.12, 25.10, 102.18, 25.16)),
            ]
            with patch(
                "applications.kml_roi.tiles.crop_bbox_from_raster", return_value=True
            ), patch("applications.kml_roi.tiles.tif_to_png", return_value=True):
                matched, names, variants = prepare_tiles(
                    root / "old.tif",
                    root / "new.tif",
                    features,
                    root / "tiles",
                    year="2026",
                )

        self.assertEqual(matched, ["23", "24"])
        self.assertEqual(
            names, ["23+2026_tile.png", "24+2026_tile.png"]
        )
        self.assertEqual(len(variants["23"]), 1)
        self.assertEqual(len(variants["24"]), 1)


class MergeKmlDuplicateFidTests(unittest.TestCase):
    """Y2-1c：base 内同 fid 多 Placemark 必须全部参与合并，结果无重复 fid。"""

    def test_base_duplicate_fid_placemarks_all_replaced(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_kml = Path(tmp_dir) / "base.kml"
            incoming_kml = Path(tmp_dir) / "incoming.kml"
            base_kml.write_text(
                _kml_document(
                    _placemark_xml("100", 100.0, 20.0, 101.0, 21.0)
                    + _placemark_xml("100", 106.0, 20.0, 107.0, 21.0)
                ),
                encoding="utf-8",
            )
            incoming_kml.write_text(
                _kml_document(_placemark_xml("100", 102.0, 20.0, 103.0, 21.0)),
                encoding="utf-8",
            )

            summary = merge_kml_increment(base_kml, incoming_kml)
            self.assertEqual(summary["updated"], 1)
            self.assertEqual(summary["inserted"], 0)

            features = load_kml_features(base_kml)
            fids = [fid for fid, _ in features]
            self.assertEqual(fids.count("100"), 1)
            ring = features[0][1]["coordinates"][0]
            xs = [pt[0] for pt in ring]
            # 保留的是 incoming 的几何，base 的两个旧图斑都不残留
            self.assertTrue(all(102.0 <= x <= 103.0 for x in xs), msg=str(xs))

    def test_incoming_duplicate_fid_collapses_to_one(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_kml = Path(tmp_dir) / "base.kml"
            incoming_kml = Path(tmp_dir) / "incoming.kml"
            base_kml.write_text(
                _kml_document(_placemark_xml("100", 100.0, 20.0, 101.0, 21.0)),
                encoding="utf-8",
            )
            incoming_kml.write_text(
                _kml_document(
                    _placemark_xml("100", 102.0, 20.0, 103.0, 21.0)
                    + _placemark_xml("100", 104.0, 20.0, 105.0, 21.0)
                ),
                encoding="utf-8",
            )

            merge_kml_increment(base_kml, incoming_kml)
            fids = [fid for fid, _ in load_kml_features(base_kml)]
            self.assertEqual(fids.count("100"), 1)


class DuplicateFidPipelineTests(unittest.TestCase):
    """Y2-1 端到端：同 fid 双 Placemark KML，两个图斑都被切片/推理/落盘。

    推理器用 fake（真实链路在 GPU 验收批次覆盖），瓦片裁切与产物分发走真实实现。
    """

    @staticmethod
    def _write_tif(path, value):
        import rasterio
        from rasterio.transform import from_origin

        profile = {
            "driver": "GTiff",
            "width": 200,
            "height": 200,
            "count": 3,
            "dtype": "uint8",
            "crs": "EPSG:4326",
            "transform": from_origin(102.0, 25.2, 0.001, 0.001),
        }
        data = np.full((3, 200, 200), value, dtype=np.uint8)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)

    def test_both_placemarks_tiled_inferred_and_written(self):
        def fake_run_mmseg_tiles(*, model_id, data_path, out_dir, file_names, device):
            import cv2

            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            for name in file_names:
                base = name.rsplit(".", 1)[0]
                cv2.imwrite(str(out / f"pred_{base}.png"), np.zeros((32, 32, 3), dtype=np.uint8))
                cv2.imwrite(str(out / f"mask_{base}.png"), np.zeros((32, 32), dtype=np.uint8))
            return [], {}

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            old_tif = root / "old.tif"
            new_tif = root / "new.tif"
            self._write_tif(old_tif, 40)
            self._write_tif(new_tif, 200)
            kml_path = root / "roi.kml"
            kml_path.write_text(
                _kml_document(
                    _placemark_xml("23", 102.02, 25.02, 102.08, 25.08)
                    + _placemark_xml("23", 102.12, 25.10, 102.18, 25.16)
                ),
                encoding="utf-8",
            )

            from applications.kml_roi.pipeline import run_kml_roi_pipeline

            result = run_kml_roi_pipeline(
                old_tif=old_tif,
                new_tif=new_tif,
                kml_path=kml_path,
                output_root=root / "outputs",
                work_dir=root / "work",
                model_id="fake/model",
                device="cpu",
                year="2026",
                tile_runner=fake_run_mmseg_tiles,
            )

            self.assertEqual(result["status"], "succeeded")
            self.assertEqual(result["matched_fid_list"], ["23"])
            fid_dir = root / "outputs" / "23"
            for base in ("23+2026", "23+2026_v2"):
                self.assertTrue((fid_dir / f"{base}.png").exists(), msg=base)
                self.assertTrue((fid_dir / f"{base}_mask.png").exists(), msg=base)
                self.assertTrue((fid_dir / f"{base}_src.png").exists(), msg=base)


if __name__ == "__main__":
    unittest.main()
