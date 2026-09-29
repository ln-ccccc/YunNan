# -*- coding: utf-8 -*-
"""DXF 导出（优化建议四.1）契约测试：R12 结构/图层绑定/洞环/API 全链路。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.models.project import ProjectExportRecord
from applications.project_hub.export_dxf import build_dxf_document
from test_project_api import TestProjectAPI


def _token_pairs(lines):
    pairs = []
    for index in range(0, len(lines) - 1, 2):
        pairs.append((lines[index].strip(), lines[index + 1].strip()))
    return pairs


def _polygon_feature(class_code, rings):
    return {
        "type": "Feature",
        "properties": {"class_code": class_code, "class_name": "grassland"},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }


class TestBuildDxfDocument(unittest.TestCase):
    def test_polygon_becomes_closed_polyline_on_class_layer(self):
        feature = _polygon_feature(1, [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]])
        lines, count = build_dxf_document([feature])
        pairs = _token_pairs(lines)

        self.assertEqual(count, 1)
        self.assertEqual(pairs[-1], ("0", "EOF"))
        entities = [i for i, (code, value) in enumerate(pairs) if code == "0" and value == "POLYLINE"]
        self.assertEqual(len(entities), 1)
        entity = pairs[entities[0]:]
        self.assertIn(("8", "CLASS_1"), entity[:4])
        self.assertIn(("70", "1"), entity[:8])  # 闭合
        vertex_xs = [value for (code, value) in pairs[entities[0]:] if code == "10"]
        self.assertIn("100.0000000", vertex_xs)

        # 图层表含 CLASS_1
        self.assertIn(("2", "CLASS_1"), pairs)

    def test_multipolygon_and_holes_each_get_polylines(self):
        feature = {
            "type": "Feature",
            "properties": {"class_code": 0},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]],
                    [[[101.0, 27.0], [101.01, 27.0], [101.01, 27.01], [101.0, 27.01], [101.0, 27.0]]],
                ],
            },
        }
        _, count = build_dxf_document([feature])
        self.assertEqual(count, 2)

    def test_empty_features_produce_valid_empty_document(self):
        lines, count = build_dxf_document([])
        pairs = _token_pairs(lines)
        self.assertEqual(count, 0)
        self.assertEqual(pairs[-1], ("0", "EOF"))
        self.assertIn(("2", "ENTITIES"), pairs)

    def test_missing_class_code_uses_fallback_layer(self):
        feature = {
            "type": "Feature",
            "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]]},
        }
        lines, _ = build_dxf_document([feature])
        self.assertIn(("2", "CLASS_NA"), _token_pairs(lines))


class TestDxfExportApi(TestProjectAPI):
    def _seed_mine_vector(self, project_id, fid=201):
        """默认要素导出走 get_project_geojson，需要活动矿山矢量资源。"""
        import json

        from applications.extensions import db
        from applications.models.project_spatial import ProjectSpatialResource

        self.client.put(
            f"/api/projects/{project_id}/mines",
            json={"mines": [{"mine_fid": fid, "mine_name_snapshot": "矿山A", "sort_order": 0}]},
        )
        mine_resource_path = f"projects/{project_id}/mines/1/mines.geojson"
        normalized_mine_path = self.storage_root / mine_resource_path
        normalized_mine_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_mine_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
                            },
                            "properties": {"OBJECTID": fid, "class_code": 3},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        db.session.add(
            ProjectSpatialResource(
                project_id=project_id,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=mine_resource_path,
                normalized_path=mine_resource_path,
                source_format="geojson",
            )
        )
        db.session.commit()

    def test_dxf_export_roundtrip(self):
        self.login_as_admin()
        project_id = self._create_project("DXF导出项目")
        self._seed_mine_vector(project_id)

        response = self.client.post(f"/api/projects/{project_id}/exports", json={"format": "dxf"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self._json(response)["code"], 0)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="dxf").one()
        self.assertEqual(record.status, "completed")
        artifact_path = self.storage_root / Path(record.file_path)
        self.assertTrue(artifact_path.is_file())
        content = artifact_path.read_text(encoding="utf-8")
        self.assertIn("POLYLINE", content)
        # 默认导出走矿山边界（无地类属性）→ 统一 CLASS_NA 层
        self.assertIn("CLASS_NA", content)
        self.assertIn("EOF", content)
        manifest = self._read_export_manifest(project_id, record.id)
        self.assertEqual(manifest["format"], "dxf")

    def test_dxf_export_accepts_explicit_features(self):
        self.login_as_admin()
        project_id = self._create_project("DXF显式要素项目")
        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
            },
            "properties": {"class_code": 2},
        }
        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "dxf", "features": [feature]},
        )
        self.assertEqual(response.status_code, 200)
        record = ProjectExportRecord.query.filter_by(project_id=project_id, format="dxf").one()
        content = (self.storage_root / Path(record.file_path)).read_text(encoding="utf-8")
        self.assertIn("CLASS_2", content)

    def test_dxf_rejects_unsupported_payload_fields(self):
        self.login_as_admin()
        project_id = self._create_project("DXF非法参数项目")
        response = self.client.post(
            f"/api/projects/{project_id}/exports",
            json={"format": "dxf", "output_dir": "/etc"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
