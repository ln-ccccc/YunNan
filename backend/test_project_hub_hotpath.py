# -*- coding: utf-8 -*-
"""问题23 回归（2026-11-14 审查）：概览/资产/项目列表热路径的查询边界。

被测行为：
1. list_project_assets 加载成果时不得选取 MEDIUMTEXT 大字段
   （auto/current_feature_collection_json、roi_geometry_json）。
2. list_projects 的要素合计与最近推理在 SQL 聚合完成，不整表加载
   InferenceJob 实体（result_json/warnings_json/request_payload_json 同为 MEDIUMTEXT）。
3. 概览/详情的最近活动带 LIMIT，不再全量加载关系集合后 Python 切片。
4. 项目列表的矿山/数据集计数批量预加载，不产生每项目一条的全量查询。
"""

import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from sqlalchemy import event

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.inference_job import InferenceJob
from applications.models.project import (
    Project,
    ProjectActivityLog,
    ProjectDataset,
    ProjectMineBinding,
)
from applications.project_hub.assets import list_project_assets
from applications.project_hub.service import (
    get_project_detail,
    get_project_overview,
    list_projects,
)

HEAVY_COLUMNS = (
    "auto_feature_collection_json",
    "current_feature_collection_json",
    "roi_geometry_json",
    "result_json",
    "warnings_json",
    "request_payload_json",
)


class _SqlCapture:
    """捕获 before_cursor_execute 的语句文本，用于断言查询边界。"""

    def __init__(self, engine):
        self.engine = engine
        self.statements = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)

    def touching(self, table_name):
        return [s for s in self.statements if table_name in s]


class TestProjectHubHotPath(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="project-hotpath-")
        self.previous_env = {
            key: os.environ.get(key)
            for key in ("PROJECT_STORAGE_ROOT", "ADMIN_USERNAME", "ADMIN_PASSWORD")
        }
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self._restore_environment)
        storage_root = Path(self.temp_dir.name) / "project_storage"
        storage_root.mkdir()
        os.environ["PROJECT_STORAGE_ROOT"] = str(storage_root)
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "Secret123!"

        self.app = create_app("testing")
        self.assertEqual(self.app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.app.config["PROPAGATE_EXCEPTIONS"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore_environment(self):
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    @staticmethod
    def _at(hour, minute=0, second=0):
        return datetime.datetime(2026, 11, 14, hour, minute, second)

    def _seed_project(self, project_id, name):
        project = Project(
            id=project_id,
            name=name,
            region="云南省",
            status="active",
            create_time=self._at(0),
            update_time=self._at(0),
        )
        db.session.add(project)
        return project

    def _seed_result(self, project_id, mine_fid, year, feature_count, job_suffix):
        db.session.add(
            ClassificationResult(
                project_id=project_id,
                mine_fid=mine_fid,
                year=year,
                inference_job_id=f"job-{job_suffix}",
                model_id="test-model",
                mine_resource_id=1,
                # 大字段填足体积：断言依赖"热路径确实会拉它们"这一前提
                roi_geometry_json='{"type": "Polygon"}',
                auto_feature_collection_json='{"features": ["' + "a" * 8000 + '"]}',
                current_feature_collection_json='{"features": ["' + "b" * 8000 + '"]}',
                current_revision_no=1,
                feature_count=feature_count,
                vector_status="ready",
                create_time=self._at(1),
                update_time=self._at(1),
            )
        )

    def _seed_job(self, job_id, project_id, status, create_time, update_time):
        db.session.add(
            InferenceJob(
                id=job_id,
                project_id=project_id,
                status=status,
                request_payload_json="{}",
                create_time=create_time,
                update_time=update_time,
            )
        )

    def _seed_activity(self, project_id, index, create_time):
        db.session.add(
            ProjectActivityLog(
                project_id=project_id,
                event_type="project_updated",
                actor="admin",
                payload_json='{"target": {"type": "project", "id": "%d"}, "result": "success", "seq": %d}'
                % (project_id, index),
                create_time=create_time,
            )
        )

    def test_list_project_assets_skips_geojson_columns(self):
        self._seed_project(1, "热路径项目")
        self._seed_result(1, 7, 2024, 12, "a")
        db.session.commit()

        project = db.session.get(Project, 1)
        db.session.expire_all()
        with _SqlCapture(db.engine) as capture:
            list_project_assets(project)

        offenders = [
            column
            for statement in capture.statements
            for column in HEAVY_COLUMNS[:3]
            if column in statement
        ]
        self.assertEqual(offenders, [], "资产读模型不得加载成果 MEDIUMTEXT 大字段")

    def test_list_projects_feature_totals_latest_per_fid_year(self):
        self._seed_project(1, "项目一")
        self._seed_project(2, "项目二")
        # (fid,year) 分组取最新 id 行：fid7/2024 两行取 fc=7；fid7/2025=3；fid8/2024=4 ⇒ 14
        self._seed_result(1, 7, 2024, 5, "old")
        self._seed_result(1, 7, 2024, 7, "new")
        self._seed_result(1, 7, 2025, 3, "b")
        self._seed_result(1, 8, 2024, 4, "c")
        self._seed_result(2, 7, 2024, 6, "d")
        db.session.commit()

        payload = list_projects()
        features = {item["id"]: item["feature_count"] for item in payload["items"]}
        self.assertEqual(features[1], 14)
        self.assertEqual(features[2], 6)

    def test_list_projects_does_not_load_heavy_columns_or_full_jobs(self):
        self._seed_project(1, "项目一")
        self._seed_project(2, "项目二")
        self._seed_result(1, 7, 2024, 5, "a")
        self._seed_job("job-a", 1, "succeeded", self._at(10), self._at(10))
        self._seed_job("job-b", 1, "failed", self._at(11), self._at(11))
        self._seed_job("job-c", 2, "queued", self._at(9), self._at(9))
        db.session.commit()

        db.session.expire_all()
        with _SqlCapture(db.engine) as capture:
            list_projects()

        offenders = [
            column
            for statement in capture.statements
            for column in HEAVY_COLUMNS
            if column in statement
        ]
        self.assertEqual(
            offenders, [], "项目列表不得加载成果/任务实体的 MEDIUMTEXT 大字段"
        )

    def test_list_projects_latest_inference_per_project(self):
        self._seed_project(1, "项目一")
        self._seed_project(2, "项目二")
        self._seed_job("job-a", 1, "succeeded", self._at(10), self._at(10))
        self._seed_job("job-b", 1, "failed", self._at(11), self._at(11))
        self._seed_job("job-c", 2, "queued", self._at(9), self._at(9))
        db.session.commit()

        payload = list_projects()
        latest = {item["id"]: item["latest_inference"] for item in payload["items"]}
        self.assertEqual(latest[1]["job_id"], "job-b")
        self.assertEqual(latest[1]["status"], "failed")
        self.assertEqual(latest[2]["job_id"], "job-c")

    def test_list_projects_latest_inference_same_second_tie_is_deterministic(self):
        self._seed_project(3, "同秒并列项目")
        # 同一 create_time 并列时取 update_time 较新的行，不依赖数据库返回顺序
        self._seed_job("job-d", 3, "queued", self._at(12), self._at(12, 0, 1))
        self._seed_job("job-e", 3, "running", self._at(12), self._at(12, 0, 2))
        db.session.commit()

        payload = list_projects()
        latest = {item["id"]: item["latest_inference"] for item in payload["items"]}
        self.assertEqual(latest[3]["job_id"], "job-e")

    def test_overview_activity_queries_are_limited(self):
        self._seed_project(1, "活动项目")
        for index in range(50):
            self._seed_activity(1, index, self._at(0, 0, 49 - index))
        db.session.commit()

        db.session.expire_all()
        with _SqlCapture(db.engine) as capture:
            get_project_overview(1)

        unlimited = [
            statement
            for statement in capture.touching("project_activity_log")
            if "LIMIT" not in statement.upper()
        ]
        self.assertEqual(
            unlimited, [], "概览的活动查询必须带 LIMIT，不得全量加载后 Python 切片"
        )

    def test_detail_recent_activity_capped_at_20(self):
        self._seed_project(1, "活动项目")
        for index in range(50):
            self._seed_activity(1, index, self._at(0, 0, 49 - index))
        db.session.commit()

        detail = get_project_detail(1)
        self.assertEqual(len(detail["recent_activity"]), 20)

        overview = get_project_overview(1)
        self.assertEqual(len(overview["recent_activity"]), 20)

    def test_summary_latest_activity_matches_newest_row(self):
        self._seed_project(1, "活动项目")
        self._seed_activity(1, 0, self._at(1))
        self._seed_activity(1, 1, self._at(5))
        self._seed_activity(1, 2, self._at(3))
        db.session.commit()

        payload = list_projects()
        item = next(row for row in payload["items"] if row["id"] == 1)
        latest_at = datetime.datetime.fromisoformat(
            item["latest_activity_at"].replace("Z", "+00:00")
        ).replace(tzinfo=None)
        self.assertEqual(latest_at, self._at(5))

    def test_list_projects_batches_mine_and_dataset_counts(self):
        for project_id in (1, 2, 3):
            self._seed_project(project_id, f"项目{project_id}")
            db.session.add(
                ProjectMineBinding(project_id=project_id, mine_fid=project_id * 10)
            )
            db.session.add(
                ProjectDataset(
                    project_id=project_id,
                    dataset_kind="imagery",
                    display_name=f"影像{project_id}",
                    file_path=f"incoming/p{project_id}/a.tif",
                    source_format="tif",
                )
            )
        db.session.commit()

        db.session.expire_all()
        with _SqlCapture(db.engine) as capture:
            payload = list_projects()

        self.assertEqual(len(payload["items"]), 3)
        mine_queries = capture.touching("project_mine_binding")
        dataset_queries = capture.touching("project_dataset")
        self.assertEqual(
            len(mine_queries), 1, "矿山计数应批量预加载，不逐项目全量查询"
        )
        self.assertEqual(
            len(dataset_queries), 1, "数据集计数应批量预加载，不逐项目全量查询"
        )


if __name__ == "__main__":
    unittest.main()
