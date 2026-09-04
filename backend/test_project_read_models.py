import datetime
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import (
    ClassificationResult,
    ClassificationRevision,
)
from applications.models.inference_job import InferenceJob
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectBackupRecord,
    ProjectDataset,
    ProjectExportRecord,
    ProjectMineBinding,
)
from applications.models.project_spatial import ProjectSpatialJob, ProjectSpatialResource


class TestProjectReadModels(unittest.TestCase):
    PROJECT_ID = 42
    FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "project-hub-v1"
    PROJECT_NAME = "云南矿山生态修复示例项目"
    REGION = "云南省"
    MANAGER = "项目管理员"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="project-read-models-")
        self.previous_env = {
            key: os.environ.get(key)
            for key in ("PROJECT_STORAGE_ROOT", "ADMIN_USERNAME", "ADMIN_PASSWORD")
        }
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._restore_environment)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"

        self.app = create_app("testing")
        self.assertEqual(self.app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(response.status_code, 200)

    def _restore_environment(self):
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @staticmethod
    def json_body(response):
        return json.loads(response.data.decode("utf-8"))

    @classmethod
    def load_fixture(cls, name):
        with (cls.FIXTURE_DIR / name).open("r", encoding="utf-8") as stream:
            return json.load(stream)

    @staticmethod
    def _timestamp(value):
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)

    def _seed_project(self, *, lifecycle_status, remark, latest_activity_at):
        timestamp = self._timestamp(latest_activity_at)
        project = Project(
            id=self.PROJECT_ID,
            name=self.PROJECT_NAME,
            region=self.REGION,
            manager=self.MANAGER,
            remark=remark,
            status=lifecycle_status,
            monitor_start_year=2024,
            monitor_end_year=2024,
            create_time=self._timestamp("2026-09-01T08:00:00Z"),
            update_time=timestamp,
        )
        db.session.add(project)
        db.session.add(
            ProjectActivityLog(
                project_id=self.PROJECT_ID,
                event_type="project_seeded",
                actor="system",
                payload_json="{}",
                create_time=timestamp,
            )
        )
        return project

    def _seed_mine_boundary(self):
        db.session.add(
            ProjectMineBinding(
                project_id=self.PROJECT_ID,
                mine_fid=101,
                mine_name_snapshot="示例矿山",
                city_snapshot="大理州",
                area_snapshot=1.0,
                status_snapshot="待治理",
                sort_order=0,
                create_time=self._timestamp("2026-09-01T08:05:00Z"),
                update_time=self._timestamp("2026-09-01T08:05:00Z"),
            )
        )
        db.session.add(
            ProjectSpatialResource(
                id=101,
                project_id=self.PROJECT_ID,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path="projects/42/mines/101/source.geojson",
                normalized_path="projects/42/mines/101/mines.geojson",
                source_format="geojson",
                feature_count=1,
                crs="EPSG:4326",
                bounds_json="[100.0, 25.0, 100.1, 25.1]",
                create_time=self._timestamp("2026-09-01T08:05:00Z"),
                update_time=self._timestamp("2026-09-01T08:05:00Z"),
            )
        )

    def _seed_datasets(self, kinds):
        for offset, dataset_kind in enumerate(kinds, start=1):
            dataset_id = 200 + offset
            file_path = f"projects/42/datasets/{dataset_kind}-{offset}.json"
            source_format = "json"
            if dataset_kind == "imagery":
                file_path = f"incoming/fixture-imagery-{dataset_id}.tif"
                source_format = "tif"
                input_file = self.storage_root / file_path
                input_file.parent.mkdir(parents=True, exist_ok=True)
                input_file.touch()
            db.session.add(
                ProjectDataset(
                    id=dataset_id,
                    project_id=self.PROJECT_ID,
                    dataset_kind=dataset_kind,
                    display_name=f"示例 {dataset_kind} {offset}",
                    file_path=file_path,
                    source_format=source_format,
                    mine_fid=101,
                    year_start=2024,
                    year_end=2024,
                    slice_config_json="{}",
                    create_time=self._timestamp("2026-09-01T08:06:00Z"),
                    update_time=self._timestamp("2026-09-01T08:06:00Z"),
                )
            )

    def _seed_active_basemap(self):
        db.session.add(
            ProjectSpatialResource(
                id=102,
                project_id=self.PROJECT_ID,
                resource_type="basemap",
                version=1,
                status="active",
                source_path="projects/42/basemaps/102/source.tif",
                tile_path="projects/42/tiles/102",
                source_format="tif",
                crs="EPSG:4326",
                bounds_json="[99.9, 24.9, 100.2, 25.2]",
                create_time=self._timestamp("2026-09-01T08:15:00Z"),
                update_time=self._timestamp("2026-09-01T08:15:00Z"),
            )
        )

    def _seed_reviewable_result(self):
        feature_collection = {"type": "FeatureCollection", "features": []}
        db.session.add(
            ClassificationResult(
                id=301,
                project_id=self.PROJECT_ID,
                mine_fid=101,
                year=2024,
                inference_job_id="fixture-job-301",
                model_id="fixture-model",
                mine_resource_id=101,
                mine_resource_version=1,
                auto_feature_collection_json=json.dumps(feature_collection),
                current_feature_collection_json=json.dumps(feature_collection),
                current_revision_no=0,
                vector_status="ready",
                create_time=self._timestamp("2026-09-01T08:18:00Z"),
                update_time=self._timestamp("2026-09-01T08:18:00Z"),
            )
        )

    def _seed_assets_read_model_sources(self):
        timestamp = self._timestamp("2026-09-01T09:00:00Z")
        feature_collection = {"type": "FeatureCollection", "features": []}
        self._seed_project(
            lifecycle_status="active",
            remark="用于项目资产只读模型的合成种子。",
            latest_activity_at="2026-09-01T09:00:00Z",
        )
        self._seed_mine_boundary()

        for resource_id, version, raw_status in (
            (102, 1, "pending"),
            (103, 2, "processing"),
            (104, 3, "active"),
            (105, 4, "failed"),
            (106, 5, "retained"),
        ):
            db.session.add(
                ProjectSpatialResource(
                    id=resource_id,
                    project_id=self.PROJECT_ID,
                    resource_type="basemap",
                    version=version,
                    status=raw_status,
                    source_path=str(self.storage_root / "private" / f"basemap-{resource_id}.tif"),
                    normalized_path=f"projects/{self.PROJECT_ID}/basemaps/{resource_id}/source.tif",
                    tile_path=f"projects/{self.PROJECT_ID}/tiles/{resource_id}",
                    source_format="tif",
                    crs="EPSG:4326",
                    bounds_json="[99.9, 24.9, 100.2, 25.2]",
                    error_message="合成空间处理失败" if raw_status == "failed" else None,
                    create_time=timestamp,
                    update_time=timestamp,
                )
            )

        imagery_key = "incoming/assets-imagery-201.tif"
        imagery_path = self.storage_root / imagery_key
        imagery_path.parent.mkdir(parents=True, exist_ok=True)
        imagery_path.touch()
        report_key = "incoming/assets-report-501.pdf"
        report_path = self.storage_root / report_key
        report_path.touch()
        db.session.add_all(
            [
                ProjectDataset(
                    id=201,
                    project_id=self.PROJECT_ID,
                    dataset_kind="imagery",
                    display_name="合成遥感影像",
                    file_path=imagery_key,
                    source_format="tif",
                    mine_fid=101,
                    year_start=2024,
                    year_end=2024,
                    slice_config_json="{}",
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                ProjectDataset(
                    id=501,
                    project_id=self.PROJECT_ID,
                    dataset_kind="report",
                    display_name="合成项目报告",
                    file_path=report_key,
                    source_format="pdf",
                    mine_fid=101,
                    year_start=2024,
                    year_end=2024,
                    slice_config_json="{}",
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                ClassificationResult(
                    id=301,
                    project_id=self.PROJECT_ID,
                    mine_fid=101,
                    year=2024,
                    inference_job_id="assets-job-301",
                    model_id="assets-model",
                    mine_resource_id=101,
                    mine_resource_version=1,
                    auto_feature_collection_json=json.dumps(feature_collection),
                    current_feature_collection_json=json.dumps(feature_collection),
                    current_revision_no=1,
                    vector_status="ready",
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                ProjectExportRecord(
                    id=601,
                    project_id=self.PROJECT_ID,
                    format="geojson",
                    file_path=str(self.storage_root / "private" / "exports" / "601.geojson"),
                    status="completed",
                    request_params_json="{}",
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                ProjectBackupRecord(
                    id=701,
                    project_id=self.PROJECT_ID,
                    scope="metadata_index",
                    manifest_path=str(self.storage_root / "private" / "snapshots" / "701.json"),
                    status="completed",
                    restorable=True,
                    create_time=timestamp,
                    update_time=timestamp,
                ),
            ]
        )
        db.session.add(
            ClassificationRevision(
                id=401,
                result_id=301,
                revision_no=1,
                source="manual",
                author="admin",
                feature_count=0,
                snapshot_path=str(self.storage_root / "private" / "revisions" / "401.geojson"),
                feature_collection_json=json.dumps(feature_collection),
                create_time=timestamp,
            )
        )
        db.session.commit()

    def _assert_no_physical_path_keys(self, value):
        forbidden_keys = {
            "file_path",
            "source_path",
            "normalized_path",
            "tile_path",
            "manifest_path",
            "output_dir",
        }
        if isinstance(value, dict):
            self.assertTrue(
                forbidden_keys.isdisjoint(value),
                f"公开响应泄露物理路径字段：{sorted(forbidden_keys.intersection(value))}",
            )
            for nested_value in value.values():
                self._assert_no_physical_path_keys(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                self._assert_no_physical_path_keys(nested_value)
        elif isinstance(value, str):
            self.assertNotIn(
                self.storage_root.as_posix(),
                value.replace("\\", "/"),
                "公开响应泄露测试临时存储根",
            )

    def _seed_blocked_project(self):
        self._seed_project(
            lifecycle_status="draft",
            remark="仅完成项目基础信息登记。",
            latest_activity_at="2026-09-01T08:00:00Z",
        )
        db.session.commit()

    def _seed_partial_project(self):
        self._seed_project(
            lifecycle_status="active",
            remark="已登记矿山边界和推理输入，等待底图与成果复核。",
            latest_activity_at="2026-09-01T08:10:00Z",
        )
        self._seed_mine_boundary()
        self._seed_datasets(("imagery", "mine_indices", "report"))
        db.session.commit()

    def _seed_ready_project(self):
        self._seed_project(
            lifecycle_status="active",
            remark="项目已具备地图浏览、推理与成果复核条件。",
            latest_activity_at="2026-09-01T08:20:00Z",
        )
        self._seed_mine_boundary()
        self._seed_datasets(("imagery", "inference_result", "report"))
        self._seed_active_basemap()
        self._seed_reviewable_result()
        db.session.commit()

    def _seed_overview_job_counts(self):
        timestamp = self._timestamp("2026-09-01T08:25:00Z")
        self._seed_ready_project()
        db.session.add(
            Project(
                id=43,
                name="其他项目",
                region="云南省",
                manager="项目管理员",
                remark="用于项目任务隔离测试。",
                status="active",
                monitor_start_year=2024,
                monitor_end_year=2024,
                create_time=timestamp,
                update_time=timestamp,
            )
        )
        db.session.add(
            ProjectSpatialResource(
                id=107,
                project_id=self.PROJECT_ID,
                resource_type="basemap",
                version=2,
                status="pending",
                source_path="projects/42/basemaps/107/source.tif",
                source_format="tif",
                crs="EPSG:4326",
                bounds_json="[99.9, 24.9, 100.2, 25.2]",
                create_time=timestamp,
                update_time=timestamp,
            )
        )
        db.session.add(
            ProjectSpatialResource(
                id=1101,
                project_id=43,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path="projects/43/mines/1101/source.geojson",
                normalized_path="projects/43/mines/1101/mines.geojson",
                source_format="geojson",
                feature_count=1,
                crs="EPSG:4326",
                bounds_json="[100.0, 25.0, 100.1, 25.1]",
                create_time=timestamp,
                update_time=timestamp,
            )
        )
        db.session.add(
            ProjectSpatialResource(
                id=1102,
                project_id=43,
                resource_type="basemap",
                version=1,
                status="pending",
                source_path="projects/43/basemaps/1102/source.tif",
                source_format="tif",
                crs="EPSG:4326",
                bounds_json="[99.9, 24.9, 100.2, 25.2]",
                create_time=timestamp,
                update_time=timestamp,
            )
        )
        db.session.add_all(
            [
                ProjectSpatialJob(
                    id="fixture-spatial-42-queued",
                    project_id=self.PROJECT_ID,
                    resource_id=107,
                    job_type="basemap_tiles",
                    status="queued",
                    stage="queued",
                    progress=0.0,
                    cancel_requested=False,
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                InferenceJob(
                    id="fixture-inference-42-running",
                    project_id=self.PROJECT_ID,
                    status="running",
                    requested_device="auto",
                    warnings_json="[]",
                    request_payload_json="{}",
                    progress_current=1,
                    progress_total=2,
                    cancel_requested=False,
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                ProjectSpatialJob(
                    id="fixture-spatial-43-queued",
                    project_id=43,
                    resource_id=1102,
                    job_type="basemap_tiles",
                    status="queued",
                    stage="queued",
                    progress=0.0,
                    cancel_requested=False,
                    create_time=timestamp,
                    update_time=timestamp,
                ),
                InferenceJob(
                    id="fixture-inference-43-running",
                    project_id=43,
                    status="running",
                    requested_device="auto",
                    warnings_json="[]",
                    request_payload_json="{}",
                    progress_current=1,
                    progress_total=2,
                    cancel_requested=False,
                    create_time=timestamp,
                    update_time=timestamp,
                ),
            ]
        )
        db.session.commit()

    def _assert_overview_fixture(self, fixture_name):
        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.json_body(response), self.load_fixture(fixture_name))

    def test_overview_empty_project_matches_blocked_fixture(self):
        self._seed_blocked_project()

        self._assert_overview_fixture("overview-blocked.json")

    def test_overview_partial_project_matches_fixture(self):
        self._seed_partial_project()

        self._assert_overview_fixture("overview-partial.json")

    def test_overview_ready_project_matches_fixture(self):
        self._seed_ready_project()

        self._assert_overview_fixture("overview-ready.json")

    def test_archiving_preserves_readiness_but_changes_capabilities(self):
        self._seed_ready_project()

        ready_response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")
        self.assertEqual(ready_response.status_code, 200, ready_response.get_data(as_text=True))
        ready_body = self.json_body(ready_response)

        project = db.session.get(Project, self.PROJECT_ID)
        project.status = "archived"
        project.update_time = self._timestamp("2026-09-01T08:21:00Z")
        db.session.commit()

        archived_response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")
        self.assertEqual(
            archived_response.status_code,
            200,
            archived_response.get_data(as_text=True),
        )
        archived_body = self.json_body(archived_response)
        self.assertEqual(
            archived_body["data"]["readiness"],
            ready_body["data"]["readiness"],
        )
        archived_capabilities = archived_body["data"]["capabilities"]
        self.assertFalse(archived_capabilities["can_configure_spatial"])
        self.assertFalse(archived_capabilities["can_start_inference"])
        self.assertTrue(archived_capabilities["can_open_map"])
        self.assertTrue(archived_capabilities["can_review_result"])
        self.assertTrue(archived_capabilities["can_export"])

    def test_overview_counts_exclude_other_project_jobs(self):
        self._seed_overview_job_counts()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        counts = self.json_body(response)["data"]["counts"]
        self.assertEqual(counts["queued_jobs"], 1)
        self.assertEqual(counts["running_jobs"], 1)

    def test_overview_requires_login_and_returns_404_for_missing_project(self):
        self._seed_blocked_project()

        anonymous_client = self.app.test_client()
        for path in (
            f"/api/projects/{self.PROJECT_ID}/overview",
            f"/api/projects/{self.PROJECT_ID}/assets",
        ):
            with self.subTest(path=path, client="anonymous"):
                self.assertEqual(anonymous_client.get(path).status_code, 401)
        for path in (
            "/api/projects/999/overview",
            "/api/projects/999/assets",
        ):
            with self.subTest(path=path, client="authenticated"):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_timeline_normalizes_legacy_activity_without_fabricating_target(self):
        self._seed_blocked_project()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/timeline")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        item = self.json_body(response)["data"]["items"][0]
        self.assertEqual(item["event_type"], "project_seeded")
        self.assertEqual(item["action_code"], "PROJECT_SEEDED")
        self.assertEqual(item["actor"], "system")
        self.assertEqual(item["target"], {})
        self.assertEqual(item["result"], "success")
        self.assertEqual(item["payload"], {})
        self.assertEqual(item["created_at"], item["timestamp"])

    def test_timeline_hides_unsafe_legacy_activity_payload(self):
        self._seed_blocked_project()
        activity = ProjectActivityLog.query.filter_by(
            project_id=self.PROJECT_ID,
            event_type="project_seeded",
        ).one()
        activity.payload_json = json.dumps(
            {"file_path": str(self.storage_root / "private" / "input.tif")}
        )
        db.session.commit()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/timeline")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        item = self.json_body(response)["data"]["items"][0]
        self.assertEqual(item["payload"], {})
        self._assert_no_physical_path_keys(item)

    def test_overview_serializes_current_activity_shape(self):
        self._seed_blocked_project()
        from applications.project_hub.service import _append_activity

        _append_activity(
            self.PROJECT_ID,
            "dataset_created",
            {"dataset_id": 7},
            actor="admin",
            target={"type": "dataset", "id": "7"},
        )
        db.session.commit()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        activity = self.json_body(response)["data"]["recent_activity"][0]
        self.assertEqual(activity["action_code"], "DATASET_REGISTERED")
        self.assertEqual(activity["actor_id"], "admin")
        self.assertEqual(activity["actor_type"], "user")
        self.assertEqual(activity["target_type"], "dataset")
        self.assertEqual(activity["target_id"], "7")
        self.assertEqual(activity["result"], "success")
        self.assertIsNone(activity["job_id"])
        self.assertEqual(activity["payload"], {"dataset_id": 7})

    def test_spatial_response_hides_storage_paths(self):
        self._seed_partial_project()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/spatial")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self._assert_no_physical_path_keys(self.json_body(response))

    def test_archived_project_rejects_spatial_mutation(self):
        self._seed_blocked_project()
        project = db.session.get(Project, self.PROJECT_ID)
        project.status = "archived"
        db.session.commit()
        geojson = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"FID_1": 101, "name": "归档矿山"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.0]]
                            ],
                        },
                    }
                ],
            }
        )
        from applications.project_hub.spatial_service import import_mine_vector

        with self.assertRaisesRegex(ValueError, "归档项目"):
            import_mine_vector(self.PROJECT_ID, "archived.geojson", geojson)

    def test_spatial_task_queue_activity_uses_machine_readable_payload(self):
        self._seed_blocked_project()
        self._seed_mine_boundary()
        incoming_path = self.storage_root / "incoming" / "audit-basemap.tif"
        incoming_path.parent.mkdir(parents=True, exist_ok=True)
        incoming_path.touch()
        metadata = {
            "crs": "EPSG:4326",
            "bounds": [99.9, 24.9, 100.2, 25.2],
            "width": 10,
            "height": 10,
        }
        from applications.project_hub.spatial_service import register_basemap

        with patch(
            "applications.project_hub.spatial_service._raster_metadata",
            return_value=metadata,
        ):
            queued = register_basemap(
                self.PROJECT_ID,
                "incoming/audit-basemap.tif",
                actor="admin",
            )

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/timeline")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        item = next(
            item
            for item in self.json_body(response)["data"]["items"]
            if item["event_type"] == "spatial_job_queued"
        )
        self.assertEqual(item["action_code"], "SPATIAL_JOB_QUEUED")
        self.assertEqual(item["actor"], "admin")
        self.assertEqual(
            item["target"],
            {"type": "spatial_job", "id": queued["job"]["id"]},
        )
        self.assertEqual(item["result"], "success")
        self.assertIsInstance(item["payload"], dict)
        self.assertEqual(item["created_at"], item["timestamp"])

    def test_overview_filters_unsafe_structured_activity_payloads(self):
        self._seed_blocked_project()
        base_event = {
            "action_code": "BASEMAP_ACTIVATED",
            "actor_id": "admin",
            "actor_type": "user",
            "target_type": "basemap",
            "target_id": "102",
            "result": "success",
            "job_id": None,
            "payload": {"previous_basemap_id": "101"},
        }
        unsafe_events = (
            (
                "forbidden_key",
                {"payload": {"nested": [{" File_Path ": "safe.txt"}]}},
            ),
            (
                "storage_root",
                {"payload": {"note": f"source={self.storage_root}/private/input.tif"}},
            ),
            (
                "windows_path",
                {"payload": {"note": r"  C:\outside\input.tif"}},
            ),
            (
                "posix_path",
                {"payload": {"note": "/srv/private/input.tif"}},
            ),
            (
                "unc_path",
                {"payload": {"note": r"\\server\share\input.tif"}},
            ),
            (
                "file_uri",
                {"payload": {"note": "file:///tmp/input.tif"}},
            ),
            ("list_target_id", {"target_id": ["102"]}),
            ("dict_target_id", {"target_id": {"id": "102"}}),
        )
        for offset, (name, override) in enumerate(unsafe_events, start=1):
            event = dict(base_event)
            event.update(override)
            db.session.add(
                ProjectActivityLog(
                    project_id=self.PROJECT_ID,
                    event_type=f"audit_{name}",
                    actor="admin",
                    payload_json=json.dumps(event),
                    create_time=self._timestamp(f"2026-09-01T08:{30 + offset:02d}:00Z"),
                )
            )
        db.session.add(
            ProjectActivityLog(
                project_id=self.PROJECT_ID,
                event_type="audit_safe",
                actor="admin",
                payload_json=json.dumps(base_event),
                create_time=self._timestamp("2026-09-01T08:40:00Z"),
            )
        )
        db.session.commit()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/overview")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        recent_activity = self.json_body(response)["data"]["recent_activity"]
        self.assertEqual(
            recent_activity,
            [
                {
                    **base_event,
                    "created_at": "2026-09-01T08:40:00Z",
                }
            ],
        )
        self.assertEqual(
            set(recent_activity[0]),
            {
                "action_code",
                "actor_id",
                "actor_type",
                "target_type",
                "target_id",
                "result",
                "job_id",
                "payload",
                "created_at",
            },
        )

    def test_assets_maps_all_existing_sources_and_hides_physical_paths(self):
        self._seed_assets_read_model_sources()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/assets")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = self.json_body(response)
        items = body["data"]["items"]
        self.assertEqual(body["data"]["count"], 12)
        self.assertEqual(body["data"]["count"], len(items))
        self.assertEqual(
            {item["asset_type"] for item in items},
            {
                "mine_boundary",
                "basemap",
                "imagery",
                "inference_result",
                "vector_revision",
                "report",
                "export",
                "backup_snapshot",
            },
        )
        expected_asset_statuses = {
            "mine_boundary:101": "ready",
            "basemap:102": "registered",
            "basemap:103": "processing",
            "basemap:104": "ready",
            "basemap:105": "failed",
            "basemap:106": "superseded",
            "imagery:201": "ready",
            "inference_result:301": "ready",
            "vector_revision:401": "ready",
            "report:501": "ready",
            "export:601": "ready",
            "backup_snapshot:701": "ready",
        }
        self.assertEqual(
            {item["id"]: item["status"] for item in items}, expected_asset_statuses
        )
        self._assert_no_physical_path_keys(body)

    def test_assets_filters_by_type_status_and_combination(self):
        self._seed_assets_read_model_sources()

        for query_string, field_name, expected_value, expected_ids in (
            ("type=imagery", "asset_type", "imagery", {"imagery:201"}),
            ("status=failed", "status", "failed", {"basemap:105"}),
            (
                "type=basemap&status=failed",
                "asset_type",
                "basemap",
                {"basemap:105"},
            ),
        ):
            with self.subTest(query_string=query_string):
                response = self.client.get(
                    f"/api/projects/{self.PROJECT_ID}/assets?{query_string}"
                )

                self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
                data = self.json_body(response)["data"]
                items = data["items"]
                self.assertEqual(data["count"], 1)
                self.assertEqual(data["count"], len(items))
                self.assertEqual({item["id"] for item in items}, expected_ids)
                self.assertTrue(all(item[field_name] == expected_value for item in items))
                if query_string == "type=basemap&status=failed":
                    self.assertTrue(all(item["status"] == "failed" for item in items))

    def test_assets_empty_project_matches_fixture(self):
        self._seed_blocked_project()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/assets")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(self.json_body(response), self.load_fixture("assets-empty.json"))

    def test_assets_invalid_filter_matches_fixture(self):
        self._seed_blocked_project()

        response = self.client.get(f"/api/projects/{self.PROJECT_ID}/assets?type=unknown")

        self.assertEqual(response.status_code, 400, response.get_data(as_text=True))
        self.assertEqual(
            self.json_body(response), self.load_fixture("assets-invalid-filter.json")
        )


if __name__ == "__main__":
    unittest.main()
