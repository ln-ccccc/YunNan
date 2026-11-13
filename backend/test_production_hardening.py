# -*- coding: utf-8 -*-
"""生产化加固批（2026-11-05）回归测试。

覆盖：/api/system/version 版本自描述、X-Request-Id 生成/回显/非法值替换、
Cache-Control 分层（API no-store / 鉴权响应 no-store）、登录失败限速
（用户名锁定 + 成功登录清零 + 锁定优先于验密）、单发通道普通图片大小闸门
（迁移审查 P2-4）、切片瓦片超 maxZoom 透明 PNG 兜底（对齐 BFF J-1 语义）。
"""
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from applications import create_app
from applications.extensions import db


def _reset_login_guard_state():
    """登录限速是模块级进程内状态，测试间必须清零防串扰。"""
    from applications.api import auth as auth_module

    auth_module._login_failures.clear()
    auth_module._login_ip_attempts.clear()


def _seed_admin_and_login(client, app):
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD"] = "Secret123!"
    app.config["ADMIN_USERNAME"] = "admin"
    app.config["ADMIN_PASSWORD"] = "Secret123!"
    from applications.auth.service import sync_admin_from_env

    sync_admin_from_env()
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "Secret123!"}
    )
    assert response.status_code == 200, response.get_data(as_text=True)


class HardeningBase(unittest.TestCase):
    def setUp(self):
        _reset_login_guard_state()
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # addCleanup 为 LIFO：先注册后执行的（ctx.pop 最先注册、最后执行）
        self.addCleanup(_reset_login_guard_state)
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)


class TestSystemVersionEndpoint(HardeningBase):
    def test_version_reports_sha_config_uptime(self):
        response = self.client.get("/api/system/version")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["service"], "yunnan-backend")
        self.assertIn("git_sha", data)
        self.assertEqual(data["flask_config"], "testing")
        self.assertGreaterEqual(data["uptime_seconds"], 0)


class TestRequestIdHeader(HardeningBase):
    def test_generated_when_client_silent(self):
        response = self.client.get("/api/system/version")
        self.assertTrue(response.headers.get("X-Request-Id"))

    def test_client_value_echoed(self):
        response = self.client.get(
            "/api/system/version", headers={"X-Request-Id": "fe-fixed-123456"}
        )
        self.assertEqual(response.headers["X-Request-Id"], "fe-fixed-123456")

    def test_invalid_value_replaced_not_echoed(self):
        response = self.client.get(
            "/api/system/version", headers={"X-Request-Id": "../etc/passwd"}
        )
        echoed = response.headers["X-Request-Id"]
        self.assertTrue(echoed)
        self.assertNotEqual(echoed, "../etc/passwd")


class TestCacheControlPolicy(HardeningBase):
    def test_api_responses_no_store(self):
        response = self.client.get("/api/auth/session")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")

    def test_unauthorized_response_no_store(self):
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")


class TestLoginRateLimit(HardeningBase):
    def _login(self, password):
        return self.client.post(
            "/api/auth/login", json={"username": "admin", "password": password}
        )

    def test_locks_after_five_failures(self):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        for _ in range(5):
            self.assertEqual(self._login("totally-wrong").status_code, 401)
        sixth = self._login("totally-wrong")
        self.assertEqual(sixth.status_code, 429, sixth.get_data(as_text=True))
        # 锁定优先于验密：正确口令也被拒
        locked_correct = self._login("Secret123!")
        self.assertEqual(locked_correct.status_code, 429)
        # 限流响应禁止缓存
        self.assertEqual(sixth.headers.get("Cache-Control"), "no-store")

    def test_success_clears_failure_counter(self):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        for _ in range(4):
            self.assertEqual(self._login("totally-wrong").status_code, 401)
        self.assertEqual(self._login("Secret123!").status_code, 200)
        for _ in range(4):
            # 成功已清零：再 4 次失败仍低于阈值，不会 429
            self.assertEqual(self._login("totally-wrong").status_code, 401)


class TestSingleShotImageSizeGate(unittest.TestCase):
    def setUp(self):
        _reset_login_guard_state()
        from applications.extensions.flask_uploads import UploadConfiguration
        from applications.extensions.init_upload import IMAGES_WITH_TIFF

        self.app = create_app("testing")
        self.upload_tmp = tempfile.mkdtemp(prefix="hardening-upload-")
        self.app.config["UPLOADED_PHOTOS_DEST"] = self.upload_tmp
        self.app.upload_set_config["photos"] = UploadConfiguration(
            self.upload_tmp, None, IMAGES_WITH_TIFF, ()
        )
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # addCleanup 为 LIFO：ctx.pop 最先注册、最后执行
        self.addCleanup(_reset_login_guard_state)
        self.addCleanup(lambda: shutil.rmtree(self.upload_tmp, ignore_errors=True))
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)

    def test_oversized_plain_image_rejected(self):
        _seed_admin_and_login(self.client, self.app)
        from applications.api import file as file_api_module

        payload = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)
        with patch.object(file_api_module, "MAX_UPLOAD_IMAGE_SIZE_MB", 1):
            response = self.client.post(
                "/api/file/upload",
                data={"files": (io.BytesIO(payload), "big.png"), "type": "地物分类"},
                content_type="multipart/form-data",
            )
        body = response.get_json()
        self.assertFalse(body.get("success"), body)
        self.assertIn("单发上传上限", body.get("msg", ""))


class TestTileOverZoomTransparentFallback(unittest.TestCase):
    def setUp(self):
        _reset_login_guard_state()
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="hardening-tiles-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        # addCleanup 为 LIFO：先注册后执行的（ctx.pop 最先注册、最后执行）
        self.addCleanup(_reset_login_guard_state)
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        self.addCleanup(self._restore_storage_root)

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    def _create_project(self):
        _seed_admin_and_login(self.client, self.app)
        response = self.client.post(
            "/api/projects",
            json={
                "name": "瓦片超zoom项目",
                "region": "昆明市",
                "manager": "测试管理员",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]["id"]

    def _seed_active_basemap(self):
        """造一个 min10/max12 的激活底图，z=12/x=3400 只有 1600.png 一格瓦片。"""
        from applications.models import ProjectSpatialResource

        project_id = self._create_project()
        tile_rel = Path("projects") / str(project_id) / "tiles" / "r-hardening"
        tile_abs = self.storage_root / tile_rel / "12" / "3400"
        tile_abs.mkdir(parents=True, exist_ok=True)
        (tile_abs / "1600.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        db.session.add(ProjectSpatialResource(
            project_id=project_id,
            resource_type="basemap",
            status="active",
            version=1,
            source_path="incoming/x.tif",
            source_format="tif",
            tile_path=str(tile_rel),
            min_zoom=10,
            max_zoom=12,
        ))
        db.session.commit()
        resource = ProjectSpatialResource.query.filter_by(project_id=project_id).first()
        return project_id, resource

    def test_over_max_zoom_returns_transparent_png(self):
        project_id, resource = self._seed_active_basemap()

        real = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/12/3400/1600.png"
        )
        self.assertEqual(real.status_code, 200)

        over = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/13/3400/1600.png"
        )
        self.assertEqual(over.status_code, 200, over.data[:40])
        self.assertEqual(over.headers["Content-Type"], "image/png")
        self.assertTrue(over.data.startswith(b"\x89PNG"))
        self.assertIn("max-age", over.headers.get("Cache-Control", ""))

        # 低于 min_zoom 维持 404（超zoom 兜底不扩大到资源级语义）
        under = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/9/3400/1600.png"
        )
        self.assertEqual(under.status_code, 404)

        # 未知资源仍 404——透明兜底不掩盖"资源不存在"
        missing = self.client.get(
            f"/api/projects/{project_id}/map-resources/999999/tiles/13/3400/1600.png"
        )
        self.assertEqual(missing.status_code, 404)

    def test_zoom_dir_exists_missing_tile_returns_transparent_short_cache(self):
        """锯齿边缺格但 zoom 目录存在 → 200 透明 + 短缓存（对齐 BFF J-1 兜底，
        2026-11-14 审查问题29：瓦片语义收敛到后端，BFF 改纯转发后此语义归 Flask）。"""
        project_id, resource = self._seed_active_basemap()

        missing_tile = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/12/3400/1599.png"
        )
        self.assertEqual(missing_tile.status_code, 200, missing_tile.data[:40])
        self.assertEqual(missing_tile.headers["Content-Type"], "image/png")
        self.assertTrue(missing_tile.data.startswith(b"\x89PNG"))
        # 透明兜底只短缓存：底图重切修复后旧浏览器不被钉一天（BFF 同款 P2-1 语义）
        self.assertIn("max-age=60", missing_tile.headers.get("Cache-Control", ""))

        # zoom 目录整体缺失仍是 404——资源级异常不被透明瓦片掩盖
        # （z=11 在 min/max 范围内但未切级；对齐旧 BFF"仅该 zoom 目录存在才兜底"）
        no_zoom_dir = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/11/3400/1600.png"
        )
        self.assertEqual(no_zoom_dir.status_code, 404)

    def test_real_tile_sets_long_cache_header(self):
        """真实瓦片显式长缓存（对齐 BFF max-age=86400；缺省 send_from_directory
        无 max-age，浏览器每次重验）。"""
        project_id, resource = self._seed_active_basemap()

        real = self.client.get(
            f"/api/projects/{project_id}/map-resources/{resource.id}/tiles/12/3400/1600.png"
        )
        self.assertEqual(real.status_code, 200)
        self.assertIn("max-age=86400", real.headers.get("Cache-Control", ""))


if __name__ == "__main__":
    unittest.main()
