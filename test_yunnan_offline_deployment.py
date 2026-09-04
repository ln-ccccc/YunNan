import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
DEPLOY_SCRIPT = ROOT / "deploy_offline.sh"
IMAGE_BUNDLE = ROOT / "image_bundle.env"
WINDOWS_DEPLOY_SCRIPT = ROOT / "deploy_offline.ps1"


class TestYunnanOfflineDeploymentContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deploy_script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        cls.image_bundle = IMAGE_BUNDLE.read_text(encoding="utf-8")
        cls.windows_deploy_script = (
            WINDOWS_DEPLOY_SCRIPT.read_text(encoding="utf-8")
            if WINDOWS_DEPLOY_SCRIPT.exists()
            else ""
        )

    def _powershell_single_quoted_array(self, variable_name):
        assignment_pattern = re.compile(
            rf"(?ms)^[ \t]*\${re.escape(variable_name)}[ \t]*=[ \t]*@\([ \t]*\r?\n"
            rf"(?P<body>.*?)^[ \t]*\)[ \t]*$"
        )
        matches = list(assignment_pattern.finditer(self.windows_deploy_script))
        self.assertEqual(
            len(matches),
            1,
            f"Expected exactly one ${variable_name} single-quoted array assignment",
        )

        values = []
        invalid_lines = []
        for line in matches[0].group("body").splitlines():
            if not line.strip():
                continue
            entry_match = re.fullmatch(r"[ \t]*'([^']*)'[ \t]*,?[ \t]*", line)
            if entry_match is None:
                invalid_lines.append(line.strip())
            else:
                values.append(entry_match.group(1))
        self.assertEqual(
            invalid_lines,
            [],
            f"${variable_name} must contain only single-quoted array entries",
        )
        return values

    def _powershell_two_field_hashtable_array(
        self, variable_name, first_key, second_key
    ):
        assignment_pattern = re.compile(
            rf"(?ms)^[ \t]*\${re.escape(variable_name)}[ \t]*=[ \t]*@\([ \t]*\r?\n"
            rf"(?P<body>.*?)^[ \t]*\)[ \t]*$"
        )
        matches = list(assignment_pattern.finditer(self.windows_deploy_script))
        self.assertEqual(
            len(matches),
            1,
            f"Expected exactly one ${variable_name} hashtable array assignment",
        )

        entry_pattern = re.compile(
            r"[ \t]*@\{[ \t]*"
            + re.escape(first_key)
            + r"[ \t]*=[ \t]*'([^']*)'[ \t]*;[ \t]*"
            + re.escape(second_key)
            + r"[ \t]*=[ \t]*'([^']*)'[ \t]*\}[ \t]*,?[ \t]*"
        )
        values = []
        invalid_lines = []
        for line in matches[0].group("body").splitlines():
            if not line.strip():
                continue
            entry_match = entry_pattern.fullmatch(line)
            if entry_match is None:
                invalid_lines.append(line.strip())
            else:
                values.append(entry_match.groups())
        self.assertEqual(
            invalid_lines,
            [],
            f"${variable_name} must contain only {first_key}/{second_key} entries",
        )
        return values

    def _powershell_function_body(self, function_name):
        function_pattern = re.compile(
            rf"(?ms)^[ \t]*function[ \t]+{re.escape(function_name)}[ \t]*\{{[^\r\n]*\r?\n"
            rf"(?P<body>.*?)^\}}[ \t]*$"
        )
        matches = list(function_pattern.finditer(self.windows_deploy_script))
        self.assertEqual(
            len(matches),
            1,
            f"Expected exactly one function {function_name} body",
        )
        return matches[0].group("body")

    def _windows_final_validate_only_block(self):
        block_pattern = re.compile(
            r"(?m)^[ \t]*if[ \t]+\(\$ValidateOnly\)[ \t]*\{[ \t]*\r?\n"
            r"(?:^[ \t]*\r?\n)*"
            r"^[ \t]*Write-Host[ \t]+'VALIDATION OK'[ \t]*\r?\n"
            r"(?:^[ \t]*\r?\n)*"
            r"^[ \t]*(?P<exit>exit[ \t]+0)[ \t]*\r?\n"
            r"(?:^[ \t]*\r?\n)*"
            r"^[ \t]*\}[ \t]*$"
        )
        matches = list(block_pattern.finditer(self.windows_deploy_script))
        self.assertEqual(
            len(matches),
            1,
            "Expected exactly one final ValidateOnly block containing only "
            "VALIDATION OK and exit 0",
        )
        return matches[0]

    def test_legacy_linux_deploy_entrypoint_is_fail_closed(self):
        self.assertEqual(
            self.deploy_script,
            """#!/usr/bin/env bash
set -euo pipefail

printf '%s\\n' \\
  'ERROR: This legacy Linux deployment entrypoint is disabled.' \\
  'Use deploy_offline.ps1 for the Windows complete migration package.' \\
  'For a Linux new deployment, follow docs/offline_deployment_guide.md; that flow does not restore migrated volumes.'
exit 1
""",
        )
        offline_guide = (ROOT / "docs/offline_deployment_guide.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("`deploy_offline.sh` 已禁用", offline_guide)

    def test_project_summary_matches_private_mysql_port(self):
        project_summary = (ROOT / "docs/project_summary.md").read_text(encoding="utf-8")
        compose = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
        mysql_service = re.search(
            r"(?ms)^  mysql:\s*\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\s*$|^volumes:\s*$)",
            compose,
        )
        self.assertIsNotNone(mysql_service)
        self.assertNotRegex(mysql_service.group("body"), r"(?m)^[ \t]{4}ports\s*:")
        self.assertRegex(compose, r"(?m)^  MYSQL_PORT: 3306\s*$")
        self.assertNotIn("3307:3306", compose)
        self.assertNotIn("宿主机映射 `3307`", project_summary)
        self.assertIn("默认不映射到宿主端口", project_summary)

    def test_image_bundle_names_yunnan_artifacts(self):
        self.assertIn("APP_IMAGE=yunnan-runtime:current", self.image_bundle)
        self.assertIn("INFERENCE_IMAGE=yunnan-inference-worker:current", self.image_bundle)
        self.assertIn("APP_IMAGE_TAR=yunnan_runtime_current.tar", self.image_bundle)
        self.assertIn("INFERENCE_IMAGE_TAR=yunnan_inference_worker_current.tar", self.image_bundle)

    def test_inference_build_scripts_default_to_yunnan_tags(self):
        for relative_path in ("docker/build-inference-image.ps1", "docker/build-inference-image.sh"):
            content = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertIn("yunnan-inference-worker:candidate", content)
            self.assertIn("yunnan-inference-worker:current", content)

    def test_windows_deploy_validates_complete_bundle(self):
        self.assertIn("[switch]$ValidateOnly", self.windows_deploy_script)
        self.assertIn("Get-FileHash", self.windows_deploy_script)
        for artifact in (
            "yunnan_runtime_current.tar",
            "yunnan_inference_worker_current.tar",
            "mysql_8.0.30-8.6.tar",
        ):
            self.assertIn(artifact, self.windows_deploy_script)
        self.assertIn('Join-Path $ImageDir "SHA256SUMS"', self.windows_deploy_script)
        self.assertIn('Join-Path $VolumeDir "SHA256SUMS"', self.windows_deploy_script)
        self.assertEqual(
            self._powershell_two_field_hashtable_array("Images", "Tag", "Archive"),
            [
                ("yunnan-runtime:current", "yunnan_runtime_current.tar"),
                (
                    "yunnan-inference-worker:current",
                    "yunnan_inference_worker_current.tar",
                ),
                (
                    "registry.openanolis.cn/openanolis/mysql:8.0.30-8.6",
                    "mysql_8.0.30-8.6.tar",
                ),
            ],
        )
        self.assertEqual(
            self._powershell_two_field_hashtable_array("Volumes", "Name", "Archive"),
            [
                ("yunnan_mysql_data", "yunnan_mysql_data.tar"),
                ("yunnan_backend_static", "yunnan_backend_static.tar"),
                ("yunnan_hf_cache", "yunnan_hf_cache.tar"),
                ("yunnan_miner_outputs", "yunnan_miner_outputs.tar"),
                ("yunnan_miner_uploads", "yunnan_miner_uploads.tar"),
                ("yunnan_inference_runtime", "yunnan_inference_runtime.tar"),
                ("yunnan_miner_tiles", "yunnan_miner_tiles.tar"),
            ],
        )

    def test_windows_deploy_validates_exact_host_input_manifest(self):
        host_checksum_definition_match = re.search(
            r"(?m)^[ \t]*\$HostChecksumFile[ \t]*=[ \t]*Join-Path[ \t]+"
            r"\$RootDir[ \t]+'HOST_SHA256SUMS'[ \t]*$",
            self.windows_deploy_script,
        )
        self.assertIsNotNone(
            host_checksum_definition_match, "Missing $HostChecksumFile definition"
        )
        self.assertEqual(
            self._powershell_single_quoted_array("HostInputPaths"),
            [
                "deploy_offline.ps1",
                "docker-compose.prod.yml",
                "docker-compose.gpu.yml",
                "image_bundle.env",
                "config.yaml",
                "docker",
                "backend",
                "frontend\\src",
                "miner\\server.js",
                "miner\\routes",
                "miner\\services",
                "miner\\yunnan.kml",
                "miner\\NDVI_2year.xlsx",
                "miner\\NDBI_by_fid_2year_avg.xlsx",
                "miner\\NDWI_by_fid_2year_avg.xlsx",
                "miner\\NDSI_by_fid_2year_avg.xlsx",
                "miner\\src",
                "miner\\vite.config.js",
                "project_storage",
                "maps\\dali",
            ],
        )
        host_inputs_definition_match = re.search(
            r"(?m)^[ \t]*\$HostInputPaths[ \t]*=[ \t]*@\([ \t]*$",
            self.windows_deploy_script,
        )
        self.assertIsNotNone(
            host_inputs_definition_match, "Missing $HostInputPaths definition"
        )

        host_input_function_match = re.search(
            r"(?m)^[ \t]*function[ \t]+Get-HostInputFileNames[ \t]*\{[ \t]*$",
            self.windows_deploy_script,
        )
        self.assertIsNotNone(
            host_input_function_match, "Missing function Get-HostInputFileNames"
        )
        host_input_function = self._powershell_function_body(
            "Get-HostInputFileNames"
        )
        self.assertIn("$HostInputPaths", host_input_function)
        no_reparse_path_function = self._powershell_function_body(
            "Assert-NoReparsePath"
        )
        normalized_no_reparse_path_function = re.sub(
            r"\s+", " ", no_reparse_path_function
        ).strip()
        for required_parent_path_flow in (
            "[System.IO.Path]::GetFullPath($RootPath)",
            "$rootItem = Get-Item -LiteralPath $rootFullPath -Force -ErrorAction Stop",
            "$pathParts = @($RelativePath.Split(",
            "foreach ($pathPart in $pathParts)",
            "$currentPath = Join-Path $currentPath $pathPart",
            "if (-not (Test-Path -LiteralPath $currentPath))",
            "$item = Get-Item -LiteralPath $currentPath -Force -ErrorAction Stop",
            "[System.IO.FileAttributes]::ReparsePoint",
        ):
            self.assertIn(
                required_parent_path_flow, normalized_no_reparse_path_function
            )

        safe_directory_function = self._powershell_function_body(
            "Get-SafeHostInputFiles"
        )
        normalized_safe_directory_function = re.sub(
            r"\s+", " ", safe_directory_function
        ).strip()
        for required_safe_directory_flow in (
            "System.Collections.Generic.Queue[string]",
            "$pendingDirectories.Enqueue($DirectoryPath)",
            "$currentDirectory = $pendingDirectories.Dequeue()",
            "Get-ChildItem -LiteralPath $currentDirectory -Force -ErrorAction Stop",
            "[System.IO.FileAttributes]::ReparsePoint",
            "$item.PSIsContainer",
            "$pendingDirectories.Enqueue($item.FullName)",
        ):
            self.assertIn(
                required_safe_directory_flow, normalized_safe_directory_function
            )
        self.assertNotIn("-Recurse", safe_directory_function)
        self.assertLess(
            normalized_safe_directory_function.index(
                "[System.IO.FileAttributes]::ReparsePoint"
            ),
            normalized_safe_directory_function.index("$item.PSIsContainer"),
            "A reparse point must be rejected before a directory is queued",
        )

        normalized_host_input_function = re.sub(
            r"\s+", " ", host_input_function
        ).strip()
        for required_flow in (
            "[System.IO.Path]::GetFullPath($RootDir)",
            "$rootPrefix = $rootFullPath + [System.IO.Path]::DirectorySeparatorChar",
            "$fullPath = [System.IO.Path]::GetFullPath((Join-Path $RootDir $relativePath))",
            "$fullPath.StartsWith($rootPrefix, "
            "[System.StringComparison]::OrdinalIgnoreCase)",
            "Assert-NoReparsePath -RootPath $rootFullPath -RelativePath $relativePath",
            "$files = @(Get-SafeHostInputFiles -DirectoryPath $fullPath "
            "-RelativePath $relativePath)",
            "New-Object 'System.Collections.Generic.HashSet[string]' "
            "([System.StringComparer]::OrdinalIgnoreCase)",
            "$fileFullPath = [System.IO.Path]::GetFullPath($file.FullName)",
            "$fileFullPath.StartsWith($rootPrefix, "
            "[System.StringComparison]::OrdinalIgnoreCase)",
            "if (-not $names.Add($name))",
            "Duplicate host input path",
            "[System.Array]::Sort($result, [System.StringComparer]::Ordinal)",
        ):
            self.assertIn(required_flow, normalized_host_input_function)
        self.assertLess(
            normalized_host_input_function.index(
                "Assert-NoReparsePath -RootPath $rootFullPath "
                "-RelativePath $relativePath"
            ),
            normalized_host_input_function.index(
                "if (Test-Path -LiteralPath $fullPath -PathType Leaf)"
            ),
            "Every fixed input path must be checked before reading it",
        )
        self.assertRegex(
            normalized_host_input_function,
            r"\.Replace\([ \t]*\[System\.IO\.Path\]::DirectorySeparatorChar,[ \t]*"
            r"\[System\.IO\.Path\]::AltDirectorySeparatorChar[ \t]*\)",
            "Get-HostInputFileNames must normalize manifest paths to forward slashes",
        )
        host_input_assignment_match = re.search(
            r"(?m)^[ \t]*\$hostInputFileNames[ \t]*=[ \t]*@\([ \t]*"
            r"Get-HostInputFileNames[ \t]*\)[ \t]*$",
            self.windows_deploy_script,
        )
        self.assertIsNotNone(
            host_input_assignment_match,
            "Missing $hostInputFileNames assignment from Get-HostInputFileNames",
        )
        checksum_call_pattern = re.compile(
            r"(?m)^[ \t]*Assert-ChecksumFile\b"
            r"(?P<block>(?:[^\r\n]*`[ \t]*\r?\n)*[^\r\n]*)$"
        )
        host_checksum_calls = [
            match
            for match in checksum_call_pattern.finditer(self.windows_deploy_script)
            if "$HostChecksumFile" in match.group(0)
        ]
        self.assertEqual(
            len(host_checksum_calls),
            1,
            "Expected one Assert-ChecksumFile call for $HostChecksumFile",
        )
        host_checksum_call_match = host_checksum_calls[0]
        host_checksum_call = host_checksum_call_match.group(0)
        normalized_host_checksum_call = re.sub(
            r"\s+", " ", host_checksum_call.replace("`", " ")
        ).strip()
        missing_call_details = [
            detail
            for detail in (
                "-ChecksumPath $HostChecksumFile",
                "-BaseDirectory $RootDir",
                "-ExpectedFileNames $hostInputFileNames",
                "-SummaryLabel 'Host data files'",
            )
            if detail not in normalized_host_checksum_call
        ]
        self.assertEqual(
            missing_call_details,
            [],
            f"Incomplete host Assert-ChecksumFile call: {missing_call_details}",
        )
        self.assertRegex(
            normalized_host_checksum_call,
            r"(?<!\S)-QuietEntries(?!\S)",
            "Host checksum call must include an independent -QuietEntries switch",
        )
        validate_only_block = self._windows_final_validate_only_block()
        ordered_positions = (
            host_checksum_definition_match.start(),
            host_inputs_definition_match.start(),
            host_input_function_match.start(),
            host_input_assignment_match.start(),
            host_checksum_call_match.start(),
            validate_only_block.start(),
        )
        self.assertTrue(
            all(left < right for left, right in zip(ordered_positions, ordered_positions[1:])),
            "Host checksum flow must be ordered as definitions, enumeration function, "
            "enumeration assignment, checksum call, then final ValidateOnly block",
        )

        checksum_function = self._powershell_function_body("Assert-ChecksumFile")
        parameter_match = re.search(
            r"(?ms)^[ \t]*param\([ \t]*\r?\n"
            r"(?P<parameters>.*?)^[ \t]*\)[ \t]*$",
            checksum_function,
        )
        self.assertIsNotNone(
            parameter_match, "Assert-ChecksumFile must have a multiline param block"
        )
        checksum_parameters = parameter_match.group("parameters")
        self.assertIn("$QuietEntries", checksum_parameters)
        self.assertIn("$SummaryLabel", checksum_parameters)
        self.assertRegex(checksum_function, r"if[ \t]*\(-not[ \t]+\$QuietEntries\)")
        self.assertRegex(checksum_function, r"if[ \t]*\(\$QuietEntries\)")
        self.assertIn('Write-Host "$SummaryLabel OK', checksum_function)
        self.assertEqual(checksum_function.count('Write-Host "$fileName OK"'), 1)
        self.assertRegex(
            checksum_function,
            r'(?m)^[ \t]*if[ \t]*\(-not[ \t]+\$QuietEntries\)[ \t]*\{[ \t]*\r?\n'
            r'^[ \t]*Write-Host[ \t]+"\$fileName OK"[ \t]*\r?\n'
            r'^[ \t]*\}[ \t]*$',
        )
        unexpected_entry_index = checksum_function.find("Unexpected checksum entry")
        get_file_hash_index = checksum_function.find("Get-FileHash")
        self.assertNotEqual(
            unexpected_entry_index,
            -1,
            "Assert-ChecksumFile must reject unexpected checksum entries",
        )
        self.assertNotEqual(
            get_file_hash_index,
            -1,
            "Assert-ChecksumFile must calculate hashes with Get-FileHash",
        )
        self.assertLess(unexpected_entry_index, get_file_hash_index)

    def test_host_checksum_manifest_exists_and_is_well_formed(self):
        manifest = ROOT / "HOST_SHA256SUMS"
        if not manifest.is_file():
            self.fail("Required host checksum manifest is missing: HOST_SHA256SUMS")

        raw = manifest.read_bytes()
        problems = []
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append("manifest has a UTF-8 BOM")
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            self.fail(f"HOST_SHA256SUMS is not valid UTF-8: {error}")

        if not lines:
            problems.append("manifest is empty")

        manifest_paths = []
        seen_casefold_paths = set()
        for line_number, line in enumerate(lines, start=1):
            match = re.fullmatch(r"([0-9a-f]{64})  ([^ \t].*)", line)
            if match is None:
                problems.append(
                    f"line {line_number} must contain lowercase SHA256, two spaces, and a path"
                )
                continue

            path = match.group(2)
            manifest_paths.append(path)
            posix_path = PurePosixPath(path)
            raw_parts = path.split("/")
            normalized_path = posix_path.as_posix()
            if (
                posix_path.is_absolute()
                or re.match(r"^[A-Za-z]:", path)
                or path.startswith("./")
            ):
                problems.append(
                    f"line {line_number} uses an absolute, drive-qualified, "
                    f"or ./-prefixed path: {path}"
                )
            if "\\" in path:
                problems.append(f"line {line_number} uses a backslash: {path}")
            if path != normalized_path:
                problems.append(f"line {line_number} is not normalized: {path}")
            if "." in raw_parts or ".." in raw_parts:
                problems.append(f"line {line_number} contains '.' or '..': {path}")
            normalized_casefold = normalized_path.casefold()
            if normalized_casefold in seen_casefold_paths:
                problems.append(
                    f"line {line_number} duplicates a checksum path ignoring case: {path}"
                )
            else:
                seen_casefold_paths.add(normalized_casefold)
            if normalized_casefold in ("images", "volumes") or normalized_casefold.startswith(
                ("images/", "volumes/")
            ):
                problems.append(f"line {line_number} covers a separate archive area: {path}")
            if normalized_casefold == ".env":
                problems.append(f"line {line_number} includes .env")
            for component in posix_path.parts:
                if ":" in component:
                    problems.append(
                        f"line {line_number} contains ':' in path component: {path}"
                    )
                if component.endswith((" ", ".")):
                    problems.append(
                        f"line {line_number} has a path component ending in space or dot: {path}"
                    )

        if manifest_paths != sorted(manifest_paths):
            problems.append("manifest paths are not in fixed ordinal order")

        self.assertEqual(problems, [], f"Invalid HOST_SHA256SUMS: {problems}")

    def test_windows_validate_only_exits_before_every_mutation(self):
        validate_only_block = self._windows_final_validate_only_block()
        exit_index = validate_only_block.start("exit")

        missing_mutations = []
        early_mutations = []
        for mutation in (
            "'load', '--input'",
            "$restoreRunId = [Guid]::NewGuid()",
            "'volume', 'create'",
            "'run', '--rm'",
            '"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d"',
        ):
            mutation_indexes = [
                match.start()
                for match in re.finditer(
                    re.escape(mutation), self.windows_deploy_script
                )
            ]
            if not mutation_indexes:
                missing_mutations.append(mutation)
            early_mutations.extend(
                (mutation, mutation_index)
                for mutation_index in mutation_indexes
                if mutation_index <= exit_index
            )

        self.assertEqual(
            (missing_mutations, early_mutations),
            ([], []),
            "Every mutation marker must exist and occur strictly after ValidateOnly exit 0",
        )

    def test_windows_deploy_requires_all_container_states_before_success(self):
        self.assertEqual(
            self._powershell_single_quoted_array("ContainerNames"),
            [
                "yunnan-backend",
                "yunnan-frontend",
                "yunnan-miner-api",
                "yunnan-miner-web",
                "yunnan-inference-worker",
                "yunnan-spatial-worker",
                "yunnan-mysql",
            ],
        )
        self.assertEqual(
            self._powershell_single_quoted_array("HealthyContainerNames"),
            [
                "yunnan-backend",
                "yunnan-frontend",
                "yunnan-miner-api",
                "yunnan-miner-web",
                "yunnan-mysql",
            ],
        )

        state_function = self._powershell_function_body("Wait-ContainerStates")
        normalized_state_function = re.sub(r"\s+", " ", state_function).strip()
        self.assertIn(".State.Status", state_function)
        self.assertIn(".State.Health.Status", state_function)
        existing_container_query = (
            "$existingContainers = @(Invoke-Docker -Arguments "
            "@('ps', '-a', '--format', '{{.Names}}') -Capture"
        )
        existing_container_query_index = normalized_state_function.find(
            existing_container_query
        )
        inspect_arguments_index = normalized_state_function.find("$arguments = @(")
        self.assertNotEqual(
            existing_container_query_index,
            -1,
            "Missing read-only container existence preflight",
        )
        self.assertNotEqual(inspect_arguments_index, -1)
        self.assertLess(
            existing_container_query_index,
            inspect_arguments_index,
            "Container existence preflight must run before bulk inspect",
        )
        preflight_flow = normalized_state_function[
            existing_container_query_index:inspect_arguments_index
        ]
        for required_preflight_flow in (
            "$missingContainers = @($ContainerNames | Where-Object { "
            "$existingContainers -notcontains $_ })",
            "if ($missingContainers.Count -gt 0)",
            '$lastPending = @($missingContainers | ForEach-Object { "$($_)=missing" })',
            "Start-Sleep -Seconds 2",
            "continue",
        ):
            self.assertIn(required_preflight_flow, preflight_flow)
        for required_inspect_flow in (
            "$arguments = @(",
            "'inspect'",
            "'--format'",
            "'{{.Name}}|{{.State.Status}}|{{if .State.Health}}"
            "{{.State.Health.Status}}{{else}}none{{end}}'",
            ") + $ContainerNames",
            "Invoke-Docker -Arguments $arguments -Capture",
            "$states = @{}",
            "$states[$parts[0].TrimStart('/')] = @{",
            "if (-not $states.ContainsKey($name))",
            '$pending += "$name=missing"',
        ):
            self.assertIn(required_inspect_flow, normalized_state_function)
        inspect_call_index = normalized_state_function.index(
            "$rows = @(Invoke-Docker -Arguments $arguments -Capture)"
        )
        inspect_try_index = normalized_state_function.rfind(
            "try {", inspect_arguments_index, inspect_call_index
        )
        inspect_catch_index = normalized_state_function.find(
            "catch {", inspect_call_index
        )
        states_index = normalized_state_function.find(
            "$states = @{}", inspect_call_index
        )
        self.assertNotEqual(
            inspect_try_index, -1, "Bulk inspect must be inside a try block"
        )
        self.assertTrue(inspect_call_index < inspect_catch_index < states_index)
        inspect_catch_flow = normalized_state_function[
            inspect_catch_index:states_index
        ]
        for required_catch_flow in (
            "$existingContainersAfterInspect = @(Invoke-Docker -Arguments "
            "@('ps', '-a', '--format', '{{.Names}}') -Capture",
            "$missingContainers = @($ContainerNames | Where-Object { "
            "$existingContainersAfterInspect -notcontains $_ })",
            "if ($missingContainers.Count -gt 0)",
            '$lastPending = @($missingContainers | ForEach-Object { "$($_)=missing" })',
            "Start-Sleep -Seconds 2",
            "continue",
        ):
            self.assertIn(required_catch_flow, inspect_catch_flow)
        raw_inspect_catch_flow = state_function[
            state_function.find("catch {", state_function.find("$rows = @(")):
            state_function.find("$states = @{}", state_function.find("$rows = @("))
        ]
        self.assertRegex(raw_inspect_catch_flow, r"(?m)^[ \t]*throw[ \t]*$")
        self.assertRegex(
            state_function,
            r"foreach[ \t]*\(\$name[ \t]+in[ \t]+\$ContainerNames\)",
        )
        self.assertRegex(
            state_function,
            r"\$name[ \t]+-in[ \t]+\$HealthyContainerNames",
        )
        self.assertIn("-ne 'running'", state_function)
        self.assertIn("-ne 'healthy'", state_function)

        compose_marker = (
            '"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d"'
        )
        compose_up = self.windows_deploy_script.find(compose_marker)
        self.assertNotEqual(compose_up, -1, f"Missing Compose up call: {compose_marker}")
        state_gate_match = re.search(
            r"(?m)^[ \t]*Wait-ContainerStates[ \t]+-TimeoutSeconds[ \t]+360[ \t]*$",
            self.windows_deploy_script[compose_up + len(compose_marker):],
        )
        self.assertIsNotNone(
            state_gate_match,
            "Missing Wait-ContainerStates -TimeoutSeconds 360 call after Compose up",
        )
        state_gate = compose_up + len(compose_marker) + state_gate_match.start()
        first_url_match = re.search(
            r"(?m)^[ \t]*Wait-Url[ \t]+-Url\b[^\r\n]*$",
            self.windows_deploy_script[compose_up + len(compose_marker):],
        )
        self.assertIsNotNone(
            first_url_match,
            "Missing Wait-Url -Url call after Wait-ContainerStates",
        )
        first_url = (
            compose_up + len(compose_marker) + first_url_match.start()
        )
        self.assertLess(compose_up, state_gate)
        self.assertLess(state_gate, first_url)

        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if powershell is None:
            self.skipTest("PowerShell is required for the controlled state-gate stub")
        state_gate_stub = r"""
$ErrorActionPreference = 'Stop'
$ContainerNames = @(
    'yunnan-backend',
    'yunnan-frontend',
    'yunnan-miner-api',
    'yunnan-miner-web',
    'yunnan-inference-worker',
    'yunnan-spatial-worker',
    'yunnan-mysql'
)
$HealthyContainerNames = @(
    'yunnan-backend',
    'yunnan-frontend',
    'yunnan-miner-api',
    'yunnan-miner-web',
    'yunnan-mysql'
)
$script:PsCallCount = 0
$script:InspectCallCount = 0

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Capture
    )

    if ($Arguments[0] -eq 'ps') {
        $script:PsCallCount++
        if ($script:PsCallCount -eq 2) {
            return @($ContainerNames | Where-Object { $_ -ne 'yunnan-spatial-worker' })
        }
        return $ContainerNames
    }
    if ($Arguments[0] -eq 'inspect') {
        $script:InspectCallCount++
        if ($script:InspectCallCount -eq 1) {
            throw 'simulated inspect race'
        }
        return @($ContainerNames | ForEach-Object {
            $health = if ($_ -in $HealthyContainerNames) { 'healthy' } else { 'none' }
            "/$_|running|$health"
        })
    }
    throw "Unexpected fake Docker arguments: $($Arguments -join ' ')"
}

function Start-Sleep {
    param([int]$Seconds)
}
"""
        state_gate_stub += (
            "\nfunction Wait-ContainerStates {\n"
            + state_function
            + "}\n"
            + "Wait-ContainerStates -TimeoutSeconds 5\n"
            + "if ($script:InspectCallCount -ne 2) { throw 'inspect count mismatch' }\n"
            + "if ($script:PsCallCount -ne 3) { throw 'ps count mismatch' }\n"
            + 'Write-Output "STUB_OK inspect=$script:InspectCallCount"\n'
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            stub_path = Path(temporary_directory) / "wait-container-states-stub.ps1"
            stub_path.write_text(state_gate_stub, encoding="utf-8")
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(stub_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        self.assertEqual(
            result.returncode,
            0,
            f"Controlled state-gate stub failed:\nSTDOUT:\n{result.stdout}\n"
            f"STDERR:\n{result.stderr}",
        )
        self.assertIn("STUB_OK inspect=2", result.stdout)

    def test_windows_deploy_restores_all_yunnan_volumes(self):
        for volume_name in (
            "yunnan_mysql_data",
            "yunnan_backend_static",
            "yunnan_hf_cache",
            "yunnan_miner_outputs",
            "yunnan_miner_uploads",
            "yunnan_inference_runtime",
            "yunnan_miner_tiles",
        ):
            self.assertIn(volume_name, self.windows_deploy_script)

    def test_windows_deploy_uses_dotenv_and_current_health_endpoints(self):
        self.assertIn('"--env-file", $EnvFile', self.windows_deploy_script)
        for endpoint in (
            "http://127.0.0.1:3000/",
            "http://127.0.0.1:4000/",
            "http://127.0.0.1:5008/api/auth/session",
            "http://127.0.0.1:8000/api/auth/session",
        ):
            self.assertIn(endpoint, self.windows_deploy_script)
        self.assertNotIn("http://127.0.0.1:8000/api/stats", self.windows_deploy_script)
        self.assertNotIn("http://127.0.0.1:8000/tiles/5/24/13.png", self.windows_deploy_script)

    def test_windows_deploy_refuses_resource_overwrite(self):
        self.assertIn('"ps", "-a", "--format", "{{.Names}}"', self.windows_deploy_script)
        self.assertIn('"volume", "ls", "--format", "{{.Name}}"', self.windows_deploy_script)
        self.assertIn("目标电脑已有同名云南资源", self.windows_deploy_script)

    def test_windows_deploy_validates_compose_without_orphan_removal(self):
        self.assertNotIn("--remove-orphans", self.windows_deploy_script)
        self.assertIn("'config', '--quiet'", self.windows_deploy_script)
        self.assertIn("'config', '--images'", self.windows_deploy_script)
        self.assertIn('"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d"', self.windows_deploy_script)
        self.assertIn('"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "ps"', self.windows_deploy_script)

    def test_windows_deploy_checksum_manifest_covers_each_expected_archive(self):
        self.assertIn("[string[]]$ExpectedFileNames", self.windows_deploy_script)
        self.assertIn("Duplicate checksum entry", self.windows_deploy_script)
        self.assertIn("Checksum manifest is missing required archive", self.windows_deploy_script)
        self.assertIn("-ExpectedFileNames @($Images | ForEach-Object { $_.Archive })", self.windows_deploy_script)
        self.assertIn("-ExpectedFileNames @($Volumes | ForEach-Object { $_.Archive })", self.windows_deploy_script)

    def test_windows_deploy_verifies_loaded_images_and_volume_ownership(self):
        self.assertIn("Loaded image: $($image.Tag)", self.windows_deploy_script)
        self.assertIn("$restoreRunId = [Guid]::NewGuid().ToString('N')", self.windows_deploy_script)
        self.assertIn('"--label", "yunnan.offline.restore=$restoreRunId"', self.windows_deploy_script)
        self.assertIn("'volume', 'inspect', '--format'", self.windows_deploy_script)
        self.assertEqual(self.windows_deploy_script.encode("utf-8")[:3], b"\xef\xbb\xbf")

    def test_windows_deploy_restores_volume_archives_without_copying_or_losing_metadata(self):
        required_restore_details = (
            "type=volume,source=$($volume.Name),target=/data,volume-nocopy",
            "cd /data && tar -xpf /backup/$($volume.Archive)",
            "type=bind,source=$VolumeDir,target=/backup,readonly",
            "yunnan-runtime:current",
        )
        missing_details = [
            detail for detail in required_restore_details if detail not in self.windows_deploy_script
        ]
        self.assertEqual(missing_details, [])

    def test_windows_deploy_overrides_runtime_entrypoint_for_volume_restore(self):
        restore_section = self.windows_deploy_script[
            self.windows_deploy_script.index("$restoreCommand"):
        ]
        missing_details = [
            detail
            for detail in (
                "'--entrypoint', '/bin/sh',",
                "'yunnan-runtime:current', '-c', $restoreCommand",
            )
            if detail not in restore_section
        ]
        unexpected_details = [
            detail
            for detail in ("'yunnan-runtime:current', '/bin/sh', '-c', $restoreCommand",)
            if detail in restore_section
        ]
        self.assertEqual((missing_details, unexpected_details), ([], []))

    def test_windows_deploy_discovers_offline_map_without_fixed_filename_encoding(self):
        self.assertNotIn("大理白族自治州_卫图1_Level_15.tif", self.windows_deploy_script)
        self.assertIn("$OfflineMapDir = Join-Path $RootDir 'maps\\dali'", self.windows_deploy_script)
        self.assertIn("Assert-Directory -Path $OfflineMapDir", self.windows_deploy_script)
        self.assertIn(
            "Get-ChildItem -LiteralPath $OfflineMapDir -Recurse -File -ErrorAction Stop",
            self.windows_deploy_script,
        )
        self.assertIn("$_.Extension -in @('.tif', '.tiff')", self.windows_deploy_script)
        self.assertIn(
            "Offline map directory does not contain a .tif or .tiff file.",
            self.windows_deploy_script,
        )
        offline_map_environment_assignment = "$env:OFFLINE_MAP_DIR = $OfflineMapDir"
        self.assertEqual(
            self.windows_deploy_script.count(offline_map_environment_assignment),
            1,
        )
        map_validation_start = self.windows_deploy_script.index("$offlineMapFiles =")
        map_validation_end = self.windows_deploy_script.index(
            "Assert-Directory -Path $ImageDir", map_validation_start
        )
        map_validation = self.windows_deploy_script[
            map_validation_start:map_validation_end
        ]
        self.assertEqual(map_validation.count("$offlineMapFiles"), 2)
        self.assertIn("$offlineMapFiles.Count", map_validation)
        self.assertRegex(
            map_validation,
            r"(?m)^if \(\$offlineMapFiles\.Count -eq 0\) \{\r?\n"
            r"^[ \t]+throw 'Offline map directory does not contain a \.tif or \.tiff file\.'\r?\n"
            r"^\}\r?\n"
            r"^\$env:OFFLINE_MAP_DIR = \$OfflineMapDir$",
        )
        environment_assignment_index = self.windows_deploy_script.index(
            offline_map_environment_assignment
        )
        compose_config_or_up_calls = [
            match.start()
            for match in re.finditer(
                r"(?m)^.*Invoke-Docker -Arguments @\((?P<arguments>[^\r\n]*)\).*$",
                self.windows_deploy_script,
            )
            if "compose" in match.group("arguments")
            and re.search(r"['\"](?:config|up)['\"]", match.group("arguments"))
        ]
        self.assertGreaterEqual(len(compose_config_or_up_calls), 3)
        self.assertTrue(
            all(
                environment_assignment_index < call_index
                for call_index in compose_config_or_up_calls
            ),
            "OFFLINE_MAP_DIR must be fixed before every Compose config/up call",
        )
        file_reference = r"\$offlineMapFiles(?:\.(?:Name|FullName))?"
        output_command = r"(?:Write-(?:Host|Output|Verbose|Warning)|Out-String|Format-[A-Za-z]+)"
        self.assertNotRegex(map_validation, rf"{output_command}[^\r\n]*{file_reference}")
        self.assertNotRegex(map_validation, rf"{file_reference}\s*\|\s*{output_command}")


if __name__ == "__main__":
    unittest.main()
