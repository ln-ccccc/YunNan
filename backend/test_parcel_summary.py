# -*- coding: utf-8 -*-
"""图斑清单汇总（M3 补交）契约测试：去重统计/分页/fid 过滤/空项目/鉴权。"""
import json
import os
import sys
import unittest
import uuid
from datetime import datetime
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.project import Project, ProjectMineBinding
from test_project_api import TestProjectAPI


class ParcelSummaryBase(TestProjectAPI):
    # 屏蔽继承的父类用例（fixture 已改写）
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable")
    def test_timeline_normalizes_project_data_export_and_snapshot_activities(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_builds_project_ledger_workbook(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_tolerates_vector_failed_result_with_null_collection(self):
        self.skipTest("parent-case not applicable")

    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self.project_id = self._create_project("图斑清单项目")
        for fid in (11, 22, 33):
            db.session.add(ProjectMineBinding(
                project_id=self.project_id, mine_fid=fid,
                mine_name_snapshot=f"矿山{fid}", status_snapshot="未治理",
                sort_order=fid,
            ))
        db.session.commit()

    def _make_result(self, fid, year, feature_count, suffix="a"):
        return ClassificationResult(
            project_id=self.project_id, mine_fid=fid, year=year,
            inference_job_id=str(uuid.uuid4())[:35] + suffix,
            model_id="mmseg:test", mine_resource_id=1,
            current_feature_collection_json='{"type":"FeatureCollection","features":[]}',
            current_revision_no=0, vector_status="ready",
            feature_count=feature_count,
            create_time=datetime(2026, 9, 1, 10, 0, 0),
        )

    def _get(self, **params):
        from urllib.parse import urlencode

        query = urlencode(params) if params else ""
        path = f"/api/projects/{self.project_id}/mines/parcel-summary"
        return self.client.get(path + (f"?{query}" if query else ""))


class TestParcelSummary(ParcelSummaryBase):
    def test_summary_dedupes_same_year_and_aggregates(self):
        older = self._make_result(11, 2024, 1, "o")
        db.session.flush()
        newer = self._make_result(11, 2024, 5, "n")  # 同年多任务：取最新 5
        db.session.add_all([older, newer, self._make_result(11, 2023, 2)])
        db.session.add(self._make_result(22, 2024, 3))
        db.session.commit()

        data = self._get().get_json()["data"]
        by_fid = {item["mine_fid"]: item for item in data["items"]}
        self.assertEqual(by_fid[11]["feature_count"], 7)  # 2024 取 5 + 2023 取 2
        self.assertEqual(by_fid[11]["latest_year"], 2024)
        self.assertEqual(by_fid[11]["result_count"], 2)
        self.assertEqual(by_fid[22]["feature_count"], 3)
        self.assertEqual(by_fid[33]["feature_count"], 0)
        self.assertIsNone(by_fid[33]["latest_year"])
        self.assertEqual(data["count"], 3)

    def test_pagination_and_fid_filter(self):
        for i in range(25):
            db.session.add(ProjectMineBinding(project_id=self.project_id, mine_fid=100 + i))
        db.session.commit()
        page1 = self._get(page=1, limit=20).get_json()["data"]
        self.assertEqual(len(page1["items"]), 20)
        self.assertEqual(page1["count"], 28)  # 3 + 25
        page2 = self._get(page=2, limit=20).get_json()["data"]
        self.assertEqual(len(page2["items"]), 8)
        only = self._get(fid=11).get_json()["data"]
        self.assertEqual(only["count"], 1)
        self.assertEqual(only["items"][0]["mine_fid"], 11)
        bad = self._get(fid="abc")
        self.assertEqual(bad.status_code, 400)

    def test_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.get(f"/api/projects/{self.project_id}/mines/parcel-summary")
        self.assertEqual(response.status_code, 401)

    def test_project_not_found(self):
        response = self.client.get("/api/projects/999999/mines/parcel-summary")
        self.assertIn(response.status_code, (400, 404))
        self.assertFalse(response.get_json().get("success"))

    # 屏蔽继承的父类用例
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable")
    def test_timeline_normalizes_project_data_export_and_snapshot_activities(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_builds_project_ledger_workbook(self):
        self.skipTest("parent-case not applicable")
    def test_xlsx_export_tolerates_vector_failed_result_with_null_collection(self):
        self.skipTest("parent-case not applicable")


if __name__ == "__main__":
    unittest.main()
