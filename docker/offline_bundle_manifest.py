#!/usr/bin/env python3
"""Generate a deterministic integrity manifest for a GeoView offline bundle."""

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath


HASH_CHUNK_SIZE = 8 * 1024 * 1024
SAFE_BASENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REQUIRED_PATHS = {
    "inference_checkpoint": "backend/model/mmseg_config/model.inference.pth",
    "compose_prod": "docker-compose.prod.yml",
    "compose_gpu": "docker-compose.gpu.yml",
    "config_template": "config.yaml",
}
IMAGE_ENV_KEYS = {
    "app_image_tar": "APP_IMAGE_TAR",
    "inference_image_tar": "INFERENCE_IMAGE_TAR",
    "mysql_image_tar": "MYSQL_IMAGE_TAR",
}


class ManifestError(Exception):
    def __init__(self, code, **details):
        super().__init__(code)
        self.code = code
        self.details = details


class ManifestArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        raise ManifestError("OFFLINE_ARGUMENT_INVALID")


def read_env(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ManifestError("OFFLINE_ENV_READ_FAILED") from None

    values = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ManifestError("OFFLINE_ENV_INVALID")
        if key in values:
            raise ManifestError("OFFLINE_ENV_INVALID", env_key=key)
        values[key] = value.strip().strip("\"'")
    return values


def image_tar_name(env, env_key):
    value = env.get(env_key, "")
    if (
        not SAFE_BASENAME.fullmatch(value)
        or value in {".", ".."}
        or PurePosixPath(value).name != value
        or PureWindowsPath(value).name != value
    ):
        raise ManifestError("OFFLINE_ENV_INVALID", env_key=env_key)
    return value


def normalized_relative_path(relative_path):
    raw_path = str(relative_path)
    posix_path = PurePosixPath(raw_path)
    windows_path = PureWindowsPath(raw_path)
    if posix_path.is_absolute() or windows_path.is_absolute():
        raise ManifestError("OFFLINE_ARTIFACT_PATH_INVALID")
    if not posix_path.parts or any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ManifestError("OFFLINE_ARTIFACT_PATH_INVALID")
    return posix_path.as_posix()


def path_within(root, path):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def safe_artifact_path(bundle_root, artifact_key, relative_path):
    try:
        relative_path = normalized_relative_path(relative_path)
    except ManifestError as error:
        error.details.update(
            {"artifact_key": artifact_key, "path": str(relative_path)}
        )
        raise

    current = bundle_root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError(
                "OFFLINE_ARTIFACT_SYMLINK",
                artifact_key=artifact_key,
                path=relative_path,
            )
    try:
        resolved = current.resolve(strict=False)
    except OSError:
        raise ManifestError(
            "OFFLINE_ARTIFACT_IO_FAILED",
            artifact_key=artifact_key,
            path=relative_path,
        ) from None
    if not path_within(bundle_root, resolved):
        raise ManifestError(
            "OFFLINE_ARTIFACT_PATH_INVALID",
            artifact_key=artifact_key,
            path=relative_path,
        )
    if not current.exists():
        raise ManifestError(
            "OFFLINE_ARTIFACT_MISSING",
            artifact_key=artifact_key,
            path=relative_path,
        )
    if not current.is_file():
        raise ManifestError(
            "OFFLINE_ARTIFACT_NOT_FILE",
            artifact_key=artifact_key,
            path=relative_path,
        )
    return current, relative_path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def artifact_entry(bundle_root, artifact_key, relative_path):
    path, relative_path = safe_artifact_path(
        bundle_root, artifact_key, relative_path
    )
    try:
        return {
            "path": relative_path,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    except OSError:
        raise ManifestError(
            "OFFLINE_ARTIFACT_IO_FAILED",
            artifact_key=artifact_key,
            path=relative_path,
        ) from None


def add_artifact(artifacts, artifact_key, entry):
    if artifact_key in artifacts:
        raise ManifestError(
            "OFFLINE_ARTIFACT_DUPLICATE", artifact_key=artifact_key
        )
    artifacts[artifact_key] = entry


def optional_files(bundle_root, directory, prefix):
    optional_root = bundle_root / directory
    if optional_root.is_symlink():
        raise ManifestError(
            "OFFLINE_ARTIFACT_SYMLINK", artifact_key=f"{prefix}:"
        )
    if not optional_root.exists():
        return []
    if not optional_root.is_dir():
        raise ManifestError(
            "OFFLINE_ARTIFACT_NOT_FILE", artifact_key=f"{prefix}:"
        )

    found = []

    def walk_error(_error):
        raise ManifestError(
            "OFFLINE_ARTIFACT_IO_FAILED", artifact_key=f"{prefix}:"
        )

    try:
        walker = os.walk(optional_root, topdown=True, followlinks=False, onerror=walk_error)
        for current_root, directories, files in walker:
            current_root = Path(current_root)
            for name in sorted(directories + files):
                candidate = current_root / name
                relative_path = candidate.relative_to(bundle_root).as_posix()
                artifact_key = (
                    f"{prefix}:{candidate.relative_to(optional_root).as_posix()}"
                )
                if candidate.is_symlink():
                    raise ManifestError(
                        "OFFLINE_ARTIFACT_SYMLINK",
                        artifact_key=artifact_key,
                        path=relative_path,
                    )
            for name in sorted(files):
                candidate = current_root / name
                relative_path = candidate.relative_to(bundle_root).as_posix()
                artifact_key = (
                    f"{prefix}:{candidate.relative_to(optional_root).as_posix()}"
                )
                found.append((artifact_key, relative_path))
    except ManifestError:
        raise
    except OSError:
        raise ManifestError(
            "OFFLINE_ARTIFACT_IO_FAILED", artifact_key=f"{prefix}:"
        ) from None
    return found


def build_manifest(bundle_root, env_file):
    bundle_root = Path(bundle_root).resolve()
    env = read_env(env_file)
    paths = dict(REQUIRED_PATHS)
    for artifact_key, env_key in IMAGE_ENV_KEYS.items():
        paths[artifact_key] = f"images/{image_tar_name(env, env_key)}"

    artifacts = {}
    for artifact_key, relative_path in sorted(paths.items()):
        add_artifact(
            artifacts,
            artifact_key,
            artifact_entry(bundle_root, artifact_key, relative_path),
        )
    for directory, prefix in (("maps", "map"), ("volumes", "volume_backup")):
        for artifact_key, relative_path in optional_files(
            bundle_root, directory, prefix
        ):
            add_artifact(
                artifacts,
                artifact_key,
                artifact_entry(bundle_root, artifact_key, relative_path),
            )
    return {"schema_version": 1, "artifacts": dict(sorted(artifacts.items()))}


def safe_output_path(output, bundle_root):
    bundle_root = Path(bundle_root).resolve()
    raw_output = Path(output)
    if any(part == ".." for part in raw_output.parts):
        raise ManifestError("OFFLINE_OUTPUT_INVALID")
    candidate = raw_output if raw_output.is_absolute() else bundle_root / raw_output
    candidate = candidate.absolute()

    current = bundle_root
    try:
        relative = candidate.relative_to(bundle_root)
    except ValueError:
        raise ManifestError("OFFLINE_OUTPUT_INVALID") from None
    if not relative.parts or relative.parts[0] in {"maps", "volumes"}:
        raise ManifestError("OFFLINE_OUTPUT_INVALID")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ManifestError("OFFLINE_OUTPUT_INVALID")
    try:
        resolved = candidate.resolve(strict=False)
    except OSError:
        raise ManifestError("OFFLINE_OUTPUT_INVALID") from None
    if not path_within(bundle_root, resolved):
        raise ManifestError("OFFLINE_OUTPUT_INVALID")
    for directory in ("maps", "volumes"):
        try:
            optional_root = (bundle_root / directory).resolve(strict=False)
        except OSError:
            raise ManifestError("OFFLINE_OUTPUT_INVALID") from None
        if path_within(optional_root, resolved):
            raise ManifestError("OFFLINE_OUTPUT_INVALID")
    if candidate.exists() and not candidate.is_file():
        raise ManifestError("OFFLINE_OUTPUT_INVALID")
    if not candidate.parent.is_dir():
        raise ManifestError("OFFLINE_OUTPUT_INVALID")
    return candidate


def write_manifest(manifest, output, bundle_root):
    output = safe_output_path(output, bundle_root)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            json.dump(manifest, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except (OSError, TypeError, ValueError):
        raise ManifestError("OFFLINE_MANIFEST_WRITE_FAILED") from None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def print_error(error):
    report = {"error_code": error.code, **error.details}
    print(
        json.dumps(report, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
    )


def main(argv=None):
    parser = ManifestArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        bundle_root = args.bundle_root.resolve(strict=True)
        if not bundle_root.is_dir():
            raise ManifestError("OFFLINE_BUNDLE_ROOT_INVALID")
        output = safe_output_path(args.output, bundle_root)
        manifest = build_manifest(bundle_root, args.env_file)
        write_manifest(manifest, output, bundle_root)
        return 0
    except ManifestError as error:
        print_error(error)
        return 1
    except (OSError, RuntimeError, UnicodeError):
        print_error(ManifestError("OFFLINE_IO_FAILED"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
