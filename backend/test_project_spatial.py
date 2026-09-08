import os
import sys
import tempfile
import unittest
import json
import datetime
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

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

    def test_geojson_preview_detects_fields_and_reports_invalid_suggested_fid(self):
        from applications.project_hub.spatial_service import (
            preview_mine_vector,
            sanitize_public_geojson_properties,
        )

        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "FID_1": 7,
                        "name": "Mine A",
                        "city": "Kunming",
                        "FILE_PATH": "D:/private/mine.geojson",
                        "source_path": "projects/1/mines/1/source.geojson",
                        "normalized_path": "projects/1/mines/1/mines.geojson",
                        "tile_path": "projects/1/tiles/1",
                        "manifest_path": "projects/1/manifest.json",
                        "output_dir": "D:/private/output",
                    },
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
        self.assertEqual(preview["suggested_fid_validation"], {"status": "valid", "message": None})
        self.assertEqual(preview["crs"], "EPSG:4326")
        reserved_keys = {"file_path", "source_path", "normalized_path", "tile_path", "manifest_path", "output_dir"}
        self.assertFalse(reserved_keys & {field.casefold() for field in preview["field_names"]})
        self.assertFalse(reserved_keys & {field.casefold() for field in preview["sample"][0]})
        self.assertEqual(
            sanitize_public_geojson_properties(
                {"metadata": [[{"manifest_path": "D:/private/manifest.json", "label": "保留"}]]}
            ),
            {"metadata": [[{"label": "保留"}]]},
        )

        payload["features"][1]["properties"]["FID_1"] = 7
        preview = preview_mine_vector("kunming.geojson", json.dumps(payload))
        self.assertEqual(
            preview["suggested_fid_validation"],
            {"status": "invalid", "message": "FID duplicate：FID 必须唯一"},
        )

    def test_geojson_preview_keeps_custom_fid_available_for_manual_selection(self):
        from applications.project_hub.spatial_service import preview_mine_vector

        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"mine_code": 301, "name": "自定义编码矿山"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.0, 25.0], [102.1, 25.0], [102.1, 25.1], [102.0, 25.0]]],
                    },
                }
            ],
        }

        preview = preview_mine_vector("custom-fid.geojson", json.dumps(payload))

        self.assertIn("mine_code", preview["field_names"])
        self.assertIsNone(preview["suggested_mapping"]["fid"])
        self.assertEqual(
            preview["suggested_fid_validation"],
            {"status": "needs_selection", "message": "未识别到唯一 FID 字段，请手动选择"},
        )

    def test_fractional_fid_is_invalid_in_preview_and_import(self):
        from applications.project_hub.spatial_service import import_mine_vector, preview_mine_vector

        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"FID_1": 1.5, "name": "小数 FID 矿山"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.0, 25.0], [102.1, 25.0], [102.1, 25.1], [102.0, 25.0]]],
                    },
                }
            ],
        }

        preview = preview_mine_vector("fractional-fid.geojson", json.dumps(payload))
        self.assertEqual(preview["suggested_fid_validation"]["status"], "invalid")
        self.assertEqual(preview["suggested_fid_validation"]["message"], "FID 必须是整数")

        created = create_project({"name": "小数 FID 项目", "region": "昆明"})
        with self.assertRaisesRegex(ValueError, "FID 必须是整数"):
            import_mine_vector(
                created["id"],
                "fractional-fid.geojson",
                json.dumps(payload),
                {"fid": "FID_1", "name": "name"},
            )
        self.assertEqual(len(Project.query.get(created["id"]).mines), 0)

    def test_import_mines_keeps_duplicate_fid_validation_strict(self):
        from applications.project_hub.spatial_service import import_mine_vector

        created = create_project({"name": "重复 FID 项目", "region": "昆明"})
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"mine_code": 301},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.0, 25.0], [102.1, 25.0], [102.1, 25.1], [102.0, 25.0]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"mine_code": 301},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[102.2, 25.2], [102.3, 25.2], [102.3, 25.3], [102.2, 25.2]]],
                    },
                },
            ],
        }

        with self.assertRaisesRegex(ValueError, "FID.*duplicate"):
            import_mine_vector(
                created["id"],
                "duplicate-fid.geojson",
                json.dumps(payload),
                {"fid": "mine_code"},
            )

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

    def test_custom_mine_fid_mapping_is_available_to_automatic_export(self):
        from applications.project_hub.service import create_export
        from applications.project_hub.spatial_service import import_mine_vector

        created = create_project({"name": "自定义 FID 项目", "region": "昆明"})
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"mine_code": 301, "name": "自定义编码矿山"},
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
                imported = import_mine_vector(
                    created["id"],
                    "custom-fid.geojson",
                    json.dumps(payload),
                    {"fid": "mine_code", "name": "name"},
                )
                resource = ProjectSpatialResource.query.get(imported["resource"]["id"])
                with (Path(temp_dir) / resource.normalized_path).open("r", encoding="utf-8") as handle:
                    normalized_geojson = json.load(handle)
                self.assertEqual(normalized_geojson["features"][0]["properties"]["FID_1"], 301)
                exported = create_export(created["id"], {"format": "geojson"})
                artifact_path = Path(temp_dir) / "projects" / str(created["id"]) / "exports" / str(exported["id"]) / "artifact.geojson"
                with artifact_path.open("r", encoding="utf-8") as handle:
                    exported_geojson = json.load(handle)
                self.assertEqual(exported_geojson["features"][0]["properties"]["mine_fid"], 301)
                self.assertEqual(exported_geojson["features"][0]["properties"]["mine_name"], "自定义编码矿山")
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root

    def test_active_basemap_replacement_overrides_failed_history(self):
        created = create_project({"name": "底图恢复项目", "region": "昆明"})
        project = Project.query.get(created["id"])
        project.spatial_resources.extend(
            [
                ProjectSpatialResource(
                    resource_type="mine_vector",
                    version=1,
                    status="active",
                    source_path="projects/1/mines/1/source.geojson",
                    normalized_path="projects/1/mines/1/mines.geojson",
                    source_format="geojson",
                ),
                ProjectSpatialResource(
                    resource_type="basemap",
                    version=1,
                    status="failed",
                    source_path="incoming/failed.tif",
                    source_format="tif",
                    error_message="切片失败",
                ),
                ProjectSpatialResource(
                    resource_type="basemap",
                    version=2,
                    status="active",
                    source_path="incoming/replacement.tif",
                    tile_path="projects/1/tiles/3",
                    source_format="tif",
                ),
            ]
        )
        db.session.commit()

        from applications.project_hub.service import _serialize_summary

        summary = _serialize_summary(project)
        self.assertEqual(summary["spatial_status"], "ready")
        self.assertTrue(summary["map_ready"])
        self.assertEqual(summary["missing_resources"], [])

    def test_failed_basemap_remains_failed_while_no_active_replacement_exists(self):
        created = create_project({"name": "失败底图项目", "region": "昆明"})
        project = Project.query.get(created["id"])
        project.spatial_resources.extend(
            [
                ProjectSpatialResource(
                    resource_type="mine_vector",
                    version=1,
                    status="active",
                    source_path="projects/1/mines/1/source.geojson",
                    normalized_path="projects/1/mines/1/mines.geojson",
                    source_format="geojson",
                ),
                ProjectSpatialResource(
                    resource_type="basemap",
                    version=1,
                    status="failed",
                    source_path="incoming/failed.tif",
                    source_format="tif",
                    error_message="切片失败",
                ),
            ]
        )
        db.session.commit()

        from applications.project_hub.service import _serialize_summary

        summary = _serialize_summary(project)
        self.assertEqual(summary["spatial_status"], "failed")
        self.assertFalse(summary["map_ready"])
        self.assertEqual(summary["missing_resources"], ["basemap"])

    def test_pending_or_processing_resource_takes_precedence_over_failed_state(self):
        from applications.project_hub.spatial_state import serialize_project_spatial_state

        for status in ("pending", "processing"):
            with self.subTest(status=status):
                state = serialize_project_spatial_state(
                    SimpleNamespace(
                        spatial_resources=[
                            SimpleNamespace(resource_type="mine_vector", status="active"),
                            SimpleNamespace(resource_type="basemap", status="active"),
                            SimpleNamespace(resource_type="basemap", status="failed"),
                            SimpleNamespace(resource_type="basemap", status=status),
                        ]
                    )
                )

                self.assertEqual(state["spatial_status"], "processing")
                self.assertTrue(state["map_ready"])
                self.assertEqual(state["missing_resources"], [])

    def test_get_project_spatial_orders_jobs_newest_first_for_terminal_recovery(self):
        from applications.models.project_spatial import ProjectSpatialJob
        from applications.project_hub.spatial_service import get_project_spatial

        created = create_project({"name": "任务恢复排序项目", "region": "昆明"})
        resource = ProjectSpatialResource(
            project_id=created["id"],
            resource_type="basemap",
            version=1,
            status="failed",
            source_path="incoming/failed.tif",
            source_format="tif",
        )
        db.session.add(resource)
        db.session.flush()
        db.session.add_all(
            [
                ProjectSpatialJob(
                    id="job-older",
                    project_id=created["id"],
                    resource_id=resource.id,
                    job_type="basemap_tiles",
                    status="failed",
                    stage="failed",
                    create_time=datetime.datetime(2026, 1, 1, 10, 0, 0),
                ),
                ProjectSpatialJob(
                    id="job-newer",
                    project_id=created["id"],
                    resource_id=resource.id,
                    job_type="basemap_tiles",
                    status="cancelled",
                    stage="cancelled",
                    create_time=datetime.datetime(2026, 1, 1, 11, 0, 0),
                ),
            ]
        )
        db.session.commit()

        spatial = get_project_spatial(created["id"])
        self.assertEqual([job["id"] for job in spatial["jobs"]], ["job-newer", "job-older"])

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

    def test_run_forever_isolates_job_with_missing_resource_and_keeps_serving(self):
        from applications.models.project_spatial import ProjectSpatialJob
        from applications.project_hub import spatial_worker as worker_module

        created = create_project({"name": "Isolate Test", "region": "昆明"})
        db.session.add(
            ProjectSpatialJob(
                id="job-orphan",
                project_id=created["id"],
                resource_id=999999,
                job_type="basemap_tiles",
                status="queued",
                stage="queued",
            )
        )
        db.session.commit()

        worker = worker_module.SpatialWorker(worker_id="test-worker", poll_seconds=0.001)
        claims = {"count": 0}
        original_claim = worker_module.claim_next_job

        def counting_claim(worker_id):
            claims["count"] += 1
            return original_claim(worker_id)

        with patch.object(worker_module, "claim_next_job", side_effect=counting_claim):
            worker.run_forever(should_stop=lambda: claims["count"] >= 2)

        fresh = ProjectSpatialJob.query.get("job-orphan")
        self.assertEqual(fresh.status, "failed")
        self.assertIn("隔离", fresh.error_message)

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
