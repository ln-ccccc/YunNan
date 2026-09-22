# -*- coding: utf-8 -*-
"""M3 图斑溯源聚合契约测试：历年条目/同年多任务去重/占比序列/修订时间线/
部分数据缺失降级/fid 归属/鉴权。"""
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
from applications.models.classification_result import (
    ClassificationEditAudit,
    ClassificationResult,
)
from applications.models.project import Project, ProjectMineBinding
from test_project_api import TestProjectAPI


def _feature(class_code):
    return {
        "type": "Feature",
        "properties": {"class_code": class_code},
        "geometry": {"type": "Polygon", "coordinates": [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]]},
    }


class TraceabilityBase(TestProjectAPI):
    # 本文件只跑溯源用例：屏蔽继承来的父类用例（其 setUp 已被改写）
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable under traceability fixture")
    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self.project_id = self._create_project("溯源项目")
        db.session.add(ProjectMineBinding(project_id=self.project_id, mine_fid=713))
        db.session.commit()
        self.inference_root = self.storage_root / "projects" / str(self.project_id) / "outputs" / "inference" / "713"

    def _make_result(self, year, feature_count=3, job_suffix="a"):
        collection = {"type": "FeatureCollection", "features": [_feature(0)] * feature_count}
        result = ClassificationResult(
            project_id=self.project_id, mine_fid=713, year=year,
            inference_job_id=str(uuid.uuid4())[:35] + job_suffix,
            model_id="mmseg:test", mine_resource_id=1,
            current_feature_collection_json=json.dumps(collection),
            current_revision_no=1, vector_status="ready",
            feature_count=feature_count,
            create_time=datetime(2026, 9, 1, 10, 0, 0),
        )
        db.session.add(result)
        db.session.flush()
        return result

    def _write_ratio(self, years, series):
        self.inference_root.mkdir(parents=True, exist_ok=True)
        (self.inference_root / "class_ratio_percent.json").write_text(
            json.dumps({
                "fid": "713", "class_names": ["grassland", "forest", "building", "road", "bareground", "water"],
                "years": years, "totals": [100] * len(years), "series_percent": series,
            }),
            encoding="utf-8",
        )

    def _get(self, fid=713):
        return self.client.get(f"/api/projects/{self.project_id}/mines/{fid}/traceability")


class TestMineTraceability(TraceabilityBase):
    def test_aggregates_years_with_ratio_and_image_urls(self):
        self._make_result(2023, feature_count=2)
        self._make_result(2024, feature_count=5)
        self._write_ratio([2023, 2024], {"grassland": [50.0, 30.0], "water": [50.0, 70.0]})
        db.session.commit()

        body = self._get().get_json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertEqual([y["year"] for y in data["years"]], [2023, 2024])
        y2024 = data["years"][1]
        self.assertEqual(y2024["feature_count"], 5)
        self.assertIn(f"/api/projects/{self.project_id}/outputs/inference/713/713+2024.png", y2024["result_image_url"])
        self.assertEqual(y2024["class_ratio_percent"]["grassland"], 30.0)
        self.assertEqual(data["ratio_series"]["years"], [2023, 2024])
        # 文件缺失的维度降级为 None 而非报错
        self.assertIsNone(data["change_matrix"])
        self.assertIsNone(data["indices"])

    def test_same_year_multi_job_takes_latest_result(self):
        older = self._make_result(2024, feature_count=1, job_suffix="o")
        db.session.flush()
        newer = self._make_result(2024, feature_count=9, job_suffix="n")
        db.session.commit()
        self.assertLess(older.id, newer.id)

        data = self._get().get_json()["data"]
        self.assertEqual(len(data["years"]), 1)
        self.assertEqual(data["years"][0]["feature_count"], 9)
        self.assertEqual(data["years"][0]["result_id"], newer.id)

    def test_revision_timeline_from_audit_table(self):
        result = self._make_result(2024)
        db.session.add(ClassificationEditAudit(
            result_id=result.id, base_revision_no=0, revision_no=1,
            actor="admin", action="manual_save", request_source="geoview_editor",
            details_json='{"feature_count": 3}',
        ))
        db.session.commit()
        data = self._get().get_json()["data"]
        revisions = data["years"][0]["revisions"]
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["action"], "manual_save")
        self.assertEqual(revisions[0]["actor"], "admin")

    def test_original_imagery_section_reuses_existing_aggregate(self):
        self._make_result(2024)
        db.session.commit()
        data = self._get().get_json()["data"]
        self.assertIn("original_imagery", data)
        self.assertEqual(data["original_imagery"]["fid"], 713)

    def test_fid_must_belong_to_project(self):
        response = self._get(fid=8888)
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        self._make_result(2024)
        db.session.commit()
        fresh = self.app.test_client()
        response = fresh.get(f"/api/projects/{self.project_id}/mines/713/traceability")
        self.assertEqual(response.status_code, 401)

    def test_empty_mine_returns_empty_years(self):
        data = self._get().get_json()["data"]
        self.assertEqual(data["years"], [])
        self.assertIsNone(data["ratio_series"])


if __name__ == "__main__":
    unittest.main()
