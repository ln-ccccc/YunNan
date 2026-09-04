import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import create_project


class TestVectorFeatures(unittest.TestCase):
    def test_geojson_loader_uses_supported_fid_fields_and_ignores_missing_ids(self):
        from applications.kml_roi.kml import load_vector_features

        polygon = {
            "type": "Polygon",
            "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.0]]],
        }
        payload = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"FID_1": 101}, "geometry": polygon},
                {"type": "Feature", "properties": {"FID": 102}, "geometry": polygon},
                {"type": "Feature", "properties": {"OBJECTID": 103}, "geometry": polygon},
                {"type": "Feature", "id": 104, "properties": {}, "geometry": polygon},
                {"type": "Feature", "properties": {}, "geometry": polygon},
            ],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            vector_path = Path(temp_dir) / "mines.geojson"
            vector_path.write_text(json.dumps(payload), encoding="utf-8")

            features = load_vector_features(vector_path)

        self.assertEqual([fid for fid, _ in features], ["101", "102", "103", "104"])
        self.assertTrue(all(geometry == polygon for _, geometry in features))


class TestInterpretationRouting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp_dir.name
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
        self.temp_dir.cleanup()

    def _create_project_vector(self, name, bindings, features):
        created = create_project({"name": name, "region": name})
        project_id = created["id"]
        for order, fid in enumerate(bindings):
            db.session.add(
                ProjectMineBinding(
                    project_id=project_id,
                    mine_fid=fid,
                    sort_order=order,
                )
            )
        relative = f"projects/{project_id}/mines/1/mines.geojson"
        path = Path(self.temp_dir.name) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}),
            encoding="utf-8",
        )
        db.session.add(
            ProjectSpatialResource(
                project_id=project_id,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=relative,
                normalized_path=relative,
                source_format="geojson",
            )
        )
        db.session.commit()
        return project_id

    @staticmethod
    def _feature(fid, min_lon, min_lat, max_lon, max_lat):
        return {
            "type": "Feature",
            "properties": {"FID_1": fid},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [min_lon, min_lat],
                    [max_lon, min_lat],
                    [max_lon, max_lat],
                    [min_lon, max_lat],
                    [min_lon, min_lat],
                ]],
            },
        }

    def _write_tif(self, name="scene.tif", crs="EPSG:4326"):
        path = Path(self.temp_dir.name) / name
        values = np.zeros((10, 10), dtype=np.uint8)
        values[:, :5] = 1
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="uint8",
            crs=crs,
            transform=from_origin(100.0, 26.0, 0.1, 0.1),
            nodata=0,
        ) as target:
            target.write(values, 1)
        return path

    def test_scope_matches_only_bound_fids_from_requested_project(self):
        from applications.inference.routing import resolve_interpretation_scope

        project_a = self._create_project_vector(
            "Dali",
            [101],
            [
                self._feature(101, 100.1, 25.2, 100.4, 25.8),
                self._feature(999, 100.1, 25.2, 100.4, 25.8),
            ],
        )
        self._create_project_vector(
            "Kunming",
            [202],
            [self._feature(202, 100.1, 25.2, 100.4, 25.8)],
        )

        scope = resolve_interpretation_scope(project_a, self._write_tif())

        self.assertEqual(scope["mode"], "project")
        self.assertEqual(scope["project_id"], project_a)
        self.assertEqual(scope["matched_fids"], [101])
        self.assertNotIn(202, scope["matched_fids"])
        self.assertNotIn(999, scope["matched_fids"])

    def test_scope_returns_standalone_without_project_or_valid_pixel_overlap(self):
        from applications.inference.routing import resolve_interpretation_scope

        tif_path = self._write_tif()
        self.assertEqual(resolve_interpretation_scope(None, tif_path)["mode"], "standalone")

        project_id = self._create_project_vector(
            "Dali",
            [303],
            [self._feature(303, 100.6, 25.2, 100.9, 25.8)],
        )
        scope = resolve_interpretation_scope(project_id, tif_path)
        self.assertEqual(scope["mode"], "standalone")
        self.assertEqual(scope["matched_fids"], [])

    def test_scope_rejects_missing_project_configuration(self):
        from applications.inference.routing import resolve_interpretation_scope

        tif_path = self._write_tif()
        with self.assertRaisesRegex(ValueError, "项目不存在"):
            resolve_interpretation_scope(9999, tif_path)

        project = create_project({"name": "Empty", "region": "Dali"})
        with self.assertRaisesRegex(ValueError, "尚未激活矿山资源"):
            resolve_interpretation_scope(project["id"], tif_path)

    def test_scope_warns_and_does_not_sync_when_tiff_has_no_crs(self):
        from applications.inference.routing import resolve_interpretation_scope

        project_id = self._create_project_vector(
            "Dali",
            [101],
            [self._feature(101, 100.1, 25.2, 100.4, 25.8)],
        )
        scope = resolve_interpretation_scope(
            project_id,
            self._write_tif(name="no-crs.tif", crs=None),
        )

        self.assertEqual(scope["mode"], "standalone")
        self.assertEqual(scope["matched_fids"], [])
        self.assertTrue(any("CRS" in warning for warning in scope["warnings"]))


if __name__ == "__main__":
    unittest.main()
