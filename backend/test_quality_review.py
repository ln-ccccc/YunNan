# -*- coding: utf-8 -*-
"""成果质量复核（优化建议三.3）契约测试：地类构成统计/面积链回落/细碎图斑线索/
current→auto 降级/vector_failed 零值报告/跨项目 404/鉴权/时间 Z 口径。"""
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
    ClassificationRevision,
)
from applications.models.project import ProjectMineBinding
from applications.project_hub.quality_review import (
    feature_area_m2,
    geometry_area_m2,
    ring_area_m2,
)
from test_project_api import TestProjectAPI


def _square_feature(class_code, area=None, tbty=None, lon=100.0, lat=26.0, side=0.01):
    properties = {"class_code": class_code, "class_name": f"class_{class_code}"}
    if area is not None:
        properties["area"] = area
    if tbty is not None:
        properties["TBTYMJ_1"] = tbty
    return {
        "type": "Feature",
        "properties": properties,
        "geometry": {"type": "Polygon", "coordinates": [[[lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat]]]},
    }


def _collection(*features):
    return {"type": "FeatureCollection", "features": list(features)}


class QualityReviewBase(TestProjectAPI):
    # 本文件只跑质检用例：屏蔽继承来的父类用例（其 setUp 已被改写）
    def test_project_workflow_crud_binding_dataset_timeline_and_status(self):
        self.skipTest("parent-case not applicable under quality-review fixture")

    def setUp(self):
        super().setUp()
        self.login_as_admin()
        self.project_id = self._create_project("质检项目")
        db.session.add(ProjectMineBinding(project_id=self.project_id, mine_fid=713))
        db.session.commit()

    def _make_result(self, features, vector_status="ready", current=True, year=2024):
        collection = _collection(*features)
        result = ClassificationResult(
            project_id=self.project_id, mine_fid=713, year=year,
            inference_job_id=str(uuid.uuid4())[:36],
            model_id="mmseg:test", mine_resource_id=1,
            current_feature_collection_json=json.dumps(collection) if current else None,
            auto_feature_collection_json=json.dumps(collection) if not current else None,
            current_revision_no=1 if current else 0,
            vector_status=vector_status,
            feature_count=len(features),
            create_time=datetime(2026, 9, 1, 10, 0, 0),
        )
        db.session.add(result)
        db.session.flush()
        return result

    def _review(self, result_id, query=""):
        return self.client.get(
            f"/api/projects/{self.project_id}/classification-results/{result_id}/quality-review{query}"
        )


class TestGeometryArea(QualityReviewBase):
    def test_equator_square_known_area(self):
        # 赤道处 0.001° 见方 ≈ (111.32m)^2 ≈ 12392 m²，球面公式与平面近似差 <1%
        ring = [[100.0, 0.0], [100.001, 0.0], [100.001, 0.001], [100.0, 0.001], [100.0, 0.0]]
        area = abs(ring_area_m2(ring))
        self.assertAlmostEqual(area, 12392.0, delta=124.0)

    def test_hole_and_multipolygon(self):
        outer = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]
        hole = [[0.002, 0.002], [0.008, 0.002], [0.008, 0.008], [0.002, 0.008], [0.002, 0.002]]
        polygon_area = geometry_area_m2({"type": "Polygon", "coordinates": [outer, hole]})
        outer_only = geometry_area_m2({"type": "Polygon", "coordinates": [outer]})
        hole_area = geometry_area_m2({"type": "Polygon", "coordinates": [hole]})
        self.assertAlmostEqual(polygon_area, outer_only - hole_area, delta=1.0)

    def test_property_alias_chain(self):
        # area 属性缺失时逐级回落 TBTYMJ_1，真值非数字（"待定"）继续回落几何
        with_property = feature_area_m2(
            {"TBTYMJ_1": 2500},
            {"type": "Polygon", "coordinates": [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]]},
        )
        self.assertEqual(with_property, 2500.0)
        fallback = feature_area_m2(
            {"area": "待定", "TBTYMJ_1": "待定", "TBTYMJ": None},
            {"type": "Polygon", "coordinates": [[[100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0]]]},
        )
        self.assertGreater(fallback, 1_000_000.0)


class TestQualityReviewApi(QualityReviewBase):
    def test_class_stats_suspects_and_threshold(self):
        result = self._make_result([
            _square_feature(0, area=5000.0),
            _square_feature(1, area=80.0),
            _square_feature(1, area=30.0),
        ])
        db.session.commit()

        body = self._review(result.id).get_json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertEqual(data["feature_count"], 3)
        self.assertEqual(data["total_area_m2"], 5110.0)
        self.assertEqual(data["class_kind_count"], 2)
        by_code = {row["class_code"]: row for row in data["class_stats"]}
        self.assertEqual(by_code[0]["count"], 1)
        self.assertAlmostEqual(by_code[0]["area_percent"], 97.85, delta=0.01)
        # 默认阈值 100 m²：两个细碎图斑按面积升序
        self.assertEqual(data["small_feature_count"], 2)
        self.assertEqual([s["area_m2"] for s in data["suspects"]], [30.0, 80.0])
        self.assertEqual(data["suspects"][0]["centroid"][0], 100.005)

        # 自定义阈值放宽后细碎清单同步变化
        data2 = self._review(result.id, "?small_area_threshold_m2=6000").get_json()["data"]
        self.assertEqual(data2["small_feature_count"], 3)
        # 非法阈值 400
        self.assertEqual(self._review(result.id, "?small_area_threshold_m2=abc").status_code, 400)
        self.assertEqual(self._review(result.id, "?small_area_threshold_m2=0").status_code, 400)

    def test_current_falls_back_to_auto_then_none(self):
        auto_only = self._make_result([_square_feature(2)], current=False)
        failed = self._make_result([], vector_status="vector_failed", current=False)
        db.session.commit()

        data = self._review(auto_only.id).get_json()["data"]
        self.assertEqual(data["vector_source"], "auto")
        self.assertEqual(data["feature_count"], 1)

        data = self._review(failed.id).get_json()["data"]
        self.assertEqual(data["vector_source"], "none")
        self.assertEqual(data["feature_count"], 0)
        self.assertEqual(data["suspects"], [])
        self.assertEqual(data["small_feature_ratio_percent"], 0.0)

    def test_result_of_other_project_is_404(self):
        result = self._make_result([_square_feature(0)])
        db.session.commit()
        other_project_id = self._create_project("另一项目")
        response = self.client.get(
            f"/api/projects/{other_project_id}/classification-results/{result.id}/quality-review"
        )
        self.assertEqual(response.status_code, 404)

    def test_requires_login(self):
        result = self._make_result([_square_feature(0)])
        db.session.commit()
        fresh = self.app.test_client()
        response = fresh.get(
            f"/api/projects/{self.project_id}/classification-results/{result.id}/quality-review"
        )
        self.assertEqual(response.status_code, 401)


class TestTimeZSweep(QualityReviewBase):
    def test_traceability_and_revisions_created_at_carry_z(self):
        result = self._make_result([_square_feature(0)])
        db.session.add(ClassificationEditAudit(
            result_id=result.id, base_revision_no=0, revision_no=1,
            actor="admin", action="manual_save", request_source="geoview_editor",
            details_json="{}", create_time=datetime(2026, 9, 2, 8, 30, 0),
        ))
        # 修订版本表与编辑审计表分开：revisions 端点读 ClassificationRevision
        db.session.add(ClassificationRevision(
            result_id=result.id, revision_no=1, source="manual",
            author="admin", feature_count=1, snapshot_path="snapshots/r1.json",
            feature_collection_json=json.dumps(_collection(_square_feature(0))),
            create_time=datetime(2026, 9, 2, 8, 30, 0),
        ))
        db.session.commit()

        trace = self.client.get(
            f"/api/projects/{self.project_id}/mines/713/traceability"
        ).get_json()["data"]
        self.assertTrue(trace["years"][0]["created_at"].endswith("Z"))
        self.assertTrue(trace["years"][0]["revisions"][0]["created_at"].endswith("Z"))

        revisions = self.client.get(
            f"/api/projects/{self.project_id}/classification-results/{result.id}/revisions"
        ).get_json()["data"]
        self.assertTrue(revisions["revisions"][0]["created_at"].endswith("Z"))

    def test_reviewed_at_carry_z(self):
        result = self._make_result([_square_feature(0)])
        db.session.commit()
        data = self._review(result.id).get_json()["data"]
        self.assertTrue(data["reviewed_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
