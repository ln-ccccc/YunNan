# -*- coding: utf-8 -*-
"""M1 主控台看板：跨项目统计聚合 + 项目列表卡片增强字段的契约测试。

覆盖：空库概览、项目计数分组、图斑要素数（同年多任务去重）、最近推理进度、
地类面积占比（球面面积聚合）、新增修复面积与疑似异常矿山（变化矩阵净转移口径）、
归档项目不入统计、鉴权。
"""
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
from applications.models.inference_job import InferenceJob
from applications.models.project import Project, ProjectMineBinding
from applications.api.stats import _change_metrics, _spherical_ring_area_m2


def _write_tif(path: Path, width=4, height=4, pixel_deg=0.0001):
    import numpy as np
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 1,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": rasterio.transform.from_origin(100.0, 26.0, pixel_deg, pixel_deg),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.zeros((1, height, width), dtype=np.uint8))


class StatsOverviewBase(unittest.TestCase):
    def setUp(self):
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory(prefix="stats-overview-")
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
            "/api/auth/login",
            json={"username": "admin", "password": password},
        )
        self.assertEqual(response.status_code, 200)

    def _create_project(self, name, status="active"):
        project = Project(
            name=name, region="昆明", status=status,
            monitor_start_year=2024, monitor_end_year=2025,
        )
        db.session.add(project)
        db.session.flush()
        return project

    def _make_result(self, project_id, fid, year, features, job_suffix="a"):
        collection = {"type": "FeatureCollection", "features": features}
        result = ClassificationResult(
            project_id=project_id, mine_fid=fid, year=year,
            inference_job_id=str(uuid.uuid4())[:36] + job_suffix,
            model_id="mmseg:test", mine_resource_id=1,
            current_feature_collection_json=json.dumps(collection),
            current_revision_no=0,
            vector_status="ready",
            feature_count=len(features),
        )
        db.session.add(result)
        return result


def _feature(class_code, ring):
    return {
        "type": "Feature",
        "properties": {"class_code": class_code},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


# 约 0.01°×0.01° 的正方形（昆明纬度约 26°，面积 ~1.0 km² 量级）
_SQUARE = [
    [100.0, 26.0], [100.01, 26.0], [100.01, 26.01], [100.0, 26.01], [100.0, 26.0],
]


class TestSphericalArea(unittest.TestCase):
    def test_known_square_area_magnitude(self):
        area = _spherical_ring_area_m2([_SQUARE])
        # 0.01° 纬向 ~1113m，经向在 26° 纬度 ~1000m → ~1.1e6 m²；取量级校验
        self.assertGreater(area, 0.9e6)
        self.assertLess(area, 1.3e6)
        # 退化输入安全
        self.assertEqual(_spherical_ring_area_m2([[]]), 0.0)
        self.assertEqual(_spherical_ring_area_m2([]), 0.0)


class TestStatsOverview(StatsOverviewBase):
    def test_empty_database_overview(self):
        body = self.client.get("/api/stats/overview").get_json()
        self.assertEqual(body["code"], 0)
        data = body["data"]
        self.assertEqual(data["project_total"], 0)
        self.assertEqual(data["feature_total"], 0)
        self.assertEqual(data["class_area"]["percent"]["grassland"], 0.0)
        self.assertEqual(data["restored_area_m2"], 0.0)
        self.assertEqual(data["suspect_mine_count"], 0)

    def test_project_counts_and_feature_dedup(self):
        project = self._create_project("统计项目A", status="active")
        self._create_project("草稿项目B", status="draft")
        self._create_project("归档项目C", status="archived")
        db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=1))
        # 同年同 fid 两条成果（同年多任务）：只计最新一条（id 更大）的 5 个要素
        older = self._make_result(project.id, 1, 2024, [_feature(0, _SQUARE)] * 2, job_suffix="old")
        db.session.flush()
        newer = self._make_result(project.id, 1, 2024, [_feature(1, _SQUARE)] * 5, job_suffix="new")
        db.session.flush()
        # 旧记录在先创建 → id 更小；让 older.id < newer.id 成立
        self.assertLess(older.id, newer.id)
        db.session.commit()

        body = self.client.get("/api/stats/overview").get_json()["data"]
        self.assertEqual(body["project_counts"], {"draft": 1, "active": 1, "completed": 0, "archived": 1})
        self.assertEqual(body["project_total"], 3)
        # 归档项目不入活跃统计
        self.assertEqual(body["mine_total"], 1)
        self.assertEqual(body["feature_total"], 5)

    def test_class_area_percent_aggregates_by_class_code(self):
        project = self._create_project("地类占比项目")
        features = [_feature(0, _SQUARE), _feature(1, _SQUARE)]  # 草地/林地各 1 个等大面积
        self._make_result(project.id, 1, 2024, features)
        db.session.commit()

        data = self.client.get("/api/stats/overview").get_json()["data"]
        percent = data["class_area"]["percent"]
        self.assertAlmostEqual(percent["grassland"], 50.0, places=1)
        self.assertAlmostEqual(percent["forest"], 50.0, places=1)
        self.assertEqual(percent["water"], 0.0)
        self.assertGreater(data["class_area"]["total_area_m2"], 1.8e6)

    def test_restored_area_and_suspect_mines_from_change_matrix(self):
        project = self._create_project("变化项目")
        db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=77))
        db.session.commit()

        fid_dir = self.storage_root / "projects" / str(project.id) / "outputs" / "inference" / "77"
        fid_dir.mkdir(parents=True)
        _write_tif(fid_dir / "77+2024_label.tif", width=4, height=4, pixel_deg=0.0001)
        # 变化矩阵：草地 90 像素→裸地（异常矿山：90/100=90%≥10%）；林地 10→草地（修复 10）
        (fid_dir / "change_matrix_pixels.csv").write_text(
            "class,grassland,forest,building,road,bareground,water\n"
            "grassland,10,0,0,0,90,0\n"
            "forest,10,0,0,0,0,0\n"
            "building,0,0,0,0,0,0\n"
            "road,0,0,0,0,0,0\n"
            "bareground,0,0,0,0,0,0\n"
            "water,0,0,0,0,0,0\n",
            encoding="utf-8",
        )

        data = self.client.get("/api/stats/overview").get_json()["data"]
        self.assertEqual(data["suspect_mine_count"], 1)
        # 净修复 = (0+10 获得) - (90 草地流失) = -80 像素 → 负贡献钳为 0（该矿山净损毁不计入修复面积）
        self.assertEqual(data["restored_area_m2"], 0.0)

    def test_metrics_unit_without_files(self):
        empty_root = self.storage_root / "projects" / "999" / "outputs" / "inference"
        metrics = _change_metrics(empty_root.parent)
        self.assertEqual(metrics, {"restored_area_m2": 0.0, "suspect_mine_count": 0, "suspect_fids": []})

    def test_requires_login(self):
        fresh = self.app.test_client()
        response = fresh.get("/api/stats/overview")
        self.assertEqual(response.status_code, 401)


class TestProjectListEnhancements(StatsOverviewBase):
    def test_list_returns_feature_count_and_latest_inference(self):
        project = self._create_project("列表增强项目")
        db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=5))
        self._make_result(project.id, 5, 2024, [_feature(0, _SQUARE)] * 3)
        job = InferenceJob(
            id=str(uuid.uuid4()), project_id=project.id, status="succeeded_with_fallback",
            requested_device="auto",
            request_payload_json="{}",
            create_time=datetime(2026, 9, 22, 12, 0, 0),
        )
        db.session.add(job)
        db.session.commit()

        body = self.client.get("/api/projects").get_json()
        item = next(i for i in body["data"]["items"] if i["id"] == project.id)
        self.assertEqual(item["feature_count"], 3)
        self.assertEqual(item["latest_inference"]["status"], "succeeded_with_fallback")
        self.assertEqual(item["latest_inference"]["create_time"], "2026-09-22T12:00:00")

    def test_list_project_without_results_has_zero_defaults(self):
        project = self._create_project("无成果项目")
        db.session.commit()
        body = self.client.get("/api/projects").get_json()
        item = next(i for i in body["data"]["items"] if i["id"] == project.id)
        self.assertEqual(item["feature_count"], 0)
        self.assertIsNone(item["latest_inference"])


if __name__ == "__main__":
    unittest.main()
