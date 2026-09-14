# -*- coding: utf-8 -*-
"""2026-09-15 安全复审 Y1-1..Y1-6 回归测试。

覆盖项：
- Y1-1 /static/<path> 免登录旁路（上传/生成目录在 Flask static 下）
- Y1-2 spectral 输入任意路径直通与 kml_path 越界
- Y1-3 IMAGES 上传白名单包含 svg
- Y1-4 泛化异常原文回显（project/analysis/file 共 6 处）
- Y1-5 init_db SQL 失败被静默吞掉
- Y1-6 分页参数与 request.json 容错
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.common.path_global import up_dir
from applications.extensions import db
from applications.extensions.flask_uploads import IMAGES

REPO_ROOT = Path(__file__).resolve().parents[1]


class SecurityRecheckBase(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.created_upload_files = []
        self.temp_dirs = []
        os.makedirs(up_dir, exist_ok=True)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        for fpath in self.created_upload_files:
            if os.path.exists(fpath):
                os.remove(fpath)
        for dpath in self.temp_dirs:
            if os.path.exists(dpath):
                shutil.rmtree(dpath)

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
        self.assertEqual(response.status_code, 200)

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))


# ---------------------------------------------------------------- Y1-1


class TestStaticUploadRequiresLogin(SecurityRecheckBase):
    def _write_probe(self):
        probe_name = f"auth_probe_{uuid.uuid4().hex[:8]}.png"
        probe_path = os.path.join(up_dir, probe_name)
        os.makedirs(up_dir, exist_ok=True)
        with open(probe_path, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\nprobe")
        self.created_upload_files.append(probe_path)
        return probe_name

    def test_anonymous_static_upload_gets_401(self):
        probe_name = self._write_probe()
        response = self.client.get(f"/static/upload/{probe_name}")
        self.assertEqual(response.status_code, 401)

    def test_logged_in_static_upload_served_or_404(self):
        probe_name = self._write_probe()
        self.login_as_admin()
        response = self.client.get(f"/static/upload/{probe_name}")
        self.assertIn(response.status_code, (200, 304))
        self.assertNotEqual(response.status_code, 401)

        missing = self.client.get("/static/upload/__missing__.png")
        self.assertEqual(missing.status_code, 404)


# ---------------------------------------------------------------- Y1-2


class TestSpectralPathConfinement(SecurityRecheckBase):
    def _make_tif(self, directory):
        os.makedirs(directory, exist_ok=True)
        fpath = os.path.join(directory, f"evil_{uuid.uuid4().hex[:8]}.tif")
        height, width = 8, 8
        base = np.linspace(10, 200, num=height * width, dtype=np.float32).reshape(height, width)
        arr = np.stack([base + i * 3 for i in range(4)], axis=0)
        with rasterio.open(
            fpath,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=4,
            dtype=np.float32,
            transform=from_origin(0, 0, 1, 1),
        ) as dst:
            for i in range(4):
                dst.write(arr[i], i + 1)
        return fpath

    def test_absolute_tiff_path_outside_upload_dir_is_not_read(self):
        secret_dir = tempfile.mkdtemp()
        self.temp_dirs.append(secret_dir)
        secret_tif = self._make_tif(secret_dir)

        self.login_as_admin()
        response = self.client.post(
            "/api/analysis/spectral_indices",
            json={
                "list": [{"src": "x", "raw_tiff_path": secret_tif}],
                "index_type": "NDVI",
                "band_map": {"nir": 4, "red": 3},
            },
        )
        body = self._json(response)
        # 若任意路径直通存在，secret_tif 会被 rasterio 打开并计算成功
        self.assertFalse(body.get("success"), msg=f"不应读取上传目录外文件: {secret_tif}")
        self.assertIn("文件不存在", body.get("msg", ""))

    def test_traversal_and_encoded_tiff_paths_collapse_to_upload_dir(self):
        self.login_as_admin()
        payloads = [
            "../../etc/evil.tif",
            "%2e%2e%2fetc%2fevil.tif",
            "/etc/evil.tif",
            "static\\upload\\..\\..\\evil.tif",
        ]
        for raw_path in payloads:
            response = self.client.post(
                "/api/analysis/spectral_indices",
                json={
                    "list": [{"src": "x", "raw_tiff_path": raw_path}],
                    "index_type": "NDVI",
                },
            )
            body = self._json(response)
            self.assertFalse(body.get("success"), msg=raw_path)
            self.assertIn("文件不存在", body.get("msg", ""), msg=raw_path)

    def test_resolve_spectral_input_collapses_paths_to_basename(self):
        from applications.interface.analysis import _resolve_spectral_input

        input_path, img_name, display_url = _resolve_spectral_input(
            {"raw_tiff_path": "/etc/evil.tif"}, "data"
        )
        self.assertEqual(input_path, os.path.join("data", "evil.tif"))
        self.assertEqual(img_name, "evil.tif")

        encoded_path, encoded_name, _ = _resolve_spectral_input(
            {"raw_tiff_path": "..%2fevil.tif"}, "data"
        )
        self.assertEqual(encoded_path, os.path.join("data", "evil.tif"))

        backslash_path, backslash_name, _ = _resolve_spectral_input(
            {"raw_tiff_path": "static\\upload\\evil.tif"}, "data"
        )
        self.assertEqual(backslash_path, os.path.join("data", "evil.tif"))
        self.assertEqual(backslash_name, "evil.tif")

    def test_resolve_spectral_input_rejects_empty_basename(self):
        from applications.interface.analysis import _resolve_spectral_input

        with self.assertRaises(ValueError):
            _resolve_spectral_input({"raw_tiff_path": "../"}, "data")
        with self.assertRaises(ValueError):
            _resolve_spectral_input({"raw_tiff_path": "static/upload/"}, "data")
        with self.assertRaises(ValueError):
            _resolve_spectral_input({"raw_tiff_path": "a/b/%2e%2e"}, "data")

    def test_kml_path_outside_controlled_root_rejected(self):
        self.login_as_admin()
        malicious_paths = [
            "/etc/mine.kml",
            "../mine.kml",
            "sub/dir/mine.kml",
            "%2e%2e%2fmine.kml",
            "static\\mine.kml",
            "miner/../mine.geojson",
        ]
        for kml_path in malicious_paths:
            response = self.client.post(
                "/api/analysis/spectral_indices",
                json={
                    "list": [{"src": "x", "raw_tiff_path": "ok.tif"}],
                    "index_type": "NDVI",
                    "kml_path": kml_path,
                },
            )
            self.assertEqual(
                response.status_code,
                400,
                msg=f"kml_path 越界应返回 400: {kml_path}",
            )


# ---------------------------------------------------------------- Y1-3


class TestUploadWhitelistExcludesSvg(SecurityRecheckBase):
    def test_images_preset_has_no_svg(self):
        self.assertNotIn("svg", IMAGES)
        self.assertTrue({"png", "jpg", "jpeg", "gif", "bmp", "webp"}.issubset(set(IMAGES)))

    def test_svg_upload_rejected(self):
        self.login_as_admin()
        before = set(os.listdir(up_dir)) if os.path.isdir(up_dir) else set()
        response = self.client.post(
            "/api/file/upload",
            data={
                "files": (io.BytesIO(b"<svg><script>alert(1)</script></svg>"), "evil.svg"),
                "type": "地物分类",
            },
            content_type="multipart/form-data",
        )
        body = self._json(response)
        self.assertFalse(body.get("success"), msg="svg 上传应被白名单拒绝")

        after = set(os.listdir(up_dir)) if os.path.isdir(up_dir) else set()
        for extra in after - before:
            self.created_upload_files.append(os.path.join(up_dir, extra))


# ---------------------------------------------------------------- Y1-4


class TestGenericExceptionEcho(SecurityRecheckBase):
    SECRET = "RuntimeError secret /var/lib/mysql/physical-path"

    def test_project_detail_internal_error_not_echoed(self):
        self.login_as_admin()
        with patch(
            "applications.api.project.get_project_detail",
            side_effect=RuntimeError(self.SECRET),
        ):
            response = self.client.get("/api/projects/1")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))

        with patch(
            "applications.api.project.get_project_detail",
            side_effect=ValueError("项目不存在: 1"),
        ):
            response = self.client.get("/api/projects/1")
        body = self._json(response)
        self.assertEqual(body["msg"], "项目不存在: 1")

    def test_project_map_manifest_internal_error_not_echoed(self):
        self.login_as_admin()
        with patch(
            "applications.api.project.get_project_map_manifest",
            side_effect=RuntimeError(self.SECRET),
        ):
            response = self.client.get("/api/projects/1/map/manifest")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))

        with patch(
            "applications.api.project.get_project_map_manifest",
            side_effect=ValueError("项目不存在: 1"),
        ):
            response = self.client.get("/api/projects/1/map/manifest")
        self.assertEqual(response.status_code, 404)
        self.assertIn("项目不存在", self._json(response)["msg"])

    def test_project_export_list_internal_error_not_echoed(self):
        self.login_as_admin()
        with patch(
            "applications.api.project.list_exports",
            side_effect=RuntimeError(self.SECRET),
        ):
            response = self.client.get("/api/projects/1/exports")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))

        with patch(
            "applications.api.project.list_exports",
            side_effect=ValueError("项目不存在: 1"),
        ):
            response = self.client.get("/api/projects/1/exports")
        self.assertIn("项目不存在", self._json(response)["msg"])

    def test_semantic_segmentation_internal_error_not_echoed(self):
        self.login_as_admin()
        payload = {
            "list": ["a.tif"],
            "prehandle": 0,
            "denoise": 0,
        }
        with patch(
            "applications.api.analysis.terrain_classification",
            side_effect=RuntimeError(self.SECRET),
        ):
            response = self.client.post(
                "/api/analysis/semantic_segmentation",
                json=payload,
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))

        with patch(
            "applications.api.analysis.terrain_classification",
            side_effect=ValueError("仅支持多要素模型"),
        ):
            response = self.client.post(
                "/api/analysis/semantic_segmentation",
                json=payload,
            )
        self.assertIn("仅支持多要素模型", self._json(response)["msg"])

    def test_spectral_indices_internal_error_not_echoed(self):
        self.login_as_admin()
        payload = {
            "list": [{"src": "x", "raw_tiff_path": "a.tif"}],
            "index_type": "NDVI",
        }
        with patch(
            "applications.api.analysis.spectral_index_calculation",
            side_effect=RuntimeError(self.SECRET),
        ):
            response = self.client.post("/api/analysis/spectral_indices", json=payload)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))

        with patch(
            "applications.api.analysis.spectral_index_calculation",
            side_effect=ValueError("不支持的指数类型"),
        ):
            response = self.client.post("/api/analysis/spectral_indices", json=payload)
        self.assertIn("不支持的指数类型", self._json(response)["msg"])

    def test_file_upload_internal_error_not_echoed(self):
        self.login_as_admin()
        upload_module = "applications.api.file.upload_curd"
        with patch(upload_module) as mock_upload:
            mock_upload.upload_one.side_effect = RuntimeError(self.SECRET)
            response = self.client.post(
                "/api/file/upload",
                data={
                    "files": (io.BytesIO(b"fake"), "a.png"),
                    "type": "地物分类",
                },
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret", response.data.decode("utf-8"))


# ---------------------------------------------------------------- Y1-5


class _FakeCursor:
    def __init__(self, fail_markers):
        self.fail_markers = fail_markers
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        for marker in self.fail_markers:
            if marker in sql:
                raise RuntimeError(f"SQL 失败: {marker}")


class _FakeDb:
    def __init__(self, fail_markers=()):
        self._cursor = _FakeCursor(fail_markers)
        self.rollback_count = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        self.rollback_count += 1

    def close(self):
        pass


class TestInitDbFailureReporting(unittest.TestCase):
    def setUp(self):
        # 注意：scripts 包把函数 init_db 重绑定到了包命名空间，
        # 普通 from-import 会拿到函数，必须用 import_module 拿模块对象。
        import importlib

        self.init_db_module = importlib.import_module(
            "applications.common.scripts.init_db"
        )
        self.tmp = tempfile.mkdtemp()
        self.sql_path = os.path.join(self.tmp, "init_fixture.sql")

    def tearDown(self):
        if os.path.exists(self.tmp):
            shutil.rmtree(self.tmp)

    def _write_sql(self, text):
        with open(self.sql_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _patch_connect(self, fake_db):
        return patch.object(self.init_db_module.pymysql, "connect", return_value=fake_db)

    def test_execute_fromfile_reports_each_failure_and_raises(self):
        import contextlib
        import io as _io

        from applications.common.scripts.init_db import execute_fromfile

        self._write_sql(
            "CREATE TABLE ok1 (id INT);;\nCREATE TABLE bad (id INT);CREATE TABLE ok2 (id INT);"
        )
        fake_db = _FakeDb(fail_markers=("bad",))
        captured = _io.StringIO()
        with self._patch_connect(fake_db), contextlib.redirect_stdout(captured):
            with self.assertRaises(RuntimeError):
                execute_fromfile(self.sql_path)

        output = captured.getvalue()
        self.assertIn("bad", output)
        # 空语句被跳过，其余语句仍逐条执行
        executed = [sql for sql in fake_db._cursor.executed if sql]
        self.assertEqual(len(executed), 3)
        self.assertEqual(fake_db.rollback_count, 1)

    def test_execute_fromfile_without_failure_prints_success(self):
        import contextlib
        import io as _io

        init_db_module = self.init_db_module

        self._write_sql("CREATE TABLE ok1 (id INT);CREATE TABLE ok2 (id INT);")
        fake_db = _FakeDb()
        captured = _io.StringIO()
        with self._patch_connect(fake_db), contextlib.redirect_stdout(captured):
            with patch.object(init_db_module, "is_exist_database", return_value=((0,),)):
                with patch.object(init_db_module, "init_database", return_value=1):
                    # execute_fromfile 固定读 backend/init_db.sql，用 fixture 文件替换
                    with patch("builtins.open", _fake_open(self.sql_path)):
                        init_db_module.init_db()

        output = captured.getvalue()
        self.assertIn("表创建成功", output)
        self.assertNotIn("SQL 执行失败", output)

    def test_init_db_does_not_print_success_when_sql_fails(self):
        import contextlib
        import io as _io

        init_db_module = self.init_db_module

        self._write_sql("CREATE TABLE bad (id INT);")
        fake_db = _FakeDb(fail_markers=("bad",))
        captured = _io.StringIO()
        with self._patch_connect(fake_db), contextlib.redirect_stdout(captured):
            with patch.object(init_db_module, "is_exist_database", return_value=((0,),)):
                with patch.object(init_db_module, "init_database", return_value=1):
                    with patch("builtins.open", _fake_open(self.sql_path)):
                        with self.assertRaises(RuntimeError):
                            init_db_module.init_db()

        self.assertNotIn("表创建成功", captured.getvalue())
        self.assertIn("SQL 执行失败", captured.getvalue())


def _fake_open(real_path):
    real_open = open

    def _open(file, *args, **kwargs):
        if str(file).endswith("init_db.sql"):
            return real_open(real_path, *args, **kwargs)
        return real_open(file, *args, **kwargs)

    return _open


# ---------------------------------------------------------------- Y1-6


class TestPaginationAndJsonTolerance(SecurityRecheckBase):
    def test_kml_roi_history_non_numeric_page_defaults_to_one(self):
        self.login_as_admin()
        response = self.client.get("/api/analysis/kml_roi_history?page=abc&limit=xyz")
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertTrue(body.get("success"))

    def test_show_result_non_numeric_page_does_not_crash(self):
        self.login_as_admin()

        class _Pagination:
            total = 0
            items = []

        fake_query = MagicMock()
        fake_query.filter_by.return_value.order_by.return_value.paginate.return_value = (
            _Pagination()
        )
        with patch("applications.api.analysis.Analysis") as analysis_cls:
            analysis_cls.query = fake_query
            with patch("applications.api.analysis.desc"):
                with patch("applications.api.analysis.model_to_dicts", return_value=[]):
                    with patch("applications.api.analysis.items_handle", return_value=[]):
                        response = self.client.get(
                            "/api/analysis/show/type_map?page=abc&limit=xyz"
                        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._json(response).get("success"))
        fake_query.filter_by.return_value.order_by.return_value.paginate.assert_called_once_with(
            page=1, per_page=10, error_out=False
        )

    def test_history_batch_remove_with_null_body_returns_param_error(self):
        self.login_as_admin()
        response = self.client.delete(
            "/api/history/batchRemove",
            data="null",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("msg"), "参数异常")


if __name__ == "__main__":
    unittest.main()
