# -*- coding: utf-8 -*-
"""地物分类原始影像溯源（Miner 矿山详情"原始影像"标签页）的后端契约测试。

覆盖：按矿山过滤/去重/存在性标注、物理路径不出现在 DTO、下载的包含性校验、
跨项目任务隔离、鉴权。
"""
import json
import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.inference_job import InferenceJob
from applications.models.project import ProjectMineBinding


def _make_job(project_id, payload, status="succeeded", created=None):
    job = InferenceJob(
        id=str(uuid.uuid4()),
        project_id=project_id,
        status=status,
        requested_device="auto",
        request_payload_json=json.dumps(payload, ensure_ascii=False),
        create_time=created or datetime(2026, 9, 22, 10, 0, 0),
        update_time=created or datetime(2026, 9, 22, 10, 5, 0),
    )
    db.session.add(job)
    return job


class TestProjectOriginalImagery(unittest.TestCase):
    def setUp(self):
        # 独立基类（不继承 test_project_api 的用例，只复刻其存储根/登录模式）
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="original-imagery-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.addCleanup(self._restore_storage_root)
        self.app = create_app("testing")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()
        self.login_as_admin()
        self._prepare_default_project()

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    def _json(self, response):
        return json.loads(response.data.decode("utf-8"))

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
        return response

    def _create_project(self, name="回归测试项目"):
        response = self.client.post(
            "/api/projects",
            json={
                "name": name,
                "region": "昆明市",
                "manager": "测试管理员",
                "monitor_start_year": 2024,
                "monitor_end_year": 2025,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        self.assertEqual(body["code"], 0)
        return body["data"]["id"]

    def _prepare_default_project(self):
        """默认项目 + 矿山绑定 + 一份 2024 输入影像（各用例复用）。"""
        self.project_id = self._create_project("溯源测试项目")
        db.session.add(ProjectMineBinding(project_id=self.project_id, mine_fid=713))
        db.session.commit()
        self.input_dir = self.storage_root / "projects" / str(self.project_id) / "inputs" / "interpretation" / "2024"
        self.input_dir.mkdir(parents=True)
        self.tif_path = self.input_dir / "a1b2c3d4-e5f6-7890-abcd-ef0123456789.tif"
        self.tif_path.write_bytes(b"II*\x00" + os.urandom(4096))

    def _list(self, fid="713", project_id=None):
        return self.client.get(
            f"/api/projects/{project_id or self.project_id}/mines/original-imagery",
            query_string={"fid": fid},
        )

    def test_lists_inputs_for_matching_mine_with_metadata_and_no_paths(self):
        _make_job(self.project_id, {
            "new_tif_path": str(self.tif_path),
            "old_tif_path": str(self.tif_path),
            "year": "2024",
            "mine_fids": [713, 714],
        })
        db.session.commit()

        body = self._json(self._list())
        self.assertEqual(body["code"], 0)
        items = body["data"]["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["year"], 2024)
        self.assertEqual(item["filename"], self.tif_path.name)
        self.assertEqual(item["size_bytes"], self.tif_path.stat().st_size)
        self.assertTrue(item["file_exists"])
        self.assertEqual(item["job_status"], "succeeded")
        # 物理路径不得出现在响应里（含 storage root 前缀的任何形式）
        self.assertNotIn("path", item)
        self.assertNotIn(str(self.storage_root), json.dumps(body))

    def test_dedupes_same_input_and_year_and_skips_other_mines(self):
        payload = {
            "new_tif_path": str(self.tif_path),
            "year": "2024",
            "mine_fids": [713],
        }
        _make_job(self.project_id, payload)
        _make_job(self.project_id, payload, status="failed", created=datetime(2026, 9, 21, 9, 0, 0))
        # 其他矿山的任务不入列
        _make_job(self.project_id, {
            "new_tif_path": str(self.tif_path),
            "year": "2023",
            "mine_fids": [999],
        })
        db.session.commit()

        items = self._json(self._list())["data"]["items"]
        self.assertEqual(len(items), 1, "同输入同年份的任务应去重")

    def test_missing_file_is_flagged_not_hidden(self):
        missing = self.input_dir / "missing-input.tif"
        _make_job(self.project_id, {
            "new_tif_path": str(missing),
            "year": "2024",
            "mine_fids": [713],
        })
        db.session.commit()

        items = self._json(self._list())["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0]["file_exists"])
        self.assertIsNone(items[0]["size_bytes"])

    def test_path_outside_storage_root_is_skipped(self):
        _make_job(self.project_id, {
            "new_tif_path": r"C:\Windows\system32\evil.tif",
            "year": "2024",
            "mine_fids": [713],
        })
        db.session.commit()
        items = self._json(self._list())["data"]["items"]
        self.assertEqual(items, [])

    def test_fid_must_belong_to_project(self):
        resp = self._list(fid="8888")
        self.assertEqual(resp.status_code, 404)
        resp = self._list(fid="abc")
        self.assertEqual(resp.status_code, 404)

    def test_download_streams_recorded_input_within_project_root(self):
        job = _make_job(self.project_id, {
            "new_tif_path": str(self.tif_path),
            "year": "2024",
            "mine_fids": [713],
        })
        db.session.commit()

        resp = self.client.get(
            f"/api/projects/{self.project_id}/mines/original-imagery/{job.id}/download"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, self.tif_path.read_bytes())
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertIn(self.tif_path.name, resp.headers.get("Content-Disposition", ""))

    def test_download_rejects_unknown_job_and_outside_root_paths(self):
        unknown = str(uuid.uuid4())
        resp = self.client.get(
            f"/api/projects/{self.project_id}/mines/original-imagery/{unknown}/download"
        )
        self.assertEqual(resp.status_code, 404)

        evil = _make_job(self.project_id, {
            "new_tif_path": r"C:\Windows\system32\evil.tif",
            "year": "2024",
            "mine_fids": [713],
        })
        db.session.commit()
        resp = self.client.get(
            f"/api/projects/{self.project_id}/mines/original-imagery/{evil.id}/download"
        )
        self.assertEqual(resp.status_code, 404)

    def test_job_of_another_project_is_isolated(self):
        other_project = self._create_project("隔壁项目")
        db.session.add(ProjectMineBinding(project_id=other_project, mine_fid=713))
        job = _make_job(other_project, {
            "new_tif_path": str(self.tif_path),
            "year": "2024",
            "mine_fids": [713],
        })
        db.session.commit()
        resp = self.client.get(
            f"/api/projects/{self.project_id}/mines/original-imagery/{job.id}/download"
        )
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.get(
            f"/api/projects/{self.project_id}/mines/original-imagery",
            query_string={"fid": "713"},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
