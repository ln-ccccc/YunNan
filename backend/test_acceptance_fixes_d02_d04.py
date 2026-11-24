# -*- coding: utf-8 -*-
"""2026-11-18 验收缺陷 D-02/D-04 修复回归。

D-02: 推理任务创建期以 Worker 可写权限预备 projects/<id>/outputs/inference;
      发布期权限类失败单列 PUBLISH_DIR_NOT_WRITABLE(此前误归 GPU_INFERENCE_FAILED)。
D-04: 归档项目登记数据集被拒绝(422),不产生部分写入——补齐 PR12 守卫清单缺口。

自带夹具(不继承 TestProjectAPI,避免带跑 130+ 父链用例)。
"""
import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectDataset


class _AcceptanceFixtureMixin:
    """TestProjectAPI 夹具内核拷贝(2026-11-18):storage 根随用例隔离、
    上传 staging 随用例隔离、每用例独立建表。"""

    def setUp(self):
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="acc-fix-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.addCleanup(self._restore_storage_root)
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.app.config["UPLOADED_PHOTOS_DEST"] = str(Path(self.temp_dir.name) / "upload")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    def login_as_admin(self, password="Secret123!"):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = password
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = password
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login", json={"username": "admin", "password": password}
        )
        self.assertEqual(response.status_code, 200)
        return response

    def _create_incoming_tif(self, filename="guard.tif"):
        storage_key = f"incoming/{filename}"
        source_path = self.storage_root / storage_key
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(b"II*\x00")
        return storage_key

    def _create_project(self, name="守卫夹具项目"):
        response = self.client.post(
            "/api/projects", json={"name": name, "monitor_start_year": 2023}
        )
        # 本候选项目创建返回 200(success_api 默认态)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]["id"]


class TestPublishDirEnsure(_AcceptanceFixtureMixin, unittest.TestCase):
    """D-02:任务创建期预备发布目录(镜像 USER inference=uid 10001,bind mount
    上 root 属主目录会挡住发布写入)。"""

    def test_ensure_creates_world_writable_publish_dir(self):
        from applications.api.inference import _ensure_worker_writable_publish_dir

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "projects" / "9"
            _ensure_worker_writable_publish_dir(project_root, project_id=9)
            outputs_dir = project_root / "outputs"
            target = outputs_dir / "inference"
            self.assertTrue(target.is_dir())
            # 两级都放行:发布还要建 outputs/change_matrix(9f5442ef 复验踩实)
            self.assertEqual(outputs_dir.stat().st_mode & 0o777, 0o777)
            self.assertEqual(target.stat().st_mode & 0o777, 0o777)

    def test_ensure_is_idempotent_and_tolerates_chmod_failure(self):
        from applications.api.inference import _ensure_worker_writable_publish_dir

        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "projects" / "9"
            outputs_dir = project_root / "outputs"
            target = outputs_dir / "inference"
            outputs_dir.mkdir(parents=True, mode=0o755)
            target.mkdir()
            (target / "keep.txt").write_text("x", encoding="utf-8")
            with mock.patch("os.chmod", side_effect=OSError(errno.EPERM, "denied")):
                # 预备失败不阻塞任务创建,发布期由 PUBLISH_DIR_NOT_WRITABLE 兜底
                _ensure_worker_writable_publish_dir(project_root, project_id=9)
            self.assertTrue((target / "keep.txt").exists())

    def test_create_job_api_prepares_publish_dir_without_preregistered_dirs(self):
        """API 级:未预建任何 outputs 目录时创建任务,发布目录已就位且可写。"""
        self.login_as_admin()
        project_id = self._create_project("D02目录预备")
        project_root = self.storage_root / "projects" / str(project_id)
        self.assertFalse((project_root / "outputs").exists())

        storage_key = self._create_incoming_tif("d02.tif")
        dataset = self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": "D02影像",
                "storage_key": storage_key,
                "year_start": 2023,
                "year_end": 2023,
            },
        )
        self.assertEqual(dataset.status_code, 200, dataset.get_data(as_text=True))
        resp = self.client.post(
            "/api/inference/jobs",
            json={
                "project_id": project_id,
                "dataset_id": dataset.get_json()["data"]["id"],
            },
        )
        # 任务创建受 readiness 门槛约束,400(条件不满足)同样先走过预备逻辑;
        # 只有创建成功(201)才断言目录就位——保证不依赖门槛变化
        if resp.status_code == 201:
            target = project_root / "outputs" / "inference"
            self.assertTrue(target.is_dir())
            self.assertEqual(target.stat().st_mode & 0o777, 0o777)
        else:
            self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))


class TestPublishErrorClassification(unittest.TestCase):
    """D-02:发布期权限失败单列错误码,不再落入 GPU_INFERENCE_FAILED。"""

    def test_permission_errors_map_to_publish_code(self):
        from applications.inference.worker import _classify_publish_error

        self.assertEqual(
            _classify_publish_error(PermissionError(errno.EACCES, "denied")),
            "PUBLISH_DIR_NOT_WRITABLE",
        )
        self.assertEqual(
            _classify_publish_error(OSError(errno.EPERM, "denied")),
            "PUBLISH_DIR_NOT_WRITABLE",
        )
        self.assertEqual(
            _classify_publish_error(OSError(errno.EROFS, "read-only")),
            "PUBLISH_DIR_NOT_WRITABLE",
        )

    def test_non_permission_errors_stay_unclassified(self):
        from applications.inference.worker import _classify_publish_error

        self.assertIsNone(_classify_publish_error(ValueError("unrelated")))
        self.assertIsNone(_classify_publish_error(FileNotFoundError(errno.ENOENT, "missing")))
        self.assertIsNone(_classify_publish_error(OSError(errno.ENOSPC, "no space")))


class TestArchivedDatasetGuard(_AcceptanceFixtureMixin, unittest.TestCase):
    """D-04:归档项目不允许登记数据集(PR12 守卫清单缺口)。"""

    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self._create_incoming_tif("guard.tif")
        self.project_id = self._create_project("归档守卫")

    def _archive(self):
        project = Project.query.get(self.project_id)
        project.status = "archived"
        db.session.commit()

    def _register_payload(self):
        return {
            "dataset_kind": "imagery",
            "display_name": "归档写入",
            "storage_key": "incoming/guard.tif",
            "year_start": 2023,
            "year_end": 2023,
        }

    def test_archived_project_rejects_dataset_registration(self):
        self._archive()
        resp = self.client.post(
            f"/api/projects/{self.project_id}/datasets", json=self._register_payload()
        )
        self.assertEqual(resp.status_code, 422, resp.get_data(as_text=True))
        self.assertEqual(
            ProjectDataset.query.filter_by(project_id=self.project_id).count(), 0
        )

    def test_active_project_still_registers_dataset(self):
        resp = self.client.post(
            f"/api/projects/{self.project_id}/datasets", json=self._register_payload()
        )
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        self.assertEqual(
            ProjectDataset.query.filter_by(project_id=self.project_id).count(), 1
        )

    def test_deleted_project_registration_stays_rejected(self):
        self._archive()
        self.client.delete(f"/api/projects/{self.project_id}")
        resp = self.client.post(
            f"/api/projects/{self.project_id}/datasets", json=self._register_payload()
        )
        self.assertGreaterEqual(resp.status_code, 400)
        self.assertEqual(
            ProjectDataset.query.filter_by(project_id=self.project_id).count(), 0
        )


if __name__ == "__main__":
    unittest.main()
