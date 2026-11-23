# -*- coding: utf-8 -*-
"""同项目同格式导出去重（2026-11-24 审计 D1）与时间预算窗口。

- 已有一条仍在预算窗口内的 pending 导出时，再次创建返回 422 且不新增记录；
- 超出窗口的 pending 视为后端中途崩溃留下的孤儿，不堵住导出；
- 已完成的记录不影响再次导出；
- 查重走锁定读——MySQL 可重复读下普通 SELECT 读到的是加锁前的旧快照，看不见对方
  刚提交的 pending（生产实测两个并发导出都 200）。SQLite 忽略 FOR UPDATE，这里用
  语句监听断言查询对象带着 FOR UPDATE 标记，并发行为的真实验证见活体验收记录。
"""
import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectExportRecord
from applications.project_hub import exports as exports_module
from applications.project_hub.exports import _within_export_budget_window


class ExportBudgetWindowTests(unittest.TestCase):
    def test_window_follows_the_ledger_budget_plus_margin(self):
        # 预算 4 分钟 + 60 秒余量 = 300 秒；用真实的"现在"相对取值，不修补 datetime 类
        # (openpyxl 等库在类定义期绑定了 datetime.datetime, 全局替换会污染同进程的后续用例)
        now = datetime.datetime.now()
        self.assertTrue(_within_export_budget_window(now))
        self.assertTrue(_within_export_budget_window(now - datetime.timedelta(seconds=5)))
        self.assertTrue(_within_export_budget_window(now - datetime.timedelta(seconds=295)))
        self.assertFalse(_within_export_budget_window(now - datetime.timedelta(seconds=305)))
        # 类型异常按"仍在窗口内"处理：宁可多拦一次重复提交
        self.assertTrue(_within_export_budget_window(None))
        self.assertTrue(_within_export_budget_window("not-a-time"))


class ExportDedupTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="export-dedup-")
        self.addCleanup(self.temp_dir.cleanup)
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp_dir.name
        self.addCleanup(self._restore)
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()
        self.project = Project(name="导出去重", region="昆明")
        db.session.add(self.project)
        db.session.commit()
        self.project_id = self.project.id

    def _restore(self):
        if self.previous is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous

    def _pending(self, age_seconds=0):
        record = ProjectExportRecord(
            project_id=self.project_id, format="csv", status="pending", request_params_json="{}",
            create_time=datetime.datetime.now() - datetime.timedelta(seconds=age_seconds),
        )
        db.session.add(record)
        db.session.commit()
        return record

    def test_fresh_pending_export_is_rejected_without_a_second_record(self):
        self._pending(age_seconds=5)
        with self.assertRaises(Exception) as caught:
            exports_module.create_export(self.project_id, {"format": "csv"})
        self.assertIn("正在生成中", str(caught.exception))
        self.assertEqual(ProjectExportRecord.query.filter_by(project_id=self.project_id).count(), 1)

    def test_stale_pending_from_a_crashed_request_does_not_block_forever(self):
        orphan = self._pending(age_seconds=301)
        created = exports_module.create_export(self.project_id, {"format": "csv"})
        self.assertEqual(created["status"], "completed")
        self.assertEqual(ProjectExportRecord.query.filter_by(project_id=self.project_id).count(), 2)
        self.assertEqual(ProjectExportRecord.query.get(orphan.id).status, "pending", "孤儿记录如实保留为 pending")

    def test_other_format_and_completed_records_do_not_block(self):
        # geojson 进行中不影响 csv；已完成的 csv 记录不影响再导一份 csv
        other = ProjectExportRecord(project_id=self.project_id, format="geojson", status="pending", request_params_json="{}")
        completed = ProjectExportRecord(project_id=self.project_id, format="csv", status="completed", request_params_json="{}")
        db.session.add_all([other, completed])
        db.session.commit()
        self.assertEqual(exports_module.create_export(self.project_id, {"format": "csv"})["status"], "completed")
        self.assertEqual(ProjectExportRecord.query.filter_by(project_id=self.project_id, format="csv").count(), 2)

    def test_dedup_lookup_is_a_locking_read(self):
        """MySQL 可重复读下普通 SELECT 看不见对方刚提交的 pending；查重必须带 FOR UPDATE。
        SQLite 不渲染 FOR UPDATE，这里拦下 Query.with_for_update 调用，检查针对导出记录表的
        查询确实带着它（项目行锁针对的是 Project 实体，不计入）。"""
        from sqlalchemy.orm import Query

        calls = []
        original = Query.with_for_update

        def spy(query, *args, **kwargs):
            calls.append(query)
            return original(query, *args, **kwargs)

        with patch.object(Query, "with_for_update", spy):
            exports_module.create_export(self.project_id, {"format": "csv"})
        export_record_queries = [
            query for query in calls
            if query.column_descriptions and query.column_descriptions[0].get("entity") is ProjectExportRecord
        ]
        self.assertEqual(len(export_record_queries), 1, "查重查询应恰好一次且带 FOR UPDATE")
        compiled = export_record_queries[0].statement.compile()
        self.assertIn("project_export_record.status", str(compiled))
        # 状态值是绑定参数，不在语句文本里
        self.assertEqual(compiled.params["status_1"], "pending")


if __name__ == "__main__":
    unittest.main()
