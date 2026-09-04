#!/usr/bin/env python3
"""推理 Worker 容器入口。"""

import logging
import signal
import threading
from pathlib import Path

from applications.inference.app import create_worker_app
from applications.inference.worker import InferenceWorker


def main():
    app = create_worker_app()
    stop_event = threading.Event()

    def request_stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    backend_root = Path(__file__).resolve().parent
    configured_runtime = app.config.get("INFERENCE_RUNTIME_ROOT")
    runtime_root = Path(configured_runtime).expanduser().resolve() if configured_runtime else backend_root / "runtime" / "inference_jobs"
    max_concurrency = int(app.config.get("INFERENCE_MAX_CONCURRENCY", 1) or 1)
    if max_concurrency != 1:
        raise RuntimeError("当前版本仅支持 INFERENCE_MAX_CONCURRENCY=1，多 GPU 请使用独立 Worker 容器")
    worker = InferenceWorker(
        runtime_root=runtime_root,
        requested_device=app.config.get("INFERENCE_ACCELERATOR", "auto"),
        gpu_device=app.config.get("INFERENCE_GPU_DEVICE", 0),
        allow_cpu_fallback=app.config.get("INFERENCE_CPU_FALLBACK", True),
        keep_failed_workdir=app.config.get("INFERENCE_KEEP_FAILED_WORKDIR", True),
        job_timeout_seconds=app.config.get("INFERENCE_JOB_TIMEOUT_SECONDS", 3600),
    )

    with app.app_context():
        worker.run_forever(stop_event.is_set)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
