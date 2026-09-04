import datetime
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class TestInferencePaths(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def load_paths_module():
        module_path = Path(__file__).parent / "applications" / "inference" / "paths.py"
        spec = importlib.util.spec_from_file_location("inference_paths", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_resolve_input_rejects_path_outside_allowed_roots(self):
        resolve_input_path = self.load_paths_module().resolve_input_path

        outside = self.root.parent / "outside-input.tif"
        outside.touch(exist_ok=True)
        self.addCleanup(outside.unlink, missing_ok=True)

        with self.assertRaises(ValueError):
            resolve_input_path(outside, [self.root])

    def test_create_job_workdir_is_unique_and_not_reusable(self):
        create_job_workdir = self.load_paths_module().create_job_workdir

        first = create_job_workdir(self.root, "job-a")
        second = create_job_workdir(self.root, "job-b")

        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())
        with self.assertRaises(FileExistsError):
            create_job_workdir(self.root, "job-a")

    def test_create_job_workdir_rejects_path_escape(self):
        create_job_workdir = self.load_paths_module().create_job_workdir

        with self.assertRaises(ValueError):
            create_job_workdir(self.root, "../outside-job")

    def test_initialize_job_workdir_rejects_non_empty_directory_without_deleting(self):
        initialize_job_workdir = self.load_paths_module().initialize_job_workdir
        work_dir = self.root / "existing-job"
        work_dir.mkdir()
        marker = work_dir / "belongs-to-another-job.txt"
        marker.write_text("keep", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            initialize_job_workdir(work_dir)

        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_resolve_output_file_rejects_fid_and_filename_traversal(self):
        resolve_output_file = self.load_paths_module().resolve_output_file

        with self.assertRaises(ValueError):
            resolve_output_file(self.root, "../outside", "result.png")
        with self.assertRaises(ValueError):
            resolve_output_file(self.root, "123", "../../secret.txt")


class TestInferenceOutcomeStatus(unittest.TestCase):
    @staticmethod
    def load_status_module():
        module_path = Path(__file__).parent / "applications" / "inference" / "status.py"
        spec = importlib.util.spec_from_file_location("inference_status", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_succeeded_when_no_tiles_failed(self):
        derive_outcome_status = self.load_status_module().derive_outcome_status
        self.assertEqual(derive_outcome_status([], ["1"]), "succeeded")

    def test_partial_failed_when_some_output_was_written(self):
        derive_outcome_status = self.load_status_module().derive_outcome_status
        self.assertEqual(derive_outcome_status(["b.png"], ["1"]), "partial_failed")

    def test_failed_when_tiles_failed_and_no_output_was_written(self):
        derive_outcome_status = self.load_status_module().derive_outcome_status
        self.assertEqual(derive_outcome_status(["a.png"], []), "failed")

    def test_job_transition_accepts_only_declared_edges(self):
        status_module = self.load_status_module()

        status_module.validate_job_transition("queued", "running")
        status_module.validate_job_transition("running", "succeeded_with_fallback")
        with self.assertRaises(ValueError):
            status_module.validate_job_transition("succeeded", "running")

    def test_terminal_statuses_are_recognized(self):
        is_terminal_status = self.load_status_module().is_terminal_status

        self.assertTrue(is_terminal_status("partial_failed"))
        self.assertTrue(is_terminal_status("cancelled"))
        self.assertFalse(is_terminal_status("running"))


class TestInferenceJobPayload(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.old_tif = self.root / "old.tif"
        self.kml = self.root / "roi.kml"
        self.old_tif.touch()
        self.kml.touch()

    def tearDown(self):
        self.temp_dir.cleanup()

    @staticmethod
    def load_jobs_module():
        module_path = Path(__file__).parent / "applications" / "inference" / "jobs.py"
        spec = importlib.util.spec_from_file_location("inference_jobs", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_normalize_request_keeps_legacy_fields_and_normalizes_cuda_to_auto(self):
        normalize_job_request = self.load_jobs_module().normalize_job_request

        result = normalize_job_request(
            {
                "old_tif_path": str(self.old_tif),
                "kml_path": str(self.kml),
                "device": "cuda:7",
                "year": "2025",
            },
            allowed_roots=[self.root],
        )

        self.assertEqual(result["old_tif_path"], str(self.old_tif.resolve()))
        self.assertEqual(result["new_tif_path"], str(self.old_tif.resolve()))
        self.assertEqual(result["kml_path"], str(self.kml.resolve()))
        self.assertEqual(result["requested_device"], "auto")
        self.assertEqual(result["year"], "2025")

    def test_normalize_request_rejects_input_outside_allowed_roots(self):
        normalize_job_request = self.load_jobs_module().normalize_job_request
        outside = self.root.parent / "outside-job.tif"
        outside.touch(exist_ok=True)
        self.addCleanup(outside.unlink, missing_ok=True)

        with self.assertRaises(ValueError):
            normalize_job_request(
                {"old_tif_path": str(outside), "kml_path": str(self.kml)},
                allowed_roots=[self.root],
            )

    def test_normalize_request_rejects_output_outside_allowed_roots(self):
        normalize_job_request = self.load_jobs_module().normalize_job_request

        with self.assertRaises(ValueError):
            normalize_job_request(
                {
                    "old_tif_path": str(self.old_tif),
                    "kml_path": str(self.kml),
                    "output_root": str(self.root.parent / "outside-output"),
                },
                allowed_roots=[self.root],
                allowed_output_roots=[self.root / "outputs"],
            )

    def test_normalize_request_validates_and_sorts_project_mine_fids(self):
        normalize_job_request = self.load_jobs_module().normalize_job_request

        result = normalize_job_request(
            {
                "old_tif_path": str(self.old_tif),
                "mine_fids": [102, "101", 102],
            },
            allowed_roots=[self.root],
        )
        self.assertEqual(result["mine_fids"], [101, 102])

        with self.assertRaisesRegex(ValueError, "mine_fids"):
            normalize_job_request(
                {
                    "old_tif_path": str(self.old_tif),
                    "mine_fids": ["not-a-fid"],
                },
                allowed_roots=[self.root],
            )

    def test_stale_worker_capability_is_not_reported_as_ready(self):
        serialize_worker_capability = self.load_jobs_module().serialize_worker_capability
        state = SimpleNamespace(
            worker_id="worker-1",
            requested_device="auto",
            effective_device="cuda:0",
            fallback_reason=None,
            warnings_json="[]",
            gpu_name="NVIDIA GPU",
            compute_capability="9.0",
            update_time=datetime.datetime.now() - datetime.timedelta(minutes=5),
        )

        result = serialize_worker_capability(state)

        self.assertEqual(result["worker_status"], "stale")
        self.assertTrue(result["warnings"])

    def test_serialize_job_removes_private_paths_from_public_sections(self):
        serialize_job = self.load_jobs_module().serialize_job
        job = SimpleNamespace(
            id="job-private-paths",
            project_id=7,
            status="failed",
            requested_device="auto",
            effective_device=None,
            fallback_reason=None,
            warnings_json='["Worker used /secret/warn.log"]',
            progress_current=1,
            progress_total=2,
            request_payload_json=json.dumps(
                {
                    "project_id": 7,
                    "dataset_id": 11,
                    "mine_resource_id": 13,
                    "mine_fids": [101],
                    "year": "2024",
                    "old_tif_path": "/secret/scene.tif",
                    "new_tif_path": "/secret/scene.tif",
                    "kml_path": "/secret/mine.geojson",
                    "output_root": "/secret/outputs",
                    "storage_key": "incoming/scene.tif",
                }
            ),
            result_json=json.dumps(
                {
                    "written_fid_list": ["101"],
                    "output_dir": "/secret/outputs/101",
                    "nested": {
                        "manifest_path": "/secret/manifest.json",
                        "count": 1,
                    },
                }
            ),
            error_code="INFERENCE_FAILED",
            error_message="无法读取 /secret/scene.tif",
            cancel_requested=False,
            create_time=None,
            started_at=None,
            finished_at=None,
        )

        result = serialize_job(job)
        public_json = json.dumps(result, ensure_ascii=False)

        self.assertEqual(
            result["request"],
            {
                "project_id": 7,
                "dataset_id": 11,
                "mine_resource_id": 13,
                "mine_fids": [101],
                "year": "2024",
            },
        )
        self.assertEqual(result["result"]["written_fid_list"], ["101"])
        self.assertEqual(result["result"]["nested"], {"count": 1})
        self.assertNotIn("/secret", public_json)
        self.assertNotIn("old_tif_path", public_json)
        self.assertNotIn("output_root", public_json)
        self.assertNotIn("manifest_path", public_json)
        self.assertNotIn("Worker used", public_json)
        self.assertNotIn("/secret", result["error"]["message"])


HAS_FLASK_TEST_STACK = all(
    importlib.util.find_spec(name) is not None
    for name in ("flask", "flask_sqlalchemy", "rasterio", "cv2")
)


@unittest.skipUnless(HAS_FLASK_TEST_STACK, "宿主环境缺少 Flask/光栅测试依赖")
class TestInferenceJobAPI(unittest.TestCase):
    def setUp(self):
        from applications import create_app
        from applications.extensions import db

        self.db = db
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.storage_root = self.root / "project_storage"
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        (self.storage_root / "incoming").mkdir(parents=True)
        self.old_tif = self.root / "old.tif"
        self.kml = self.root / "roi.kml"
        self.output_root = self.root / "outputs"
        self.old_tif.touch()
        self.kml.touch()
        self.app = create_app("testing")
        self.app.config.update(
            INFERENCE_INPUT_ROOTS=str(self.root),
            INFERENCE_OUTPUT_ROOTS=str(self.output_root),
            PROPAGATE_EXCEPTIONS=True,
        )
        self.client = self.app.test_client()
        self.context = self.app.app_context()
        self.context.push()
        self.db.create_all()

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.context.pop()
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
        self.temp_dir.cleanup()

    def login(self):
        from applications.auth.service import sync_admin_from_env

        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(response.status_code, 200)

    def seed_ready_project(self, *, status="active", include_basemap=True):
        from applications.models.project import Project, ProjectDataset, ProjectMineBinding
        from applications.models.project_spatial import ProjectSpatialResource

        project = Project(
            name="安全推理项目",
            status=status,
            monitor_start_year=2024,
            monitor_end_year=2025,
        )
        self.db.session.add(project)
        self.db.session.flush()

        vector_relative_path = f"projects/{project.id}/normalized/mine.geojson"
        vector_path = self.storage_root / vector_relative_path
        vector_path.parent.mkdir(parents=True, exist_ok=True)
        vector_path.write_text("{}", encoding="utf-8")
        mine_resource = ProjectSpatialResource(
            project_id=project.id,
            resource_type="mine_vector",
            version=1,
            status="active",
            source_path=f"projects/{project.id}/raw/mine.geojson",
            normalized_path=vector_relative_path,
            source_format="geojson",
        )
        self.db.session.add(mine_resource)
        self.db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=101))
        self.db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=102))
        if include_basemap:
            self.db.session.add(
                ProjectSpatialResource(
                    project_id=project.id,
                    resource_type="basemap",
                    version=1,
                    status="active",
                    source_path=f"projects/{project.id}/basemap/base.tif",
                    normalized_path=f"projects/{project.id}/basemap/base.tif",
                    source_format="tif",
                )
            )

        dataset_path = self.storage_root / "incoming" / "scene.tif"
        dataset_path.touch()
        dataset = ProjectDataset(
            project_id=project.id,
            dataset_kind="imagery",
            display_name="2024 年影像",
            file_path="incoming/scene.tif",
            source_format="tif",
            year_start=2024,
            year_end=2024,
        )
        self.db.session.add(dataset)
        self.db.session.commit()
        return SimpleNamespace(
            project=project,
            mine_resource=mine_resource,
            dataset=dataset,
            dataset_path=dataset_path,
            vector_path=vector_path,
        )

    @staticmethod
    def project_scope(seed, matched_fids=(101, 102)):
        return {
            "mode": "project",
            "project_id": seed.project.id,
            "mine_resource_id": seed.mine_resource.id,
            "vector_path": str(seed.vector_path),
            "matched_fids": list(matched_fids),
            "warnings": [],
        }

    def post_safe_job(self, seed, body=None, matched_fids=(101, 102)):
        request_body = body or {
            "project_id": seed.project.id,
            "dataset_id": seed.dataset.id,
            "year": "2024",
            "device": "auto",
        }
        with patch(
            "applications.api.inference.resolve_interpretation_scope",
            return_value=self.project_scope(seed, matched_fids),
        ):
            return self.client.post("/api/inference/jobs", json=request_body)

    def job_count(self):
        from applications.models.inference_job import InferenceJob

        return InferenceJob.query.count()

    def test_get_job_requires_login(self):
        response = self.client.get("/api/inference/jobs/missing")
        self.assertEqual(response.status_code, 401)

    def test_create_job_rejects_legacy_path_body(self):
        self.login()
        response = self.client.post(
            "/api/inference/jobs",
            json={
                "old_tif_path": str(self.old_tif),
                "kml_path": str(self.kml),
                "output_root": str(self.output_root),
                "device": "cuda:0",
            },
        )

        self.assertEqual(response.status_code, 422)
        data = json.loads(response.data.decode("utf-8"))
        self.assertIn("不支持", data["msg"])
        self.assertEqual(self.job_count(), 0)

    def test_create_job_uses_registered_dataset_and_hides_private_paths(self):
        from applications.models.inference_job import InferenceJob

        self.login()
        seed = self.seed_ready_project()

        response = self.post_safe_job(seed)

        self.assertEqual(response.status_code, 201)
        public_job = json.loads(response.data.decode("utf-8"))["data"]
        job = InferenceJob.query.one()
        private_payload = json.loads(job.request_payload_json)
        expected_output_root = (
            self.storage_root / "projects" / str(seed.project.id) / "outputs" / "inference"
        ).resolve()
        self.assertEqual(private_payload["old_tif_path"], str(seed.dataset_path.resolve()))
        self.assertEqual(private_payload["new_tif_path"], str(seed.dataset_path.resolve()))
        self.assertEqual(private_payload["kml_path"], str(seed.vector_path.resolve()))
        self.assertEqual(private_payload["output_root"], str(expected_output_root))
        self.assertEqual(private_payload["dataset_id"], seed.dataset.id)
        self.assertEqual(private_payload["mine_fids"], [101, 102])
        self.assertEqual(
            public_job["request"],
            {
                "project_id": seed.project.id,
                "dataset_id": seed.dataset.id,
                "mine_resource_id": seed.mine_resource.id,
                "mine_fids": [101, 102],
                "year": "2024",
                "old_year": "",
                "new_year": "",
                "requested_device": "auto",
                "limit": 0,
            },
        )
        self.assertNotIn(str(self.storage_root), json.dumps(public_job, ensure_ascii=False))

    def test_create_job_rejects_unknown_fields_without_creating_job(self):
        self.login()
        seed = self.seed_ready_project()
        unsupported_fields = {
            "old_tif_path": "/secret/old.tif",
            "new_tif_path": "/secret/new.tif",
            "kml_path": "/secret/mine.kml",
            "output_root": "/secret/output",
            "storage_key": "incoming/scene.tif",
            "file_path": "incoming/scene.tif",
            "limit": 1,
        }

        for field_name, field_value in unsupported_fields.items():
            with self.subTest(field_name=field_name):
                response = self.post_safe_job(
                    seed,
                    {
                        "project_id": seed.project.id,
                        "dataset_id": seed.dataset.id,
                        field_name: field_value,
                    },
                )

                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.job_count(), 0)

    def test_create_job_rejects_dataset_from_another_project(self):
        self.login()
        seed = self.seed_ready_project()
        other_seed = self.seed_ready_project()

        response = self.post_safe_job(
            seed,
            {
                "project_id": seed.project.id,
                "dataset_id": other_seed.dataset.id,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("数据集", json.loads(response.data.decode("utf-8"))["msg"])
        self.assertEqual(self.job_count(), 0)

    def test_create_job_rejects_archived_or_not_ready_project(self):
        self.login()
        archived_seed = self.seed_ready_project(status="archived")
        not_ready_seed = self.seed_ready_project(include_basemap=False)

        for seed in (archived_seed, not_ready_seed):
            with self.subTest(project_id=seed.project.id):
                response = self.post_safe_job(seed)

                self.assertEqual(response.status_code, 400)
                self.assertIn("推理", json.loads(response.data.decode("utf-8"))["msg"])
                self.assertEqual(self.job_count(), 0)

    def test_create_job_rejects_invalid_dataset_storage_keys_without_creating_job(self):
        self.login()
        seed = self.seed_ready_project()
        invalid_storage_keys = (
            "../scene.tif",
            "/secret/scene.tif",
            r"C:\secret\scene.tif",
            r"\\server\share\scene.tif",
            r"incoming\..\scene.tif",
            "incoming/scene.jpg",
            "incoming/missing.tif",
            "incoming",
        )

        for storage_key in invalid_storage_keys:
            with self.subTest(storage_key=storage_key):
                seed.dataset.file_path = storage_key
                self.db.session.commit()
                response = self.post_safe_job(seed)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.job_count(), 0)

    def test_create_job_hides_private_path_from_internal_input_failure(self):
        self.login()
        seed = self.seed_ready_project()

        with patch(
            "applications.api.inference.normalize_job_request",
            side_effect=FileNotFoundError("new_tif_path 不存在: /secret/scene.tif"),
        ):
            response = self.post_safe_job(seed)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("/secret", response.data.decode("utf-8"))
        self.assertEqual(self.job_count(), 0)

    def test_create_job_converts_tiff_scope_error_to_safe_failure(self):
        from rasterio.errors import RasterioIOError

        self.login()
        seed = self.seed_ready_project()
        body = {
            "project_id": seed.project.id,
            "dataset_id": seed.dataset.id,
            "year": "2024",
        }
        with patch(
            "applications.api.inference.resolve_interpretation_scope",
            side_effect=RasterioIOError("无法打开 /secret/scene.tif"),
        ):
            response = self.client.post("/api/inference/jobs", json=body)

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("/secret", response.data.decode("utf-8"))
        self.assertEqual(self.job_count(), 0)

    def test_create_job_rejects_requested_fids_outside_interpretation_scope(self):
        self.login()
        seed = self.seed_ready_project()
        invalid_values = ([103], [], [101, 103], [0])

        for mine_fids in invalid_values:
            with self.subTest(mine_fids=mine_fids):
                response = self.post_safe_job(
                    seed,
                    {
                        "project_id": seed.project.id,
                        "dataset_id": seed.dataset.id,
                        "mine_fids": mine_fids,
                    },
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.job_count(), 0)

    def test_get_job_hides_persisted_private_paths(self):
        from applications.models.inference_job import InferenceJob

        self.login()
        job = InferenceJob(
            id="private-path-job",
            status="failed",
            requested_device="auto",
            request_payload_json=json.dumps(
                {
                    "project_id": 1,
                    "dataset_id": 2,
                    "old_tif_path": "/secret/scene.tif",
                }
            ),
            result_json=json.dumps(
                {
                    "written_fid_list": ["101"],
                    "work_dir": "/secret/work",
                }
            ),
            error_code="FAILED",
            error_message="处理失败: /secret/work",
        )
        self.db.session.add(job)
        self.db.session.commit()

        response = self.client.get(f"/api/inference/jobs/{job.id}")

        self.assertEqual(response.status_code, 200)
        public_json = response.data.decode("utf-8")
        self.assertIn("written_fid_list", public_json)
        self.assertNotIn("/secret", public_json)
        self.assertNotIn("old_tif_path", public_json)
        self.assertNotIn("work_dir", public_json)

    def test_recover_abandoned_jobs_only_fails_previous_process_in_same_container(self):
        from applications.inference.jobs import recover_abandoned_jobs
        from applications.models.inference_job import InferenceJob

        jobs = [
            InferenceJob(id="old", status="running", worker_id="worker-host:41", request_payload_json="{}"),
            InferenceJob(id="current", status="running", worker_id="worker-host:42", request_payload_json="{}"),
            InferenceJob(id="other", status="running", worker_id="other-host:7", request_payload_json="{}"),
        ]
        self.db.session.add_all(jobs)
        self.db.session.commit()

        changed = recover_abandoned_jobs("worker-host", "worker-host:42")

        self.assertEqual(changed, 1)
        self.assertEqual(InferenceJob.query.get("old").status, "failed")
        self.assertEqual(InferenceJob.query.get("old").error_code, "WORKER_RESTARTED")
        self.assertEqual(InferenceJob.query.get("current").status, "running")
        self.assertEqual(InferenceJob.query.get("other").status, "running")

    def test_distribute_outputs_publishes_staged_files_with_atomic_replace(self):
        from applications.kml_roi.tiles import distribute_outputs

        mmseg_dir = self.root / "mmseg"
        output_root = self.root / "published"
        staging_root = self.root / "staging"
        mmseg_dir.mkdir()
        (mmseg_dir / "pred_7_old_tile.png").write_bytes(b"image")
        (mmseg_dir / "mask_7_old_tile.png").write_bytes(b"mask")

        with patch("applications.kml_roi.tiles.os.replace", wraps=os.replace) as replace_file:
            result = distribute_outputs(
                ["7"],
                mmseg_dir,
                output_root,
                {
                    "7": [
                        {
                            "src_base": "7_old_tile",
                            "dst_base": "7_old",
                            "already_cropped": True,
                        }
                    ]
                },
                staging_root=staging_root,
            )

        self.assertEqual(result["written_fid_list"], ["7"])
        self.assertEqual((output_root / "7" / "7_old.png").read_bytes(), b"image")
        self.assertEqual((output_root / "7" / "7_old_mask.png").read_bytes(), b"mask")
        self.assertEqual(replace_file.call_count, 2)

    def test_distribute_outputs_rejects_fid_path_traversal(self):
        from applications.kml_roi.tiles import distribute_outputs

        with self.assertRaisesRegex(ValueError, "FID"):
            distribute_outputs(
                ["../escape"],
                self.root / "mmseg",
                self.root / "published",
                {"../escape": [{"src_base": "x", "dst_base": "x", "already_cropped": True}]},
                staging_root=self.root / "staging",
            )


if __name__ == "__main__":
    unittest.main()
