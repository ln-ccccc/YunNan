# -*- coding: utf-8 -*-
"""数据集登记去重（2026-11-14 审查问题19）。

身份 = (项目, dataset_kind, 规范化 storage_key)。同一文件重复登记
（双击/双标签页/断线重试）必须复用同一记录：两次响应指向同一 ID，
库里只有一行。serial 去重在 SQLite 即可确定性地验证；并发窗口
（两事务同时通过"先查后插"）由项目行锁串行化，MySQL 隔离副本另行验证。
"""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import ProjectDataset

from test_project_api import TestProjectAPI


class TestDatasetRegistrationDedup(TestProjectAPI):
    def _register(self, project_id, storage_key, display_name):
        return self.client.post(
            f"/api/projects/{project_id}/datasets",
            json={
                "dataset_kind": "imagery",
                "display_name": display_name,
                "storage_key": storage_key,
            },
        )

    def test_duplicate_registration_returns_same_dataset(self):
        self.login_as_admin()
        project_id = self._create_project("登记去重项目")
        storage_key = self._create_incoming_tif("dedup.tif")

        first = self._register(project_id, storage_key, "影像一次")
        self.assertEqual(first.status_code, 200)
        first_id = self._json(first)["data"]["id"]

        # 双击/重试：同名同文件再登记 → 复用同一记录
        second = self._register(project_id, storage_key, "影像一次")
        self.assertEqual(second.status_code, 200)
        second_id = self._json(second)["data"]["id"]
        self.assertEqual(second_id, first_id)
        self.assertEqual(
            ProjectDataset.query.filter_by(project_id=project_id).count(), 1
        )

    def test_duplicate_registration_different_display_name_still_deduped(self):
        # 身份是路径不是名称：换 display_name 的重复登记仍指向同一文件
        self.login_as_admin()
        project_id = self._create_project("登记去重名称项目")
        storage_key = self._create_incoming_tif("dedup-name.tif")

        first = self._register(project_id, storage_key, "名称A")
        second = self._register(project_id, storage_key, "名称B")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            self._json(second)["data"]["id"], self._json(first)["data"]["id"]
        )
        self.assertEqual(
            ProjectDataset.query.filter_by(project_id=project_id).count(), 1
        )

    def test_same_file_in_different_projects_registers_independently(self):
        # 去重按项目隔离：同一 incoming 文件可被不同项目分别登记
        self.login_as_admin()
        first_project = self._create_project("登记去重项目一")
        second_project = self._create_project("登记去重项目二")
        storage_key = self._create_incoming_tif("cross-project.tif")

        first = self._register(first_project, storage_key, "项目一影像")
        second = self._register(second_project, storage_key, "项目二影像")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(
            self._json(second)["data"]["id"], self._json(first)["data"]["id"]
        )
        self.assertEqual(ProjectDataset.query.count(), 2)

    def test_duplicate_registration_does_not_flip_draft_twice(self):
        # 重复登记走幂等分支：不重复写 dataset_created 活动之外的状态翻转
        self.login_as_admin()
        project_id = self._create_project("登记去重状态项目")
        storage_key = self._create_incoming_tif("dedup-status.tif")

        first = self._register(project_id, storage_key, "影像")
        second = self._register(project_id, storage_key, "影像")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        activities = self.client.get(f"/api/projects/{project_id}/timeline").get_json()
        created = [
            item
            for item in activities["data"]["items"]
            if item.get("event_type") == "dataset_created"
        ]
        self.assertEqual(len(created), 1)


if __name__ == "__main__":
    unittest.main()
