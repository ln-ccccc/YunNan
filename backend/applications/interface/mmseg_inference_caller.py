#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
from typing import Dict, List, Tuple, Union

from applications.common.path_global import generate_url

MMSEG_CONDA_ENV = "MMSeg310"
_curr_dir = os.path.dirname(os.path.abspath(__file__))
MMSEG_SCRIPT = os.path.join(_curr_dir, "mmseg_segmentation.py")

CUGRS_CONFIG = {
    "model_id": "cc-ln/CUGRS",
    "config_path": os.path.join(_curr_dir, "..", "..", "model", "mmseg_config", "dinov3_swinV1.py"),
    "inference_checkpoint_path": os.path.join(
        _curr_dir, "..", "..", "model", "mmseg_config", "model.inference.pth"
    ),
    "checkpoint_path": os.path.join(_curr_dir, "..", "..", "model", "mmseg_config", "model.pth"),
}
MMSEG_SOURCE_ROOT = os.path.join(_curr_dir, "..", "..", "model", "mmseg_config", "dinov3_swinV1")


def get_model_paths(
    model_id: str,
    *,
    require_inference_checkpoint: bool = False,
) -> Tuple[str, str]:
    if model_id == "cc-ln/CUGRS":
        config = os.path.abspath(CUGRS_CONFIG["config_path"])
        inference_checkpoint = os.path.abspath(CUGRS_CONFIG["inference_checkpoint_path"])
        if os.path.isfile(inference_checkpoint):
            return config, inference_checkpoint
        if require_inference_checkpoint:
            raise FileNotFoundError(f"MODEL_CHECKPOINT_MISSING: {inference_checkpoint}")
        checkpoint = os.path.abspath(CUGRS_CONFIG["checkpoint_path"])
        if os.path.isfile(checkpoint):
            return config, checkpoint
        raise FileNotFoundError(
            "MODEL_CHECKPOINT_MISSING: "
            f"inference checkpoint: {inference_checkpoint}; training checkpoint: {checkpoint}"
        )
    raise ValueError(f"Unknown MMSeg model: {model_id}")


def _resolve_mmseg_python() -> List[str]:
    candidate_paths = [
        "/opt/conda/envs/MMSeg310/bin/python",
        "/home/livablecity/miniconda3/envs/MMSeg310/bin/python",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            return [path]
    return ["conda", "run", "-n", MMSEG_CONDA_ENV, "python"]


def _build_mmseg_env() -> dict:
    env = os.environ.copy()
    mmseg_pkg_dir = os.path.join(MMSEG_SOURCE_ROOT, "mmseg")
    if os.path.isdir(mmseg_pkg_dir):
        old = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{MMSEG_SOURCE_ROOT}:{old}" if old else MMSEG_SOURCE_ROOT
    return env


def call_mmseg_inference(
    model_id: str,
    data_path: str,
    out_dir: str,
    names: List[str],
    device: str = "cpu",
    timeout: int = 1200,
    return_details: bool = False,
) -> Union[List[str], Dict]:
    if not names:
        if return_details:
            return {"status": "completed", "total": 0, "success": 0, "results": []}
        return []

    abs_data_path = os.path.abspath(data_path)
    abs_out_dir = os.path.abspath(out_dir)
    config_path, checkpoint_path = get_model_paths(model_id)
    file_names_str = ",".join(names)

    cmd = _resolve_mmseg_python() + [
        MMSEG_SCRIPT,
        "--config",
        config_path,
        "--checkpoint",
        checkpoint_path,
        "--input_dir",
        abs_data_path,
        "--output_dir",
        abs_out_dir,
        "--file_names",
        file_names_str,
        "--device",
        device,
    ]
    print(f"[MMSeg-Caller] cwd={_curr_dir}", file=sys.stderr)
    print(f"[MMSeg-Caller] MMSEG_SOURCE_ROOT={MMSEG_SOURCE_ROOT}", file=sys.stderr)
    print(f"[MMSeg-Caller] python={' '.join(_resolve_mmseg_python())}", file=sys.stderr)

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=_curr_dir,
        env=_build_mmseg_env(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"MMSeg inference failed: {result.stderr or result.stdout}")

    output_data = None
    for line in reversed((result.stdout or "").splitlines()):
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            output_data = json.loads(s)
            break
        except Exception:
            continue
    if not output_data or output_data.get("status") != "completed":
        raise RuntimeError(f"Inference incomplete: {output_data}")

    normalized_results = []
    raw_results = output_data.get("results", [])
    by_input_name = {res.get("input_name"): res for res in raw_results if res.get("input_name")}
    by_output_name = {res.get("name"): res for res in raw_results if res.get("name")}
    for name in names:
        base_name = os.path.splitext(name)[0]
        expected_out_name = f"pred_{base_name}.png"
        res = by_input_name.get(name) or by_output_name.get(expected_out_name) or by_output_name.get(name)
        if not res:
            normalized_results.append({
                "input_name": name,
                "output_name": None,
                "mask_name": None,
                "status": "error",
                "error": f"推理进程未返回结果，预期输出: {expected_out_name}",
            })
            continue
        if res.get("status") == "success":
            normalized_results.append({
                "input_name": name,
                "output_name": res.get("output_name") or res.get("name"),
                "mask_name": res.get("mask_name"),
                "status": "success",
                "error": None,
            })
        else:
            normalized_results.append({
                "input_name": name,
                "output_name": None,
                "mask_name": res.get("mask_name"),
                "status": "error",
                "error": res.get("error") or res.get("message") or "未知推理错误",
            })

    details = {
        "status": "completed",
        "total": len(names),
        "success": sum(1 for item in normalized_results if item["status"] == "success"),
        "results": normalized_results,
    }
    if return_details:
        return details

    temps = []
    for item in normalized_results:
        if item["status"] != "success":
            raise RuntimeError(f"Processing failed for {item['input_name']}: {item['error']}")
        temps.append(generate_url + item["output_name"])
    return temps


def execute(
    model_id: str,
    data_path: str,
    out_dir: str,
    names: List[str],
    device: str = "cpu",
    return_details: bool = False,
) -> Union[List[str], Dict]:
    return call_mmseg_inference(
        model_id=model_id,
        data_path=data_path,
        out_dir=out_dir,
        names=names,
        device=device,
        return_details=return_details,
    )


SUPPORTED_MODELS = {
    "cugrs": {
        "model_id": "cc-ln/CUGRS",
        "description": "CUGRS DinoV3+SwinTransformer land cover model",
    }
}

