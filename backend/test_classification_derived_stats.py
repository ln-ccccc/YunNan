# -*- coding: utf-8 -*-
"""分类成果派生统计集成链（2026-11-24 按派生数据性质重构）。

审计实证的缺陷与本文件对应的防线：
- 同一页面混用两条成果（保存较旧成果后溯源取最新、占比取较旧）→ 有效成果唯一规则，
  统计随成果走（test_saving_older_result_of_a_year...）；
- 两年并发保存后一次冲掉另一次 → 逐修订 sidecar，各是各的文件（..._keeps_first_year）；
- 清空后变化矩阵仍是旧值 → 矢量为空时矩阵显式失效，并在 DTO 标注状态；
- 推理发布重建占比文件丢掉人工值 → 人工值不再存放在占比文件里（发布后仍可读）；
- 保存提交失败留下孤立 GeoJSON → 提交失败后与数据库对账再决定文件去留；
- 保存同步等待统计重算 → 矩阵重算与保存解耦（测试环境内联执行，行为确定）；
- 趋势用"占比 × 整矿面积"估算地类面积 → 修订年份用图斑绝对面积。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult, ClassificationRevision
from applications.models.project import ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub import classification_results as classification_results_module
from applications.project_hub import classification_stats
from applications.project_hub.classification_stats import (
    MATRIX_STATE_INFERENCE,
    MATRIX_STATE_PENDING,
    MATRIX_STATE_REVISED,
    MATRIX_STATE_REVISED_EMPTY,
    _change_matrix_path,
    _rebuild_marker_path,
    _revision_stats_path,
)

CLASS_NAMES = ["grassland", "forest", "building", "road", "bareground", "water"]
ROI = {"type": "Polygon", "coordinates": [
    [[99.9, 25.9], [100.1, 25.9], [100.1, 26.1], [99.9, 26.1], [99.9, 25.9]],
]}


def _left_right_fc(left_class):
    """左半一个面 + 右半裸地面（各占一半面积）。feature_id 沿用自动基线里的既有 ID
    （改类不改身份），否则保存校验会把它当成"既有 ID 不属于基准版本"。"""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"class_code": left_class, "feature_id": "auto-1-0-1"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[100.0, 26.0], [100.005, 26.0], [100.005, 25.99], [100.0, 25.99], [100.0, 26.0]],
         ]}},
        {"type": "Feature", "properties": {"class_code": 4, "feature_id": "auto-1-4-1"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[100.005, 26.0], [100.01, 26.0], [100.01, 25.99], [100.005, 25.99], [100.005, 26.0]],
         ]}},
    ]}


def _single_fc(class_code, lon=100.002, lat=25.995, side=0.001):
    """只保留基线里的左侧面（既有 ID），缩成一个小面并改成指定类别。"""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"class_code": class_code, "feature_id": "auto-1-0-1"},
         "geometry": {"type": "Polygon", "coordinates": [
             [[lon, lat], [lon + side, lat], [lon + side, lat - side], [lon, lat - side], [lon, lat]],
         ]}},
    ]}


class TestClassificationDerivedStats(unittest.TestCase):
    def setUp(self):
        self.previous_storage_root = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="derived-stats-")
        self.addCleanup(self.temp_dir.cleanup)
        self.storage_root = Path(self.temp_dir.name) / "project_storage"
        self.storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(self.storage_root)
        self.addCleanup(self._restore_storage_root)
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_storage_root(self):
        if self.previous_storage_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_storage_root

    @staticmethod
    def _json(response):
        return json.loads(response.data.decode("utf-8"))

    def _login(self):
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"
        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        from applications.auth.service import sync_admin_from_env

        sync_admin_from_env()
        response = self.client.post("/api/auth/login", json={"username": "admin", "password": "Secret123!"})
        self.assertEqual(response.status_code, 200)

    def _create_project(self, name):
        response = self.client.post("/api/projects", json={
            "name": name, "region": "昆明市", "manager": "测试管理员",
            "monitor_start_year": 2023, "monitor_end_year": 2024,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self._json(response)["data"]["id"]

    def _seed_mine(self, project_id, fid=713, years=(2023, 2024), extra_2024_result=False):
        """推理占比文件 + 两期 label + 自动基线成果（修订号 0）。"""
        db.session.add(ProjectMineBinding(project_id=project_id, mine_fid=fid))
        db.session.commit()
        fid_dir = self.storage_root / "projects" / str(project_id) / "outputs" / "inference" / str(fid)
        fid_dir.mkdir(parents=True)
        (fid_dir / "class_ratio_percent.json").write_text(json.dumps({
            "fid": str(fid),
            "class_names": CLASS_NAMES,
            "years": list(years),
            "totals": [100] * len(years),
            "series_percent": {
                "grassland": [30.0, 40.0][: len(years)], "forest": [20.0, 20.0][: len(years)],
                "building": [10.0, 10.0][: len(years)], "road": [5.0, 5.0][: len(years)],
                "bareground": [15.0, 15.0][: len(years)], "water": [20.0, 10.0][: len(years)],
            },
        }, ensure_ascii=False), encoding="utf-8")
        for year in years:
            data = np.full((10, 10), 255, dtype=np.uint8)
            data[:, :5] = 1
            data[:, 5:] = 0
            with rasterio.open(
                fid_dir / f"{fid}+{year}_label.tif", "w", driver="GTiff",
                height=10, width=10, count=1, dtype="uint8",
                crs="EPSG:4326", transform=from_origin(100.0, 26.0, 0.001, 0.001), nodata=255,
            ) as dataset:
                dataset.write(data, 1)
        fc_json = json.dumps(_left_right_fc(0), ensure_ascii=False)
        results = {}
        for year in years:
            result = ClassificationResult(
                project_id=project_id, mine_fid=fid, year=year,
                inference_job_id=f"job-{fid}-{year}", model_id="cc-ln/CUGRS", mine_resource_id=1,
                roi_geometry_json=json.dumps(ROI), current_feature_collection_json=fc_json,
                auto_feature_collection_json=fc_json, current_revision_no=0,
                feature_count=2, vector_status="ready",
            )
            db.session.add(result)
            db.session.flush()
            db.session.add(ClassificationRevision(
                result_id=result.id, revision_no=0, source="auto", author="system", feature_count=2,
                snapshot_path=f"classification-results/{result.id}/auto.geojson",
                feature_collection_json=fc_json,
            ))
            results[year] = result
        if extra_2024_result:
            # 同年第二条成果（id 更大 → 有效成果是它）
            newer = ClassificationResult(
                project_id=project_id, mine_fid=fid, year=years[-1],
                inference_job_id=f"job-{fid}-{years[-1]}-newer", model_id="cc-ln/CUGRS", mine_resource_id=1,
                roi_geometry_json=json.dumps(ROI), current_feature_collection_json=fc_json,
                auto_feature_collection_json=fc_json, current_revision_no=0,
                feature_count=2, vector_status="ready",
            )
            db.session.add(newer)
            db.session.flush()
            db.session.add(ClassificationRevision(
                result_id=newer.id, revision_no=0, source="auto", author="system", feature_count=2,
                snapshot_path=f"classification-results/{newer.id}/auto.geojson",
                feature_collection_json=fc_json,
            ))
            results["newer"] = newer
        db.session.commit()
        return fid_dir, results

    def _save(self, project_id, result_id, base_revision_no, feature_collection):
        return self.client.post(
            f"/api/projects/{project_id}/classification-results/{result_id}/revisions",
            json={"base_revision_no": base_revision_no, "feature_collection": feature_collection},
        )

    def _traceability(self, project_id, fid):
        response = self.client.get(f"/api/projects/{project_id}/mines/{fid}/traceability")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self._json(response)["data"]

    @staticmethod
    def _year(payload, year):
        return next(entry for entry in payload["years"] if entry["year"] == year)

    @staticmethod
    def _matrix(fid_dir, fid=713):
        path = fid_dir.parent.parent / "change_matrix" / f"{fid}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    # ---------------------------------------------------------------- 保存 → 统计

    def test_save_writes_revision_sidecar_and_rebuilds_matrix_without_touching_ratio_file(self):
        self._login()
        project_id = self._create_project("保存派生统计项目")
        fid_dir, results = self._seed_mine(project_id)
        ratio_file = fid_dir / "class_ratio_percent.json"
        ratio_before = ratio_file.read_bytes()
        result_2024 = results[2024]

        revised = _left_right_fc(2)  # 左半草地 → 建筑：建筑/裸地各 50%
        response = self._save(project_id, result_2024.id, 0, revised)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        # 逐修订统计：按 (成果, 版本号) 寻址的 sidecar
        sidecar = _revision_stats_path(project_id, result_2024.id, 1)
        self.assertTrue(sidecar.is_file(), "保存应写出修订派生统计 sidecar")
        stored = classification_stats.read_revision_stats(project_id, result_2024.id, 1)
        self.assertEqual((stored["result_id"], stored["revision_no"]), (result_2024.id, 1))
        self.assertAlmostEqual(stored["percent"]["building"], 50.0, places=1)
        self.assertAlmostEqual(stored["percent"]["grassland"], 0.0, places=1)

        # 推理占比文件不再被保存改写——它只有一个主人（推理发布）
        self.assertEqual(ratio_file.read_bytes(), ratio_before)
        self.assertNotIn("revised", json.loads(ratio_file.read_text(encoding="utf-8")))

        # 变化矩阵按审校矢量重算，并留下回执
        matrix = self._matrix(fid_dir)
        self.assertEqual(matrix["basis"], "revised_vector")
        self.assertEqual(matrix["revised_revision_no"], {"2023": 0, "2024": 1})
        marker = json.loads(_rebuild_marker_path(project_id, 713).read_text(encoding="utf-8"))
        self.assertEqual(marker["outcome"], "written")
        self.assertEqual(marker["refs"], {"2023": [results[2023].id, 0], "2024": [result_2024.id, 1]})
        grassland_row = next(row for row in matrix["matrix"] if row["label"] == "grassland")
        self.assertAlmostEqual(grassland_row["values"][matrix["headers"].index("building")], 100.0, places=1)

    def test_traceability_reads_each_year_from_its_own_source(self):
        self._login()
        project_id = self._create_project("溯源口径项目")
        _fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))

        payload = self._traceability(project_id, 713)
        year_2024 = self._year(payload, 2024)
        self.assertAlmostEqual(year_2024["class_ratio_percent"]["building"], 50.0, places=1)
        self.assertEqual(year_2024["class_ratio_area_source"], "vector_shoelace")
        year_2023 = self._year(payload, 2023)
        # 未修订年份读推理占比原值
        self.assertAlmostEqual(year_2023["class_ratio_percent"]["grassland"], 30.0, places=1)
        self.assertEqual(year_2023["class_ratio_area_source"], "inference_mask")
        # 序列视图与逐年条目一致：2024 位是审校值、2023 位是推理值
        series = payload["ratio_series"]
        self.assertAlmostEqual(series["series_percent"]["building"][1], 50.0, places=1)
        self.assertAlmostEqual(series["series_percent"]["grassland"][0], 30.0, places=1)
        self.assertEqual(series["area_source"], "mixed")
        self.assertEqual(payload["change_matrix_state"], MATRIX_STATE_REVISED)
        self.assertIsNone(payload["change_matrix_note"])

    def test_revising_second_year_keeps_first_year_values(self):
        """审计复现：两个年份先后保存，后一次不得冲掉前一年份的人工值。"""
        self._login()
        project_id = self._create_project("两年先后修订项目")
        _fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))   # 2024：建筑/裸地
        self._save(project_id, results[2023].id, 0, _single_fc(5))       # 2023：只剩一个小水体面

        payload = self._traceability(project_id, 713)
        self.assertAlmostEqual(self._year(payload, 2024)["class_ratio_percent"]["building"], 50.0, places=1)
        self.assertAlmostEqual(self._year(payload, 2023)["class_ratio_percent"]["water"], 100.0, places=1)
        self.assertEqual(self._year(payload, 2023)["class_ratio_area_source"], "vector_shoelace")
        matrix = self._matrix(_fid_dir)
        self.assertEqual(matrix["basis"], "revised_vector")
        self.assertEqual(matrix["revised_revision_no"], {"2023": 1, "2024": 1})
        self.assertEqual(payload["change_matrix_state"], MATRIX_STATE_REVISED)

    def test_saving_older_result_of_a_year_does_not_leak_into_shared_views(self):
        """审计复现：同年有更新成果时保存较旧成果——溯源/矩阵只能反映有效成果。"""
        self._login()
        project_id = self._create_project("同年多成果项目")
        fid_dir, results = self._seed_mine(project_id, extra_2024_result=True)
        older, newer = results[2024], results["newer"]
        self.assertLess(older.id, newer.id)

        response = self._save(project_id, older.id, 0, _left_right_fc(2))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        # 较旧成果自己的修订与统计是成立的（可复查）
        self.assertTrue(_revision_stats_path(project_id, older.id, 1).is_file())

        payload = self._traceability(project_id, 713)
        year_2024 = self._year(payload, 2024)
        self.assertEqual(year_2024["result_id"], newer.id, "溯源取有效成果（较新那条）")
        self.assertEqual(year_2024["current_revision_no"], 0)
        self.assertEqual(year_2024["class_ratio_area_source"], "inference_mask")
        self.assertAlmostEqual(year_2024["class_ratio_percent"]["grassland"], 40.0, places=1)
        # 矩阵不受历史成果的修订影响：有效成果都没有人工修订 → 推理口径即正确口径
        self.assertEqual(payload["change_matrix_state"], MATRIX_STATE_INFERENCE)
        self.assertIsNone(self._matrix(fid_dir), "没有有效人工修订时不该产生审校矩阵")

    def test_history_result_revision_never_produces_a_revised_matrix(self):
        """最近两期有效成果都没有人工修订时，不产审校矩阵——"含人工修订"的口径标注不得说谎。"""
        self._login()
        project_id = self._create_project("无有效修订不产矩阵项目")
        fid_dir, results = self._seed_mine(project_id, extra_2024_result=True)
        older = results[2024]
        with patch.object(classification_stats, "compute_revised_change_matrix") as rebuild:
            self.assertFalse(classification_stats.request_change_matrix_rebuild_for_result(
                project_id, 713, 2024, older.id
            ))
            rebuild.assert_not_called()
        self.assertFalse(classification_stats.compute_revised_change_matrix(project_id, 713))
        self.assertIsNone(self._matrix(fid_dir))

    def test_clearing_all_features_is_explicit_zero_and_invalidates_matrix(self):
        """审计复现：清空后画布/质检/占比归零，变化矩阵不得仍是旧值。"""
        self._login()
        project_id = self._create_project("清空图斑项目")
        fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))
        self.assertEqual(self._matrix(fid_dir)["basis"], "revised_vector")

        response = self._save(project_id, results[2024].id, 1, {"type": "FeatureCollection", "features": []})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        payload = self._traceability(project_id, 713)
        year_2024 = self._year(payload, 2024)
        self.assertEqual(year_2024["feature_count"], 0)
        self.assertEqual(year_2024["class_ratio_percent"], {name: 0.0 for name in CLASS_NAMES})
        matrix = self._matrix(fid_dir)
        self.assertEqual(matrix["basis"], "revised_vector_invalid")
        self.assertFalse(matrix["has_change_matrix"])
        self.assertEqual(matrix["matrix"], [])
        self.assertEqual(payload["change_matrix_state"], MATRIX_STATE_REVISED_EMPTY)
        self.assertIn("图斑为空", payload["change_matrix_note"])

    def test_save_does_not_depend_on_matrix_rebuild_succeeding(self):
        """矩阵重算是派生缓存：它失败不得让一次已提交的保存变成失败。"""
        self._login()
        project_id = self._create_project("矩阵失败不影响保存项目")
        _fid_dir, results = self._seed_mine(project_id)
        with patch.object(
            classification_stats, "_rebuild_change_matrix_once",
            side_effect=RuntimeError("模拟栅格化失败"),
        ):
            response = self._save(project_id, results[2024].id, 0, _left_right_fc(2))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(ClassificationResult.query.get(results[2024].id).current_revision_no, 1)
        self.assertTrue(_revision_stats_path(project_id, results[2024].id, 1).is_file())
        payload = self._traceability(project_id, 713)
        self.assertAlmostEqual(self._year(payload, 2024)["class_ratio_percent"]["building"], 50.0, places=1)
        self.assertEqual(payload["change_matrix_state"], MATRIX_STATE_PENDING)

    # ---------------------------------------------------------------- 提交失败对账

    def test_failed_commit_removes_snapshot_and_sidecar(self):
        """审计复现：提交失败留下库里不存在的孤立 GeoJSON——现在连同 sidecar 一并清理。"""
        self._login()
        project_id = self._create_project("提交失败对账项目")
        _fid_dir, results = self._seed_mine(project_id)
        result = results[2024]
        calls = {"count": 0}

        def fail_first_commit(connection):
            calls["count"] += 1
            raise OperationalError("COMMIT", {}, RuntimeError("模拟修订提交失败"))

        event.listen(db.engine, "commit", fail_first_commit)
        try:
            response = self._save(project_id, result.id, 0, _left_right_fc(2))
        finally:
            event.remove(db.engine, "commit", fail_first_commit)
        db.session.remove()

        self.assertEqual(response.status_code, 500)
        self.assertEqual(ClassificationResult.query.get(result.id).current_revision_no, 0)
        self.assertEqual(ClassificationRevision.query.filter_by(result_id=result.id).count(), 1)
        revisions_dir = self.storage_root / "projects" / str(project_id) / "outputs" / "classification-results" / str(result.id) / "revisions"
        self.assertEqual(sorted(path.name for path in revisions_dir.iterdir()), [], "不得留下孤立快照或 sidecar")

    def test_settle_keeps_files_when_revision_actually_committed_and_when_db_unreachable(self):
        self._login()
        project_id = self._create_project("提交结局对账项目")
        _fid_dir, results = self._seed_mine(project_id)
        result = results[2024]
        from applications.project_hub.classification_results import _settle_snapshot_after_failure

        snapshot = _revision_stats_path(project_id, result.id, 1).with_name("1.geojson")
        stats = classification_stats.write_revision_stats(project_id, result.id, 1, _left_right_fc(2))
        snapshot.write_text("{}", encoding="utf-8")
        # 修订行不存在 → 文件是孤儿，清掉
        self.assertEqual(_settle_snapshot_after_failure(result, 1, snapshot, stats, True), "removed")
        self.assertFalse(snapshot.exists() or stats.exists())

        # 修订行存在（commit 抛错但实际已落库）→ 文件必须保留
        snapshot.write_text("{}", encoding="utf-8")
        classification_stats.write_revision_stats(project_id, result.id, 1, _left_right_fc(2))
        db.session.add(ClassificationRevision(
            result_id=result.id, revision_no=1, source="manual", author="admin", feature_count=2,
            snapshot_path="classification-results/1/revisions/1.geojson",
            feature_collection_json=json.dumps(_left_right_fc(2)),
        ))
        db.session.commit()
        self.assertEqual(_settle_snapshot_after_failure(result, 1, snapshot, stats, True), "committed")
        self.assertTrue(snapshot.exists() and stats.exists())

        # 数据库不可用、对不了账 → 不动文件（孤儿无害，缺失有害）
        unreachable = MagicMock()
        unreachable.filter_by.return_value.first.side_effect = OperationalError(
            "SELECT", {}, RuntimeError("模拟数据库不可用")
        )
        with patch.object(ClassificationRevision, "query", unreachable):
            self.assertEqual(_settle_snapshot_after_failure(result, 1, snapshot, stats, True), "unknown")
        self.assertTrue(snapshot.exists() and stats.exists())

    # ---------------------------------------------------------------- 矩阵：快照复核 / 待重算自愈

    def test_rebuild_discards_result_when_results_change_during_computation(self):
        """正确性靠提交前复核：计算期间又有新修订落地，本次结果必须丢弃并按新状态重算。"""
        self._login()
        project_id = self._create_project("矩阵快照复核项目")
        fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))   # 2024 → 修订 1
        real_accumulate = classification_stats._accumulate_review_matrix
        writes = []
        real_write = classification_stats._atomic_write_json
        accumulate_calls = {"count": 0}

        def save_lands_during_computation(*args, **kwargs):
            # 第一次计算进行到一半时，另一份保存落地（2024 → 修订 2）。
            # 保存模块是按名字直接导入重算入口的，补丁必须打在它的命名空间上：
            # 否则嵌套保存会在本线程里再次申请重计算槽位（外层正持有），白等到超时
            accumulate_calls["count"] += 1
            if accumulate_calls["count"] == 1:
                with patch.object(
                    classification_results_module, "request_change_matrix_rebuild_for_result",
                    return_value=False,
                ):
                    later = self._save(project_id, results[2024].id, 1, _single_fc(3))
                self.assertEqual(later.status_code, 200, later.get_data(as_text=True))
            return real_accumulate(*args, **kwargs)

        def spy_write(target_dir, filename, payload):
            writes.append(filename)
            return real_write(target_dir, filename, payload)

        with patch.object(
            classification_stats, "_accumulate_review_matrix", side_effect=save_lands_during_computation,
        ), patch.object(classification_stats, "_atomic_write_json", side_effect=spy_write):
            self.assertTrue(classification_stats.compute_revised_change_matrix(project_id, 713))

        # 第一次算的是修订 1 的矢量，复核发现已是修订 2 → 丢弃；第二次按修订 2 写出
        self.assertEqual(accumulate_calls["count"], 2)
        self.assertEqual(writes.count("713.json"), 1)
        matrix = self._matrix(fid_dir)
        self.assertEqual(matrix["revised_revision_no"], {"2023": 0, "2024": 2})

    def test_results_changing_on_every_attempt_gives_up_without_writing(self):
        """复核始终通不过（持续有新修订落地）→ 放弃且不落盘；读端仍判待重算，下次再触发。"""
        self._login()
        project_id = self._create_project("矩阵复核始终失败项目")
        fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))
        before = self._matrix(fid_dir)
        counter = {"value": 0}

        def ever_changing(*_args, **_kwargs):
            counter["value"] += 1
            return {2023: (results[2023].id, 0), 2024: (results[2024].id, counter["value"])}

        with patch.object(
            classification_stats, "_effective_result_refs_by_year", side_effect=ever_changing
        ), patch.object(classification_stats, "_atomic_write_json") as spy_write:
            self.assertFalse(classification_stats.compute_revised_change_matrix(project_id, 713, max_attempts=4))
        self.assertEqual(spy_write.call_count, 0, "复核通不过就不得写矩阵或回执")
        self.assertEqual(self._matrix(fid_dir), before, "已有矩阵保持原样")

    def test_traceability_heals_a_lost_rebuild(self):
        """进程重启丢了后台任务 / 推理发布刚覆盖了矩阵：读端发现待重算就补请求并自愈。"""
        self._login()
        project_id = self._create_project("待重算自愈项目")
        fid_dir, results = self._seed_mine(project_id)
        self._save(project_id, results[2024].id, 0, _left_right_fc(2))
        # 模拟推理发布整文件覆盖矩阵（回执还在，内容对不上）
        self._matrix(fid_dir)
        (fid_dir.parent.parent / "change_matrix" / "713.json").write_text(
            json.dumps({"basis": "inference", "matrix": []}), encoding="utf-8"
        )

        first = self._traceability(project_id, 713)
        self.assertEqual(first["change_matrix_state"], MATRIX_STATE_PENDING)
        self.assertIn("重算", first["change_matrix_note"])
        second = self._traceability(project_id, 713)
        self.assertEqual(second["change_matrix_state"], MATRIX_STATE_REVISED)
        self.assertEqual(self._matrix(fid_dir)["basis"], "revised_vector")

    def test_background_queue_coalesces_requests_and_survives_failures(self):
        """生产路径：重算在后台单线程里跑，重复请求合并，单次失败不杀死线程。"""
        self._login()
        project_id = self._create_project("后台重算队列项目")
        self._seed_mine(project_id)
        ran = []

        def fake_rebuild(pid, fid):
            ran.append((pid, fid))
            if fid == 999:
                raise RuntimeError("模拟单矿重算失败")

        self.app.config["TESTING"] = False
        try:
            with patch.object(classification_stats, "compute_revised_change_matrix", side_effect=fake_rebuild):
                self.assertIsNone(classification_stats.request_change_matrix_rebuild(project_id, 713))
                self.assertIsNone(classification_stats.request_change_matrix_rebuild(project_id, 713))
                self.assertIsNone(classification_stats.request_change_matrix_rebuild(project_id, 999))
                self.assertIsNone(classification_stats.request_change_matrix_rebuild(project_id, 714))
                deadline = time.time() + 10
                while classification_stats._REBUILD_QUEUE.queued and time.time() < deadline:
                    time.sleep(0.02)
                self.assertEqual(classification_stats._REBUILD_QUEUE.queued, set(), "队列应排空")
        finally:
            self.app.config["TESTING"] = True
        self.assertEqual(sorted(ran), [(project_id, 713), (project_id, 714), (project_id, 999)])
        self.assertEqual(ran.count((project_id, 713)), 1, "同一座矿排队期间的重复请求只算一次")

    # ---------------------------------------------------------------- 趋势面积

    def _seed_trend_mine(self, project_id, fid, area_m2):
        features = [{
            "type": "Feature",
            "properties": {"FID_1": fid, "Name": f"矿山{fid}", "TBTYMJ": area_m2},
            "geometry": {"type": "Polygon", "coordinates": [
                [[100.0, 25.0], [100.01, 25.0], [100.0, 25.01], [100.0, 25.0]],
            ]},
        }]
        mine_dir = self.storage_root / "projects" / str(project_id) / "mines" / "1"
        mine_dir.mkdir(parents=True)
        (mine_dir / "mines.geojson").write_text(
            json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
        )
        db.session.add(ProjectSpatialResource(
            project_id=project_id, resource_type="mine_vector", version=1, status="active",
            source_path=f"projects/{project_id}/mines/1/mines.geojson",
            normalized_path=f"projects/{project_id}/mines/1/mines.geojson",
            source_format="geojson", bounds_json=json.dumps([100.0, 25.0, 100.01, 25.01]),
        ))
        db.session.commit()

    def test_trend_uses_absolute_revision_area_instead_of_share_times_mine_area(self):
        """审计复现：删到只剩一个小水体面 → 旧算法把整矿算成水体。"""
        from applications.project_hub.project_map import get_project_trend_report

        self._login()
        project_id = self._create_project("趋势面积分母项目")
        fid_dir, results = self._seed_mine(project_id)
        self._seed_trend_mine(project_id, 713, 1_000_000.0)  # 矿山面积 1 km²
        self._save(project_id, results[2024].id, 0, _single_fc(5))  # 只剩 0.001°×0.001° 的水体面

        report = get_project_trend_report(project_id, None, class_name="water", direction="all")
        row = next(item for item in report["tables"]["selected_class_rows"] if item["fid"] == 713)
        self.assertAlmostEqual(row["end_percent"], 100.0, places=2)
        # 旧算法：100% × 1 km² = 1.0；新算法取图斑自身面积 ≈ 0.0123 km²
        self.assertLess(row["end_area_km2"], 0.02)
        self.assertGreater(row["end_area_km2"], 0.01)
        # 未修订年份仍为推理占比 × 矿山面积（水体 20% → 0.2 km²）
        self.assertAlmostEqual(row["start_area_km2"], 0.2, places=3)
        self.assertEqual(fid_dir.joinpath("class_ratio_percent.json").is_file(), True)


if __name__ == "__main__":
    unittest.main()
