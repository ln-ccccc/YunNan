# -*- coding: utf-8 -*-
"""问题14（35项审查）第一阶段：回收站运维恢复接口回归。

旧实现（未提交初版 undelete）两个缺陷：
1. 无目录纯元数据项目删除后不可恢复（无 trash 目录即拒绝）；
2. 按 trash/{id}_* glob 排序取最新——未持久化准确回收位置，
   同项目多次删除/残留目录时可能恢复错对象。

本文件契约：
1. 有目录项目：删除→undelete 恢复，目录内容完整，状态回到 archived；
2. 纯元数据项目（删除时无目录）可恢复；
3. 恢复定位来自 project_deleted 活动持久化的 trash_target——trash 中存在
   字典序更晚的干扰目录时仍恢复正确对象（glob 猜测的红用例）；
4. 未删除项目 409、不存在 404、目录已存在 409、未登录 401；
5. DB 提交失败回滚：目录移回 trash 原位，项目保持删除态；
6. 恢复写入 project_undeleted 审计事件（只追加）。
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog
from test_project_api import TestProjectAPI


class UndeleteBase(TestProjectAPI):
    def _archive(self, project_id):
        response = self.client.post(f"/api/projects/{project_id}/archive")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

    def _delete(self, project_id):
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["data"]

    def _undelete(self, project_id):
        return self.client.post(f"/api/projects/{project_id}/undelete")

    def _make_project_dir(self, project_id, sentinel="data.txt"):
        root = self.storage_root / "projects" / str(project_id)
        root.mkdir(parents=True, exist_ok=True)
        (root / sentinel).write_text("project-data")
        return root

    def _deleted_event(self, project_id):
        row = (
            ProjectActivityLog.query.filter_by(
                project_id=project_id, event_type="project_deleted"
            )
            .order_by(ProjectActivityLog.create_time.desc(), ProjectActivityLog.id.desc())
            .first()
        )
        assert row is not None, "缺少 project_deleted 审计事件"
        return json.loads(row.payload_json or "{}")


class TestUndeleteCore(UndeleteBase):
    def test_undelete_restores_directory_and_archived_state(self):
        self.login_as_admin()
        project_id = self._create_project("恢复主路径")
        project_root = self._make_project_dir(project_id)
        self._archive(project_id)
        self._delete(project_id)
        self.assertFalse(project_root.exists())

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()["data"]
        self.assertTrue(body["restored"])
        self.assertTrue(project_root.exists())
        self.assertEqual((project_root / "data.txt").read_text(), "project-data")
        project = db.session.get(Project, project_id)
        self.assertIsNone(project.deleted_at)
        self.assertEqual(project.status, "archived", "undelete 只回 archived，不越权激活")
        # 审计追加 project_undeleted
        events = [
            row.event_type
            for row in ProjectActivityLog.query.filter_by(project_id=project_id).all()
        ]
        self.assertIn("project_undeleted", events)
        self.assertIn("project_deleted", events)
        # trash 已清空该项目
        self.assertFalse(list((self.storage_root / "trash").glob(f"{project_id}_*")))

    def test_undelete_metadata_only_project_without_directory(self):
        """纯元数据项目（删除时就没有目录）必须可恢复——旧实现直接拒绝。"""
        self.login_as_admin()
        project_id = self._create_project("无目录恢复")
        self._archive(project_id)
        delete_body = self._delete(project_id)
        self.assertIsNone(delete_body["trash_target"])

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()["data"]["restored"])
        self.assertIsNone(db.session.get(Project, project_id).deleted_at)

    def test_undelete_uses_persisted_trash_target_not_glob_guess(self):
        """trash 中存在字典序更晚的干扰目录（同 id 前缀），仍恢复删除事件
        持久化指向的那个——glob 排序猜测在此场景会拿错目录。"""
        self.login_as_admin()
        project_id = self._create_project("持久化定位")
        project_root = self._make_project_dir(project_id, sentinel="real.txt")
        self._archive(project_id)
        self._delete(project_id)
        event = self._deleted_event(project_id)
        persisted = event.get("trash_target")
        self.assertIsNotNone(persisted)

        # 伪造字典序更晚的同前缀干扰目录（内容是假数据）
        trash_root = self.storage_root / "trash"
        decoy = trash_root / f"{project_id}_99991231_235959_zzzzzzzz"
        decoy.mkdir(parents=True)
        (decoy / "fake.txt").write_text("decoy")

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(project_root.exists())
        self.assertEqual((project_root / "real.txt").read_text(), "project-data")
        self.assertFalse((project_root / "fake.txt").exists(), "不得恢复到干扰目录内容")
        self.assertTrue(decoy.exists(), "干扰目录保持不动")
        self.assertFalse(
            (trash_root / persisted).exists(), "持久化指向的目录已被移回"
        )

    def test_undelete_falls_back_to_glob_when_delete_event_missing(self):
        """存量已删项目（活动缺失）回退 glob 取最新——兼容历史数据。"""
        self.login_as_admin()
        project_id = self._create_project("存量回退")
        self._make_project_dir(project_id, sentinel="legacy.txt")
        self._archive(project_id)
        self._delete(project_id)
        ProjectActivityLog.query.filter_by(
            project_id=project_id, event_type="project_deleted"
        ).delete()
        db.session.commit()

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue((self.storage_root / "projects" / str(project_id) / "legacy.txt").is_file())

    def test_undelete_rejects_missing_trash_directory_when_event_says_it_existed(self):
        """删除事件持久化了 trash_target 但目录已被人工移走：明确报错，不猜。"""
        self.login_as_admin()
        project_id = self._create_project("目录丢失")
        self._make_project_dir(project_id)
        self._archive(project_id)
        self._delete(project_id)
        for leftover in (self.storage_root / "trash").glob(f"{project_id}_*"):
            import shutil as _shutil

            _shutil.rmtree(leftover)

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("回收站", response.get_json()["msg"])
        self.assertIsNotNone(db.session.get(Project, project_id).deleted_at)


class TestUndeleteGuards(UndeleteBase):
    def test_undelete_rejects_not_deleted_project(self):
        self.login_as_admin()
        project_id = self._create_project("未删除")
        self._archive(project_id)

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))

    def test_undelete_rejects_unknown_project(self):
        self.login_as_admin()
        response = self._undelete(99999)
        self.assertEqual(response.status_code, 404, response.get_data(as_text=True))

    def test_undelete_rejects_when_project_directory_already_exists(self):
        self.login_as_admin()
        project_id = self._create_project("目录冲突")
        project_root = self._make_project_dir(project_id)
        self._archive(project_id)
        self._delete(project_id)
        # 人工残留：projects/<id>/ 又出现了目录
        project_root.mkdir(parents=True)

        response = self._undelete(project_id)

        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        self.assertIn("已存在", response.get_json()["msg"])

    def test_undelete_requires_login(self):
        self.login_as_admin()
        project_id = self._create_project("鉴权")
        self._archive(project_id)
        self._delete(project_id)
        fresh = self.app.test_client()
        response = fresh.post(f"/api/projects/{project_id}/undelete")
        self.assertEqual(response.status_code, 401)

    def test_undelete_rolls_back_directory_on_commit_failure(self):
        self.login_as_admin()
        project_id = self._create_project("提交失败回滚")
        self._make_project_dir(project_id, sentinel="rollback.txt")
        self._archive(project_id)
        self._delete(project_id)
        event = self._deleted_event(project_id)
        persisted = self.storage_root / "trash" / event["trash_target"]

        from applications.project_hub import service as project_service

        with patch.object(
            project_service.db.session,
            "commit",
            side_effect=RuntimeError("模拟提交失败"),
        ):
            response = self._undelete(project_id)

        self.assertEqual(response.status_code, 500, response.get_data(as_text=True))
        # 目录回到 trash 持久化原位，项目保持删除态
        self.assertTrue(persisted.exists(), "目录须移回 trash 原位")
        self.assertTrue((persisted / "rollback.txt").is_file())
        self.assertFalse((self.storage_root / "projects" / str(project_id)).exists())
        self.assertIsNotNone(db.session.get(Project, project_id).deleted_at)


if __name__ == "__main__":
    unittest.main()
