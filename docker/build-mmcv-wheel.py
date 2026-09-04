#!/usr/bin/env python3
"""Build, validate and publish an ABI-keyed MMCV wheel cache entry."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from pip_network_retry import run_network_pip


def cache_fingerprint(values):
    return (
        f"python={values['python_version']}|"
        f"torch={values['torch_version']}|"
        f"cuda={values['cuda_version']}|"
        f"mmcv={values['mmcv_version']}|"
        f"arch={values['arch_list']}"
    )


def _key_component(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")


def cache_key(values):
    return "_".join(
        (
            f"python-{_key_component(values['python_version'])}",
            f"torch-{_key_component(values['torch_version'])}",
            f"cuda-{_key_component(values['cuda_version'])}",
            f"mmcv-{_key_component(values['mmcv_version'])}",
            f"arch-{_key_component(values['arch_list'])}",
        )
    )


def _single_wheel(directory, mmcv_version):
    wheels = sorted(Path(directory).glob(f"mmcv-{mmcv_version}-*.whl"))
    return wheels[0] if len(wheels) == 1 else None


def _single_sdist(directory, mmcv_version):
    sources = sorted(Path(directory).glob(f"mmcv-{mmcv_version}.tar.gz"))
    sources.extend(sorted(Path(directory).glob(f"mmcv-{mmcv_version}.zip")))
    return sources[0] if len(sources) == 1 else None


def download_mmcv_source(destination, values, *, network_runner=run_network_pip):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    network_runner(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--retries",
            "12",
            "--timeout",
            "180",
            "--no-build-isolation",
            "--no-deps",
            "--no-binary",
            "mmcv",
            "--dest",
            str(destination),
            f"mmcv=={values['mmcv_version']}",
        ]
    )
    source = _single_sdist(destination, values["mmcv_version"])
    if source is None:
        raise RuntimeError("MMCV_SOURCE_DOWNLOAD_MISSING")
    return source


def build_wheel(
    staging_dir,
    values,
    *,
    download_source=download_mmcv_source,
    wheel_runner=subprocess.run,
):
    staging_dir = Path(staging_dir)
    source = download_source(staging_dir / "source", values)
    wheel_runner(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(staging_dir),
            str(source),
        ],
        check=True,
    )
    wheel = _single_wheel(staging_dir, values["mmcv_version"])
    if wheel is None:
        raise RuntimeError("MMCV_WHEEL_BUILD_MISSING")
    return wheel


def validate_wheel(wheel, values, *, runner=subprocess.run):
    try:
        runner(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--force-reinstall",
                "--no-deps",
                str(wheel),
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        print(f"MMCV_WHEEL_INSTALL_FAILED: {wheel}", file=sys.stderr)
        return False

    dependency_code = "import cv2, mmcv, mmengine, numpy, PIL, yaml"
    try:
        runner([sys.executable, "-c", dependency_code], check=True)
    except subprocess.CalledProcessError:
        print(f"MMCV_WHEEL_DEPENDENCY_IMPORT_FAILED: {wheel}", file=sys.stderr)
        return False

    abi_code = (
            "import mmcv, torch; "
            "from mmcv.ops import roi_align; "
            f"assert mmcv.__version__ == {values['mmcv_version']!r}; "
            f"assert torch.__version__.split('+')[0] == {values['torch_version']!r}; "
            f"assert torch.version.cuda == {values['cuda_version']!r}; "
            "print(roi_align)"
    )
    try:
        runner([sys.executable, "-c", abi_code], check=True)
        return True
    except subprocess.CalledProcessError:
        print(f"MMCV_WHEEL_ABI_INVALID: {wheel}", file=sys.stderr)
        return False


def _cached_wheel(key_dir, values):
    if key_dir.is_symlink() or not key_dir.is_dir():
        return None
    fingerprint_path = key_dir / "fingerprint.txt"
    if fingerprint_path.is_symlink() or not fingerprint_path.is_file():
        return None
    try:
        fingerprint = fingerprint_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    if fingerprint != cache_fingerprint(values):
        return None
    wheel = _single_wheel(key_dir, values["mmcv_version"])
    return None if wheel is None or wheel.is_symlink() else wheel


def safe_remove_entry(path):
    """Remove a cache entry without following a symlink outside the cache."""
    path = Path(path)
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _copy_output(wheel, output_dir):
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    result = output_dir / wheel.name
    shutil.copy2(wheel, result)
    return result


def pip_cache_wheel_candidates(values, *, runner=subprocess.run):
    try:
        result = runner(
            [sys.executable, "-m", "pip", "cache", "dir"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        print("PIP_WHEEL_CACHE_DISCOVERY_FAILED", file=sys.stderr)
        return []

    wheel_root = Path(result.stdout.strip()) / "wheels"
    if wheel_root.is_symlink() or not wheel_root.is_dir():
        return []
    return [
        wheel
        for wheel in sorted(
            wheel_root.rglob(f"mmcv-{values['mmcv_version']}-*.whl")
        )
        if wheel.is_file() and not wheel.is_symlink()
    ]


def _publish_validated_wheel(wheel, cache_root, key_dir, values):
    publish_dir = cache_root / f".{cache_key(values)}.publish-{uuid.uuid4().hex}"
    try:
        publish_dir.mkdir()
        published_wheel = publish_dir / wheel.name
        shutil.copy2(wheel, published_wheel)
        (publish_dir / "fingerprint.txt").write_text(
            cache_fingerprint(values), encoding="utf-8"
        )
        # BuildKit mounts this cache with sharing=locked. os.replace keeps
        # readers from observing a partially populated key directory.
        os.replace(publish_dir, key_dir)
    finally:
        if publish_dir.exists() or publish_dir.is_symlink():
            safe_remove_entry(publish_dir)
    return _cached_wheel(key_dir, values)


def prepare_mmcv_wheel(
    cache_root,
    output_dir,
    values,
    *,
    build_wheel=build_wheel,
    validate_wheel=validate_wheel,
    candidate_provider=pip_cache_wheel_candidates,
):
    cache_root = Path(cache_root)
    output_dir = Path(output_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    key_dir = cache_root / cache_key(values)

    cached = _cached_wheel(key_dir, values)
    if cached is not None and validate_wheel(cached, values):
        return _copy_output(cached, output_dir)

    # Invalid metadata and ABI failures are both removed before rebuilding so a
    # poisoned cache entry cannot make every subsequent build fail forever.
    if key_dir.exists() or key_dir.is_symlink():
        safe_remove_entry(key_dir)

    for candidate in candidate_provider(values):
        candidate = Path(candidate)
        if candidate.is_symlink() or not candidate.is_file():
            continue
        if validate_wheel(candidate, values):
            published = _publish_validated_wheel(
                candidate, cache_root, key_dir, values
            )
            return _copy_output(published, output_dir)

    with tempfile.TemporaryDirectory(prefix=".mmcv-build-", dir=cache_root) as temp_dir:
        built = Path(build_wheel(Path(temp_dir), values))
        if not built.is_file():
            raise RuntimeError("MMCV_WHEEL_BUILD_MISSING")
        if not validate_wheel(built, values):
            raise RuntimeError("MMCV_WHEEL_ABI_INVALID")
        _publish_validated_wheel(built, cache_root, key_dir, values)

    return _copy_output(_cached_wheel(key_dir, values), output_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--torch-version", required=True)
    parser.add_argument("--cuda-version", required=True)
    parser.add_argument("--mmcv-version", required=True)
    parser.add_argument("--arch-list", required=True)
    args = parser.parse_args(argv)
    values = {
        "python_version": args.python_version,
        "torch_version": args.torch_version,
        "cuda_version": args.cuda_version,
        "mmcv_version": args.mmcv_version,
        "arch_list": args.arch_list,
    }
    result = prepare_mmcv_wheel(args.cache_root, args.output_dir, values)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
