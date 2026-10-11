#!/usr/bin/env python3
"""Validate the immutable inference-image content contract and print JSON."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


WEIGHT_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".onnx",
    ".pdiparams",
    ".pdmodel",
    ".pdopt",
    ".pth",
    ".safetensors",
    ".weights",
}
VENDORED_NON_RUNTIME_DIRECTORIES = {
    ".git",
    ".ipynb_checkpoints",
    "data",
    "demo",
    "docs",
    "eval",
    "notebooks",
    "resources",
    "results",
    "tests",
    "tools",
    "train",
}
ALLOWED_MINER_ROOTS = {"change_matrix_outputs", "uploads"}


def probe_versions():
    import mmcv
    import mmengine
    import mmseg
    import torch
    from mmcv.ops import roi_align  # noqa: F401 - imports the compiled extension

    return {
        "python_version": sys.version.split()[0],
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
        "mmcv_version": mmcv.__version__,
        "mmengine_version": mmengine.__version__,
        "mmseg_version": mmseg.__version__,
        "import_errors": [],
    }


def probe_nvcc(root):
    root = Path(root).resolve()
    known_paths = (
        root / "usr" / "local" / "cuda" / "bin" / "nvcc",
        root / "usr" / "bin" / "nvcc",
    )
    if any(path.exists() for path in known_paths):
        return True
    return root == Path("/").resolve() and shutil.which("nvcc") is not None


def probe_worker_runtime():
    # 子进程与临时 SQLite 避免触碰部署数据库，也不加载模型或权重。
    source = """
import json
import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import inspect
from applications.inference.app import create_worker_app
from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding
from applications.project_hub.inference_results import publish_project_inference_result, record_inference_failure
from applications.project_hub.classification_results import publish_classification_result

with tempfile.TemporaryDirectory(prefix="worker-image-check-") as directory:
    root = Path(directory)
    os.environ["PROJECT_STORAGE_ROOT"] = str(root / "storage")
    app = create_worker_app("testing")
    with app.app_context():
        project = Project(name="镜像自检", status="active")
        db.session.add(project)
        db.session.flush()
        db.session.add(ProjectMineBinding(project_id=project.id, mine_fid=17))
        db.session.commit()
        staged = root / "staged" / "17"
        staged.mkdir(parents=True)
        image = np.full((4, 4, 3), 120, dtype=np.uint8)
        for name in ("17+2022.png", "17+2022_src.png"):
            assert cv2.imwrite(str(staged / name), image)
        assert cv2.imwrite(str(staged / "17+2022_mask.png"), np.zeros((4, 4), dtype=np.uint8))
        output = root / "storage" / "projects" / str(project.id) / "outputs" / "inference"
        published = publish_project_inference_result(project.id, {
            "year": "2022", "mine_fids": [17], "inference_job_id": "image-check-success", "output_root": str(output),
        }, {"written_fid_list": [17], "output_root": str(staged.parent)})
        record_inference_failure(project.id, {"inference_job_id": "image-check-failure"})
        activities = sorted(row.event_type for row in ProjectActivityLog.query.filter_by(project_id=project.id).all())
        assert published["synced_fids"] == [17]
        assert (output / "17" / "17+2022.png").is_file()
        assert activities == ["inference_failed", "inference_result_published"]
        assert not app.blueprints
        assert not any(name.startswith("applications.schemas") for name in sys.modules)
        assert "mmseg.apis" not in sys.modules
        print(json.dumps({"tables": sorted(inspect(db.engine).get_table_names()), "activities": activities, "synced_fids": published["synced_fids"], "model_loaded": False}))
"""
    process = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, timeout=60,
    )
    if process.returncode:
        raise RuntimeError("Worker 冷启动或项目发布自检失败: " + process.stderr.strip())
    return json.loads(process.stdout.strip().splitlines()[-1])


def _relative(root, path):
    return path.relative_to(root).as_posix()


def find_forbidden_assets(root):
    root = Path(root).resolve()
    app_root = root / "app"
    if not app_root.exists():
        return []

    found = set()

    frontend = app_root / "frontend"
    if frontend.exists() or frontend.is_symlink():
        found.add(_relative(root, frontend))

    for path in app_root.rglob("node_modules"):
        found.add(_relative(root, path))

    miner = app_root / "miner"
    if miner.exists() or miner.is_symlink():
        if not miner.is_dir() or miner.is_symlink():
            found.add(_relative(root, miner))
        else:
            for child in miner.iterdir():
                if (
                    child.name not in ALLOWED_MINER_ROOTS
                    or not child.is_dir()
                    or child.is_symlink()
                ):
                    found.add(_relative(root, child))
                    continue
                for descendant in child.rglob("*"):
                    found.add(_relative(root, descendant))

    vendored_root = app_root / "backend" / "model" / "mmseg_config" / "dinov3_swinV1"
    if vendored_root.is_dir():
        for path in vendored_root.rglob("*"):
            if path.is_dir() and path.name.lower() in VENDORED_NON_RUNTIME_DIRECTORIES:
                found.add(_relative(root, path))

    covered_roots = tuple(root / relative for relative in found)
    for path in app_root.rglob("*"):
        if path.is_symlink():
            if path.suffix.lower() in WEIGHT_SUFFIXES:
                found.add(_relative(root, path))
            continue
        if not path.is_file():
            continue
        if any(path == covered or covered in path.parents for covered in covered_roots):
            continue
        if path.suffix.lower() in WEIGHT_SUFFIXES:
            found.add(_relative(root, path))
    return sorted(found)


def build_report(root=Path("/"), *, version_probe=probe_versions, nvcc_probe=probe_nvcc,
                 worker=False, worker_probe=probe_worker_runtime):
    try:
        versions = version_probe()
    except Exception as error:
        versions = {
            "python_version": sys.version.split()[0],
            "torch_version": None,
            "cuda_version": None,
            "mmcv_version": None,
            "mmengine_version": None,
            "mmseg_version": None,
            "import_errors": [str(error)],
        }

    nvcc = bool(nvcc_probe(Path(root)))
    forbidden_assets = find_forbidden_assets(root)
    report = {
        **versions,
        "nvcc": nvcc,
        "forbidden_assets": forbidden_assets,
    }
    if worker:
        try:
            report["worker_runtime"] = worker_probe()
        except Exception as error:
            report["import_errors"].append(str(error))
    report["ok"] = not report.get("import_errors") and not nvcc and not forbidden_assets
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/"),
        help="Filesystem root to inspect (used by image builds and fixture tests).",
    )
    parser.add_argument("--worker", action="store_true", help="验证独立 Worker 冷启动、项目发布与审计依赖。")
    args = parser.parse_args(argv)
    report = build_report(args.root, worker=args.worker)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
