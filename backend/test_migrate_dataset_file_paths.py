# -*- coding: utf-8 -*-
"""migrate_dataset_file_paths 契约测试（契约 B，2026-10-24）：
绝对→相对正确、storage 根外不动、相对键行永不触碰（幂等）。"""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.project import ProjectDataset
from migrate_dataset_file_paths import plan_migration
from test_project_api import TestProjectAPI


class TestMigrateDatasetFilePaths(TestProjectAPI):
    # 屏蔽继承的父类用例（fixture 已改写，只跑本文件用例——沿 test_imagery_processing 惯例）
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable")

    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self.project_id = self._create_project("迁移项目")

    def _add_dataset(self, file_path):
        dataset = ProjectDataset(
            project_id=self.project_id,
            dataset_kind="imagery",
            display_name="迁移样本",
            file_path=file_path,
            source_format="tif",
        )
        db.session.add(dataset)
        db.session.commit()
        return dataset.id

    def test_plan_converts_absolute_inside_root_and_skips_outside(self):
        inside = self.storage_root / "projects" / str(self.project_id) / "inputs" / "imagery" / "a.tif"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_bytes(b"tif")
        inside_id = self._add_dataset(str(inside))
        outside_id = self._add_dataset("/nonexistent-root/elsewhere/b.tif")
        relative_id = self._add_dataset("incoming/keep.tif")

        rows, skipped = plan_migration()

        self.assertEqual(rows, [{
            "dataset_id": inside_id,
            "from": str(inside),
            "to": f"projects/{self.project_id}/inputs/imagery/a.tif",
        }])
        self.assertEqual([item["dataset_id"] for item in skipped], [outside_id])
        # 相对键行永不触碰：relative_id 不出现在任何计划里
        self.assertNotIn(relative_id, [item["dataset_id"] for item in rows + skipped])

    def test_migration_is_idempotent_after_apply(self):
        inside = self.storage_root / "projects" / str(self.project_id) / "inputs" / "imagery" / "c.tif"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_bytes(b"tif")
        self._add_dataset(str(inside))

        rows, _ = plan_migration()
        dataset = db.session.get(ProjectDataset, rows[0]["dataset_id"])
        dataset.file_path = rows[0]["to"]
        db.session.commit()

        again_rows, again_skipped = plan_migration()
        self.assertEqual(again_rows, [])
        self.assertEqual(again_skipped, [])


if __name__ == "__main__":
    unittest.main()
