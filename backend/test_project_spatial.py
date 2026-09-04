import os
import sys
import tempfile
import unittest
import json
from pathlib import Path

from sqlalchemy import inspect
from osgeo import gdal, osr

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import create_project
from applications.project_hub.spatial_worker import _estimate_xyz_tile_count


class TestProjectSpatialState(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_new_project_summary_requires_mines_and_basemap(self):
        summary = create_project({"name": "昆明项目", "region": "昆明市"})

        self.assertEqual(summary.get("map_ready"), False)
        self.assertEqual(summary.get("spatial_status"), "unconfigured")
        self.assertEqual(summary.get("missing_resources"), ["mine_vector", "basemap"])

    def test_spatial_resource_and_job_tables_are_registered(self):
        table_names = set(inspect(db.engine).get_table_names())

        self.assertIn("project_spatial_resource", table_names)
        self.assertIn("project_spatial_job", table_names)

    def test_xyz_tile_estimate_uses_bounds_and_zoom_range(self):
        self.assertEqual(_estimate_xyz_tile_count([-180, -85, 180, 85], 0, 0), 1)
        self.assertEqual(
            _estimate_xyz_tile_count(
                [98.86330604553225, 24.664490407124255, 101.04241847991946, 26.699458952576304],
                8,
                15,
            ),
            55405,
        )

    def test_project_is_map_ready_only_with_both_active_resources(self):
        created = create_project({"name": "昆明项目", "region": "昆明市"})
        project = Project.query.get(created["id"])
        project.spatial_resources.append(
            ProjectSpatialResource(
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path="projects/1/mines/1/source.geojson",
                normalized_path="projects/1/mines/1/mines.geojson",
                source_format="geojson",
            )
        )
        db.session.commit()

        from applications.project_hub.service import _serialize_summary

        summary = _serialize_summary(project)
        self.assertFalse(summary["map_ready"])
        self.assertEqual(summary["spatial_status"], "unconfigured")
        self.assertEqual(summary["missing_resources"], ["basemap"])

        project.spatial_resources.append(
            ProjectSpatialResource(
                resource_type="basemap",
                version=1,
                status="active",
                source_path="projects/1/basemaps/2/source.tif",
                tile_path="projects/1/tiles/2",
                source_format="tif",
            )
        )
        db.session.commit()

        summary = _serialize_summary(project)
        self.assertTrue(summary["map_ready"])
        self.assertEqual(summary["spatial_status"], "ready")
        self.assertEqual(summary["missing_resources"], [])

    def test_storage_paths_cannot_escape_project_storage_root(self):
        try:
            from applications.project_hub.spatial_storage import resolve_storage_path
        except ModuleNotFoundError:
            self.fail("spatial_storage 模块尚未实现")

        with tempfile.TemporaryDirectory() as temp_dir:
            resolved = resolve_storage_path(temp_dir, "projects/7/mines/2/source.geojson")
            self.assertTrue(str(resolved).startswith(temp_dir))
            with self.assertRaisesRegex(ValueError, "路径越界"):
                resolve_storage_path(temp_dir, "../outside.tif")
            with self.assertRaisesRegex(ValueError, "必须使用相对路径"):
                resolve_storage_path(temp_dir, os.path.abspath("outside.tif"))

    def test_geojson_preview_detects_fields_and_rejects_duplicate_fid(self):
        from applications.project_hub.spatial_service import preview_mine_vector

        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"FID_1": 7, "name": "Mine A", "city": "Kunming"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.0, 25.0], [102.1, 25.0], [102.1, 25.1], [102.0, 25.0]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"FID_1": 8, "name": "Mine B", "city": "Kunming"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.2, 25.2], [102.3, 25.2], [102.3, 25.3], [102.2, 25.2]]],
                    },
                },
            ],
        }

        preview = preview_mine_vector("kunming.geojson", json.dumps(payload))
        self.assertEqual(preview["feature_count"], 2)
        self.assertEqual(preview["suggested_mapping"]["fid"], "FID_1")
        self.assertEqual(preview["suggested_mapping"]["name"], "name")
        self.assertEqual(preview["crs"], "EPSG:4326")

        payload["features"][1]["properties"]["FID_1"] = 7
        with self.assertRaisesRegex(ValueError, "FID.*duplicate"):
            preview_mine_vector("kunming.geojson", json.dumps(payload))

    def test_import_mines_activates_project_resource_and_replaces_bindings(self):
        from applications.project_hub.spatial_service import import_mine_vector

        created = create_project({"name": "Kunming", "region": "Kunming"})
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"OBJECTID": 101, "name": "Mine A", "city": "Kunming", "area": 12.5},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.0, 25.0], [102.1, 25.0], [102.1, 25.1], [102.0, 25.0]]],
                    },
                }
            ],
        }
        previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["PROJECT_STORAGE_ROOT"] = temp_dir
            try:
                result = import_mine_vector(
                    created["id"],
                    "kunming.geojson",
                    json.dumps(payload),
                    {"fid": "OBJECTID", "name": "name", "region": "city", "area": "area"},
                )
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root

            project = Project.query.get(created["id"])
            resource = ProjectSpatialResource.query.get(result["resource"]["id"])
            self.assertEqual(resource.status, "active")
            self.assertEqual(resource.version, 1)
            self.assertEqual(resource.feature_count, 1)
            self.assertEqual(project.mines[0].mine_fid, 101)
            self.assertEqual(project.mines[0].mine_name_snapshot, "Mine A")
            self.assertTrue(os.path.isfile(os.path.join(temp_dir, resource.normalized_path)))

    def test_basemap_candidate_is_scoped_to_incoming_and_queues_job(self):
        from applications.project_hub.spatial_service import list_basemap_candidates, register_basemap

        created = create_project({"name": "Kunming", "region": "Kunming"})
        project = Project.query.get(created["id"])
        project.spatial_resources.append(
            ProjectSpatialResource(
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=f"projects/{project.id}/mines/1/source.geojson",
                normalized_path=f"projects/{project.id}/mines/1/mines.geojson",
                source_format="geojson",
                bounds_json=json.dumps([102.0, 25.0, 102.5, 25.5]),
            )
        )
        db.session.commit()

        previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["PROJECT_STORAGE_ROOT"] = temp_dir
            incoming = os.path.join(temp_dir, "incoming")
            os.makedirs(incoming)
            tif_path = os.path.join(incoming, "kunming.tif")
            driver = gdal.GetDriverByName("GTiff")
            dataset = driver.Create(tif_path, 10, 10, 1, gdal.GDT_Byte)
            dataset.SetGeoTransform([102.0, 0.05, 0.0, 25.5, 0.0, -0.05])
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(4326)
            dataset.SetProjection(crs.ExportToWkt())
            dataset = None
            try:
                candidates = list_basemap_candidates(project.id)
                self.assertEqual([item["candidate"] for item in candidates], ["incoming/kunming.tif"])
                self.assertTrue(candidates[0]["intersects_mines"])
                result = register_basemap(project.id, "incoming/kunming.tif", 8, 15)
                self.assertEqual(result["resource"]["status"], "pending")
                self.assertEqual(result["job"]["status"], "queued")
                with self.assertRaisesRegex(ValueError, "incoming"):
                    register_basemap(project.id, "../outside.tif", 8, 15)
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root

    def test_worker_requeues_running_jobs_after_restart(self):
        from applications.models.project_spatial import ProjectSpatialJob
        from applications.project_hub.spatial_worker import recover_running_jobs

        created = create_project({"name": "Kunming", "region": "Kunming"})
        resource = ProjectSpatialResource(
            project_id=created["id"],
            resource_type="basemap",
            version=1,
            status="processing",
            source_path="incoming/kunming.tif",
            source_format="tif",
        )
        db.session.add(resource)
        db.session.flush()
        db.session.add(
            ProjectSpatialJob(
                id="job-running",
                project_id=created["id"],
                resource_id=resource.id,
                job_type="basemap_tiles",
                status="running",
                stage="tiling",
                progress=40,
                worker_id="old-worker",
            )
        )
        db.session.commit()

        self.assertEqual(recover_running_jobs(), 1)
        job = ProjectSpatialJob.query.get("job-running")
        self.assertEqual(job.status, "queued")
        self.assertEqual(job.stage, "queued")
        self.assertIsNone(job.worker_id)

    def test_worker_generates_xyz_tile_and_activates_basemap(self):
        from applications.project_hub.spatial_service import register_basemap
        from applications.project_hub.spatial_worker import claim_next_job, process_job

        created = create_project({"name": "Tile Test", "region": "昆明"})
        db.session.add(
            ProjectSpatialResource(
                project_id=created["id"],
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=f"projects/{created['id']}/mines/1/source.geojson",
                normalized_path=f"projects/{created['id']}/mines/1/mines.geojson",
                source_format="geojson",
                bounds_json=json.dumps([102.0, 25.0, 102.5, 25.5]),
            )
        )
        db.session.commit()
        previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["PROJECT_STORAGE_ROOT"] = temp_dir
            incoming = os.path.join(temp_dir, "incoming")
            os.makedirs(incoming)
            tif_path = os.path.join(incoming, "small.tif")
            dataset = gdal.GetDriverByName("GTiff").Create(tif_path, 10, 10, 1, gdal.GDT_Byte)
            dataset.SetGeoTransform([102.0, 0.05, 0.0, 25.5, 0.0, -0.05])
            crs = osr.SpatialReference()
            crs.ImportFromEPSG(4326)
            dataset.SetProjection(crs.ExportToWkt())
            dataset.GetRasterBand(1).Fill(100)
            dataset = None
            try:
                queued = register_basemap(created["id"], "incoming/small.tif", 0, 0)
                job = claim_next_job("test-worker")
                process_job(job, poll_seconds=0.01)
                resource = ProjectSpatialResource.query.get(queued["resource"]["id"])
                self.assertEqual(resource.status, "active")
                self.assertEqual(job.status, "succeeded")
                self.assertTrue(any(Path(os.path.join(temp_dir, resource.tile_path)).rglob("*.png")))
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root


if __name__ == "__main__":
    unittest.main()
