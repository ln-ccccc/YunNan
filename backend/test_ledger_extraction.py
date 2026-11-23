# -*- coding: utf-8 -*-
"""台账抽取（2026-11-24）单元测试：纯解析函数、切片并行、非法 JSON 回退、SQLite 回退路径。

MySQL 侧的并行抽取无法在 SQLite 测试环境里执行，生产库上的行为由活体探针验证
（项目 10：类别码约 3 秒、任务计时约 8 秒，原为 14 秒与 39 秒）。这里覆盖与方言无关的部分：
- MySQL 把多路径 JSON_EXTRACT 的结果摊平、缺失路径不占位——按类型拆分计时；
- 类别名称由类别码按固定类别表推出；
- 按 id 均分切片、结果按原顺序合并、只有一片时不启线程；
- 只有 3141（非法 JSON 文本）才退回带校验的慢路径，其余异常原样抛出。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.inference_job import InferenceJob
from applications.models.project import Project
from applications.project_hub import ledger


def _fc(*class_codes):
    return json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"class_code": code, "class_name": name}, "geometry": None}
        for code, name in ((0, "grassland"), (2, "building"), (5, "water"))
    ][:0] + [{"type": "Feature", "properties": {"class_code": code}, "geometry": None} for code in class_codes]})


class TimingsFromExtractedTests(unittest.TestCase):
    def test_object_is_stage_durations_and_number_is_total(self):
        # MySQL 把 '$.stage_durations' 与 '$.total_seconds' 两个路径的结果摊平成一个数组
        self.assertEqual(ledger._timings_from_extracted('[{"inference": 2.187, "cut": 0.3}, 2.588]'), (2.187, 2.588))

    def test_missing_path_leaves_no_placeholder(self):
        self.assertEqual(ledger._timings_from_extracted('[{"inference": 1.5}]'), (1.5, None))
        self.assertEqual(ledger._timings_from_extracted('[7.5]'), (None, 7.5))
        self.assertEqual(ledger._timings_from_extracted('[{"cut": 0.2}]'), (None, None))

    def test_null_and_garbage_are_empty(self):
        self.assertEqual(ledger._timings_from_extracted(None), (None, None))
        self.assertEqual(ledger._timings_from_extracted("null"), (None, None))
        self.assertEqual(ledger._timings_from_extracted("{not json"), (None, None))
        # 数字字符串按原实现的宽容口径解析；布尔值不算数值
        self.assertEqual(ledger._timings_from_extracted('[{"inference": "2.5"}, "9"]'), (2.5, 9.0))
        self.assertEqual(ledger._timings_from_extracted('[{"inference": true}, false]'), (None, None))


class PairsFromCodesTests(unittest.TestCase):
    def test_names_come_from_the_fixed_class_table(self):
        self.assertEqual(
            ledger._pairs_from_codes("[1, 2, 2, 0]"),
            [(1, "forest"), (2, "building"), (2, "building"), (0, "grassland")],
        )

    def test_empty_missing_and_unknown(self):
        self.assertEqual(ledger._pairs_from_codes("null"), [])
        self.assertEqual(ledger._pairs_from_codes(None), [])
        self.assertEqual(ledger._pairs_from_codes("5"), [(5, "water")])
        self.assertEqual(ledger._pairs_from_codes("[9]"), [(9, None)])
        self.assertEqual(ledger._pairs_from_codes("{not json"), [])


class QueryInShardsTests(unittest.TestCase):
    def test_ids_are_split_evenly_and_results_keep_order(self):
        calls = []

        def fake_fetch(engine, statement, params):
            calls.append(list(params["ids"]))
            return [(identifier,) for identifier in params["ids"]]

        with patch.object(ledger, "LEDGER_EXTRACT_WORKERS", 4), patch.object(ledger, "_fetch_rows", fake_fetch):
            rows = ledger._query_in_shards(object(), lambda shard: ("sql", {"ids": shard}), list(range(10)))
        self.assertEqual([row[0] for row in rows], list(range(10)))
        self.assertEqual([len(shard) for shard in calls], [3, 3, 2, 2])
        self.assertEqual(sorted(identifier for shard in calls for identifier in shard), list(range(10)))

    def test_single_shard_or_empty_ids_does_not_start_threads(self):
        calls = []

        def fake_fetch(engine, statement, params):
            calls.append(list(params["ids"]))
            return [(params["ids"][0],)]

        with patch.object(ledger, "LEDGER_EXTRACT_WORKERS", 4), patch.object(ledger, "_fetch_rows", fake_fetch), \
                patch.object(ledger, "ThreadPoolExecutor", side_effect=AssertionError("不应启用线程池")):
            self.assertEqual(ledger._query_in_shards(object(), lambda shard: ("sql", {"ids": shard}), ["a"]), [("a",)])
            self.assertEqual(ledger._query_in_shards(object(), lambda shard: ("sql", {"ids": shard}), []), [])
        self.assertEqual(calls, [["a"]])


class InvalidJsonFallbackTests(unittest.TestCase):
    @staticmethod
    def _error(code):
        error = RuntimeError("statement failed")
        error.orig = type("Orig", (), {"args": (code, "Invalid JSON text: ...")})()
        return error

    def test_invalid_json_text_retries_with_the_guarded_query(self):
        guarded_flags = []

        def fake_shards(engine, build_query, ids):
            guarded_flags.append(build_query(["x"])[0])
            if len(guarded_flags) == 1:
                raise self._error(3141)
            return [("ok",)]

        with patch.object(ledger, "_query_in_shards", fake_shards):
            rows = ledger._extract_with_json_fallback(
                object(), lambda shard, guarded: (f"sql guarded={guarded}", {"ids": shard}), ["x"]
            )
        self.assertEqual(rows, [("ok",)])
        self.assertEqual(guarded_flags, ["sql guarded=False", "sql guarded=True"])

    def test_other_errors_propagate_without_retry(self):
        def fake_shards(engine, build_query, ids):
            raise self._error(1205)  # 锁等待超时等：不是非法 JSON，不应被吞掉

        with patch.object(ledger, "_query_in_shards", fake_shards):
            with self.assertRaises(RuntimeError):
                ledger._extract_with_json_fallback(object(), lambda shard, guarded: ("sql", {}), ["x"])

    def test_error_without_orig_is_not_treated_as_invalid_json(self):
        self.assertFalse(ledger._is_invalid_json_error(RuntimeError("plain")))
        self.assertFalse(ledger._is_invalid_json_error(self._error(1064)))


class SqliteFallbackPathTests(unittest.TestCase):
    """非 MySQL 方言：任务计时逐个读取、类别计数只取两列后进程内解析。"""

    def setUp(self):
        self.previous = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ledger-extract-")
        self.addCleanup(self.temp_dir.cleanup)
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp_dir.name
        self.addCleanup(self._restore)
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.drop_all)
        self.addCleanup(db.session.remove)
        db.create_all()

    def _restore(self):
        if self.previous is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous

    def test_class_counts_skip_results_without_vectors(self):
        project = Project(name="台账", region="昆明")
        db.session.add(project)
        db.session.flush()
        rows = [
            ClassificationResult(project_id=project.id, mine_fid=1, year=2024, inference_job_id="j1",
                                 model_id="m", mine_resource_id=1, current_feature_collection_json=_fc(1, 2, 2),
                                 auto_feature_collection_json=_fc(1), current_revision_no=0, feature_count=3,
                                 vector_status="ready"),
            ClassificationResult(project_id=project.id, mine_fid=1, year=2023, inference_job_id="j2",
                                 model_id="m", mine_resource_id=1, current_feature_collection_json=None,
                                 auto_feature_collection_json=None, current_revision_no=0, feature_count=0,
                                 vector_status="vector_failed"),
        ]
        db.session.add_all(rows)
        db.session.commit()
        counts = ledger._prefetch_class_counts(project.id)
        self.assertEqual(counts, {rows[0].id: {(1, "forest"): 1, (2, "building"): 2}})

    def test_job_timings_match_the_stored_result_json(self):
        project = Project(name="台账2", region="昆明")
        db.session.add(project)
        db.session.flush()
        good = InferenceJob(id="11111111-1111-4111-8111-111111111111", project_id=project.id,
                            request_payload_json="{}", result_json=json.dumps(
                                {"stage_durations": {"inference": 2.5, "cut": 0.2}, "total_seconds": 3.0}))
        only_total = InferenceJob(id="22222222-2222-4222-8222-222222222222", project_id=project.id,
                                  request_payload_json="{}", result_json=json.dumps({"total_seconds": 9}))
        broken = InferenceJob(id="33333333-3333-4333-8333-333333333333", project_id=project.id,
                              request_payload_json="{}", result_json="{not json")
        db.session.add_all([good, only_total, broken])
        db.session.commit()
        timings = ledger._prefetch_job_timings([good.id, only_total.id, broken.id, None, good.id])
        self.assertEqual(timings[good.id], (2.5, 3.0))
        self.assertEqual(timings[only_total.id], (None, 9.0))
        # 坏 JSON 的任务不进缓存；取值侧按空值处理，两种表达等价
        self.assertNotIn(broken.id, timings)
        self.assertEqual(ledger._job_timings(broken.id, timings), (None, None))
        self.assertEqual(len(timings), 2)  # 重复传入的 id 只算一次


if __name__ == "__main__":
    unittest.main()
