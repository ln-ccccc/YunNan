# -*- coding: utf-8 -*-
"""大文件影像上传支持（移植江西 2026-09-19 验收需求）。

覆盖项：
- 上传硬上限 8GB（MAX_UPLOAD_TIFF_SIZE_MB），500MB 仅保留为切片预览闸门
- 地物分类 keep_tiff_raw 快速路径：不切片、不生成预览 PNG，直传原始 tif
- 切片预览失败时优雅降级：保留原始文件，不再拒绝上传
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.datastructures import FileStorage

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.common.utils import upload as upload_curd
from applications.common.utils.tiff_processor import (
    MAX_TIFF_SIZE_MB,
    MAX_UPLOAD_TIFF_SIZE_MB,
)
from applications.extensions import db
from applications.extensions.flask_uploads import UploadConfiguration
from applications.extensions.init_upload import IMAGES_WITH_TIFF

REPO_ROOT = Path(__file__).resolve().parents[1]


class LargeTiffUploadBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        # 源码目录可能只读挂载（容器内跑测试），上传目标改指临时可写目录
        self.upload_tmp = tempfile.mkdtemp(prefix="upload-test-")
        self.app.config["UPLOADED_PHOTOS_DEST"] = self.upload_tmp
        self.app.upload_set_config["photos"] = UploadConfiguration(
            self.upload_tmp, None, IMAGES_WITH_TIFF, ()
        )
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        shutil.rmtree(self.upload_tmp, ignore_errors=True)

    def login_as_admin(self, password="Secret123!"):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = password
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = password
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        assert response.status_code == 200


class TestUploadSizeConstants(LargeTiffUploadBase):
    def test_upload_hard_limit_is_8gb_and_preview_gate_stays_500mb(self):
        self.assertEqual(MAX_UPLOAD_TIFF_SIZE_MB, 8192)
        # 切片预览/整图读内存路径的闸门保留，不被上传放宽连带抬高
        self.assertEqual(MAX_TIFF_SIZE_MB, 500)


class TestUploadSizeGate(LargeTiffUploadBase):
    def test_tiff_over_hard_limit_rejected(self):
        self.login_as_admin()
        # 把硬上限 patch 到 1 字节以下：4 字节流即超限，避免构造 GB 级文件
        with patch("applications.api.file.MAX_UPLOAD_TIFF_SIZE_MB", 0.000001):
            response = self.client.post(
                "/api/file/upload",
                data={
                    "files": (io.BytesIO(b"II*\x00"), "big.tif"),
                    "type": "地物分类",
                },
                content_type="multipart/form-data",
            )
        body = response.get_json()
        self.assertFalse(body.get("success"))
        self.assertIn("硬上限", body.get("msg", ""))

    def test_tiff_within_hard_limit_passes_gate(self):
        self.login_as_admin()
        with patch("applications.api.file.upload_curd") as mock_upload:
            mock_upload.upload_one.return_value = [("/_uploads/photos/x.tif", 1, "big.tif", "/tmp/x.tif")]
            response = self.client.post(
                "/api/file/upload",
                data={
                    "files": (io.BytesIO(b"II*\x00"), "big.tif"),
                    "type": "地物分类",
                    "keepRawTiff": "true",
                },
                content_type="multipart/form-data",
            )
        body = response.get_json()
        self.assertTrue(body.get("success"), msg=str(body))
        self.assertEqual(body["data"][0]["raw_tiff_path"], "/tmp/x.tif")

    def test_request_body_over_60mb_passes_http_layer(self):
        """回归：MAX_CONTENT_LENGTH 曾固定 60MB，>60MB 的上传在 Werkzeug 表单
        解析阶段即 413，视图内 8GB 闸门与 keep_tiff_raw 路径完全不可达。"""
        self.login_as_admin()
        payload = io.BytesIO(b"\x00" * (61 * 1024 * 1024))
        with patch("applications.api.file.upload_curd") as mock_upload:
            mock_upload.upload_one.return_value = [("/_uploads/photos/big.tif", 1, "big.tif", "/tmp/big.tif")]
            response = self.client.post(
                "/api/file/upload",
                data={
                    "files": (payload, "big.tif"),
                    "type": "地物分类",
                    "keepRawTiff": "true",
                },
                content_type="multipart/form-data",
            )
        body = response.get_json()
        self.assertNotEqual(response.status_code, 413)
        self.assertTrue(body.get("success"), msg=str(body))


class TestUploadOneFastPath(LargeTiffUploadBase):
    """keep_tiff_raw=True 的地物分类快速路径与失败优雅降级。"""

    def _storage_dir(self):
        return Path(self.app.config["UPLOADED_PHOTOS_DEST"])

    def test_keep_tiff_raw_skips_processing_and_keeps_original(self):
        photo = FileStorage(
            stream=io.BytesIO(b"fake-tiff-bytes"), filename="county.tif", content_type="image/tiff"
        )
        with patch("applications.common.utils.tiff_processor.process_uploaded_tiff") as mock_process:
            results = upload_curd.upload_one(
                photo, mime="image/tiff", type_=3, enable_slicing=True, keep_tiff_raw=True
            )
        mock_process.assert_not_called()
        self.assertEqual(len(results), 1)
        file_url, photo_id, display_name, raw_tiff_path = results[0]
        self.assertEqual(display_name, "county.tif")
        # 原始 tif 保留在服务器，raw_tiff_path 指向本体供推理链路取用
        self.assertTrue(os.path.exists(raw_tiff_path))
        self.assertTrue(raw_tiff_path.endswith(".tif"))

    def test_processing_failure_degrades_gracefully_without_delete(self):
        photo = FileStorage(
            stream=io.BytesIO(b"fake-tiff-bytes"), filename="broken.tif", content_type="image/tiff"
        )
        with patch(
            "applications.common.utils.tiff_processor.process_uploaded_tiff",
            side_effect=ValueError("文件大小 (2400.0MB) 超过限制 (500MB)"),
        ):
            results = upload_curd.upload_one(
                photo, mime="image/tiff", type_=3, enable_slicing=True, keep_tiff_raw=False
            )
        self.assertEqual(len(results), 1)
        file_url, photo_id, display_name, raw_tiff_path = results[0]
        # 2.4GB 影像不再被切片预览拒绝：原始文件保留，降级为直传条目
        self.assertEqual(display_name, "broken.tif")
        self.assertTrue(os.path.exists(raw_tiff_path))

    def test_processing_success_still_removes_original_without_keep(self):
        photo = FileStorage(
            stream=io.BytesIO(b"fake-tiff-bytes"), filename="small.tif", content_type="image/tiff"
        )
        with patch("applications.common.utils.tiff_processor.process_uploaded_tiff") as mock_process:
            mock_process.return_value = [
                {"filename": "small_tile.png", "path": str(self._storage_dir() / "small_tile.png")}
            ]
            (self._storage_dir() / "small_tile.png").write_bytes(b"png")
            results = upload_curd.upload_one(
                photo, mime="image/tiff", type_=3, enable_slicing=True, keep_tiff_raw=False
            )
        self.assertEqual(len(results), 1)
        _, _, display_name, raw_tiff_path = results[0]
        self.assertEqual(display_name, "small_tile.png")
        # 原语义回归：不保留原始 tif 时，切片后原文件被删除
        self.assertIsNone(raw_tiff_path)


if __name__ == "__main__":
    unittest.main()
