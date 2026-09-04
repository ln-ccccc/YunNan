"""持久化推理任务 Worker。"""

import json
import logging
import os
import shutil
import socket
import time
from pathlib import Path
from types import SimpleNamespace


logger = logging.getLogger(__name__)


def _default_model_forward_runner(model):
    import numpy as np
    from mmseg.apis import inference_model

    sample = np.zeros((512, 512, 3), dtype=np.uint8)
    result = inference_model(model, sample)
    mask = result.pred_sem_seg.data
    if tuple(mask.shape[-2:]) != (512, 512):
        raise RuntimeError(f"模型 smoke 输出尺寸异常: {tuple(mask.shape)}")


def run_model_forward_smoke_test(model, *, inference_runner=None):
    runner = inference_runner or _default_model_forward_runner
    try:
        runner(model)
    except Exception as error:
        raise RuntimeError(f"model forward smoke failed: {error}") from error


def _default_model_loader(device):
    from applications.interface.mmseg_inference_caller import get_model_paths
    from applications.interface.mmseg_segmentation import load_model

    config_path, checkpoint_path = get_model_paths(
        "cc-ln/CUGRS",
        require_inference_checkpoint=True,
    )
    return load_model(config_path, checkpoint_path, device=device)


def _default_inference_fn(
    model,
    *,
    input_dir,
    output_dir,
    file_names,
    progress_callback=None,
    should_cancel=None,
):
    from applications.interface.mmseg_segmentation import run_inference_with_model

    return run_inference_with_model(
        model,
        input_dir=input_dir,
        output_dir=output_dir,
        file_names=file_names,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
    )


def run_loaded_mmseg_tiles(
    *,
    model,
    inference_fn,
    data_path,
    out_dir,
    file_names,
    progress_callback=None,
    should_cancel=None,
    **_,
):
    details = inference_fn(
        model,
        input_dir=data_path,
        output_dir=out_dir,
        file_names=file_names,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
    )
    results = {item.get("input_name"): item for item in details.get("results", [])}
    failed_tiles = []
    tile_errors = {}
    for tile_name in file_names:
        item = results.get(tile_name)
        if item is None:
            failed_tiles.append(tile_name)
            tile_errors[tile_name] = "推理 Worker 未返回该瓦片结果"
        elif item.get("status") != "success":
            failed_tiles.append(tile_name)
            tile_errors[tile_name] = item.get("error") or "未知推理错误"
    return failed_tiles, tile_errors


class InferenceWorker:
    def __init__(
        self,
        *,
        device_resolver=None,
        model_loader=None,
        model_smoke_test=None,
        inference_fn=None,
        pipeline_runner=None,
        project_result_publisher=None,
        job_store=None,
        runtime_root=None,
        requested_device="auto",
        gpu_device=0,
        allow_cpu_fallback=True,
        keep_failed_workdir=True,
        job_timeout_seconds=3600,
        poll_interval=1.0,
        worker_id=None,
    ):
        if job_store is None:
            from applications.inference import jobs as job_store
        if pipeline_runner is None:
            from applications.kml_roi.pipeline import run_kml_roi_pipeline as pipeline_runner

        self.device_resolver = device_resolver
        self.model_loader = model_loader or _default_model_loader
        self.model_smoke_test = model_smoke_test or run_model_forward_smoke_test
        self.inference_fn = inference_fn or _default_inference_fn
        self.pipeline_runner = pipeline_runner
        self.project_result_publisher = project_result_publisher
        self.job_store = job_store
        self.runtime_root = Path(runtime_root or Path.cwd() / "runtime" / "inference_jobs").resolve()
        self.requested_device = requested_device
        self.gpu_device = int(gpu_device)
        self.allow_cpu_fallback = bool(allow_cpu_fallback)
        self.keep_failed_workdir = bool(keep_failed_workdir)
        self.job_timeout_seconds = max(0, int(job_timeout_seconds or 0))
        self.poll_interval = float(poll_interval)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.resolution = None
        self.model = None
        self.cpu_model = None
        self.active_job = None
        self.job_deadline = None

    def initialize(self):
        if self.model is not None:
            return self.resolution

        if self.device_resolver is None:
            from applications.inference.device import DeviceResolver, run_mmcv_cuda_smoke_test

            loaded = {}

            def load_smoke_test(device):
                run_mmcv_cuda_smoke_test(device)
                model = self.model_loader(device)
                self.model_smoke_test(model)
                loaded["model"] = model

            resolver = DeviceResolver(smoke_test=load_smoke_test)
            self.resolution = resolver.resolve(
                requested=self.requested_device,
                gpu_device=self.gpu_device,
                allow_cpu_fallback=self.allow_cpu_fallback,
            )
            self.model = loaded.get("model")
            if self.model is None:
                self.model = self.model_loader(self.resolution.effective)
                self.model_smoke_test(self.model)
        else:
            self.resolution = self.device_resolver.resolve(
                requested=self.requested_device,
                gpu_device=self.gpu_device,
                allow_cpu_fallback=self.allow_cpu_fallback,
            )
            self.model = self.model_loader(self.resolution.effective)
        if self.resolution.effective == "cpu":
            self.cpu_model = self.model
        if hasattr(self.job_store, "publish_worker_capability"):
            self.job_store.publish_worker_capability(self.worker_id, self.resolution)
        return self.resolution

    def _tile_runner(self, **kwargs):
        total = len(kwargs.get("file_names") or [])
        if hasattr(self.job_store, "publish_worker_capability"):
            self.job_store.publish_worker_capability(self.worker_id, self.resolution)
        if hasattr(self.job_store, "update_job_progress"):
            self.job_store.update_job_progress(self.active_job, 0, total)

        def progress_callback(current, progress_total):
            if hasattr(self.job_store, "update_job_progress"):
                self.job_store.update_job_progress(self.active_job, current, progress_total)
            if hasattr(self.job_store, "publish_worker_capability"):
                self.job_store.publish_worker_capability(self.worker_id, self.resolution)

        def should_cancel():
            if self.job_deadline is not None and time.monotonic() >= self.job_deadline:
                raise RuntimeError("INFERENCE_TIMEOUT")
            if hasattr(self.job_store, "refresh_job"):
                self.job_store.refresh_job(self.active_job)
            return bool(self.active_job.cancel_requested)

        return run_loaded_mmseg_tiles(
            model=self.model,
            inference_fn=self.inference_fn,
            progress_callback=progress_callback,
            should_cancel=should_cancel,
            **kwargs,
        )

    @staticmethod
    def _is_cuda_oom_summary(summary):
        errors = (summary or {}).get("tile_errors") or {}
        return any("out of memory" in str(message).lower() for message in errors.values())

    def _switch_to_cpu_after_oom(self):
        self.model = None
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError as error:
            logger.debug("当前进程无 Torch，跳过 CUDA 缓存清理: %s", error)
        except Exception as error:
            logger.warning("CUDA 缓存清理失败，继续加载 CPU 模型: %s", error)
        if self.cpu_model is None:
            self.cpu_model = self.model_loader("cpu")
        self.model = self.cpu_model
        warning = "CUDA 显存不足，已回退 CPU 并重试当前任务"
        self.resolution = SimpleNamespace(
            requested=self.resolution.requested,
            effective="cpu",
            fallback_reason="CUDA_OUT_OF_MEMORY",
            warnings=tuple(self.resolution.warnings) + (warning,),
        )
        if hasattr(self.job_store, "publish_worker_capability"):
            self.job_store.publish_worker_capability(self.worker_id, self.resolution)

    def _run_pipeline(self, payload, work_dir):
        is_project_job = payload.get("project_id") not in (None, "")
        return self.pipeline_runner(
            old_tif=Path(payload["old_tif_path"]),
            new_tif=Path(payload["new_tif_path"]),
            kml_path=Path(payload["kml_path"]),
            output_root=(work_dir / "staged_outputs") if is_project_job else Path(payload["output_root"]),
            work_dir=work_dir,
            model_id="cc-ln/CUGRS",
            device=self.resolution.effective,
            limit=payload.get("limit", 0),
            keep_workdir=is_project_job,
            year=payload.get("year") or None,
            old_year=payload.get("old_year") or None,
            new_year=payload.get("new_year") or None,
            selected_fids=payload.get("mine_fids") or None,
            tile_runner=self._tile_runner,
        )

    def run_job(self, job):
        if self.model is None:
            self.initialize()
        if job.cancel_requested:
            return self.job_store.finish_job(job, status="cancelled")

        payload = json.loads(job.request_payload_json)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        work_dir = self.runtime_root / str(job.id)
        work_dir.mkdir(exist_ok=False)
        if hasattr(self.job_store, "set_job_workdir"):
            self.job_store.set_job_workdir(job, work_dir)

        self.active_job = job
        process_resolution = self.resolution
        process_model = self.model
        restore_process_device = False
        self.job_deadline = (
            time.monotonic() + self.job_timeout_seconds
            if self.job_timeout_seconds > 0
            else None
        )
        try:
            if payload.get("requested_device") == "cpu" and self.resolution.effective != "cpu":
                restore_process_device = True
                self.resolution = SimpleNamespace(
                    requested="cpu",
                    effective="cpu",
                    fallback_reason=None,
                    warnings=(),
                )
                if self.cpu_model is None:
                    self.cpu_model = self.model_loader("cpu")
                self.model = self.cpu_model
            summary = self._run_pipeline(payload, work_dir)
            if (
                self.resolution.effective.startswith("cuda")
                and self.allow_cpu_fallback
                and self._is_cuda_oom_summary(summary)
            ):
                self._switch_to_cpu_after_oom()
                fallback_work_dir = self.runtime_root / f"{job.id}-cpu-fallback"
                fallback_work_dir.mkdir(exist_ok=False)
                if hasattr(self.job_store, "set_job_workdir"):
                    self.job_store.set_job_workdir(job, fallback_work_dir)
                summary = self._run_pipeline(payload, fallback_work_dir)
            if (
                payload.get("project_id") not in (None, "")
                and summary.get("written_fid_list")
                and summary.get("status") in {"completed", "succeeded", "partial_failed"}
            ):
                publisher = self.project_result_publisher
                if publisher is None:
                    from applications.project_hub.inference_results import (
                        publish_project_inference_result,
                    )

                    publisher = publish_project_inference_result
                publication_payload = dict(payload)
                publication_payload["inference_job_id"] = str(job.id)
                publication_payload["model_id"] = "cc-ln/CUGRS"
                publication = publisher(payload["project_id"], publication_payload, summary)
                summary["routing"] = {
                    "mode": "project",
                    "project_id": payload["project_id"],
                    "matched_fids": payload.get("mine_fids") or [],
                    "synced_fids": publication["synced_fids"],
                }
                summary["display_results"] = publication["display_results"]
                summary["classification_results"] = publication["classification_results"]
            status = summary.get("status", "failed")
            if status in {"completed", "no_features"}:
                status = "succeeded"
            if status == "succeeded" and self.resolution.fallback_reason:
                status = "succeeded_with_fallback"
            return self.job_store.finish_job(
                job,
                status=status,
                result=summary,
                effective_device=self.resolution.effective,
                fallback_reason=self.resolution.fallback_reason,
                warnings=self.resolution.warnings,
            )
        except Exception as error:
            if not self.keep_failed_workdir:
                shutil.rmtree(work_dir, ignore_errors=True)
            if str(error) == "INFERENCE_CANCELLED":
                return self.job_store.finish_job(
                    job,
                    status="cancelled",
                    effective_device=self.resolution.effective,
                    fallback_reason=self.resolution.fallback_reason,
                    warnings=self.resolution.warnings,
                )
            if str(error) == "INFERENCE_TIMEOUT":
                return self.job_store.finish_job(
                    job,
                    status="failed",
                    effective_device=self.resolution.effective,
                    fallback_reason=self.resolution.fallback_reason,
                    warnings=self.resolution.warnings,
                    error_code="JOB_TIMEOUT",
                    error_message=f"推理任务超过 {self.job_timeout_seconds} 秒时限",
                )
            return self.job_store.finish_job(
                job,
                status="failed",
                effective_device=self.resolution.effective,
                fallback_reason=self.resolution.fallback_reason,
                warnings=self.resolution.warnings,
                error_code="GPU_INFERENCE_FAILED" if self.resolution.effective.startswith("cuda") else "INFERENCE_FAILED",
                error_message=str(error),
            )
        finally:
            if restore_process_device:
                self.model = process_model
                self.resolution = process_resolution
                if hasattr(self.job_store, "publish_worker_capability"):
                    self.job_store.publish_worker_capability(self.worker_id, self.resolution)
            self.active_job = None
            self.job_deadline = None

    def run_forever(self, should_stop=lambda: False):
        self.initialize()
        if hasattr(self.job_store, "recover_abandoned_jobs"):
            worker_host = self.worker_id.rsplit(":", 1)[0]
            recovered = self.job_store.recover_abandoned_jobs(worker_host, self.worker_id)
            if recovered:
                logger.warning("已将 %s 个上一个 Worker 进程遗留的任务标记为失败", recovered)
        last_heartbeat = 0.0
        while not should_stop():
            now = time.monotonic()
            if now - last_heartbeat >= 30:
                if hasattr(self.job_store, "publish_worker_capability"):
                    self.job_store.publish_worker_capability(self.worker_id, self.resolution)
                last_heartbeat = now
            job = self.job_store.claim_next_job(self.worker_id)
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self.run_job(job)
