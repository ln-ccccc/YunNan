# 宿主数据完整性与七容器健康门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让完整离线包同时校验全部 Compose 宿主绑定输入，并且只在七个容器运行、五个带 healthcheck 的服务健康时判定部署成功。

**Architecture:** 根目录新增 `HOST_SHA256SUMS`，由 Windows 部署脚本按固定允许路径枚举实际文件集合，与清单做精确集合比较后逐项重算 SHA256。部署启动后新增只读 Docker inspect 轮询，检查七个固定容器的 running 状态及五个服务的 healthy 状态；现有 HTTP 和推理运行时检查继续保留。

**Tech Stack:** Windows PowerShell 5.1、Docker Desktop Linux containers、Python `unittest` 静态契约、SHA256。

**Repository note:** 当前 `D:\项目\YunNan\.git` 不是有效 Git 仓库，`git status` 返回 128，因此本计划不执行 commit；每个任务改为记录精确文件和验证输出。

---

## 文件职责

- `deploy_offline.ps1`：定义允许的宿主绑定输入、精确校验三类清单、执行七容器状态门禁。
- `test_yunnan_offline_deployment.py`：锁定宿主清单范围、ValidateOnly 写前退出顺序和健康门禁调用顺序。
- `HOST_SHA256SUMS`：固定排序的宿主绑定文件 SHA256 清单，UTF-8 无 BOM。
- `docs/offline_deployment_guide.md`：说明第三类清单、校验耗时和七容器健康标准。
- `docs/system_guide.md`：同步 Windows 完整迁移包构成与成功标准。

### Task 1: 为宿主清单与健康门禁建立 RED 契约

**Files:**
- Modify: `test_yunnan_offline_deployment.py`
- Test: `test_yunnan_offline_deployment.py`

- [ ] **Step 1: 写宿主输入与清单契约**

在现有测试类中新增：

```python
    def test_windows_deploy_validates_exact_host_input_manifest(self):
        self.assertIn("$HostChecksumFile = Join-Path $RootDir 'HOST_SHA256SUMS'", self.windows_deploy_script)
        for relative_path in (
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
        ):
            self.assertIn(f"'{relative_path}'", self.windows_deploy_script)
        self.assertIn("Get-HostInputFileNames", self.windows_deploy_script)
        self.assertIn("Unexpected checksum entry", self.windows_deploy_script)
        self.assertIn("Host data files OK", self.windows_deploy_script)
        self.assertNotIn("'.env'", self.windows_deploy_script[
            self.windows_deploy_script.index("$HostInputPaths ="):
            self.windows_deploy_script.index("$Images =")
        ])

    def test_host_checksum_manifest_exists_and_is_well_formed(self):
        manifest = ROOT / "HOST_SHA256SUMS"
        self.assertTrue(manifest.is_file())
        raw = manifest.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        lines = raw.decode("utf-8").splitlines()
        self.assertGreater(len(lines), 0)
        self.assertEqual(lines, sorted(lines, key=lambda line: line.split("  ", 1)[1]))
        for line in lines:
            self.assertRegex(line, r"^[0-9a-f]{64}  [^/\\].+$")
            path = line.split("  ", 1)[1]
            self.assertNotIn("..", Path(path).parts)
            self.assertFalse(path.startswith(("images/", "volumes/")))
            self.assertNotEqual(path, ".env")
```

- [ ] **Step 2: 写 ValidateOnly 控制流顺序契约**

```python
    def test_windows_validate_only_exits_before_every_mutation(self):
        validate_index = self.windows_deploy_script.index("if ($ValidateOnly)")
        exit_index = self.windows_deploy_script.index("exit 0", validate_index)
        for mutation in (
            "'load', '--input'",
            "$restoreRunId = [Guid]::NewGuid()",
            "'volume', 'create'",
            "'run', '--rm'",
            '"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d"',
        ):
            self.assertLess(exit_index, self.windows_deploy_script.index(mutation, exit_index))
```

- [ ] **Step 3: 写七容器/五健康服务门禁契约**

```python
    def test_windows_deploy_requires_all_container_states_before_success(self):
        self.assertIn("function Wait-ContainerStates", self.windows_deploy_script)
        for name in (
            "yunnan-backend",
            "yunnan-frontend",
            "yunnan-miner-api",
            "yunnan-miner-web",
            "yunnan-inference-worker",
            "yunnan-spatial-worker",
            "yunnan-mysql",
        ):
            self.assertIn(name, self.windows_deploy_script)
        for name in (
            "yunnan-backend",
            "yunnan-frontend",
            "yunnan-miner-api",
            "yunnan-miner-web",
            "yunnan-mysql",
        ):
            self.assertIn(name, self.windows_deploy_script[
                self.windows_deploy_script.index("$HealthyContainerNames ="): 
                self.windows_deploy_script.index("function Invoke-Docker")
            ])
        compose_up = self.windows_deploy_script.index(
            '"compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d"'
        )
        state_gate = self.windows_deploy_script.index("Wait-ContainerStates", compose_up)
        first_url = self.windows_deploy_script.index("Wait-Url", state_gate)
        self.assertLess(compose_up, state_gate)
        self.assertLess(state_gate, first_url)
        self.assertIn(".State.Status", self.windows_deploy_script)
        self.assertIn(".State.Health.Status", self.windows_deploy_script)
```

- [ ] **Step 4: 运行 RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
```

Expected: 现有 14 项通过；新增 4 项因 `HOST_SHA256SUMS`、宿主枚举、顺序契约或状态门禁尚不存在而失败，不得出现 Python ERROR。

### Task 2: 实现宿主输入精确校验和健康门禁

**Files:**
- Modify: `deploy_offline.ps1`
- Test: `test_yunnan_offline_deployment.py`

- [ ] **Step 1: 定义清单路径、宿主输入与健康容器集合**

在根目录变量和容器定义附近加入：

```powershell
$HostChecksumFile = Join-Path $RootDir 'HOST_SHA256SUMS'

$HostInputPaths = @(
    'deploy_offline.ps1',
    'docker-compose.prod.yml',
    'docker-compose.gpu.yml',
    'image_bundle.env',
    'config.yaml',
    'docker',
    'backend',
    'frontend\src',
    'miner\server.js',
    'miner\routes',
    'miner\services',
    'miner\yunnan.kml',
    'miner\NDVI_2year.xlsx',
    'miner\NDBI_by_fid_2year_avg.xlsx',
    'miner\NDWI_by_fid_2year_avg.xlsx',
    'miner\NDSI_by_fid_2year_avg.xlsx',
    'miner\src',
    'miner\vite.config.js',
    'project_storage',
    'maps\dali'
)

$HealthyContainerNames = @(
    'yunnan-backend',
    'yunnan-frontend',
    'yunnan-miner-api',
    'yunnan-miner-web',
    'yunnan-mysql'
)
```

- [ ] **Step 2: 枚举固定宿主输入**

在 `Assert-Directory` 后新增：

```powershell
function Get-HostInputFileNames {
    $rootFullPath = [System.IO.Path]::GetFullPath($RootDir).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $rootPrefix = $rootFullPath + [System.IO.Path]::DirectorySeparatorChar
    $names = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($relativePath in $HostInputPaths) {
        $fullPath = [System.IO.Path]::GetFullPath((Join-Path $RootDir $relativePath))
        if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Host input escapes the bundle root: $relativePath"
        }

        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            $files = @((Get-Item -LiteralPath $fullPath -Force))
        }
        elseif (Test-Path -LiteralPath $fullPath -PathType Container) {
            $files = @(Get-ChildItem -LiteralPath $fullPath -Recurse -Force -File -ErrorAction Stop)
        }
        else {
            throw "Required host input is missing: $relativePath"
        }

        foreach ($file in $files) {
            $fileFullPath = [System.IO.Path]::GetFullPath($file.FullName)
            if (-not $fileFullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Host input file escapes the bundle root: $relativePath"
            }
            $name = $fileFullPath.Substring($rootPrefix.Length).Replace(
                [System.IO.Path]::DirectorySeparatorChar,
                [System.IO.Path]::AltDirectorySeparatorChar
            )
            if (-not $names.Add($name)) {
                throw "Duplicate host input path: $name"
            }
        }
    }

    $result = @($names)
    [System.Array]::Sort($result, [System.StringComparer]::Ordinal)
    return $result
}
```

- [ ] **Step 3: 使清单验证要求精确集合并支持安静汇总**

将 `Assert-ChecksumFile` 参数扩展为：

```powershell
        [Parameter(Mandatory = $true)][string[]]$ExpectedFileNames,
        [switch]$QuietEntries,
        [string]$SummaryLabel = 'Checksum files'
```

在遍历清单前先构建期望集合并拒绝大小写重复。每个清单文件名解析完成后、读取或计算该文件哈希之前，立即拒绝不在允许集合中的条目：

```powershell
    $expectedFileNameSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($expectedFileName in $ExpectedFileNames) {
        if (-not $expectedFileNameSet.Add($expectedFileName)) {
            throw "Duplicate expected checksum path: $expectedFileName"
        }
    }

    foreach ($entry in $entries) {
        $match = [System.Text.RegularExpressions.Regex]::Match($entry, '^(?i:([0-9a-f]{64}))  (.+)$')
        if (-not $match.Success) {
            throw "Invalid checksum entry in ${ChecksumPath}: $entry"
        }
        $expectedHash = $match.Groups[1].Value.ToUpperInvariant()
        $fileName = $match.Groups[2].Value
        if (-not $manifestFileNames.Add($fileName)) {
            throw "Duplicate checksum entry for $fileName in $ChecksumPath"
        }
        if (-not $expectedFileNameSet.Contains($fileName)) {
            throw "Unexpected checksum entry in ${ChecksumPath}: $fileName"
        }
        if ([System.IO.Path]::IsPathRooted($fileName) -or $fileName.Split(@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)) -contains '..') {
            throw "Checksum entry escapes its bundle directory: $fileName"
        }
        $filePath = [System.IO.Path]::GetFullPath((Join-Path $BaseDirectory $fileName))
        if (-not $filePath.StartsWith($basePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Checksum entry escapes its bundle directory: $fileName"
        }
        Assert-File -Path $filePath
        $actualHash = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($actualHash -ne $expectedHash) {
            throw "Checksum mismatch for $fileName. Re-copy the offline bundle and try again."
        }
        if (-not $QuietEntries) {
            Write-Host "$fileName OK"
        }
    }
```

在现有必需项检查后只需确认集合数量相同：

```powershell
    if ($manifestFileNames.Count -ne $expectedFileNameSet.Count) {
        throw "Checksum manifest file count does not match the required input set: $ChecksumPath"
    }
```

将逐项成功输出改为：

```powershell
        if (-not $QuietEntries) {
            Write-Host "$fileName OK"
        }
```

函数末尾加入：

```powershell
    if ($QuietEntries) {
        Write-Host "$SummaryLabel OK ($($entries.Count) files)"
    }
```

- [ ] **Step 4: 在任何 Docker 写入前验证宿主清单**

在现有镜像/卷清单调用后加入：

```powershell
$hostInputFileNames = @(Get-HostInputFileNames)
Assert-ChecksumFile `
    -ChecksumPath $HostChecksumFile `
    -BaseDirectory $RootDir `
    -ExpectedFileNames $hostInputFileNames `
    -QuietEntries `
    -SummaryLabel 'Host data files'
```

不要把 `.env` 加入 `$HostInputPaths`；现有五项非空变量检查继续负责它。

- [ ] **Step 5: 实现七容器状态轮询**

在 `Wait-Url` 附近新增：

```powershell
function Wait-ContainerStates {
    param([int]$TimeoutSeconds = 360)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastPending = @('container state not inspected')
    while ([DateTime]::UtcNow -lt $deadline) {
        $arguments = @(
            'inspect',
            '--format',
            '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
        ) + $ContainerNames
        $rows = @(Invoke-Docker -Arguments $arguments -Capture)
        $states = @{}
        foreach ($row in $rows) {
            $parts = $row.ToString().Trim().Split('|')
            if ($parts.Count -ne 3) {
                throw "Unexpected docker inspect state output."
            }
            $states[$parts[0].TrimStart('/')] = @{
                Status = $parts[1]
                Health = $parts[2]
            }
        }

        $pending = @()
        foreach ($name in $ContainerNames) {
            if (-not $states.ContainsKey($name)) {
                $pending += "$name=missing"
                continue
            }
            if ($states[$name].Status -ne 'running') {
                $pending += "$name=$($states[$name].Status)"
                continue
            }
            if ($name -in $HealthyContainerNames -and $states[$name].Health -ne 'healthy') {
                $pending += "$name=health:$($states[$name].Health)"
            }
        }
        if ($pending.Count -eq 0) {
            Write-Host 'All 7 Yunnan containers are running; 5 healthchecks are healthy.'
            return
        }

        $lastPending = $pending
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for Yunnan container states after $TimeoutSeconds seconds: $($lastPending -join ', ')"
}
```

在 Compose `up -d` 成功后、任何 `Wait-Url` 前调用：

```powershell
Wait-ContainerStates -TimeoutSeconds 360
```

- [ ] **Step 6: 运行部分 GREEN 并解析 PowerShell**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
$tokens = $null; $errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\deploy_offline.ps1), [ref]$tokens, [ref]$errors
)
if ($errors.Count -ne 0) { $errors | Format-List; throw 'PowerShell 5.1 parser failed' }
pwsh -NoProfile -Command '$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\deploy_offline.ps1),[ref]$t,[ref]$e); if($e.Count){$e;exit 1}'
```

Expected: 除 `HOST_SHA256SUMS` 尚未生成的存在性测试外，其余新增测试转绿；两种 Parser 均为 0 errors。

### Task 3: 生成并独立核验宿主数据清单

**Files:**
- Create: `HOST_SHA256SUMS`
- Verify: all paths in `$HostInputPaths`

- [ ] **Step 1: 确认业务服务停止且输入稳定**

Run:

```powershell
$running = @(docker ps --filter name=yunnan- --format '{{.Names}}')
if ($running.Count -ne 0) { throw "云南业务容器仍在运行: $($running -join ', ')" }
```

Expected: `running.Count=0`。不要启动 Compose。

- [ ] **Step 2: 使用与脚本相同的固定输入集合生成清单**

运行一次性 PowerShell 生成命令；它必须复用 Task 2 的 `$HostInputPaths` 精确值，按 `Ordinal` 排序，并使用 UTF-8 无 BOM 写入：

```powershell
$root = (Resolve-Path .).Path
$rootPrefix = $root.TrimEnd('\') + '\'
$hostInputPaths = @(
    'deploy_offline.ps1','docker-compose.prod.yml','docker-compose.gpu.yml','image_bundle.env','config.yaml',
    'docker','backend','frontend\src','miner\server.js','miner\routes','miner\services','miner\yunnan.kml',
    'miner\NDVI_2year.xlsx','miner\NDBI_by_fid_2year_avg.xlsx','miner\NDWI_by_fid_2year_avg.xlsx',
    'miner\NDSI_by_fid_2year_avg.xlsx','miner\src','miner\vite.config.js','project_storage','maps\dali'
)
$names = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($relativePath in $hostInputPaths) {
    $fullPath = [System.IO.Path]::GetFullPath((Join-Path $root $relativePath))
    if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
        $files = @((Get-Item -LiteralPath $fullPath -Force))
    } elseif (Test-Path -LiteralPath $fullPath -PathType Container) {
        $files = @(Get-ChildItem -LiteralPath $fullPath -Recurse -Force -File -ErrorAction Stop)
    } else {
        throw "Missing host input: $relativePath"
    }
    foreach ($file in $files) {
        $fileFullPath = [System.IO.Path]::GetFullPath($file.FullName)
        if (-not $fileFullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Host input escaped root: $relativePath"
        }
        $name = $fileFullPath.Substring($rootPrefix.Length).Replace('\','/')
        if (-not $names.Add($name)) { throw "Duplicate host input: $name" }
    }
}
$ordered = @($names)
[System.Array]::Sort($ordered, [System.StringComparer]::Ordinal)
$lines = foreach ($name in $ordered) {
    $hash = (Get-FileHash -LiteralPath (Join-Path $root $name) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $name"
}
[System.IO.File]::WriteAllLines(
    (Join-Path $root 'HOST_SHA256SUMS'),
    $lines,
    (New-Object System.Text.UTF8Encoding($false))
)
```

- [ ] **Step 3: 独立复算与集合审计**

独立读取 `HOST_SHA256SUMS`，验证每行格式、无 BOM、路径不绝对且无 `..`，重新计算全部 SHA256，并把清单路径集合与同一允许路径枚举结果做双向比较。不得输出每个路径，只输出：

```text
HOST_MANIFEST_FILES=<count>
HOST_MANIFEST_BYTES=<sum>
HOST_MANIFEST_MATCH=<count>/<count>
HOST_MANIFEST_SET=EXACT
```

Expected: 全部匹配且集合为 `EXACT`。

- [ ] **Step 4: 运行完整 GREEN**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
```

Expected: 新总数全部通过，无 FAIL/ERROR。

### Task 4: 同步部署文档

**Files:**
- Modify: `docs/offline_deployment_guide.md`
- Modify: `docs/system_guide.md`
- Modify: `docs/superpowers/specs/2026-08-19-windows-offline-complete-package-design.md`
- Modify: `docs/superpowers/plans/2026-08-19-host-data-integrity-health-gate.md`

- [ ] **Step 1: 更新 Windows 完整迁移包说明**

写清：

```text
- 根目录 HOST_SHA256SUMS 是第三类清单，保护 Compose 宿主绑定源码、project_storage、maps 和模型。
- -ValidateOnly 会读取三类清单覆盖的 59,026,472,163 字节（54.973GiB，约 55.0GiB）被校验数据；这与完整目录占用大小不是同一口径。机械盘耗时可能较长，期间不要修改目录。
- 成功输出包含 Host data files OK (<N> files)、10 个镜像/卷 OK 和 VALIDATION OK。
- 正式部署成功要求 7 个容器 running，且 backend/frontend/miner-api/miner-web/mysql 为 healthy。
```

- [ ] **Step 2: 验证文档**

Run:

```powershell
rg -n "HOST_SHA256SUMS|Host data files OK|7.*running|5.*healthy|100 GB" docs\offline_deployment_guide.md docs\system_guide.md docs\superpowers\specs\2026-08-19-windows-offline-complete-package-design.md
```

Expected: 三份现役文档描述一致；Markdown 围栏成对；不新增旧接口、固定 TIF 文件名或孤儿清理参数。

### Task 5: 最终全量验收

**Files:**
- Verify: `deploy_offline.ps1`
- Verify: `HOST_SHA256SUMS`
- Verify: `images/SHA256SUMS`
- Verify: `volumes/SHA256SUMS`
- Verify: documentation and tests

- [ ] **Step 1: 运行全部测试和解析**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
node --test frontend\test\backendUrl.test.mjs
Push-Location miner
try { npm test -- --test-reporter=spec } finally { Pop-Location }
```

Expected: 部署契约全部通过、前端 `1/1`、Miner `33/33`；PS5.1/7 Parser 均 0 errors，BOM 仍为 `EF BB BF`。

- [ ] **Step 2: 运行真实只读预检**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy_offline.ps1 -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'Windows 离线包验证失败' }
```

Expected: 宿主清单汇总通过，3 个镜像和 7 个卷均 `OK`，源机同名资源仅警告，最后输出 `VALIDATION OK`；不加载镜像、不创建卷、不启动服务。

- [ ] **Step 3: 独立核验最终状态**

确认：

```text
HOST_SHA256SUMS=EXACT/MATCH
IMAGE_SHA256SUMS=3/3 MATCH
VOLUME_SHA256SUMS=7/7 MATCH
IMAGE_PLATFORM=3/3 linux/amd64
YUNNAN_RUNNING_CONTAINERS=0
VERIFY_TEMP_VOLUMES=0
VERIFY_HELPERS=0
REPARSE_POINTS=0
```

重新计算目录文件数、字节数和 `ceil(totalGiB + 30)`，文档继续建议目标机至少 100 GB。

- [ ] **Step 4: 最终代码与交付审查**

按 `requesting-code-review` 模板做只读总审查；任何 Critical/Important 必须修复并重跑相应门禁。明确保留的唯一限制是：尚未在一台完全干净的目标 Windows + Docker Desktop 电脑上执行首次端到端部署。
