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


class TestSyncInferenceConcurrencyGuard(unittest.TestCase):
    """semantic_segmentation 并发闸（2026-11-13 风险复盘）。

    同步推理占满 gunicorn 线程池会堵死其余 API；闸门占满时必须快速失败，
    且不触碰推理实现。
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = str(Path(self.temp_dir.name) / "project_storage")

        self.app = create_app("testing")
        self.app.config.update(PROPAGATE_EXCEPTIONS=True)
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
    def _payload():
        return {
            "model_path": "mmseg:cc-ln/CUGRS",
            "list": [{"url": "a.tif"}],
            "prehandle": 0,
            "denoise": 0,
        }

    def test_busy_slot_returns_429_without_touching_inference(self):
        from applications.api.analysis import _sync_inference_slots

        acquired = _sync_inference_slots.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with patch("applications.api.analysis.terrain_classification") as mocked:
                response = self.client.post(
                    "/api/analysis/semantic_segmentation", json=self._payload()
                )
            self.assertEqual(response.status_code, 429)
            body = json.loads(response.data.decode("utf-8"))
            self.assertEqual(body["code"], 1)
            self.assertIn("正在执行", body["msg"])
            mocked.assert_not_called()
        finally:
            _sync_inference_slots.release()

    def test_free_slot_runs_inference_and_releases(self):
        with patch("applications.api.analysis.terrain_classification") as mocked:
            response = self.client.post(
                "/api/analysis/semantic_segmentation", json=self._payload()
            )
        self.assertEqual(response.status_code, 200)
        mocked.assert_called_once()
        from applications.api.analysis import _sync_inference_slots

        # 执行完成后闸门必须归还，否则后续请求全部 429
        self.assertTrue(_sync_inference_slots.acquire(blocking=False))
        _sync_inference_slots.release()


if __name__ == "__main__":
    unittest.main()
