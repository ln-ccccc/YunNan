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

    def test_get_job_requires_login(self):
        response = self.client.get("/api/inference/jobs/missing")
        self.assertEqual(response.status_code, 401)

    def test_create_job_requires_project_context(self):
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

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data.decode("utf-8"))
        self.assertIn("project_id", data["msg"])

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
