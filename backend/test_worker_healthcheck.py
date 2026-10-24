# -*- coding: utf-8 -*-
"""推理 Worker 容器健康探针判定逻辑（2026-10-24 审查 P2-12）。

按既有 DB 心跳判活：空闲期 InferenceWorkerState.update_time（≤30s 刷新，
>150s 失联）；CPU 回退下单 tile 分钟级时以 running 任务进度心跳兜底。"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.extensions import db
from applications.models.inference_job import InferenceJob, InferenceWorkerState

import worker_healthcheck
from test_project_api import TestProjectAPI


def _add_worker_state(update_time):
    db.session.add(
        InferenceWorkerState(
            worker_id="probe-worker",
            requested_device="auto",
            effective_device="cuda",
            update_time=update_time,
        )
    )
    db.session.commit()


def _add_running_job(update_time):
    db.session.add(
        InferenceJob(
            id="job-probe",
            status="running",
            request_payload_json="{}",
            update_time=update_time,
        )
    )
    db.session.commit()


class TestInferenceWorkerLiveness(TestProjectAPI):
    def test_fresh_worker_state_is_alive(self):
        _add_worker_state(datetime.now())
        alive, detail = worker_healthcheck.check_worker_liveness()
        self.assertTrue(alive, detail)
        self.assertIn("worker_state_age", detail)

    def test_no_heartbeat_recorded_is_not_alive(self):
        alive, _ = worker_healthcheck.check_worker_liveness()
        self.assertFalse(alive)

    def test_stale_state_without_running_job_is_not_alive(self):
        _add_worker_state(datetime.now() - timedelta(minutes=10))
        alive, _ = worker_healthcheck.check_worker_liveness()
        self.assertFalse(alive)

    def test_stale_state_with_progressing_job_is_alive(self):
        # CPU 回退下单 tile 可达分钟级：能力心跳停更但任务仍在推进
        _add_worker_state(datetime.now() - timedelta(minutes=10))
        _add_running_job(datetime.now() - timedelta(seconds=60))
        alive, detail = worker_healthcheck.check_worker_liveness()
        self.assertTrue(alive, detail)
        self.assertIn("running_job_age", detail)

    def test_stale_state_with_stalled_job_is_not_alive(self):
        _add_worker_state(datetime.now() - timedelta(minutes=10))
        _add_running_job(datetime.now() - timedelta(minutes=20))
        alive, _ = worker_healthcheck.check_worker_liveness()
        self.assertFalse(alive)


if __name__ == "__main__":
    unittest.main()
