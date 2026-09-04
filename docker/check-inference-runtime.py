#!/usr/bin/env python3
"""输出推理容器的可机器读运行时自检结果。"""

import importlib
import hashlib
import json
import os
import sys
from pathlib import Path


HASH_CHUNK_SIZE = 8 * 1024 * 1024
INFERENCE_CHECKPOINT_PATH = Path(
    "/app/backend/model/mmseg_config/model.inference.pth"
)


class ModelCheckpointMissingError(FileNotFoundError):
    code = "MODEL_CHECKPOINT_MISSING"


backend_root = Path(__file__).resolve().parents[1] / "backend"
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))


def module_version(name):
    try:
        module = importlib.import_module(name)
        return getattr(module, "__version__", "unknown")
    except Exception as error:
        return f"unavailable: {error}"


def checkpoint_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def checkpoint_metadata(path, *, image_build):
    checkpoint = Path(path).resolve()
    if not checkpoint.is_file():
        raise ModelCheckpointMissingError(
            f"MODEL_CHECKPOINT_MISSING: {checkpoint}"
        )
    return {
        "image_build": image_build,
        "checkpoint_path": str(checkpoint),
        "checkpoint_size": checkpoint.stat().st_size,
        "checkpoint_sha256": checkpoint_sha256(checkpoint),
    }


def main():
    allow_fallback = str(os.getenv("INFERENCE_CPU_FALLBACK", "true")).strip().lower() in {"1", "true", "yes", "on"}
    requested = os.getenv("INFERENCE_ACCELERATOR", "auto")
    gpu_device = int(os.getenv("INFERENCE_GPU_DEVICE", "0") or 0)
    image_build = os.getenv("INFERENCE_IMAGE_BUILD", "unknown").strip() or "unknown"
    checkpoint_path = Path(INFERENCE_CHECKPOINT_PATH).resolve()
    report = {
        "python": sys.version.split()[0],
        "torch": module_version("torch"),
        "mmcv": module_version("mmcv"),
        "mmengine": module_version("mmengine"),
        "mmseg": module_version("mmseg"),
        "requested_device": requested,
        "image_build": image_build,
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_size": None,
        "checkpoint_sha256": None,
    }
    try:
        report.update(
            checkpoint_metadata(checkpoint_path, image_build=image_build)
        )
        from applications.inference.device import DeviceResolver, run_mmcv_cuda_smoke_test

        resolution = DeviceResolver(smoke_test=run_mmcv_cuda_smoke_test).resolve(
            requested=requested,
            gpu_device=gpu_device,
            allow_cpu_fallback=allow_fallback,
        )
        report.update(
            {
                "effective_device": resolution.effective,
                "fallback_reason": resolution.fallback_reason,
                "warnings": list(resolution.warnings),
                "gpu_name": resolution.gpu_name,
                "compute_capability": resolution.compute_capability,
                "ok": True,
            }
        )
        print(json.dumps(report, ensure_ascii=False))
        return 0
    except Exception as error:
        report.update(
            {
                "effective_device": None,
                "error_code": getattr(error, "code", "RUNTIME_CHECK_FAILED"),
                "error": str(error),
                "ok": False,
            }
        )
        print(json.dumps(report, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
