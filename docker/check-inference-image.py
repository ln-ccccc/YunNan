#!/usr/bin/env python3
"""Validate the immutable inference-image content contract and print JSON."""

import argparse
import json
import shutil
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


def build_report(root=Path("/"), *, version_probe=probe_versions, nvcc_probe=probe_nvcc):
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
    args = parser.parse_args(argv)
    report = build_report(args.root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
