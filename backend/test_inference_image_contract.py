import importlib.util
import hashlib
import inspect
import io
import json
import os
import re
import runpy
import shutil
import shlex
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CORE_REQUIREMENTS = ROOT / "docker" / "requirements" / "inference-core.txt"
WORKER_REQUIREMENTS = ROOT / "docker" / "requirements" / "inference-worker.txt"
DOCKERIGNORE = ROOT / "docker" / "Dockerfile.inference-gpu.dockerignore"
DOCKERFILE = ROOT / "docker" / "Dockerfile.inference-gpu"
PROD_COMPOSE = ROOT / "docker-compose.prod.yml"
GPU_COMPOSE = ROOT / "docker-compose.gpu.yml"
INFERENCE_DEV_COMPOSE = ROOT / "docker-compose.inference-dev.yml"
IMAGE_CHECKER = ROOT / "docker" / "check-inference-image.py"
RUNTIME_CHECKER = ROOT / "docker" / "check-inference-runtime.py"
WORKER_ENTRYPOINT = ROOT / "docker" / "start-inference-worker.sh"
MMCV_CACHE_HELPER = ROOT / "docker" / "build-mmcv-wheel.py"
PIP_INSTALL_HELPER = ROOT / "docker" / "pip-install-with-retry.py"
PIP_NETWORK_RETRY = ROOT / "docker" / "pip_network_retry.py"
DINO_RUNTIME_HUBCONF = ROOT / "docker" / "dinov3-hubconf.py"
POWERSHELL_BUILD_WRAPPER = ROOT / "docker" / "build-inference-image.ps1"
BASH_BUILD_WRAPPER = ROOT / "docker" / "build-inference-image.sh"
OFFLINE_MANIFEST = ROOT / "docker" / "offline_bundle_manifest.py"
IMAGE_BUNDLE_ENV = ROOT / "image_bundle.env"

FINAL_ASSET_EXCLUSIONS = {
    "**/*.pth",
    "**/*.whl",
    "**/*.pdiparams",
    "**/*.pdmodel",
    "**/*.pdopt",
    "**/*.onnx",
    "**/*.ckpt",
    "**/*.safetensors",
    "**/*.bin",
    "**/*.weights",
    "**/*.gz",
    "**/*Zone.Identifier*",
}
VENDORED_ROOT = "backend/model/mmseg_config/dinov3_swinV1"
VENDORED_DIRECTORY_EXCLUSIONS = {
    f"{VENDORED_ROOT}/**/{name}/"
    for name in (
        ".ipynb_checkpoints",
        "docs",
        "tests",
        "demo",
        "results",
        "data",
        "eval",
        "resources",
        "tools",
        "train",
    )
}
AUDIT_DIRECTORY_EXCLUSIONS = {
    "backend/.tmp_test_outputs/",
    f"{VENDORED_ROOT}/**/.git/",
    f"{VENDORED_ROOT}/**/notebooks/",
}


def meaningful_lines(path):
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def load_script_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OfflineBundleManifestContractTest(unittest.TestCase):
    REQUIRED_ARTIFACTS = {
        "app_image_tar": "images/app.tar",
        "inference_image_tar": "images/inference.tar",
        "mysql_image_tar": "images/mysql.tar",
        "inference_checkpoint": (
            "backend/model/mmseg_config/model.inference.pth"
        ),
        "compose_prod": "docker-compose.prod.yml",
        "compose_gpu": "docker-compose.gpu.yml",
        "config_template": "config.yaml",
    }

    def make_fixture(self, root):
        payloads = {}
        for index, relative_path in enumerate(self.REQUIRED_ARTIFACTS.values()):
            path = root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = f"artifact-{index}\n".encode("utf-8")
            path.write_bytes(payload)
            payloads[relative_path] = payload
        env_file = root / "image_bundle.env"
        env_file.write_text(
            "APP_IMAGE_TAR=app.tar\n"
            "INFERENCE_IMAGE_TAR=inference.tar\n"
            "MYSQL_IMAGE_TAR=mysql.tar\n",
            encoding="utf-8",
        )
        return env_file, payloads

    def run_manifest(self, root, env_file, output=None):
        output = output or root / "offline-bundle-manifest.json"
        result = subprocess.run(
            [
                sys.executable,
                str(OFFLINE_MANIFEST),
                "--bundle-root",
                str(root),
                "--env-file",
                str(env_file),
                "--output",
                str(output),
            ],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        return result, output

    def assert_stable_error(self, result, code, root):
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(str(root.resolve()), result.stderr)
        error = json.loads(result.stderr)
        self.assertEqual(error["error_code"], code)
        return error

    def test_manifest_records_sorted_relative_paths_sizes_and_lowercase_sha256(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, payloads = self.make_fixture(root)

            result, output = self.run_manifest(root, env_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads(output.read_text(encoding="utf-8"))
            artifacts = manifest["artifacts"]
            self.assertEqual(list(artifacts), sorted(self.REQUIRED_ARTIFACTS))
            self.assertEqual(set(artifacts), set(self.REQUIRED_ARTIFACTS))
            for key, relative_path in self.REQUIRED_ARTIFACTS.items():
                with self.subTest(artifact=key):
                    payload = payloads[relative_path]
                    entry = artifacts[key]
                    self.assertEqual(entry["path"], relative_path)
                    self.assertEqual(entry["bytes"], len(payload))
                    self.assertEqual(
                        entry["sha256"], hashlib.sha256(payload).hexdigest()
                    )
                    self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")

    def test_missing_required_artifact_has_stable_error_code(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            missing_relative = self.REQUIRED_ARTIFACTS["compose_gpu"]
            (root / missing_relative).unlink()

            result, output = self.run_manifest(root, env_file)

            self.assertEqual(result.returncode, 1)
            self.assertFalse(output.exists())
            error = json.loads(result.stderr)
            self.assertEqual(error["error_code"], "OFFLINE_ARTIFACT_MISSING")
            self.assertEqual(error["artifact_key"], "compose_gpu")
            self.assertEqual(error["path"], missing_relative)

    def test_manifest_includes_optional_maps_and_volume_backups(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            optional_paths = {
                "map:dali/base.tif": "maps/dali/base.tif",
                "volume_backup:mysql.tar": "volumes/mysql.tar",
            }
            for relative_path in optional_paths.values():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative_path.encode("utf-8"))

            result, output = self.run_manifest(root, env_file)

            self.assertEqual(result.returncode, 0, result.stderr)
            artifacts = json.loads(output.read_text(encoding="utf-8"))["artifacts"]
            for artifact_key, relative_path in optional_paths.items():
                with self.subTest(artifact=artifact_key):
                    self.assertEqual(artifacts[artifact_key]["path"], relative_path)

    def test_image_tar_env_values_must_be_safe_nonempty_basenames(self):
        invalid_values = ("", "../escape.tar", "/tmp/escape.tar", "dir/image.tar")
        for invalid_value in invalid_values:
            with self.subTest(value=invalid_value), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                env_file, _ = self.make_fixture(root)
                env_file.write_text(
                    f"APP_IMAGE_TAR={invalid_value}\n"
                    "INFERENCE_IMAGE_TAR=inference.tar\n"
                    "MYSQL_IMAGE_TAR=mysql.tar\n",
                    encoding="utf-8",
                )

                result, output = self.run_manifest(root, env_file)

                error = self.assert_stable_error(
                    result, "OFFLINE_ENV_INVALID", root
                )
                self.assertEqual(error["env_key"], "APP_IMAGE_TAR")
                self.assertFalse(output.exists())

    def test_traversal_and_required_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            link = root / self.REQUIRED_ARTIFACTS["compose_gpu"]
            target = root / "real-compose.yml"
            target.write_text("services: {}\n", encoding="utf-8")
            link.unlink()
            try:
                link.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")

            result, _ = self.run_manifest(root, env_file)

            error = self.assert_stable_error(
                result, "OFFLINE_ARTIFACT_SYMLINK", root
            )
            self.assertEqual(error["artifact_key"], "compose_gpu")

    def test_optional_file_and_directory_symlinks_are_rejected(self):
        for symlink_directory in (False, True):
            with self.subTest(directory=symlink_directory), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                env_file, _ = self.make_fixture(root)
                external = root.parent / f"external-{root.name}"
                if symlink_directory:
                    external.mkdir()
                    link = root / "maps" / "linked"
                    link.parent.mkdir()
                else:
                    external.write_text("external", encoding="utf-8")
                    link = root / "volumes" / "linked.tar"
                    link.parent.mkdir()
                try:
                    link.symlink_to(external, target_is_directory=symlink_directory)
                    result, _ = self.run_manifest(root, env_file)
                except OSError as error:
                    self.skipTest(f"symlink unavailable: {error}")
                finally:
                    if external.is_dir():
                        external.rmdir()
                    elif external.exists():
                        external.unlink()

                self.assert_stable_error(
                    result, "OFFLINE_ARTIFACT_SYMLINK", root
                )

    def test_required_directory_is_not_accepted_as_an_artifact(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            artifact = root / self.REQUIRED_ARTIFACTS["compose_gpu"]
            artifact.unlink()
            artifact.mkdir()

            result, _ = self.run_manifest(root, env_file)

            self.assert_stable_error(result, "OFFLINE_ARTIFACT_NOT_FILE", root)

    def test_output_must_be_safe_inside_root_and_outside_optional_trees(self):
        cases = (
            lambda root: root.parent / f"outside-{root.name}.json",
            lambda root: root / "maps" / "manifest.json",
            lambda root: root / "volumes" / "manifest.json",
        )
        for make_output in cases:
            with tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                env_file, _ = self.make_fixture(root)
                output = make_output(root)
                output.parent.mkdir(parents=True, exist_ok=True)

                result, _ = self.run_manifest(root, env_file, output)

                self.assert_stable_error(result, "OFFLINE_OUTPUT_INVALID", root)
                self.assertFalse(output.exists())

    @unittest.skipUnless(
        os.path.normcase("Maps") == os.path.normcase("maps"),
        "requires a case-insensitive filesystem",
    )
    def test_output_rejects_case_aliases_of_optional_trees_on_windows(self):
        for directory_name in ("Maps", "VOLUMES"):
            with self.subTest(directory=directory_name), tempfile.TemporaryDirectory() as temporary_directory:
                root = Path(temporary_directory)
                env_file, _ = self.make_fixture(root)
                output = root / directory_name / "manifest.json"
                output.parent.mkdir()

                result, _ = self.run_manifest(root, env_file, output)

                self.assert_stable_error(result, "OFFLINE_OUTPUT_INVALID", root)
                self.assertFalse(output.exists())

    @unittest.skipIf(
        os.path.normcase("Maps") == os.path.normcase("maps"),
        "requires a case-sensitive filesystem",
    )
    def test_output_allows_distinct_capitalized_maps_tree_on_linux(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            output = root / "Maps" / "manifest.json"
            output.parent.mkdir()

            result, _ = self.run_manifest(root, env_file, output)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())

    def test_existing_output_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            target = root.parent / f"manifest-target-{root.name}.json"
            target.write_text("preserve", encoding="utf-8")
            output = root / "manifest.json"
            try:
                output.symlink_to(target)
                result, _ = self.run_manifest(root, env_file, output)
            except OSError as error:
                self.skipTest(f"symlink unavailable: {error}")
            finally:
                if target.exists():
                    preserved = target.read_text(encoding="utf-8")
                    target.unlink()

            self.assert_stable_error(result, "OFFLINE_OUTPUT_INVALID", root)
            self.assertEqual(preserved, "preserve")

    def test_manifest_write_is_atomic_and_cleans_temp_on_replace_failure(self):
        manifest_module = load_script_module(
            OFFLINE_MANIFEST, "offline_bundle_manifest_atomic_test"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output = root / "manifest.json"
            output.write_text("old", encoding="utf-8")

            with patch.object(
                manifest_module.os, "replace", side_effect=OSError("denied")
            ):
                with self.assertRaises(manifest_module.ManifestError) as raised:
                    manifest_module.write_manifest(
                        {"schema_version": 1, "artifacts": {}}, output, root
                    )

            self.assertEqual(
                raised.exception.code, "OFFLINE_MANIFEST_WRITE_FAILED"
            )
            self.assertEqual(output.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(root.glob(".manifest.json.*.tmp")), [])

    def test_manifest_io_failure_from_main_is_stable_json_without_host_path(self):
        manifest_module = load_script_module(
            OFFLINE_MANIFEST, "offline_bundle_manifest_io_test"
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            output = root / "manifest.json"
            stderr = io.StringIO()
            with patch.object(
                manifest_module,
                "sha256_file",
                side_effect=OSError("host path must stay private"),
            ), redirect_stderr(stderr):
                exit_code = manifest_module.main(
                    [
                        "--bundle-root",
                        str(root),
                        "--env-file",
                        str(env_file),
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 1)
            self.assertFalse(output.exists())
            self.assertNotIn(str(root.resolve()), stderr.getvalue())
            error = json.loads(stderr.getvalue())
            self.assertEqual(
                error["error_code"], "OFFLINE_ARTIFACT_IO_FAILED"
            )

    def test_duplicate_artifact_keys_are_rejected_by_common_add_path(self):
        manifest_module = load_script_module(
            OFFLINE_MANIFEST, "offline_bundle_manifest_duplicate_test"
        )
        artifacts = {}
        manifest_module.add_artifact(artifacts, "same", {"path": "one"})
        with self.assertRaises(manifest_module.ManifestError) as raised:
            manifest_module.add_artifact(artifacts, "same", {"path": "two"})
        self.assertEqual(
            raised.exception.code, "OFFLINE_ARTIFACT_DUPLICATE"
        )

    def test_malformed_or_unreadable_env_has_stable_json_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            env_file, _ = self.make_fixture(root)
            env_file.write_text("NOT-AN-ASSIGNMENT\n", encoding="utf-8")
            result, _ = self.run_manifest(root, env_file)
            self.assert_stable_error(result, "OFFLINE_ENV_INVALID", root)

            result, _ = self.run_manifest(root, root / "missing.env")
            self.assert_stable_error(result, "OFFLINE_ENV_READ_FAILED", root)

    def test_invalid_cli_format_has_stable_json_error(self):
        result = subprocess.run(
            [sys.executable, str(OFFLINE_MANIFEST), "--bundle-root", "."],
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("usage:", result.stderr)
        self.assertEqual(
            json.loads(result.stderr)["error_code"],
            "OFFLINE_ARGUMENT_INVALID",
        )


class InferenceBuildWrapperContractTest(unittest.TestCase):
    def make_fake_docker(self, directory):
        fake = directory / "docker"
        fake.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            "count_file=\"${FAKE_DOCKER_LOG_DIR}/count\"\n"
            "count=0\n"
            "test ! -f \"${count_file}\" || count=$(cat \"${count_file}\")\n"
            "count=$((count + 1))\n"
            "printf '%s' \"${count}\" > \"${count_file}\"\n"
            "printf '%s\\n' \"$@\" > \"${FAKE_DOCKER_LOG_DIR}/${count}.argv\"\n"
            "test \"${FAKE_DOCKER_FAIL_AT:-0}\" != \"${count}\" || exit 9\n"
            "if test \"${1:-}\" = image && test \"${2:-}\" = inspect; then\n"
            "  printf 'sha256:candidate 12345\\n'\n"
            "fi\n",
            encoding="utf-8",
            newline="\n",
        )
        fake.chmod(0o755)
        return fake

    def make_fake_docker_powershell(self, directory):
        fake = directory / "docker.ps1"
        fake.write_text(
            "$countFile = Join-Path $env:FAKE_DOCKER_LOG_DIR 'count'\n"
            "$count = 0\n"
            "if (Test-Path $countFile) { $count = [int](Get-Content $countFile) }\n"
            "$count += 1\n"
            "Set-Content -LiteralPath $countFile -Value $count -NoNewline "
            "-Encoding UTF8\n"
            "$args | Set-Content -LiteralPath "
            "(Join-Path $env:FAKE_DOCKER_LOG_DIR \"$count.argv\") "
            "-Encoding UTF8\n"
            "if ([int]$env:FAKE_DOCKER_FAIL_AT -eq $count) { exit 9 }\n"
            "if ($args[0] -eq 'image' -and $args[1] -eq 'inspect') {\n"
            "  Write-Output 'sha256:candidate 12345'\n"
            "}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        return fake

    def run_bash_wrapper(self, checkpoint, candidate, canonical, fail_at=0):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory(
            dir=ROOT, prefix=".fake-docker-"
        ) as fake_directory:
            fake_root = Path(fake_directory)
            self.make_fake_docker(fake_root)
            log_root = fake_root / "logs"
            log_root.mkdir()
            environment = os.environ.copy()
            checkpoint_argument = str(checkpoint)
            if not Path(checkpoint).is_absolute():
                checkpoint_argument = Path(checkpoint).as_posix()
            result = subprocess.run(
                [
                    bash,
                    "-c",
                    f'export DOCKER="$PWD/{fake_root.name}/docker"; '
                    f'export FAKE_DOCKER_LOG_DIR="$PWD/{fake_root.name}/logs"; '
                    f'export FAKE_DOCKER_FAIL_AT="{fail_at}"; '
                    "exec bash docker/build-inference-image.sh "
                    f"{shlex.quote(candidate)} {shlex.quote(canonical)} "
                    f"{shlex.quote(checkpoint_argument)}",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            calls = [
                path.read_text(encoding="utf-8").splitlines()
                for path in sorted(log_root.glob("*.argv"))
            ]
            return result, calls

    def run_powershell_wrapper(
        self, checkpoint, candidate, canonical, fail_at=0
    ):
        powershell = shutil.which("powershell")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as fake_directory:
            fake_root = Path(fake_directory)
            self.make_fake_docker_powershell(fake_root)
            log_root = fake_root / "logs"
            log_root.mkdir()
            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": str(fake_root) + os.pathsep + environment["PATH"],
                    "FAKE_DOCKER_LOG_DIR": str(log_root),
                    "FAKE_DOCKER_FAIL_AT": str(fail_at),
                }
            )
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(POWERSHELL_BUILD_WRAPPER),
                    "-CandidateImage",
                    candidate,
                    "-CanonicalImage",
                    canonical,
                    "-CheckpointPath",
                    str(checkpoint),
                    "-DockerCommand",
                    str(fake_root / "docker.ps1"),
                ],
                cwd=ROOT.parent,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            calls = [
                path.read_text(encoding="utf-8-sig").splitlines()
                for path in sorted(log_root.glob("*.argv"))
            ]
            return result, calls

    def test_wrappers_build_and_verify_before_canonical_tag_without_cleanup(self):
        for wrapper in (POWERSHELL_BUILD_WRAPPER, BASH_BUILD_WRAPPER):
            with self.subTest(wrapper=wrapper.name):
                content = wrapper.read_text(encoding="utf-8")
                lowered = content.lower()
                self.assertIn("inference-worker", content)
                self.assertIn("check-inference-image.py", content)
                self.assertIn("check-inference-runtime.py", content)
                self.assertIn("image inspect", lowered)
                self.assertIn("image tag", lowered)
                self.assertLess(
                    content.index("check-inference-runtime.py"),
                    lowered.index("image tag"),
                )
                self.assertLess(lowered.index("image inspect"), lowered.index("image tag"))
                self.assertNotRegex(lowered, r"\b(rmi|prune)\b")

    def test_image_bundle_adds_inference_values_without_changing_existing_values(self):
        values = dict(
            line.split("=", 1)
            for line in meaningful_lines(IMAGE_BUNDLE_ENV)
        )
        self.assertEqual(values["APP_IMAGE"], "yunnan-runtime:current")
        self.assertEqual(
            values["MYSQL_IMAGE"],
            "registry.openanolis.cn/openanolis/mysql:8.0.30-8.6",
        )
        self.assertEqual(values["APP_IMAGE_TAR"], "yunnan_runtime_current.tar")
        self.assertEqual(values["MYSQL_IMAGE_TAR"], "mysql_8.0.30-8.6.tar")
        self.assertEqual(values["INFERENCE_IMAGE"], "yunnan-inference-worker:current")
        self.assertEqual(
            values["INFERENCE_IMAGE_TAR"],
            "yunnan_inference_worker_current.tar",
        )

    def test_bash_wrapper_rejects_equivalent_tags_before_docker(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            checkpoint = Path(temporary_directory) / "model.pth"
            checkpoint.write_bytes(b"checkpoint")
            result, calls = self.run_bash_wrapper(
                checkpoint.relative_to(ROOT),
                "worker",
                "docker.io/library/worker:latest",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertIn("INFERENCE_IMAGE_TAG_CONFLICT", result.stderr)

    def test_bash_wrapper_uses_absolute_checkpoint_and_tags_only_after_checks(self):
        with tempfile.TemporaryDirectory(
            dir=ROOT, prefix=".wrapper fixture "
        ) as temporary_directory:
            checkpoint = Path(temporary_directory) / "model file.pth"
            checkpoint.write_bytes(b"checkpoint")
            relative_checkpoint = checkpoint.relative_to(ROOT)
            result, calls = self.run_bash_wrapper(
                relative_checkpoint, "candidate:test", "canonical:test"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call[:2] for call in calls], [
            ["build", "--progress=plain"],
            ["run", "--rm"],
            ["run", "--rm"],
            ["image", "inspect"],
            ["image", "tag"],
        ])
        mount_index = calls[2].index("--volume") + 1
        mounted_source = calls[2][mount_index].split(":/app/backend/", 1)[0]
        self.assertTrue(mounted_source.startswith("/"))
        self.assertIn("model file.pth", mounted_source)

    def test_bash_wrapper_never_tags_after_any_pre_tag_failure(self):
        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory(
                dir=ROOT
            ) as temporary_directory:
                checkpoint = Path(temporary_directory) / "model.pth"
                checkpoint.write_bytes(b"checkpoint")
                result, calls = self.run_bash_wrapper(
                    checkpoint.relative_to(ROOT),
                    "candidate:test",
                    "canonical:test",
                    fail_at,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(["image", "tag"], [call[:2] for call in calls])

    def test_powershell_wrapper_rejects_equivalent_tags_before_docker(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            checkpoint = Path(temporary_directory) / "model.pth"
            checkpoint.write_bytes(b"checkpoint")
            result, calls = self.run_powershell_wrapper(
                checkpoint,
                "worker",
                "index.docker.io/library/worker:latest",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertIn("INFERENCE_IMAGE_TAG_CONFLICT", result.stderr)

    def test_powershell_wrapper_preserves_absolute_checkpoint_with_spaces(self):
        with tempfile.TemporaryDirectory(
            dir=ROOT, prefix=".wrapper ps fixture "
        ) as temporary_directory:
            checkpoint = Path(temporary_directory) / "model file.pth"
            checkpoint.write_bytes(b"checkpoint")
            result, calls = self.run_powershell_wrapper(
                checkpoint, "candidate:test", "canonical:test"
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1][:2], ["image", "tag"])
        mount_index = calls[2].index("--volume") + 1
        mounted_source = calls[2][mount_index].split(":/app/backend/", 1)[0]
        self.assertEqual(Path(mounted_source), checkpoint.resolve())

    def test_powershell_wrapper_never_tags_after_any_pre_tag_failure(self):
        for fail_at in range(1, 5):
            with self.subTest(fail_at=fail_at), tempfile.TemporaryDirectory(
                dir=ROOT
            ) as temporary_directory:
                checkpoint = Path(temporary_directory) / "model.pth"
                checkpoint.write_bytes(b"checkpoint")
                result, calls = self.run_powershell_wrapper(
                    checkpoint,
                    "candidate:test",
                    "canonical:test",
                    fail_at,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(["image", "tag"], [call[:2] for call in calls])

    def test_wrappers_reject_missing_checkpoint_before_docker(self):
        missing = ROOT / "missing-wrapper-checkpoint.pth"
        bash_result, bash_calls = self.run_bash_wrapper(
            missing.relative_to(ROOT), "candidate:test", "canonical:test"
        )
        powershell_result, powershell_calls = self.run_powershell_wrapper(
            missing, "candidate:test", "canonical:test"
        )
        for result, calls in (
            (bash_result, bash_calls),
            (powershell_result, powershell_calls),
        ):
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])
            self.assertIn("MODEL_CHECKPOINT_MISSING", result.stderr)


def package_name(requirement):
    name = re.split(r"[<>=!~\[\s]", requirement, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def docker_glob_regex(pattern):
    parts = []
    index = 0
    while index < len(pattern):
        if pattern[index : index + 3] == "**/":
            parts.append("(?:.*/)?")
            index += 3
        elif pattern[index : index + 2] == "**":
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return "".join(parts)


def docker_rule_matches(path, rule):
    path = path.strip("/")
    pattern = rule.lstrip("/")
    directory_rule = pattern.endswith("/")
    pattern = pattern.rstrip("/")

    if pattern == "**":
        return True

    suffix = "(?:/.*)?" if directory_rule else ""
    return re.fullmatch(f"{docker_glob_regex(pattern)}{suffix}", path) is not None


def context_includes(path, rules):
    included = True
    for rule in rules:
        negated = rule.startswith("!")
        pattern = rule[1:] if negated else rule
        if docker_rule_matches(path, pattern):
            included = negated
    return included


class InferenceRequirementsContractTest(unittest.TestCase):
    def assert_constrained_requirements(self, path, expected, forbidden):
        self.assertTrue(path.is_file(), f"缺少依赖锁文件: {path}")
        self.assertGreater(path.stat().st_size, 0, f"依赖锁文件为空: {path}")

        requirements = meaningful_lines(path)
        names = [package_name(requirement) for requirement in requirements]
        self.assertEqual(len(names), len(set(names)), f"存在重复包名: {path}")
        self.assertEqual(set(requirements), expected)
        forbidden_matches = set(names) & forbidden
        self.assertFalse(
            forbidden_matches,
            f"包含禁止包: {', '.join(sorted(forbidden_matches))}",
        )

    def test_core_requirements_are_minimal_and_constrained(self):
        self.assert_constrained_requirements(
            CORE_REQUIREMENTS,
            {
                "mmengine==0.10.4",
                "mmsegmentation==1.2.2",
                "numpy==1.26.4",
                "opencv-python-headless==4.10.0.84",
                "rasterio==1.4.4",
                "shapely==1.8.5.post1",
                "Pillow>=9.2.0,<12",
                "PyYAML>=6.0,<7",
            },
            {"torch", "torchvision", "mmcv"},
        )

    def test_worker_requirements_exclude_web_only_packages(self):
        self.assert_constrained_requirements(
            WORKER_REQUIREMENTS,
            {
                "Flask==2.2.2",
                "Flask-SQLAlchemy==2.5.1",
                "SQLAlchemy==1.4.46",
                "PyMySQL==1.2.0",
                "Werkzeug==2.2.3",
                "python-dotenv>=0.21.0,<2",
            },
            {"flask-cors", "flask-marshmallow", "marshmallow", "gunicorn"},
        )


class InferenceDockerignoreContractTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(DOCKERIGNORE.is_file(), f"缺少专用 ignore 文件: {DOCKERIGNORE}")
        self.assertGreater(DOCKERIGNORE.stat().st_size, 0, "专用 ignore 文件为空")
        self.rules = meaningful_lines(DOCKERIGNORE)

    def test_context_is_an_allowlist_with_traversable_parent_directories(self):
        self.assertEqual(self.rules[0], "**")
        self.assertLess(self.rules.index("**"), self.rules.index("!backend/"))
        self.assertLess(self.rules.index("!backend/"), self.rules.index("!backend/**"))
        self.assertLess(self.rules.index("**"), self.rules.index("!docker/"))
        self.assertLess(self.rules.index("!docker/"), self.rules.index("!docker/**"))
        self.assertLess(self.rules.index("**"), self.rules.index("!config.yaml"))

    def test_representative_context_paths_follow_last_match_wins(self):
        allowed = {
            "backend/applications/inference/worker.py",
            "docker/check-inference-runtime.py",
            "config.yaml",
            "backend/model/mmseg_config/dinov3_swinV1.py",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/__init__.py",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/dinov3/__init__.py",
            "backend/nested/frontend/secret.dat",
        }
        rejected = {
            "backend/model/mmseg_config/model.pth",
            "model.inference.pth",
            "backend/model/object_detection/yolo/model.pdiparams",
            "semantic_segmentation/deeplab/model.pdmodel",
            "backend/model/runtime/model.pdopt",
            "backend/model/runtime/model.onnx",
            "backend/model/runtime/model.ckpt",
            "backend/model/runtime/model.safetensors",
            "backend/model/runtime/model.bin",
            "backend/model/runtime/model.weights",
            "backend/applications/inference/worker.py.Zone.Identifier",
            "backend/cache/part-Zone.Identifier-backup",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/docs/index.md",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/tests/test_api.py",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/demo/demo.py",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/results/result.json",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/data/sample.bin",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/resources/logo.png",
            "backend/model/mmseg_config/dinov3_swinV1/mmseg/tools/train.py",
            "backend/.tmp_test_outputs/seg_1119_before.png",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/.git/objects/pack/model.pack",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/notebooks/demo.ipynb",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/.ipynb_checkpoints/demo-checkpoint.ipynb",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/dinov3/train/train.py",
            "backend/model/mmseg_config/dinov3_swinV1/dinov3/dinov3/eval/utils.py",
            "backend/model/mmseg_config/dinov3_swinV1/projects/CAT-Seg/cat_seg/utils/bpe_vocab/bpe_simple_vocab_16e6.txt.gz",
            "backend/test_inference_jobs.py",
            "frontend/src/App.vue",
            "miner/main.py",
            "maps/tile.png",
            "volumes/mysql/data.ibd",
            "logs/backend.log",
            "images/runtime.tar",
            "other/config.yaml",
        }

        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(context_includes(path, self.rules), path)
        for path in rejected:
            with self.subTest(path=path):
                self.assertFalse(context_includes(path, self.rules), path)

    def test_large_generated_and_out_of_scope_content_is_reexcluded(self):
        exclusions = (
            FINAL_ASSET_EXCLUSIONS
            | VENDORED_DIRECTORY_EXCLUSIONS
            | AUDIT_DIRECTORY_EXCLUSIONS
            | {
            "**/__pycache__/",
            "**/*.pyc",
            "backend/test*.py",
            "frontend/",
            "miner/",
            "maps/",
            "volumes/",
            "logs/",
            "images/",
            }
        )
        self.assertTrue(exclusions.issubset(self.rules))

        last_runtime_reinclude = max(
            index for index, rule in enumerate(self.rules) if rule.startswith("!")
        )
        for rule in FINAL_ASSET_EXCLUSIONS:
            self.assertGreater(self.rules.index(rule), last_runtime_reinclude)


class InferenceDockerfileContractTest(unittest.TestCase):
    def setUp(self):
        self.text = DOCKERFILE.read_text(encoding="utf-8")

    def test_uses_builtin_buildkit_frontend_without_remote_syntax_dependency(self):
        self.assertNotRegex(self.text, r"(?m)^\s*#\s*syntax\s*=")

    def test_uses_named_cuda_runtime_and_builder_stages(self):
        self.assertRegex(
            self.text,
            r"FROM\s+nvidia/cuda:12\.8\.0-base-ubuntu22\.04\s+AS\s+inference-base",
        )
        self.assertRegex(
            self.text,
            r"FROM\s+nvidia/cuda:12\.8\.0-devel-ubuntu22\.04\s+AS\s+mmcv-builder",
        )
        self.assertRegex(self.text, r"FROM\s+inference-base\s+AS\s+inference-core")
        self.assertRegex(self.text, r"FROM\s+inference-core\s+AS\s+inference-worker")

    def test_uses_python_venv_without_conda_or_cuda_build_tools_in_final_stage(self):
        self.assertIn("python3 -m venv /opt/venv", self.text)
        self.assertIn('PATH="/opt/venv/bin:', self.text)
        self.assertNotIn("/opt/conda", self.text)
        worker_stage = self.text.split("FROM inference-core AS inference-worker", 1)[1]
        self.assertNotRegex(worker_stage, r"\b(nvcc|build-essential|cuda-toolkit)\b")

    def test_both_apt_stages_use_https_sources_and_explicit_retries(self):
        base_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-base-ubuntu22.04 AS inference-base", 1
        )[1].split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[0]
        builder_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[1].split("FROM inference-base AS inference-core", 1)[0]

        scripts = []
        for name, stage, expected_runs in (
            ("inference-base", base_stage, 1),
            ("mmcv-builder", builder_stage, 2),
        ):
            with self.subTest(stage=name):
                matches = re.findall(
                    r"sed -i -E '([^']+)' /etc/apt/sources\.list", stage
                )
                self.assertEqual(len(matches), expected_runs)
                scripts.extend(matches)
                self.assertIn("apt-get -o Acquire::Retries=5 update", stage)
                self.assertIn("apt-get -o Acquire::Retries=5 install", stage)

        self.assertTrue(all(script == scripts[0] for script in scripts[1:]))
        sources = (
            "deb http://archive.ubuntu.com/ubuntu/ jammy main\n"
            "deb http://security.ubuntu.com/ubuntu/ jammy-security main\n"
        )
        result = subprocess.run(
            ["bash", "-c", f"sed -E '{scripts[0]}'"],
            input=sources,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            sources.replace("http://archive", "https://archive").replace(
                "http://security", "https://security"
            ),
        )

    def test_network_pip_boundaries_are_cached_split_and_finitely_retried(self):
        base_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-base-ubuntu22.04 AS inference-base", 1
        )[1].split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[0]
        builder_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[1].split("FROM inference-base AS inference-core", 1)[0]
        core_stage = self.text.split("FROM inference-base AS inference-core", 1)[1].split(
            "FROM inference-core AS inference-worker", 1
        )[0]

        base_pip_run = base_stage.index("RUN --mount=type=cache,target=/root/.cache/pip")
        self.assertLess(base_stage.index("python3 -m venv /opt/venv"), base_pip_run)
        self.assertNotIn("pip install", base_stage[:base_pip_run])

        for name, stage in (
            ("inference-base", base_stage),
            ("mmcv-builder", builder_stage),
            ("inference-core", core_stage),
        ):
            with self.subTest(stage=name):
                self.assertIn("RUN --mount=type=cache,target=/root/.cache/pip", stage)
                self.assertIn(
                    "python /usr/local/bin/pip-install-with-retry.py", stage
                )

    def test_mmcv_cache_is_fingerprinted_and_every_wheel_is_abi_checked(self):
        for value in (
            '--python-version "${PYTHON_VERSION}"',
            '--torch-version "${TORCH_VERSION}"',
            '--cuda-version "${CUDA_VERSION}"',
            '--mmcv-version "${MMCV_VERSION}"',
            '--arch-list "${TORCH_CUDA_ARCH_LIST}"',
        ):
            self.assertIn(value, self.text)
        self.assertIn("MMCV_WITH_OPS=1", self.text)
        self.assertIn("MAX_JOBS=2", self.text)
        self.assertIn('TORCH_CUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0;10.0;12.0+PTX"', self.text)
        self.assertIn("build-mmcv-wheel.py", self.text)
        helper = MMCV_CACHE_HELPER.read_text(encoding="utf-8")
        self.assertIn("--no-binary", helper)
        self.assertIn("mmcv", helper)
        self.assertRegex(helper, r"from mmcv\.ops import roi_align")

    def test_mmcv_builder_installs_core_lock_after_cached_torch_layer(self):
        builder_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[1].split("FROM inference-base AS inference-core", 1)[0]
        torch_install = builder_stage.index(f'"torch==${{TORCH_VERSION}}"')
        core_copy = builder_stage.index(
            "COPY docker/requirements/inference-core.txt "
            "/tmp/requirements/inference-core.txt"
        )
        core_install = builder_stage.index(
            "-r /tmp/requirements/inference-core.txt", core_copy
        )
        helper_run = builder_stage.index(
            "python /usr/local/bin/build-mmcv-wheel.py"
        )

        self.assertLess(torch_install, core_copy)
        self.assertLess(core_copy, core_install)
        self.assertLess(core_install, helper_run)

    def test_mmcv_builder_adds_validation_runtime_after_cached_python_layers(self):
        builder_stage = self.text.split(
            "FROM nvidia/cuda:12.8.0-devel-ubuntu22.04 AS mmcv-builder", 1
        )[1].split("FROM inference-base AS inference-core", 1)[0]
        core_install = builder_stage.index("-r /tmp/requirements/inference-core.txt")
        validation_run = builder_stage.index("# MMCV validation runtime libraries")
        helper_copy = builder_stage.index(
            "COPY docker/build-mmcv-wheel.py /usr/local/bin/build-mmcv-wheel.py"
        )
        validation_stage = builder_stage[validation_run:helper_copy]

        self.assertLess(core_install, validation_run)
        self.assertLess(validation_run, helper_copy)
        self.assertIn("https://archive.ubuntu.com/ubuntu", validation_stage)
        self.assertIn("https://security.ubuntu.com/ubuntu", validation_stage)
        self.assertIn("apt-get -o Acquire::Retries=5 update", validation_stage)
        self.assertIn("apt-get -o Acquire::Retries=5 install", validation_stage)
        for package in (
            "libgl1",
            "libglib2.0-0",
            "libgomp1",
            "libsm6",
            "libxext6",
            "libxrender1",
        ):
            with self.subTest(package=package):
                self.assertRegex(validation_stage, rf"(?m)^\s+{re.escape(package)}\s+\\$")
        self.assertIn("rm -rf /var/lib/apt/lists/*", validation_stage)

    def test_core_installs_both_dependency_locks_before_worker_code(self):
        core_stage, worker_stage = self.text.split(
            "FROM inference-core AS inference-worker", 1
        )
        self.assertIn(
            "COPY docker/requirements/inference-core.txt",
            core_stage,
        )
        self.assertIn(
            "COPY docker/requirements/inference-worker.txt",
            core_stage,
        )
        self.assertIn("-r /tmp/requirements/inference-core.txt", core_stage)
        self.assertIn("-r /tmp/requirements/inference-worker.txt", core_stage)
        self.assertNotIn("pip install", worker_stage)

    def test_final_stage_copies_only_runtime_allowlist_and_runs_contract_checker(self):
        worker_stage = self.text.split("FROM inference-core AS inference-worker", 1)[1]
        copy_sources = re.findall(r"^COPY\s+(?:--[^ ]+\s+)*([^\s]+)", worker_stage, re.MULTILINE)
        self.assertTrue(copy_sources)
        self.assertFalse(any(source.startswith("frontend") for source in copy_sources))
        self.assertFalse(any(source == "miner" or source.startswith("miner/") for source in copy_sources))
        self.assertFalse(any(source.endswith((".pth", ".ckpt", ".onnx")) for source in copy_sources))
        self.assertIn("python /app/docker/check-inference-image.py", worker_stage)
        self.assertIn('ENTRYPOINT ["/bin/bash", "/app/docker/start-inference-worker.sh"]', worker_stage)
        self.assertIn("USER inference", worker_stage)
        self.assertIn("HEALTHCHECK", worker_stage)

    def test_final_checker_uses_the_same_pythonpath_as_worker_startup(self):
        worker_stage = self.text.split("FROM inference-core AS inference-worker", 1)[1]
        pythonpath = (
            'ENV PYTHONPATH="/app/backend/model/mmseg_config/dinov3_swinV1:'
            '/app/backend"'
        )
        checker = "python /app/docker/check-inference-image.py"
        self.assertIn(pythonpath, worker_stage)
        self.assertLess(worker_stage.index(pythonpath), worker_stage.index(checker))

    def test_final_stage_uses_backbone_only_dinov3_hub_runtime(self):
        worker_stage = self.text.split("FROM inference-core AS inference-worker", 1)[1]
        self.assertIn(
            "COPY docker/dinov3-hubconf.py /app/backend/model/mmseg_config/dinov3_swinV1/dinov3/hubconf.py",
            worker_stage,
        )
        self.assertNotIn(
            "COPY backend/model/mmseg_config/dinov3_swinV1/dinov3/dinov3 /app/",
            worker_stage,
        )
        hubconf = DINO_RUNTIME_HUBCONF.read_text(encoding="utf-8")
        self.assertIn("dinov3_vitl16", hubconf)
        self.assertNotRegex(hubconf, r"dinov3\.(eval|train)")

    def test_runtime_hub_wrapper_disables_pretrained_but_preserves_weights(self):
        calls = []

        class FakeWeights:
            LVD1689M = object()

        def upstream(**kwargs):
            calls.append(kwargs)
            return "model"

        dinov3 = types.ModuleType("dinov3")
        hub = types.ModuleType("dinov3.hub")
        backbones = types.ModuleType("dinov3.hub.backbones")
        backbones.Weights = FakeWeights
        backbones.dinov3_vitl16 = upstream
        fake_modules = {
            "dinov3": dinov3,
            "dinov3.hub": hub,
            "dinov3.hub.backbones": backbones,
        }
        missing_weights = "/model/does-not-exist.pth"
        with patch.dict(sys.modules, fake_modules):
            namespace = runpy.run_path(str(DINO_RUNTIME_HUBCONF))
            result = namespace["dinov3_vitl16"](
                weights=missing_weights,
                pretrained=True,
                check_hash=False,
            )

        self.assertEqual(result, "model")
        self.assertEqual(
            calls,
            [
                {
                    "weights": missing_weights,
                    "pretrained": False,
                    "check_hash": False,
                }
            ],
        )


class InferenceComposeContractTest(unittest.TestCase):
    def compose_config(self, *files):
        environment = os.environ.copy()
        environment.update(
            {
                "MYSQL_ROOT_PASSWORD": "verify-root",
                "MYSQL_PASSWORD": "verify-user",
                "ADMIN_PASSWORD": "verify-admin",
                "SECRET_KEY": "verify-secret",
                "INFERENCE_RASTER_INPUT_DIR": "./backend/bianhua_2years",
                "INFERENCE_IMAGE_BUILD": "unknown",
            }
        )
        command = ["docker", "compose"]
        for path in files:
            command.extend(("-f", str(path)))
        command.extend(("config", "--format", "json"))
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
        except FileNotFoundError:
            self.skipTest("docker compose CLI is unavailable")
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    @staticmethod
    def volumes_by_target(config):
        return {
            volume["target"]: volume
            for volume in config["services"]["inference-worker"]["volumes"]
        }

    def test_production_worker_uses_legacy_image_with_host_runtime_code(self):
        config = self.compose_config(PROD_COMPOSE)
        volumes = self.volumes_by_target(config)

        self.assertEqual(
            config["services"]["inference-worker"]["image"],
            "geoview-runtime:gpu-cu128",
        )
        self.assertIn("/app/backend", volumes)
        self.assertIn("/app/docker", volumes)
        self.assertEqual(
            config["services"]["inference-worker"]["environment"][
                "INFERENCE_IMAGE_BUILD"
            ],
            "unknown",
        )
        expected_binds = {
            "/app/config.yaml": ROOT / "config.yaml",
            "/app/docker": ROOT / "docker",
            "/app/backend": ROOT / "backend",
            "/app/miner/yunnan.kml": ROOT / "miner" / "yunnan.kml",
        }
        self.assertEqual(
            {
                target
                for target, volume in volumes.items()
                if volume["type"] == "bind"
            },
            set(expected_binds),
        )
        for target, expected_source in expected_binds.items():
            with self.subTest(target=target):
                volume = volumes[target]
                self.assertEqual(volume["type"], "bind")
                self.assertTrue(volume["read_only"])
                self.assertEqual(Path(volume["source"]), expected_source.resolve())

        for target in (
            "/app/miner/uploads",
            "/app/miner/change_matrix_outputs",
            "/app/backend/runtime/inference_jobs",
        ):
            with self.subTest(named_volume=target):
                self.assertEqual(volumes[target]["type"], "volume")

        backend_targets = {
            volume["target"]: volume
            for volume in config["services"]["backend"]["volumes"]
        }
        self.assertEqual(backend_targets["/app/backend"]["type"], "bind")
        self.assertTrue(backend_targets["/app/backend"]["read_only"])
        for service_name in ("backend", "frontend", "miner-api", "miner-web"):
            with self.subTest(existing_docker_mount=service_name):
                targets = {
                    volume["target"]
                    for volume in config["services"][service_name]["volumes"]
                }
                self.assertIn("/app/docker", targets)

    def test_only_gpu_override_grants_nvidia_to_legacy_worker(self):
        production = self.compose_config(PROD_COMPOSE)
        gpu = self.compose_config(PROD_COMPOSE, GPU_COMPOSE)

        production_worker = production["services"]["inference-worker"]
        gpu_worker = gpu["services"]["inference-worker"]
        reservations = (
            production_worker.get("deploy", {}).get("resources", {}).get("reservations", {})
        )
        self.assertNotIn("devices", reservations)
        devices = gpu_worker["deploy"]["resources"]["reservations"]["devices"]
        self.assertEqual(devices[0]["driver"], "nvidia")
        self.assertIn("gpu", devices[0]["capabilities"])

        production_volumes = self.volumes_by_target(production)
        self.assertEqual(production_volumes["/app/backend"]["type"], "bind")
        self.assertTrue(production_volumes["/app/backend"]["read_only"])
        self.assertEqual(production_volumes["/app/docker"]["type"], "bind")
        self.assertTrue(production_volumes["/app/docker"]["read_only"])


class InferenceRuntimeCheckerContractTest(unittest.TestCase):
    def setUp(self):
        self.runtime = load_script_module(RUNTIME_CHECKER, "check_inference_runtime")

    def test_checkpoint_metadata_uses_fixed_path_and_8_mib_sha256_chunks(self):
        self.assertEqual(self.runtime.HASH_CHUNK_SIZE, 8 * 1024 * 1024)
        self.assertEqual(
            self.runtime.INFERENCE_CHECKPOINT_PATH,
            Path("/app/backend/model/mmseg_config/model.inference.pth"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "model.inference.pth"
            content = b"x" * (8 * 1024 * 1024 + 17)
            checkpoint.write_bytes(content)

            metadata = self.runtime.checkpoint_metadata(
                checkpoint, image_build="build-test-123"
            )

        self.assertEqual(
            metadata,
            {
                "image_build": "build-test-123",
                "checkpoint_path": str(checkpoint.resolve()),
                "checkpoint_size": len(content),
                "checkpoint_sha256": hashlib.sha256(content).hexdigest(),
            },
        )

    def test_main_reports_checkpoint_metadata_without_other_paths_or_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "model.inference.pth"
            checkpoint.write_bytes(b"checkpoint")
            resolution = types.SimpleNamespace(
                effective="cpu",
                fallback_reason=None,
                warnings=(),
                gpu_name=None,
                compute_capability=None,
            )

            class FakeResolver:
                def __init__(self, smoke_test):
                    self.smoke_test = smoke_test

                def resolve(self, **_kwargs):
                    return resolution

            device_module = types.ModuleType("applications.inference.device")
            device_module.DeviceResolver = FakeResolver
            device_module.run_mmcv_cuda_smoke_test = lambda: None
            output = io.StringIO()
            environment = {
                "INFERENCE_IMAGE_BUILD": "build-test-456",
                "INFERENCE_INPUT_ROOTS": "C:/private/raster-do-not-print",
                "MYSQL_PASSWORD": "credential-do-not-print",
            }
            with (
                patch.dict(sys.modules, {"applications.inference.device": device_module}),
                patch.dict(os.environ, environment, clear=False),
                patch.object(self.runtime, "INFERENCE_CHECKPOINT_PATH", checkpoint),
                patch.object(self.runtime, "module_version", return_value="test-version"),
                redirect_stdout(output),
            ):
                result = self.runtime.main()

        raw_report = output.getvalue()
        report = json.loads(raw_report)
        self.assertEqual(result, 0)
        self.assertEqual(report["image_build"], "build-test-456")
        self.assertEqual(report["checkpoint_path"], str(checkpoint.resolve()))
        self.assertEqual(report["checkpoint_size"], len(b"checkpoint"))
        self.assertEqual(
            report["checkpoint_sha256"], hashlib.sha256(b"checkpoint").hexdigest()
        )
        self.assertNotIn("raster-do-not-print", raw_report)
        self.assertNotIn("credential-do-not-print", raw_report)

    def test_missing_checkpoint_reports_stable_error_without_other_paths_or_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "missing-model.inference.pth"
            output = io.StringIO()
            environment = {
                "INFERENCE_IMAGE_BUILD": "missing-checkpoint-build",
                "INFERENCE_INPUT_ROOTS": "C:/private/input-do-not-print",
                "MYSQL_PASSWORD": "missing-secret-do-not-print",
            }
            with (
                patch.dict(os.environ, environment, clear=False),
                patch.object(self.runtime, "INFERENCE_CHECKPOINT_PATH", checkpoint),
                patch.object(self.runtime, "module_version", return_value="test-version"),
                redirect_stdout(output),
            ):
                result = self.runtime.main()

        raw_report = output.getvalue()
        report = json.loads(raw_report)
        self.assertEqual(result, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["error_code"], "MODEL_CHECKPOINT_MISSING")
        self.assertEqual(report["checkpoint_path"], str(checkpoint.resolve()))
        self.assertIsNone(report["checkpoint_size"])
        self.assertIsNone(report["checkpoint_sha256"])
        self.assertEqual(
            report["error"], f"MODEL_CHECKPOINT_MISSING: {checkpoint.resolve()}"
        )
        self.assertNotIn("input-do-not-print", raw_report)
        self.assertNotIn("missing-secret-do-not-print", raw_report)


class PipInstallRetryContractTest(unittest.TestCase):
    def setUp(self):
        self.retry = load_script_module(PIP_INSTALL_HELPER, "pip_install_with_retry")

    def test_success_uses_internal_network_limits_with_normal_cache(self):
        events = []

        def run(command, check):
            events.append(("run", tuple(command), check))

        self.retry.install_with_retry(
            ["package==1.0"],
            runner=run,
        )

        self.assertEqual(len(events), 1)
        command = events[0][1]
        self.assertIn("--retries", command)
        self.assertIn("12", command)
        self.assertIn("--timeout", command)
        self.assertIn("180", command)
        self.assertNotIn("--no-cache-dir", command)

    def test_transient_failures_preserve_shared_cache_and_bypass_it_on_retries(self):
        commands = []
        attempts = iter((False, False, True))

        def run(command, check):
            succeeded = next(attempts)
            commands.append(tuple(command))
            if not succeeded:
                log_path = Path(command[command.index("--log") + 1])
                message = (
                    "incomplete-download: not enough bytes; network connectivity; "
                    "No matching distribution found for package==1.0"
                    if len(commands) == 1
                    else "RemoteDisconnected: remote end closed connection"
                )
                log_path.write_text(message, encoding="utf-8")
                raise subprocess.CalledProcessError(1, command)

        self.retry.install_with_retry(
            ["package==1.0"],
            runner=run,
        )
        self.assertNotIn("--no-cache-dir", commands[0])
        for command in commands[1:]:
            self.assertEqual(
                command.index("--no-cache-dir"), command.index("install") + 1
            )

    def test_exhaustion_never_purges_shared_cache_and_reraises_last_error(self):
        commands = []

        def run(command, check):
            commands.append(tuple(command))
            log_path = Path(command[command.index("--log") + 1])
            log_path.write_text(
                "ProtocolError: connection aborted during download", encoding="utf-8"
            )
            raise subprocess.CalledProcessError(9, command)

        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.retry.install_with_retry(
                ["package==1.0"],
                attempts=3,
                runner=run,
            )

        self.assertEqual(raised.exception.returncode, 9)
        self.assertEqual(len(commands), 3)
        self.assertNotIn("--no-cache-dir", commands[0])
        self.assertIn("--no-cache-dir", commands[1])
        self.assertIn("--no-cache-dir", commands[2])

    def test_non_network_resolution_errors_fail_immediately_without_retry(self):
        for message in (
            "ERROR: ResolutionImpossible",
            "ERROR: No matching distribution found for package==99",
            "ERROR: subprocess-exited-with-error",
        ):
            events = []

            def run(command, check):
                events.append(("run",))
                Path(command[command.index("--log") + 1]).write_text(
                    message, encoding="utf-8"
                )
                raise subprocess.CalledProcessError(7, command)

            with self.subTest(message=message), self.assertRaises(
                subprocess.CalledProcessError
            ):
                self.retry.install_with_retry(
                    ["package==99"],
                    runner=run,
                )
            self.assertEqual(events, [("run",)])

    def test_main_returns_original_pip_exit_code(self):
        for returncode in (2, 7, 9):
            with self.subTest(returncode=returncode), patch.object(
                self.retry,
                "install_with_retry",
                side_effect=subprocess.CalledProcessError(returncode, ["pip"]),
            ):
                self.assertEqual(self.retry.main(["package==1.0"]), returncode)


class PipNetworkClassificationContractTest(unittest.TestCase):
    def setUp(self):
        self.network = load_script_module(PIP_NETWORK_RETRY, "pip_network_retry")

    def test_classifies_only_verified_transient_download_failures(self):
        transient = (
            "SSLEOFError EOF occurred in violation of protocol; Expected sha256 a Got b",
            "ReadTimeoutError: HTTPSConnectionPool timed out",
            "HTTP 502 Server Error: Bad Gateway",
            "incomplete-download",
            "not enough bytes received",
            "Check your network connectivity and proxy configuration",
            "RemoteDisconnected",
            "remote end closed connection without response",
            "ProtocolError",
            "connection aborted by peer",
            "network connectivity failure; No matching distribution found for Pillow",
        )
        permanent = (
            "ResolutionImpossible",
            "No matching distribution found for mmcv==99",
            "subprocess-exited-with-error: nvcc compilation failed",
        )
        for message in transient:
            with self.subTest(transient=message):
                self.assertTrue(self.network.is_transient_pip_failure(message))
        for message in permanent:
            with self.subTest(permanent=message):
                self.assertFalse(self.network.is_transient_pip_failure(message))

    def test_download_retries_bypass_cache_without_purging_shared_cache(self):
        commands = []

        def run(command, check):
            commands.append(tuple(command))
            if len(commands) == 1:
                Path(command[command.index("--log") + 1]).write_text(
                    "incomplete-download: not enough bytes", encoding="utf-8"
                )
                raise subprocess.CalledProcessError(1, command)

        self.network.run_network_pip(
            [sys.executable, "-m", "pip", "download", "mmcv==2.1.0"],
            attempts=2,
            runner=run,
        )

        self.assertNotIn("--no-cache-dir", commands[0])
        self.assertEqual(
            commands[1].index("--no-cache-dir"), commands[1].index("download") + 1
        )

    def test_retry_production_api_has_no_shared_cache_purge_surface(self):
        helper = load_script_module(PIP_INSTALL_HELPER, "pip_install_api_contract")
        self.assertNotIn(
            "clear_cache", inspect.signature(self.network.run_network_pip).parameters
        )
        self.assertNotIn(
            "clear_cache", inspect.signature(helper.install_with_retry).parameters
        )
        self.assertFalse(
            any(name.startswith("clear_pip") for name in vars(self.network))
        )
        source = PIP_NETWORK_RETRY.read_text(encoding="utf-8").lower()
        self.assertNotIn("pip cache purge", source)


class MmcvWheelCacheContractTest(unittest.TestCase):
    def setUp(self):
        self.cache = load_script_module(MMCV_CACHE_HELPER, "build_mmcv_wheel")
        self.values = {
            "python_version": "3.10",
            "torch_version": "2.7.0",
            "cuda_version": "12.8",
            "mmcv_version": "2.1.0",
            "arch_list": "7.5;8.0;8.6;8.9;9.0;10.0;12.0+PTX",
        }

    def test_cache_fingerprint_binds_every_abi_input(self):
        self.assertEqual(
            self.cache.cache_fingerprint(self.values),
            "python=3.10|torch=2.7.0|cuda=12.8|mmcv=2.1.0|"
            "arch=7.5;8.0;8.6;8.9;9.0;10.0;12.0+PTX",
        )

    def test_mmcv_source_download_uses_network_retry_and_unique_local_sdist(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)

            def retry(command):
                events.append(tuple(command))
                (destination / "mmcv-2.1.0.tar.gz").write_text(
                    "source", encoding="utf-8"
                )

            source = self.cache.download_mmcv_source(
                destination,
                self.values,
                network_runner=retry,
            )

        self.assertEqual(source.name, "mmcv-2.1.0.tar.gz")
        command = events[0]
        self.assertIn("download", command)
        self.assertIn("--no-build-isolation", command)
        self.assertIn("--no-binary", command)
        self.assertNotIn("wheel", command)

    def test_mmcv_local_compile_failure_runs_once_without_network_retry(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            staging = Path(temp_dir)
            source = staging / "mmcv-2.1.0.tar.gz"
            source.write_text("source", encoding="utf-8")

            def download(_destination, _values):
                events.append(("download",))
                return source

            def compile_once(command, check):
                events.append(("compile", tuple(command), check))
                raise subprocess.CalledProcessError(7, command)

            with self.assertRaises(subprocess.CalledProcessError):
                self.cache.build_wheel(
                    staging,
                    self.values,
                    download_source=download,
                    wheel_runner=compile_once,
                )

        self.assertEqual([event[0] for event in events], ["download", "compile"])
        compile_command = events[1][1]
        self.assertIn(str(source), compile_command)
        self.assertIn("--no-build-isolation", compile_command)
        self.assertNotIn(f"mmcv=={self.values['mmcv_version']}", compile_command)

    def test_valid_pip_cache_candidate_is_validated_published_and_skips_build(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            output = root / "output"
            candidate = root / "pip" / "mmcv-2.1.0-cp310-linux.whl"
            candidate.parent.mkdir()
            candidate.write_text("valid-candidate", encoding="utf-8")

            def validate(wheel, _values):
                events.append(f"validate:{wheel.read_text(encoding='utf-8')}")
                return True

            def unexpected_build(_staging, _values):
                events.append("build")
                raise AssertionError("valid pip cache candidate must skip source build")

            result = self.cache.prepare_mmcv_wheel(
                cache_root,
                output,
                self.values,
                build_wheel=unexpected_build,
                validate_wheel=validate,
                candidate_provider=lambda _values: [candidate],
            )

            key_dir = cache_root / self.cache.cache_key(self.values)
            self.assertEqual(events, ["validate:valid-candidate"])
            self.assertEqual(result.read_text(encoding="utf-8"), "valid-candidate")
            self.assertEqual(
                next(key_dir.glob("*.whl")).read_text(encoding="utf-8"),
                "valid-candidate",
            )
            self.assertEqual(
                (key_dir / "fingerprint.txt").read_text(encoding="utf-8"),
                self.cache.cache_fingerprint(self.values),
            )

    def test_default_candidate_provider_discovers_exact_version_in_pip_wheel_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_root = Path(temp_dir)
            wheel_dir = cache_root / "wheels" / "aa" / "bb"
            wheel_dir.mkdir(parents=True)
            expected = wheel_dir / "mmcv-2.1.0-cp310-linux.whl"
            wrong_version = wheel_dir / "mmcv-2.2.0-cp310-linux.whl"
            expected.write_text("candidate", encoding="utf-8")
            wrong_version.write_text("wrong", encoding="utf-8")
            commands = []

            def run(command, check, capture_output, text):
                commands.append((tuple(command), check, capture_output, text))
                return types.SimpleNamespace(stdout=f"{cache_root}\n")

            candidates = self.cache.pip_cache_wheel_candidates(
                self.values, runner=run
            )

        self.assertEqual(candidates, [expected])
        self.assertEqual(
            commands,
            [
                (
                    (sys.executable, "-m", "pip", "cache", "dir"),
                    True,
                    True,
                    True,
                )
            ],
        )

    def test_all_invalid_pip_cache_candidates_are_checked_before_source_build(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            candidates = []
            for name in ("bad-one", "bad-two"):
                wheel = root / f"mmcv-2.1.0-{name}.whl"
                wheel.write_text(name, encoding="utf-8")
                candidates.append(wheel)

            def validate(wheel, _values):
                content = wheel.read_text(encoding="utf-8")
                events.append(f"validate:{content}")
                return content == "built"

            def build(staging, _values):
                events.append("build")
                wheel = staging / "mmcv-2.1.0-built.whl"
                wheel.write_text("built", encoding="utf-8")
                return wheel

            result = self.cache.prepare_mmcv_wheel(
                root / "cache",
                root / "output",
                self.values,
                build_wheel=build,
                validate_wheel=validate,
                candidate_provider=lambda _values: candidates,
            )

            self.assertEqual(
                events,
                ["validate:bad-one", "validate:bad-two", "build", "validate:built"],
            )
            self.assertEqual(result.read_text(encoding="utf-8"), "built")

    def test_validation_reports_dependency_import_separately_from_abi_failure(self):
        wheel = Path("mmcv-2.1.0-test.whl")
        for failed_python_call, expected_error in (
            (1, "MMCV_WHEEL_DEPENDENCY_IMPORT_FAILED"),
            (2, "MMCV_WHEEL_ABI_INVALID"),
        ):
            python_calls = 0

            def run(command, check):
                nonlocal python_calls
                if command[1:3] == ["-m", "pip"]:
                    return subprocess.CompletedProcess(command, 0)
                python_calls += 1
                if python_calls == failed_python_call:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)

            error_output = io.StringIO()
            with self.subTest(expected_error=expected_error), redirect_stderr(
                error_output
            ):
                self.assertFalse(
                    self.cache.validate_wheel(wheel, self.values, runner=run)
                )
            self.assertIn(expected_error, error_output.getvalue())

    def test_invalid_cache_is_removed_rebuilt_validated_then_published(self):
        events = []
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            output = root / "output"
            key_dir = cache_root / self.cache.cache_key(self.values)
            key_dir.mkdir(parents=True)
            (key_dir / "fingerprint.txt").write_text(
                self.cache.cache_fingerprint(self.values), encoding="utf-8"
            )
            (key_dir / "mmcv-2.1.0-bad.whl").write_text("bad", encoding="utf-8")

            def validate(wheel, _values):
                content = wheel.read_text(encoding="utf-8")
                events.append(f"validate:{content}")
                if content == "new":
                    self.assertFalse(key_dir.exists(), "new wheel published before validation")
                return content == "new"

            def build(staging, _values):
                events.append("build")
                wheel = staging / "mmcv-2.1.0-new.whl"
                wheel.write_text("new", encoding="utf-8")
                return wheel

            result = self.cache.prepare_mmcv_wheel(
                cache_root,
                output,
                self.values,
                build_wheel=build,
                validate_wheel=validate,
                candidate_provider=lambda _values: [],
            )

            self.assertEqual(events, ["validate:bad", "build", "validate:new"])
            self.assertEqual(result.read_text(encoding="utf-8"), "new")
            self.assertEqual(
                next(key_dir.glob("*.whl")).read_text(encoding="utf-8"), "new"
            )

    def test_failed_rebuild_validation_is_never_published(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            output = root / "output"
            key_dir = cache_root / self.cache.cache_key(self.values)

            def build(staging, _values):
                wheel = staging / "mmcv-2.1.0-invalid.whl"
                wheel.write_text("invalid", encoding="utf-8")
                return wheel

            with self.assertRaisesRegex(RuntimeError, "MMCV_WHEEL_ABI_INVALID"):
                self.cache.prepare_mmcv_wheel(
                    cache_root,
                    output,
                    self.values,
                    build_wheel=build,
                    validate_wheel=lambda _wheel, _values: False,
                    candidate_provider=lambda _values: [],
                )

            self.assertFalse(key_dir.exists())
            self.assertFalse(output.exists())

    def test_invalid_utf8_fingerprint_is_cleaned_and_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            output = root / "output"
            key_dir = cache_root / self.cache.cache_key(self.values)
            key_dir.mkdir(parents=True)
            (key_dir / "fingerprint.txt").write_bytes(b"\xff\xfeinvalid")
            (key_dir / "mmcv-2.1.0-old.whl").write_text("old", encoding="utf-8")

            def build(staging, _values):
                wheel = staging / "mmcv-2.1.0-new.whl"
                wheel.write_text("new", encoding="utf-8")
                return wheel

            result = self.cache.prepare_mmcv_wheel(
                cache_root,
                output,
                self.values,
                build_wheel=build,
                validate_wheel=lambda wheel, _values: wheel.read_text("utf-8") == "new",
                candidate_provider=lambda _values: [],
            )

            self.assertEqual(result.read_text(encoding="utf-8"), "new")
            self.assertEqual(
                (key_dir / "fingerprint.txt").read_text(encoding="utf-8"),
                self.cache.cache_fingerprint(self.values),
            )

    def test_symlink_cache_key_is_unlinked_without_touching_external_target(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cache_root = root / "cache"
            outside = root / "outside"
            output = root / "output"
            cache_root.mkdir()
            outside.mkdir()
            marker = outside / "keep.txt"
            marker.write_text("untouched", encoding="utf-8")
            (outside / "fingerprint.txt").write_text(
                self.cache.cache_fingerprint(self.values), encoding="utf-8"
            )
            (outside / "mmcv-2.1.0-outside.whl").write_text(
                "outside", encoding="utf-8"
            )
            key_dir = cache_root / self.cache.cache_key(self.values)
            try:
                os.symlink(outside, key_dir, target_is_directory=True)
            except OSError as error:
                self.skipTest(f"current platform cannot create directory symlinks: {error}")

            def build(staging, _values):
                wheel = staging / "mmcv-2.1.0-new.whl"
                wheel.write_text("new", encoding="utf-8")
                return wheel

            result = self.cache.prepare_mmcv_wheel(
                cache_root,
                output,
                self.values,
                build_wheel=build,
                validate_wheel=lambda wheel, _values: wheel.read_text("utf-8") == "new",
                candidate_provider=lambda _values: [],
            )

            self.assertFalse(key_dir.is_symlink())
            self.assertTrue(key_dir.is_dir())
            self.assertEqual(result.read_text(encoding="utf-8"), "new")
            self.assertEqual(marker.read_text(encoding="utf-8"), "untouched")
            self.assertEqual(
                (outside / "mmcv-2.1.0-outside.whl").read_text(encoding="utf-8"),
                "outside",
            )


class InferenceImageCheckerContractTest(unittest.TestCase):
    @staticmethod
    def load_checker():
        return load_script_module(IMAGE_CHECKER, "check_inference_image")

    @staticmethod
    def fake_versions():
        return {
            "python_version": "3.10.12",
            "torch_version": "2.7.0+cu128",
            "cuda_version": "12.8",
            "mmcv_version": "2.1.0",
            "mmengine_version": "0.10.4",
            "mmseg_version": "1.1.2",
            "import_errors": [],
        }

    def test_checker_reports_versions_and_accepts_empty_runtime_mount_directories(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app" / "miner" / "uploads").mkdir(parents=True)
            (root / "app" / "miner" / "change_matrix_outputs").mkdir(parents=True)
            report = checker.build_report(
                root,
                version_probe=self.fake_versions,
                nvcc_probe=lambda _root: False,
            )

        self.assertTrue(report["ok"])
        self.assertFalse(report["nvcc"])
        self.assertEqual(report["forbidden_assets"], [])
        for key, value in self.fake_versions().items():
            self.assertEqual(report[key], value)

    def test_checker_rejects_frontend_node_and_checkpoint_assets(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            forbidden = (
                root / "app" / "frontend" / "dist" / "index.html",
                root / "app" / "node_modules" / "package" / "index.js",
                root / "app" / "backend" / "model" / "model.pth",
            )
            for path in forbidden:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            report = checker.build_report(
                root,
                version_probe=self.fake_versions,
                nvcc_probe=lambda _root: True,
            )

        self.assertFalse(report["ok"])
        self.assertTrue(report["nvcc"])
        self.assertEqual(
            report["forbidden_assets"],
            [
                "app/backend/model/model.pth",
                "app/frontend",
                "app/node_modules",
            ],
        )

    def test_checker_rejects_empty_frontend_and_nested_node_modules_directories(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "app" / "frontend").mkdir(parents=True)
            (root / "app" / "backend" / "vendor" / "node_modules").mkdir(
                parents=True
            )
            report = checker.build_report(
                root,
                version_probe=self.fake_versions,
                nvcc_probe=lambda _root: False,
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["forbidden_assets"],
            ["app/backend/vendor/node_modules", "app/frontend"],
        )

    def test_checker_rejects_weight_symlink_without_following_external_target(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outside = root / "outside-target"
            outside.write_text("do not inspect or modify", encoding="utf-8")
            link = root / "app" / "backend" / "model" / "external.pth"
            link.parent.mkdir(parents=True)
            try:
                os.symlink(outside, link)
            except OSError as error:
                self.skipTest(f"current platform cannot create file symlinks: {error}")
            report = checker.build_report(
                root,
                version_probe=self.fake_versions,
                nvcc_probe=lambda _root: False,
            )

            self.assertEqual(outside.read_text(encoding="utf-8"), "do not inspect or modify")

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["forbidden_assets"], ["app/backend/model/external.pth"]
        )

    def test_checker_rejects_nonempty_mount_dirs_illegal_miner_and_vendored_training_dirs(self):
        checker = self.load_checker()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = (
                root / "app" / "miner" / "uploads" / "runtime-file.png",
                root / "app" / "miner" / "change_matrix_outputs" / "nested",
                root / "app" / "miner" / "src",
                root
                / "app"
                / "backend"
                / "model"
                / "mmseg_config"
                / "dinov3_swinV1"
                / "dinov3"
                / "dinov3"
                / "train",
                root
                / "app"
                / "backend"
                / "model"
                / "mmseg_config"
                / "dinov3_swinV1"
                / "dinov3"
                / ".ipynb_checkpoints",
            )
            paths[0].parent.mkdir(parents=True)
            paths[0].touch()
            for path in paths[1:]:
                path.mkdir(parents=True)
            report = checker.build_report(
                root,
                version_probe=self.fake_versions,
                nvcc_probe=lambda _root: False,
            )

        self.assertFalse(report["ok"])
        self.assertEqual(
            report["forbidden_assets"],
            [
                "app/backend/model/mmseg_config/dinov3_swinV1/dinov3/.ipynb_checkpoints",
                "app/backend/model/mmseg_config/dinov3_swinV1/dinov3/dinov3/train",
                "app/miner/change_matrix_outputs/nested",
                "app/miner/src",
                "app/miner/uploads/runtime-file.png",
            ],
        )

    def test_main_prints_json_and_returns_success_or_failure_status(self):
        checker = self.load_checker()
        for ok, expected_status in ((True, 0), (False, 1)):
            report = {"ok": ok, "nvcc": False, "forbidden_assets": []}
            stdout = io.StringIO()
            with self.subTest(ok=ok), patch.object(
                checker, "build_report", return_value=report
            ), redirect_stdout(stdout):
                status = checker.main(["--root", "."])
            self.assertEqual(status, expected_status)
            self.assertEqual(json.loads(stdout.getvalue()), report)


class InferenceWorkerEntrypointContractTest(unittest.TestCase):
    def test_worker_entrypoint_uses_venv_without_conda(self):
        text = WORKER_ENTRYPOINT.read_text(encoding="utf-8")
        self.assertIn('export PATH="/opt/venv/bin:${PATH}"', text)
        self.assertIn("/opt/venv/bin/python /app/docker/wait-for-mysql.py", text)
        self.assertIn("exec /opt/venv/bin/python run_inference_worker.py", text)
        self.assertNotRegex(text, r"(?i)\bconda\b")


if __name__ == "__main__":
    unittest.main()
