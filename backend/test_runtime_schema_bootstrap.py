# -*- coding: utf-8 -*-
"""runtime_schema 启动 bootstrap 的并发安全与回填可重入（2026-11-14 审查问题27）。

多启动进程（backend Flask 与 inference Worker）同时"发现缺列→添加列"的
竞争由命名锁 + 幂等 DDL 收口；feature_count 回填与补列解耦——列存在
不代表回填完成，中断后下次启动无条件重跑收敛，不静默遗漏。
"""
import json
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from sqlalchemy import inspect, text

from applications import create_app
from applications.extensions import db

FEATURES_2 = json.dumps({"features": [{}, {}]}, ensure_ascii=False)
FEATURES_0 = json.dumps({"features": []}, ensure_ascii=False)


class TestRuntimeSchemaBootstrap(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _seed_result(self, *, mine_fid=7, features_json=FEATURES_2, feature_count=0):
        from applications.models.classification_result import ClassificationResult

        result = ClassificationResult(
            project_id=1,
            mine_fid=mine_fid,
            year=2025,
            inference_job_id="job-bootstrap",
            model_id="cc-ln/CUGRS",
            mine_resource_id=3,
            current_feature_collection_json=features_json,
            feature_count=feature_count,
        )
        db.session.add(result)
        db.session.commit()
        return result.id

    def _drop_feature_count_column(self):
        db.session.execute(text("ALTER TABLE classification_results DROP COLUMN feature_count"))
        db.session.commit()
        db.session.expire_all()

    def _feature_count_values(self):
        from applications.models.classification_result import ClassificationResult

        return sorted(r.feature_count for r in ClassificationResult.query.all())

    def test_backfill_reruns_when_column_already_exists(self):
        # 回填中断场景：列已存在（补列后中断或新库直建），feature_count
        # 仍是默认 0——不能以"列存在"为完成标志，启动必须重跑回填收敛
        from applications.runtime_schema import ensure_runtime_schema

        self._seed_result(mine_fid=7, features_json=FEATURES_2, feature_count=0)
        self._seed_result(mine_fid=8, features_json=FEATURES_0, feature_count=5)

        ensure_runtime_schema()

        self.assertEqual(self._feature_count_values(), [0, 2])

    def test_missing_column_is_added_and_backfilled(self):
        # 存量库升级路径：补列 + 回填一次完成；重复启动幂等
        from applications.runtime_schema import ensure_runtime_schema

        result_id = self._seed_result(feature_count=0)
        self._drop_feature_count_column()

        ensure_runtime_schema()
        ensure_runtime_schema()

        from applications.models.classification_result import ClassificationResult

        self.assertEqual(db.session.get(ClassificationResult, result_id).feature_count, 2)
        columns = {c["name"] for c in inspect(db.engine).get_columns("classification_results")}
        self.assertIn("feature_count", columns)

    def test_duplicate_ddl_from_concurrent_bootstrap_is_tolerated(self):
        # 并行启动竞争的另一结局：检查缺列后、ALTER 落地前另一进程已抢先
        # 建列——重复 DDL 错误必须视为成功而非启动失败
        from applications.runtime_schema import _execute_idempotent_ddl

        _execute_idempotent_ddl(
            text("ALTER TABLE classification_results ADD COLUMN feature_count INTEGER NOT NULL DEFAULT 0")
        )
        _execute_idempotent_ddl(text("CREATE INDEX ix_inference_jobs_project_id ON inference_jobs (project_id)"))

        columns = {c["name"] for c in inspect(db.engine).get_columns("classification_results")}
        self.assertIn("feature_count", columns)


if __name__ == "__main__":
    unittest.main()
