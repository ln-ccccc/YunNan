# -*- coding: utf-8 -*-
"""台账抽取（2026-11-24）单元测试：纯解析函数、切片并行、非法 JSON 回退、SQLite 回退路径。

MySQL 侧的并行抽取无法在 SQLite 测试环境里执行，生产库上的行为由活体探针验证
（项目 10：类别码约 3 秒、任务计时约 8 秒，原为 14 秒与 39 秒）。这里覆盖与方言无关的部分：
- MySQL 把多路径 JSON_EXTRACT 的结果摊平、缺失路径不占位——按类型拆分计时；
- 类别名称由类别码按固定类别表推出；
- 按 id 均分切片、结果按原顺序合并、只有一片时不启线程；
- 只有 3141（非法 JSON 文本）才退回带校验的慢路径，其余异常原样抛出。
"""
import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.inference_job import InferenceJob
from applications.models.project import Project
from applications.project_hub import derived_cache, ledger


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


class BudgetHintTests(unittest.TestCase):
    def test_hint_carries_the_remaining_budget_right_after_select(self):
        hint = ledger._mysql_budget_hint(time.monotonic() + 30)
        self.assertRegex(hint, r"^/\*\+ MAX_EXECUTION_TIME\(\d+\) \*/$")
        milliseconds = int(re.search(r"\((\d+)\)", hint).group(1))
        self.assertTrue(25000 <= milliseconds <= 30000)

    def test_expired_budget_raises_instead_of_querying(self):
        with self.assertRaises(ledger.LedgerBudgetExceeded):
            ledger._mysql_budget_hint(time.monotonic() - 1)


class StatementTimeoutTests(unittest.TestCase):
    @staticmethod
    def _error(code):
        error = RuntimeError("statement failed")
        error.orig = type("Orig", (), {"args": (code, "...")})()
        return error

    def test_database_statement_timeout_becomes_budget_exceeded_without_retry(self):
        attempts = []

        def fake_shards(engine, build_query, ids):
            attempts.append(1)
            raise StatementTimeoutTests._error(3024)

        with patch.object(ledger, "_query_in_shards", fake_shards):
            with self.assertRaises(ledger.LedgerBudgetExceeded):
                ledger._extract_with_json_fallback(object(), lambda shard, guarded: ("sql", {}), ["x"])
        self.assertEqual(len(attempts), 1, "超时不应再退回更慢的带校验路径")


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

    def test_mysql_statements_carry_the_budget_hint(self):
        """MySQL 路径的语句必须把剩余预算下推给数据库(SQLite 路径不带提示)。"""
        project = Project(name="预算提示", region="昆明")
        db.session.add(project)
        db.session.flush()
        db.session.add(ClassificationResult(
            project_id=project.id, mine_fid=1, year=2024, inference_job_id="j1", model_id="m",
            mine_resource_id=1, current_feature_collection_json=_fc(0, 1), auto_feature_collection_json=_fc(0),
            current_revision_no=0, feature_count=2, vector_status="ready",
        ))
        db.session.commit()
        with self.app.app_context():
            captured = []

            def fake_shards(engine, build_query, ids):
                captured.append(build_query(["x"]))  # 回退封装后只收切片参数
                return [(ClassificationResult.query.one().id, "[0, 1]")]  # MySQL 驱动回传整数主键

            dialect = db.engine.dialect  # name 是方言实例上的属性, 补丁要打在实例上
            with patch.object(ledger, "_query_in_shards", fake_shards), patch.object(dialect, "name", "mysql"):
                counts = ledger._prefetch_class_counts(project.id, deadline=time.monotonic() + 60)
            self.assertEqual(captured and captured[0][0] is not None, True)
            sql = str(captured[0][0])
            self.assertTrue(sql.startswith("SELECT /*+ MAX_EXECUTION_TIME("), sql[:60])
            result_id = ClassificationResult.query.one().id
            self.assertEqual(counts, {result_id: {(0, "grassland"): 1, (1, "forest"): 1}})


class LedgerDerivedCacheTests(unittest.TestCase):
    """台账抽取结果的派生缓存（2026-11-24）：项目 10 的 585 个任务要让 MySQL 解析 1.42GB 文本、
    四个核满载约 8 秒，成果和任务一结束就不再变，不该每次导出都重来。关闭缓存时两个预取的
    行为与此前完全一致（上面的用例即是）；这里验证打开缓存后结果相同、且只重算变过的。"""

    def setUp(self):
        self.previous = os.environ.get("PROJECT_STORAGE_ROOT")
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ledger-cache-")
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

    def _cache_file(self, project, filename):
        return Path(self.temp_dir.name) / "projects" / str(project.id) / "outputs" / "stats" / filename

    def _seed(self):
        project = Project(name="台账缓存", region="昆明")
        db.session.add(project)
        db.session.flush()
        first = ClassificationResult(
            project_id=project.id, mine_fid=1, year=2024, inference_job_id="job-first", model_id="m",
            mine_resource_id=1, current_feature_collection_json=_fc(1, 2, 2), auto_feature_collection_json=_fc(1),
            current_revision_no=0, feature_count=3, vector_status="ready",
        )
        second = ClassificationResult(
            project_id=project.id, mine_fid=2, year=2024, inference_job_id="job-second", model_id="m",
            mine_resource_id=1, current_feature_collection_json=_fc(0), auto_feature_collection_json=_fc(0),
            current_revision_no=0, feature_count=1, vector_status="ready",
        )
        failed = ClassificationResult(
            project_id=project.id, mine_fid=3, year=2024, inference_job_id="job-running", model_id="m",
            mine_resource_id=1, current_feature_collection_json=None, auto_feature_collection_json=None,
            current_revision_no=0, feature_count=0, vector_status="vector_failed",
        )
        done = InferenceJob(
            id="job-first", project_id=project.id, status="succeeded", request_payload_json="{}",
            result_json=json.dumps({"stage_durations": {"inference": 2.5, "cut": 0.2}, "total_seconds": 3.0}),
            finished_at=datetime(2026, 11, 1, 10, 0, 0),
        )
        no_result = InferenceJob(
            id="job-second", project_id=project.id, status="failed", request_payload_json="{}",
            result_json=None, finished_at=datetime(2026, 11, 1, 10, 0, 0),
        )
        running = InferenceJob(id="job-running", project_id=project.id, status="running", request_payload_json="{}")
        db.session.add_all([first, second, failed, done, no_result, running])
        db.session.commit()
        return project, first, second, failed, done, no_result, running

    def _track_extractions(self):
        return (
            patch.object(ledger, "_extract_class_counts", wraps=ledger._extract_class_counts),
            patch.object(ledger, "_extract_job_timings", wraps=ledger._extract_job_timings),
        )

    def test_cached_result_equals_uncached_and_unchanged_inputs_are_not_extracted_again(self):
        project, first, second, failed, done, no_result, running = self._seed()
        job_ids = [done.id, no_result.id, running.id]
        full_counts = ledger._prefetch_class_counts(project.id)
        full_timings = ledger._prefetch_job_timings(job_ids)
        self.assertEqual(full_timings, {done.id: (2.5, 3.0)})
        counts_file = self._cache_file(project, ledger._CLASS_COUNTS_CACHE_FILE)
        timings_file = self._cache_file(project, ledger._JOB_TIMINGS_CACHE_FILE)

        counts_patch, timings_patch = self._track_extractions()
        with counts_patch as extract_counts, timings_patch as extract_timings:
            self.assertEqual(ledger._prefetch_class_counts(project.id, cache_file=counts_file), full_counts)
            self.assertEqual(ledger._prefetch_job_timings(job_ids, cache_file=timings_file), full_timings)
            # 首次没有缓存：全部抽取
            self.assertEqual(sorted(extract_counts.call_args_list[-1].args[0]), sorted([first.id, second.id, failed.id]))
            self.assertEqual(extract_timings.call_args_list[-1].args[0], job_ids)
            again_counts = ledger._prefetch_class_counts(project.id, cache_file=counts_file)
            again_timings = ledger._prefetch_job_timings(job_ids, cache_file=timings_file)
        self.assertEqual((again_counts, again_timings), (full_counts, full_timings))
        self.assertEqual(extract_counts.call_args_list[-1].args[0], [], "成果没变：没有需要抽取的")
        # 没有结果的任务也记下了，不会被一次又一次地取回
        self.assertEqual(extract_timings.call_args_list[-1].args[0], [])

    def test_only_changed_inputs_are_extracted_again(self):
        project, first, second, failed, done, no_result, running = self._seed()
        job_ids = [done.id, no_result.id, running.id]
        counts_file = self._cache_file(project, ledger._CLASS_COUNTS_CACHE_FILE)
        timings_file = self._cache_file(project, ledger._JOB_TIMINGS_CACHE_FILE)
        ledger._prefetch_class_counts(project.id, cache_file=counts_file)
        ledger._prefetch_job_timings(job_ids, cache_file=timings_file)

        # 第二条成果保存了新修订；运行中的任务结束并写入结果（finish_job 的写法：状态、结果、完成时间一起变）
        second.current_feature_collection_json = _fc(0, 0, 5)
        second.current_revision_no = 1
        second.feature_count = 3
        running.status = "succeeded"
        running.result_json = json.dumps({"stage_durations": {"inference": 7.0}, "total_seconds": 8.5})
        running.finished_at = datetime(2026, 11, 1, 11, 0, 0)
        db.session.commit()
        db.session.expire_all()

        counts_patch, timings_patch = self._track_extractions()
        with counts_patch as extract_counts, timings_patch as extract_timings:
            counts = ledger._prefetch_class_counts(project.id, cache_file=counts_file)
            timings = ledger._prefetch_job_timings(job_ids, cache_file=timings_file)
        self.assertEqual(extract_counts.call_args_list[-1].args[0], [second.id], "只重算变过的那条成果")
        self.assertEqual(extract_timings.call_args_list[-1].args[0], [running.id], "只重算变过的那个任务")
        self.assertEqual(counts, ledger._prefetch_class_counts(project.id), "与不用缓存的结果一致")
        self.assertEqual(timings, ledger._prefetch_job_timings(job_ids))
        self.assertEqual(timings[running.id], (7.0, 8.5))

    def test_cache_keeps_exactly_the_project_current_results(self):
        project, first, second, failed, *_ = self._seed()
        counts_file = self._cache_file(project, ledger._CLASS_COUNTS_CACHE_FILE)
        ledger._prefetch_class_counts(project.id, cache_file=counts_file)
        self.assertEqual(sorted(derived_cache.load_cache(counts_file)), sorted(str(row.id) for row in (first, second, failed)))
        third = ClassificationResult(
            project_id=project.id, mine_fid=4, year=2025, inference_job_id="job-fourth", model_id="m",
            mine_resource_id=1, current_feature_collection_json=_fc(3), auto_feature_collection_json=_fc(3),
            current_revision_no=0, feature_count=1, vector_status="ready",
        )
        other_project = Project(name="别的项目", region="昆明")
        db.session.add_all([third, other_project])
        db.session.flush()
        db.session.add(ClassificationResult(
            project_id=other_project.id, mine_fid=1, year=2024, inference_job_id="job-other", model_id="m",
            mine_resource_id=1, current_feature_collection_json=_fc(4), auto_feature_collection_json=_fc(4),
            current_revision_no=0, feature_count=1, vector_status="ready",
        ))
        db.session.commit()
        db.session.expire_all()
        counts = ledger._prefetch_class_counts(project.id, cache_file=counts_file)
        current_ids = sorted(str(row_id) for (row_id,) in db.session.query(ClassificationResult.id).filter_by(project_id=project.id).all())
        self.assertEqual(sorted(derived_cache.load_cache(counts_file)), current_ids)
        self.assertEqual(sorted(counts), sorted([first.id, second.id, third.id]), "没有图斑的成果照旧不留条目")

    def test_unreadable_or_foreign_cache_is_ignored_and_rewritten(self):
        project, first, *_ = self._seed()
        counts_file = self._cache_file(project, ledger._CLASS_COUNTS_CACHE_FILE)
        counts_file.parent.mkdir(parents=True)
        full = ledger._prefetch_class_counts(project.id)
        for content in ("not json", json.dumps({"version": 0, "entries": {"1": {"signature": "x", "counts": [[1, 9]]}}}), json.dumps([1])):
            counts_file.write_text(content, encoding="utf-8")
            self.assertEqual(ledger._prefetch_class_counts(project.id, cache_file=counts_file), full)
            self.assertEqual(derived_cache.load_cache(counts_file)[str(first.id)]["counts"], [[1, 1], [2, 2]])

    def test_cache_write_failure_does_not_change_the_result(self):
        project, *_ = self._seed()
        full = ledger._prefetch_class_counts(project.id)
        blocker = self._cache_file(project, ledger._CLASS_COUNTS_CACHE_FILE).parent
        blocker.parent.mkdir(parents=True)
        blocker.write_text("占位", encoding="utf-8")
        with self.assertLogs(derived_cache.LOGGER, level="WARNING") as logs:
            self.assertEqual(ledger._prefetch_class_counts(project.id, cache_file=blocker / ledger._CLASS_COUNTS_CACHE_FILE), full)
        self.assertTrue(any("派生缓存写入失败" in line for line in logs.output))

    def test_workbook_with_cache_equals_workbook_without(self):
        from openpyxl import load_workbook

        project, *_ = self._seed()
        plain = ledger.build_project_ledger_workbook(project, use_cache=False).read()

        def cells(stream):
            workbook = load_workbook(io.BytesIO(stream))
            return {sheet.title: [tuple(row) for row in sheet.iter_rows(values_only=True)] for sheet in workbook}

        first_pass = cells(ledger.build_project_ledger_workbook(project, use_cache=True).read())
        second_pass = cells(ledger.build_project_ledger_workbook(project, use_cache=True).read())
        self.assertEqual(first_pass, cells(plain))
        self.assertEqual(second_pass, cells(plain))
        # 图斑统计：表头 + 第一条成果两个类别 + 第二条成果一个类别（没有图斑的成果不留行）；主表每条成果一行
        self.assertEqual(len(second_pass["地类图斑统计"]), 1 + 3)
        self.assertEqual(len(second_pass["推理成果台账"]), 1 + 3)


if __name__ == "__main__":
    unittest.main()
