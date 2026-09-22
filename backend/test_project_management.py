# -*- coding: utf-8 -*-
"""M2 项目管理增强契约测试：删除（仅归档可删+目录入 trash）、批量归档/删除、
快照跨环境导入、鉴权与上限。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectMineBinding
from applications.project_hub.service import create_backup, ProjectDeleteNotAllowed, delete_project
from test_project_api import TestProjectAPI


class ProjectManageBase(TestProjectAPI):
    def _archive(self, project_id):
        response = self.client.post(f"/api/projects/{project_id}/archive")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def _make_project_dir(self, project_id, sentinel="marker.txt"):
        root = self.storage_root / "projects" / str(project_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / sentinel).write_text("data")
        return root


class TestProjectDelete(ProjectManageBase):
    def test_active_project_cannot_be_deleted(self):
        self.login_as_admin()
        project_id = self._create_project("活跃项目不可删")
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 409)
        self.assertIn("归档", response.get_json()["msg"])
        # 项目仍在列表
        body = self.client.get("/api/projects").get_json()
        self.assertTrue(any(i["id"] == project_id for i in body["data"]["items"]))

    def test_archived_project_deletes_soft_and_moves_storage_to_trash(self):
        self.login_as_admin()
        project_id = self._create_project("可删项目")
        project_dir = self._make_project_dir(project_id)
        self._archive(project_id)

        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()["data"]
        self.assertTrue(body["deleted"])
        self.assertIsNotNone(body["trash_target"])
        # 目录已移入 trash 且数据保留（人工可恢复）
        self.assertFalse(project_dir.exists())
        trash_root = self.storage_root / "trash"
        moved = list(trash_root.glob(f"{project_id}_*"))
        self.assertEqual(len(moved), 1)
        self.assertTrue((moved[0] / "marker.txt").is_file())
        # DB 软删：列表不再可见，记录仍在
        listing = self.client.get("/api/projects").get_json()["data"]["items"]
        self.assertFalse(any(i["id"] == project_id for i in listing))
        self.assertIsNotNone(db.session.get(Project, project_id).deleted_at)

    def test_archived_project_without_storage_dir_deletes(self):
        self.login_as_admin()
        project_id = self._create_project("无目录项目")
        self._archive(project_id)
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["data"]["trash_target"])

    def test_delete_requires_login(self):
        self.login_as_admin()
        project_id = self._create_project("鉴权项目")
        self._archive(project_id)
        fresh = self.app.test_client()
        response = fresh.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 401)


class TestProjectBatch(ProjectManageBase):
    def test_batch_archive_reports_per_item_results(self):
        self.login_as_admin()
        a = self._create_project("批归A")
        b = self._create_project("批归B")
        response = self.client.post(
            "/api/projects/batch/archive",
            json={"project_ids": [a, b, 99999]},
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"]
        self.assertEqual(data["succeeded"], 2)
        self.assertEqual(data["failed"], 1)
        by_id = {r["project_id"]: r for r in data["results"]}
        self.assertTrue(by_id[a]["ok"])
        self.assertFalse(by_id[99999]["ok"])

    def test_batch_delete_mixes_archive_gate_and_success(self):
        self.login_as_admin()
        active = self._create_project("活动勿删")
        archived = self._create_project("已归档可删")
        self._make_project_dir(archived)
        self._archive(archived)
        response = self.client.post(
            "/api/projects/batch/delete",
            json={"project_ids": [active, archived, "not-int"]},
        )
        data = response.get_json()["data"]
        by_id = {r["project_id"]: r for r in data["results"]}
        self.assertFalse(by_id[active]["ok"])
        self.assertIn("归档", by_id[active]["msg"])
        self.assertTrue(by_id[archived]["ok"])
        self.assertFalse(by_id["not-int"]["ok"])
        self.assertEqual(data["succeeded"], 1)

    def test_batch_rejects_invalid_payloads(self):
        self.login_as_admin()
        for payload in (None, {}, {"project_ids": []}, {"project_ids": "1,2"}, {"project_ids": [1] * 101}):
            response = self.client.post("/api/projects/batch/archive", json=payload)
            self.assertEqual(response.status_code, 422, payload)


class TestBackupImport(ProjectManageBase):
    def _build_manifest(self, source_project_id, name="迁移项目", dataset_path_prefix=None):
        prefix = dataset_path_prefix or f"projects/{source_project_id}/inputs/imagery"
        return {
            "snapshot_version": 1,
            "project_id": source_project_id,
            "backup_id": 7,
            "summary": {
                "name": name, "region": "大理", "manager": "张三", "remark": "迁移",
                "status": "active", "monitor_start_year": 2023, "monitor_end_year": 2025,
            },
            "mines": [
                {"mine_fid": 101, "mine_name_snapshot": "矿山一", "sort_order": 0},
                {"mine_fid": 102, "sort_order": 1},
            ],
            "datasets": [
                # file_path 必须是本项目根内的路径（收官审查 S1 收口后非法路径整批 422）
                {"dataset_kind": "imagery", "display_name": "影像A",
                 "file_path": f"{prefix}/a.tif"},
            ],
            "exports": [{"format": "geojson", "file_path": "out.geojson"}],
            "activities": [{"event_type": "project_created", "created_at": "2026-01-01T00:00:00"}],
        }

    def test_import_manifest_applies_summary_and_rebuilds_bindings(self):
        self.login_as_admin()
        target = self._create_project("导入目标")
        db.session.add(ProjectMineBinding(project_id=target, mine_fid=555))
        db.session.commit()

        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": self._build_manifest(
                999, name="迁移后的项目",
                dataset_path_prefix=f"projects/{target}/inputs/imagery",
            )},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        project = db.session.get(Project, target)
        self.assertEqual(project.name, "迁移后的项目")
        self.assertEqual(project.region, "大理")
        fids = sorted(b.mine_fid for b in project.mines)
        self.assertEqual(fids, [101, 102])
        kinds = [d.dataset_kind for d in project.datasets]
        self.assertIn("imagery", kinds)
        # 导入来源 manifest 已存档
        imports_dir = self.storage_root / "projects" / str(target) / "imports"
        self.assertTrue(list(imports_dir.glob("manifest_*.json")))
        # 活动只追加一条导入事件；源 manifest 里的活动不重放
        # （project_created 计数保持建项目时的 1，不因导入 +1）
        events = [a.event_type for a in project.activities]
        self.assertIn("project_imported", events)
        self.assertEqual(events.count("project_created"), 1)

    def test_import_rejects_dataset_path_outside_project_root(self):
        """收官审查 S1：manifest 直传任意路径曾被原样入库，间接读取服务器任意栅格。"""
        self.login_as_admin()
        target = self._create_project("越界导入目标")
        manifest = self._build_manifest(999, name="越界尝试")
        manifest["datasets"] = [
            {"dataset_kind": "imagery", "display_name": "evil", "file_path": "/etc/passwd"},
        ]
        response = self.client.post(
            f"/api/projects/{target}/backups/import",
            json={"manifest": manifest},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("不在本项目存储内", response.get_json()["msg"])

    def test_import_rejects_malformed_manifest(self):
        self.login_as_admin()
        target = self._create_project("坏清单目标")
        for bad in (
            {},
            {"snapshot_version": 2, "summary": {}, "mines": [], "datasets": [], "exports": [], "activities": []},
            {"snapshot_version": 1, "summary": {}, "mines": "not-list", "datasets": [], "exports": [], "activities": []},
            {"snapshot_version": 1, "summary": {}, "mines": [{"no_fid": 1}], "datasets": [], "exports": [], "activities": []},
        ):
            response = self.client.post(
                f"/api/projects/{target}/backups/import",
                json={"manifest": bad},
            )
            self.assertEqual(response.status_code, 422, bad)

    def test_import_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.post(
            "/api/projects/1/backups/import",
            json={"manifest": {"snapshot_version": 1}},
        )
        self.assertEqual(response.status_code, 401)


class TestDeleteServiceDirect(ProjectManageBase):
    """服务层直接调用：ProjectDeleteNotAllowed 异常类型契约。"""

    def test_service_raises_typed_error_for_active_project(self):
        self.login_as_admin()
        project_id = self._create_project("服务层类型")
        with self.assertRaises(ProjectDeleteNotAllowed):
            delete_project(project_id)


if __name__ == "__main__":
    unittest.main()
