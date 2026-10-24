#!/usr/bin/env python3
"""推理 Worker 容器健康探针（compose healthcheck 调用，2026-10-24 审查 P2-12）。

按 worker 既有的数据库心跳判定循环存活，进程内不做任何写入：
- InferenceWorkerState.update_time：空闲期每 ≤30s 经 publish_worker_capability
  刷新（worker.run_forever），>150s 视为失联；
- 兜底信号：存在 running 任务且 update_time 新鲜（每 tile 进度提交经列级
  onupdate 刷新）——CPU 回退下单 tile 可达分钟级，能力心跳会短暂停更，
  任务仍在推进即视为存活。

启动引导（start_period）内的心跳缺失由 compose start_period 容忍，此处不判。
"""
import sys
from datetime import datetime

from applications.inference.app import create_worker_app
from applications.models.inference_job import InferenceJob, InferenceWorkerState

WORKER_STATE_STALE_SECONDS = 150
RUNNING_JOB_STALE_SECONDS = 600


def check_worker_liveness() -> tuple:
    """返回 (是否存活, 诊断信息)。需在 app_context 内调用。"""
    state = (
        InferenceWorkerState.query.order_by(InferenceWorkerState.update_time.desc()).first()
    )
    state_age = (
        (datetime.now() - state.update_time).total_seconds()
        if state is not None and state.update_time is not None
        else None
    )
    if state_age is not None and state_age <= WORKER_STATE_STALE_SECONDS:
        return True, f"worker_state_age={state_age:.0f}s"

    job = (
        InferenceJob.query.filter_by(status="running")
        .order_by(InferenceJob.update_time.desc())
        .first()
    )
    job_age = (
        (datetime.now() - job.update_time).total_seconds()
        if job is not None and job.update_time is not None
        else None
    )
    if job_age is not None and job_age <= RUNNING_JOB_STALE_SECONDS:
        return True, f"running_job_age={job_age:.0f}s"
    return False, f"worker_state_age={state_age}, running_job_age={job_age}"


def main():
    app = create_worker_app()
    with app.app_context():
        alive, detail = check_worker_liveness()
    if not alive:
        print(f"inference worker heartbeat stale ({detail})", file=sys.stderr)
    sys.exit(0 if alive else 1)


if __name__ == "__main__":
    main()
