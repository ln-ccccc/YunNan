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
from applications.models.inference_job import InferenceJob
from applications.project_hub.service import create_project


class TestInterpretationAPI(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.upload_root = self.root / "uploads"
        self.upload_root.mkdir()
        self.tif_path = self.upload_root / "scene.tif"
        self.tif_path.touch()
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.root / "project_storage")

        self.app = create_app("testing")
        self.app.config.update(
            UPLOADED_PHOTOS_DEST=str(self.upload_root),
            PROPAGATE_EXCEPTIONS=True,
        )
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self._login()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root
        self.temp_dir.cleanup()

    def _login(self):
        from applications.auth.service import sync_admin_from_env

        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(response.status_code, 200)

    @staticmethod
    def _json(response):
        return json.loads(response.data.decode("utf-8"))

    def test_without_project_returns_standalone_without_creating_job(self):
        response = self.client.post(
            "/api/analysis/kml_roi_inference",
            json={
                "old_tif_path": str(self.tif_path),
                "new_tif_path": str(self.tif_path),
                "year": "2022",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = self._json(response)["data"]
        self.assertEqual(payload["mode"], "standalone")
        self.assertIsNone(payload["job"])
        # 响应白名单：standalone 分支同样不外泄 vector_path 物理路径
        self.assertNotIn("vector_path", payload)
        self.assertEqual(InferenceJob.query.count(), 0)

    def test_matched_project_creates_scoped_job_and_copies_input(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        storage_root = Path(os.environ["PROJECT_STORAGE_ROOT"])
        vector_path = storage_root / f"projects/{project['id']}/mines/1/mines.geojson"
        vector_path.parent.mkdir(parents=True, exist_ok=True)
        vector_path.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
        with patch(
            "applications.inference.interpretation.resolve_interpretation_scope",
            return_value={
                "mode": "project",
                "project_id": project["id"],
                "mine_resource_id": 1,
                "vector_path": str(vector_path),
                "matched_fids": [101, 102],
                "warnings": [],
            },
        ):
            response = self.client.post(
                "/api/analysis/kml_roi_inference",
                json={
                    "project_id": project["id"],
                    "old_tif_path": str(self.tif_path),
                    "new_tif_path": str(self.tif_path),
                    "year": "2022",
                    "device": "auto",
                },
            )

        self.assertEqual(response.status_code, 201)
        payload = self._json(response)["data"]
        self.assertEqual(payload["mode"], "project")
        self.assertEqual(payload["matched_fids"], [101, 102])
        # 响应白名单：scope 中的服务器物理路径/内部资源 id 不得外泄（2026-09-22 契约审查）
        self.assertNotIn("vector_path", payload)
        self.assertNotIn("mine_resource_id", payload)
        self.assertEqual(payload["job"]["project_id"], project["id"])
        public_request = payload["job"]["request"]
        self.assertEqual(public_request["mine_fids"], [101, 102])
        self.assertNotIn("new_tif_path", public_request)
        job = InferenceJob.query.filter_by(id=payload["job"]["id"]).one()
        private_request = json.loads(job.request_payload_json)
        copied_input = Path(private_request["new_tif_path"])
        self.assertTrue(copied_input.is_file())
        self.assertIn(
            f"projects{os.sep}{project['id']}{os.sep}inputs{os.sep}interpretation{os.sep}2022",
            str(copied_input),
        )

    def test_rejects_paths_outside_upload_root(self):
        outside = self.root / "outside.tif"
        outside.touch()

        response = self.client.post(
            "/api/analysis/kml_roi_inference",
            json={"new_tif_path": str(outside), "year": "2022"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("上传目录", self._json(response)["msg"])
        self.assertEqual(InferenceJob.query.count(), 0)

    def test_project_configuration_error_does_not_fallback_to_global_kml(self):
        project = create_project({"name": "Empty", "region": "Dali"})

        response = self.client.post(
            "/api/analysis/kml_roi_inference",
            json={
                "project_id": project["id"],
                "new_tif_path": str(self.tif_path),
                "year": "2022",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("尚未激活矿山资源", self._json(response)["msg"])
        self.assertEqual(InferenceJob.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
