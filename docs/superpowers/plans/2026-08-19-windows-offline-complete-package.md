# 云南项目 Windows 完整离线迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将当前可运行的云南系统整理成可复制到 Windows x86_64 + Docker Desktop 新电脑、完整保留现有数据的一键离线部署目录。

**Architecture:** 先在源电脑验证七服务和项目级地图链路，再以静态契约测试约束新的 `deploy_offline.ps1`。所有 Docker 命名卷在容器停止后逐卷导出、计算 SHA256，并恢复到临时卷校验；目标机脚本只依赖 Docker Desktop 和目录内文件，遇到资源冲突时拒绝覆盖。

**Tech Stack:** Windows PowerShell 5.1+、Docker Desktop Linux containers、Docker Compose、Python `unittest`、现有 Flask/Vue/Vite/Node/MySQL 服务。

---

## 文件职责

- Create: `deploy_offline.ps1` — Windows 离线包校验、镜像加载、卷恢复、Compose 启动和健康检查的唯一入口。
- Modify: `test_yunnan_offline_deployment.py` — 为 Windows 部署脚本增加不依赖 Docker 的契约测试。
- Modify: `docs/offline_deployment_guide.md` — 增加 Windows 完整迁移流程并修正当前目录路径。
- Modify: `docs/system_guide.md` — 将 Windows PowerShell 入口加入部署入口清单。
- Generate: `volumes/yunnan_*.tar` — 七个当前 Docker 命名卷的停止态归档。
- Generate: `volumes/SHA256SUMS` — 七个卷归档的 SHA256。

## Git 限制

项目根目录的 `.git` 为空，`git rev-parse --show-toplevel` 返回 `fatal: not a git repository`。本计划不得伪造提交；每个任务以测试输出、文件哈希和容器状态作为检查点。若执行前用户恢复了有效 Git 仓库，再按任务边界分别提交。

### Task 1: 建立源系统真实运行基线

**Files:**
- Read: `.env`
- Read: `docker-compose.prod.yml`
- Read: `project_storage/projects/1/`
- No source-file changes

- [ ] **Step 1: 记录当前容器状态并确认四个端口可用**

Run:

```powershell
$ports = 3000, 4000, 5008, 8000
$listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
$ports | ForEach-Object {
    [pscustomobject]@{
        Port = $_
        Available = -not ($listeners.Port -contains $_)
    }
}
docker ps -a --filter name=yunnan- --format "table {{.Names}}`t{{.Status}}`t{{.Image}}"
```

Expected: four ports show `Available=True`; existing `yunnan-*` containers may be stopped. If any unrelated process owns a required port, stop here and report its PID rather than changing the port contract.

- [ ] **Step 2: 使用根目录 `.env` 启动七个服务**

Run:

```powershell
docker compose --env-file .env -f docker-compose.prod.yml up -d
if ($LASTEXITCODE -ne 0) { throw "云南 Compose 启动失败" }
```

Expected: Docker returns exit code `0` and creates or starts backend、frontend、miner-api、miner-web、inference-worker、spatial-worker、mysql。

- [ ] **Step 3: 等待四个 HTTP 入口可用**

Run:

```powershell
$checks = [ordered]@{
    frontend = "http://127.0.0.1:3000/"
    miner_web = "http://127.0.0.1:4000/"
    backend = "http://127.0.0.1:5008/api/auth/session"
    miner_api = "http://127.0.0.1:8000/api/auth/session"
}
foreach ($entry in $checks.GetEnumerator()) {
    $deadline = (Get-Date).AddMinutes(6)
    do {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri $entry.Value
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) { break }
        } catch {
            $response = $null
        }
        if ((Get-Date) -ge $deadline) { throw "$($entry.Key) 健康检查超时: $($entry.Value)" }
        Start-Sleep -Seconds 2
    } while ($true)
    [pscustomobject]@{ Service = $entry.Key; Status = $response.StatusCode; Url = $entry.Value }
}
```

Expected: all four checks return `200`。任何超时都必须先查看对应容器日志并使用 `systematic-debugging` 定位，不能继续导出数据卷。

- [ ] **Step 4: 登录并验证项目 1 manifest 与一个真实瓦片**

Run:

```powershell
$settings = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        $settings[$Matches[1].Trim()] = $Matches[2].Trim().Trim('"').Trim("'")
    }
}
$webSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$loginBody = @{
    username = $settings.ADMIN_USERNAME
    password = $settings.ADMIN_PASSWORD
} | ConvertTo-Json
$login = Invoke-RestMethod -WebSession $webSession -Method Post `
    -Uri "http://127.0.0.1:8000/api/auth/login" `
    -ContentType "application/json" -Body $loginBody
$manifestResponse = Invoke-RestMethod -WebSession $webSession `
    -Uri "http://127.0.0.1:8000/api/projects/1/map/manifest"
$manifest = if ($manifestResponse.data) { $manifestResponse.data } else { $manifestResponse }
if (-not $manifest.basemap_resource_id) { throw "项目 1 没有已激活底图" }
$tileRoot = Join-Path $PWD "project_storage\projects\1\tiles\$($manifest.basemap_resource_id)"
$tile = Get-ChildItem -LiteralPath $tileRoot -Recurse -File -Filter *.png | Select-Object -First 1
if (-not $tile) { throw "项目 1 没有可验证瓦片" }
$relative = $tile.FullName.Substring($tileRoot.Length + 1) -replace '\\', '/'
$tileResponse = Invoke-WebRequest -UseBasicParsing -TimeoutSec 20 `
    -Uri "http://127.0.0.1:8000/tiles/projects/1/$($manifest.basemap_resource_id)/$relative"
if ($tileResponse.StatusCode -ne 200) { throw "项目瓦片验证失败" }
[pscustomobject]@{
    Authenticated = $login.data.authenticated
    BasemapResourceId = $manifest.basemap_resource_id
    TileStatus = $tileResponse.StatusCode
}
```

Expected: login is authenticated, manifest has `basemap_resource_id`, and the selected PNG returns `200`。

- [ ] **Step 5: 验证推理运行时与 Compose 状态**

Run:

```powershell
docker exec yunnan-inference-worker python /app/docker/check-inference-runtime.py
if ($LASTEXITCODE -ne 0) { throw "推理运行时检查失败" }
docker compose --env-file .env -f docker-compose.prod.yml ps
```

Expected: runtime checker exits `0`; Web services and MySQL show healthy/running, workers show running。

- [ ] **Step 6: 恢复源电脑的停止状态**

Run:

```powershell
docker compose --env-file .env -f docker-compose.prod.yml stop
if ($LASTEXITCODE -ne 0) { throw "停止云南服务失败" }
$running = docker ps --filter name=yunnan- --format '{{.Names}}'
if ($running) { throw "仍有云南容器运行: $($running -join ', ')" }
```

Expected: no `yunnan-*` container remains running; named volumes are unchanged。

### Task 2: 为 Windows 部署入口建立失败契约测试

**Files:**
- Modify: `test_yunnan_offline_deployment.py:5-58`
- Test: `test_yunnan_offline_deployment.py`

- [ ] **Step 1: 增加 Windows 脚本常量、读取逻辑与契约测试**

Add beside the existing constants:

```python
WINDOWS_DEPLOY_SCRIPT = ROOT / "deploy_offline.ps1"
```

Add in `setUpClass`:

```python
cls.windows_deploy_script = (
    WINDOWS_DEPLOY_SCRIPT.read_text(encoding="utf-8")
    if WINDOWS_DEPLOY_SCRIPT.exists()
    else ""
)
```

Add these methods to `TestYunnanOfflineDeploymentContract`:

```python
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
```

- [ ] **Step 2: 运行测试并确认因脚本不存在而失败**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
```

Expected: existing four tests pass; new Windows tests fail because `deploy_offline.ps1` is not present/content empty。

### Task 3: 实现 Windows 一键部署脚本

**Files:**
- Create: `deploy_offline.ps1`
- Test: `test_yunnan_offline_deployment.py`

- [ ] **Step 1: 创建参数、固定清单和 Docker 调用函数**

Create `deploy_offline.ps1` with the following complete implementation:

```powershell
[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$ImageDir = Join-Path $RootDir "images"
$VolumeDir = Join-Path $RootDir "volumes"
$ComposeFile = Join-Path $RootDir "docker-compose.prod.yml"
$EnvFile = Join-Path $RootDir ".env"

$ImageArchives = [ordered]@{
    "yunnan-runtime:current" = "yunnan_runtime_current.tar"
    "yunnan-inference-worker:current" = "yunnan_inference_worker_current.tar"
    "registry.openanolis.cn/openanolis/mysql:8.0.30-8.6" = "mysql_8.0.30-8.6.tar"
}
$VolumeArchives = [ordered]@{
    "yunnan_mysql_data" = "yunnan_mysql_data.tar"
    "yunnan_backend_static" = "yunnan_backend_static.tar"
    "yunnan_hf_cache" = "yunnan_hf_cache.tar"
    "yunnan_miner_outputs" = "yunnan_miner_outputs.tar"
    "yunnan_miner_uploads" = "yunnan_miner_uploads.tar"
    "yunnan_inference_runtime" = "yunnan_inference_runtime.tar"
    "yunnan_miner_tiles" = "yunnan_miner_tiles.tar"
}
$ContainerNames = @(
    "yunnan-backend",
    "yunnan-frontend",
    "yunnan-miner-api",
    "yunnan-miner-web",
    "yunnan-inference-worker",
    "yunnan-spatial-worker",
    "yunnan-mysql"
)
$RequiredEnvNames = @(
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "SECRET_KEY",
    "MYSQL_PASSWORD",
    "MYSQL_ROOT_PASSWORD"
)

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$Capture
    )
    $output = & docker @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    if ($Capture) {
        return @($output | ForEach-Object { "$_" })
    }
    $output | ForEach-Object { Write-Host "$_" }
}

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "缺少必需文件: $Path"
    }
}

function Assert-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "缺少必需目录: $Path"
    }
}

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) {
            continue
        }
        $pair = $trimmed.Split("=", 2)
        $value = $pair[1].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$pair[0].Trim()] = $value
    }
    return $values
}

function Assert-ChecksumFile {
    param(
        [Parameter(Mandatory = $true)][string]$Directory,
        [Parameter(Mandatory = $true)][string]$ChecksumFile
    )
    Assert-File $ChecksumFile
    $directoryRoot = [IO.Path]::GetFullPath($Directory).TrimEnd([char[]]@('\', '/')) + [IO.Path]::DirectorySeparatorChar
    $checked = 0
    foreach ($line in Get-Content -LiteralPath $ChecksumFile) {
        if (-not $line.Trim()) { continue }
        if ($line -notmatch '^([0-9a-fA-F]{64})\s\s(.+)$') {
            throw "无效 SHA256 行: $line"
        }
        $expected = $Matches[1].ToLowerInvariant()
        $relativeName = $Matches[2]
        $target = [IO.Path]::GetFullPath((Join-Path $Directory $relativeName))
        if (-not $target.StartsWith($directoryRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "SHA256 文件包含越界路径: $relativeName"
        }
        Assert-File $target
        $actual = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) {
            throw "SHA256 不匹配: $relativeName"
        }
        $checked += 1
        Write-Host "SHA256 OK: $relativeName"
    }
    if ($checked -eq 0) { throw "SHA256 文件为空: $ChecksumFile" }
}

function Wait-Url {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 360
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 -Uri $Url
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "Health OK: $Name $Url"
                return
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "健康检查超时: $Name $Url; last error: $lastError"
}

Write-Host "=== 云南项目 Windows 离线部署 ==="
Write-Host "Root: $RootDir"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "未找到 Docker CLI，请先安装并启动 Docker Desktop。"
}
$serverOs = (Invoke-Docker -Arguments @("version", "--format", "{{.Server.Os}}") -Capture | Select-Object -First 1).Trim()
if ($serverOs -ne "linux") {
    throw "Docker Desktop 必须切换到 Linux containers；当前 Server.Os=$serverOs"
}
Invoke-Docker -Arguments @("compose", "version")

Assert-File $ComposeFile
Assert-File $EnvFile
Assert-Directory (Join-Path $RootDir "backend")
Assert-Directory (Join-Path $RootDir "frontend")
Assert-Directory (Join-Path $RootDir "miner")
Assert-Directory (Join-Path $RootDir "project_storage")
Assert-File (Join-Path $RootDir "backend\model\mmseg_config\model.inference.pth")
Assert-Directory (Join-Path $RootDir "maps\dali")
$offlineMapFiles = @(Get-ChildItem -LiteralPath (Join-Path $RootDir "maps\dali") -Recurse -File -ErrorAction Stop |
    Where-Object { $_.Extension -in @('.tif', '.tiff') })
if ($offlineMapFiles.Count -eq 0) {
    throw 'Offline map directory does not contain a .tif or .tiff file.'
}

foreach ($archive in $ImageArchives.Values) {
    Assert-File (Join-Path $ImageDir $archive)
}
foreach ($archive in $VolumeArchives.Values) {
    Assert-File (Join-Path $VolumeDir $archive)
}
Assert-File (Join-Path $ImageDir "SHA256SUMS")
Assert-File (Join-Path $VolumeDir "SHA256SUMS")

$envValues = Read-DotEnv $EnvFile
foreach ($name in $RequiredEnvNames) {
    if (-not $envValues.ContainsKey($name) -or -not $envValues[$name]) {
        throw ".env 缺少非空变量: $name"
    }
}
Write-Host ".env required values: OK"

Assert-ChecksumFile -Directory $ImageDir -ChecksumFile (Join-Path $ImageDir "SHA256SUMS")
Assert-ChecksumFile -Directory $VolumeDir -ChecksumFile (Join-Path $VolumeDir "SHA256SUMS")

$listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
$busyPorts = @(3000, 4000, 5008, 8000 | Where-Object { $listeners.Port -contains $_ })
if ($busyPorts.Count -gt 0) {
    throw "必需端口已占用: $($busyPorts -join ', ')"
}

$existingContainers = @(Invoke-Docker -Arguments @("ps", "-a", "--format", "{{.Names}}") -Capture)
$existingVolumes = @(Invoke-Docker -Arguments @("volume", "ls", "--format", "{{.Name}}") -Capture)
$containerConflicts = @($ContainerNames | Where-Object { $existingContainers -contains $_ })
$volumeConflicts = @($VolumeArchives.Keys | Where-Object { $existingVolumes -contains $_ })
if ($containerConflicts.Count -gt 0 -or $volumeConflicts.Count -gt 0) {
    $message = "目标电脑已有同名云南资源。containers=[$($containerConflicts -join ', ')] volumes=[$($volumeConflicts -join ', ')]"
    if ($ValidateOnly) {
        Write-Warning $message
    } else {
        throw $message
    }
}

if ($ValidateOnly) {
    Write-Host "VALIDATION OK: 离线包文件、SHA256、环境和端口检查通过。"
    exit 0
}

foreach ($entry in $ImageArchives.GetEnumerator()) {
    Invoke-Docker -Arguments @("load", "--input", (Join-Path $ImageDir $entry.Value))
    Invoke-Docker -Arguments @("image", "inspect", $entry.Key)
}

$restoreRunId = [Guid]::NewGuid().ToString('N')
foreach ($entry in $VolumeArchives.GetEnumerator()) {
    $volumeName = $entry.Key
    $archiveName = $entry.Value
    Invoke-Docker -Arguments @("volume", "create", "--label", "yunnan.offline.restore=$restoreRunId", $volumeName)
    $volumeRestoreOwner = (Invoke-Docker -Arguments @("volume", "inspect", "--format", "{{ index .Labels \"yunnan.offline.restore\" }}", $volumeName) -Capture | Out-String).Trim()
    if ($volumeRestoreOwner -ne $restoreRunId) { throw "恢复卷所有权校验失败: $volumeName" }
    $volumeMount = "type=volume,source=$volumeName,target=/data,volume-nocopy"
    $backupMount = "type=bind,source=$VolumeDir,target=/backup,readonly"
    Invoke-Docker -Arguments @(
        "run", "--rm", "--entrypoint", "/bin/sh",
        "--mount", $volumeMount,
        "--mount", $backupMount,
        "yunnan-runtime:current",
        "-c", "cd /data && tar -xpf /backup/$archiveName"
    )
    Write-Host "Volume restored: $volumeName"
}

Push-Location $RootDir
try {
    Invoke-Docker -Arguments @(
        "compose", "--env-file", $EnvFile,
        "-f", $ComposeFile,
        "up", "-d"
    )
} finally {
    Pop-Location
}

Wait-Url -Name "frontend" -Url "http://127.0.0.1:3000/"
Wait-Url -Name "miner-web" -Url "http://127.0.0.1:4000/"
Wait-Url -Name "backend" -Url "http://127.0.0.1:5008/api/auth/session"
Wait-Url -Name "miner-api" -Url "http://127.0.0.1:8000/api/auth/session"
Invoke-Docker -Arguments @(
    "exec", "yunnan-inference-worker",
    "python", "/app/docker/check-inference-runtime.py"
)
Invoke-Docker -Arguments @(
    "compose", "--env-file", $EnvFile,
    "-f", $ComposeFile, "ps"
)

Write-Host "=== 部署完成 ==="
Write-Host "GeoView: http://localhost:3000/"
Write-Host "Miner:   http://localhost:4000/"
Write-Host "API:     http://localhost:5008/api/auth/session"
Write-Host "MinerAPI:http://localhost:8000/api/auth/session"
```

- [ ] **Step 2: 运行契约测试确认转绿**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
```

Expected: all existing and new deployment contract tests pass。

- [ ] **Step 3: 运行 PowerShell 语法解析**

Run:

```powershell
$errors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile(
    (Resolve-Path .\deploy_offline.ps1),
    [ref]$null,
    [ref]$errors
)
if ($errors.Count -gt 0) { $errors; throw "deploy_offline.ps1 语法错误" }
"POWERSHELL_SYNTAX=OK"
```

Expected: `POWERSHELL_SYNTAX=OK`。

### Task 4: 同步 Windows 离线部署文档

**Files:**
- Modify: `docs/offline_deployment_guide.md:1-31`
- Modify: `docs/system_guide.md:97-102`

- [ ] **Step 1: 在离线部署指南顶部增加 Windows 完整迁移入口**

Insert after the title in `docs/offline_deployment_guide.md`:

```markdown
## Windows 完整迁移包

目标电脑要求 Windows x86_64、Docker Desktop 已启动并切换到 Linux containers。建议将整个目录复制到不含特殊权限限制的本地磁盘目录，例如 `D:\YunNan`，并预留至少 100 GB 空间。

在 PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\YunNan
.\deploy_offline.ps1 -ValidateOnly
.\deploy_offline.ps1
```

`-ValidateOnly` 不加载镜像、不创建卷、不启动容器。正式部署会加载 `images/` 中的三个镜像，恢复 `volumes/` 中的七个云南卷，并使用根目录 `.env` 启动服务。若目标电脑已有同名 `yunnan-*` 容器或 `yunnan_*` 卷，脚本会拒绝部署并报告冲突，不会停止或覆盖。

迁移目录含数据库账号和会话密钥，不得上传到公共网盘或代码仓库。首次目标机部署完成后，按本文“验证”章节检查四个入口、项目 manifest 和项目级瓦片。
```

现有 Linux 目录检查和镜像加载示例使用 `images/`、`maps/dali/`，Compose 命令为：

```bash
docker compose --env-file .env -f docker-compose.prod.yml up -d
```

- [ ] **Step 2: 将 Windows 入口加入系统说明**

Add under `docs/system_guide.md` “部署入口”:

```markdown
- Windows 完整离线迁移：根目录 `deploy_offline.ps1`
  - 仅校验：`powershell -ExecutionPolicy Bypass -File .\deploy_offline.ps1 -ValidateOnly`
  - 正式部署：`powershell -ExecutionPolicy Bypass -File .\deploy_offline.ps1`
```

- [ ] **Step 3: 检查文档不再把旧全局接口当成成功条件**

Run:

```powershell
rg -n "api/auth/session|tiles/projects|maps/dali" docs\offline_deployment_guide.md
```

Expected: `/api/stats` may appear only in the explicit “旧接口返回 410” explanation；旧固定瓦片和迁移前目录路径均无匹配。

### Task 5: 停止态导出七个云南数据卷

**Files:**
- Generate: `volumes/yunnan_mysql_data.tar`
- Generate: `volumes/yunnan_backend_static.tar`
- Generate: `volumes/yunnan_hf_cache.tar`
- Generate: `volumes/yunnan_miner_outputs.tar`
- Generate: `volumes/yunnan_miner_uploads.tar`
- Generate: `volumes/yunnan_inference_runtime.tar`
- Generate: `volumes/yunnan_miner_tiles.tar`
- Generate: `volumes/SHA256SUMS`

- [ ] **Step 1: 确认源容器全部停止且七个卷存在**

Run:

```powershell
$running = @(docker ps --filter name=yunnan- --format '{{.Names}}')
if ($running.Count -gt 0) { throw "导出前仍有云南容器运行: $($running -join ', ')" }
$requiredVolumes = @(
    "yunnan_mysql_data",
    "yunnan_backend_static",
    "yunnan_hf_cache",
    "yunnan_miner_outputs",
    "yunnan_miner_uploads",
    "yunnan_inference_runtime",
    "yunnan_miner_tiles"
)
$existingVolumes = @(docker volume ls --format '{{.Name}}')
$missing = @($requiredVolumes | Where-Object { $existingVolumes -notcontains $_ })
if ($missing.Count -gt 0) { throw "缺少云南卷: $($missing -join ', ')" }
```

Expected: no running云南 container and no missing volume。

- [ ] **Step 2: 防止覆盖已有云南卷归档**

Run:

```powershell
$targets = $requiredVolumes | ForEach-Object { Join-Path $PWD "volumes\$_.tar" }
$existingTargets = @($targets | Where-Object { Test-Path -LiteralPath $_ })
if ($existingTargets.Count -gt 0) {
    throw "以下归档已存在，停止以保护用户文件: $($existingTargets -join ', ')"
}
```

Expected: no target `volumes/yunnan_*.tar` exists before this export。

- [ ] **Step 3: 逐卷导出停止态 tar**

Run:

```powershell
$volumeDir = (Resolve-Path .\volumes).Path
foreach ($volumeName in $requiredVolumes) {
    $archiveName = "$volumeName.tar"
    docker run --rm --entrypoint sh `
        --mount "type=volume,source=$volumeName,target=/data,readonly" `
        --mount "type=bind,source=$volumeDir,target=/backup" `
        yunnan-runtime:current `
        -lc "cd /data && tar -cf /backup/$archiveName ."
    if ($LASTEXITCODE -ne 0) { throw "导出失败: $volumeName" }
    $file = Get-Item -LiteralPath (Join-Path $volumeDir $archiveName)
    if ($file.Length -le 0) { throw "导出文件为空: $archiveName" }
    [pscustomobject]@{ Volume = $volumeName; Bytes = $file.Length }
}
```

Expected: seven non-empty `yunnan_*.tar` files; existing `geoview_*.tar` files remain unchanged。

- [ ] **Step 4: 生成并复核卷 SHA256**

Run:

```powershell
$lines = foreach ($volumeName in $requiredVolumes) {
    $fileName = "$volumeName.tar"
    $hash = (Get-FileHash -LiteralPath (Join-Path $volumeDir $fileName) -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $fileName"
}
[IO.File]::WriteAllLines(
    (Join-Path $volumeDir "SHA256SUMS"),
    $lines,
    (New-Object Text.UTF8Encoding($false))
)
foreach ($line in Get-Content (Join-Path $volumeDir "SHA256SUMS")) {
    if ($line -notmatch '^([0-9a-f]{64})  (.+)$') { throw "无效 SHA256 行: $line" }
    $actual = (Get-FileHash -LiteralPath (Join-Path $volumeDir $Matches[2]) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Matches[1]) { throw "SHA256 复核失败: $($Matches[2])" }
}
"VOLUME_SHA256=OK"
```

Expected: `VOLUME_SHA256=OK` and exactly seven checksum lines。

### Task 6: 临时恢复并进行七个卷的最终验收比较

**Files:**
- Read: `volumes/yunnan_*.tar`
- Temporary Docker resources: `yunnan_verify_*` volumes
- No persistent source-file changes

- [ ] **Step 1: 确认验证卷名称未被占用**

Run:

```powershell
$verifyVolumes = $requiredVolumes | ForEach-Object { "yunnan_verify_$($_.Substring('yunnan_'.Length))" }
$restoreRunId = [Guid]::NewGuid().ToString('N')
$existingVolumes = @(docker volume ls --format '{{.Name}}')
$conflicts = @($verifyVolumes | Where-Object { $existingVolumes -contains $_ })
if ($conflicts.Count -gt 0) { throw "临时验证卷已存在，停止以避免误删: $($conflicts -join ', ')" }
```

Expected: no conflicts。

- [ ] **Step 2: 逐卷恢复并比较最终验收清单**

Run:

```powershell
for ($index = 0; $index -lt $requiredVolumes.Count; $index++) {
    $sourceVolume = $requiredVolumes[$index]
    $verifyVolume = $verifyVolumes[$index]
    $archiveName = "$sourceVolume.tar"
    docker volume create --label "yunnan.offline.restore=$restoreRunId" $verifyVolume | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "创建验证卷失败: $verifyVolume" }
    $volumeRestoreOwner = docker volume inspect --format '{{ index .Labels "yunnan.offline.restore" }}' $verifyVolume
    if ($LASTEXITCODE -ne 0 -or $volumeRestoreOwner.Trim() -ne $restoreRunId) {
        throw "验证卷所有权校验失败: $verifyVolume"
    }

    docker run --rm --entrypoint /bin/sh `
        --mount "type=volume,source=$verifyVolume,target=/data,volume-nocopy" `
        --mount "type=bind,source=$volumeDir,target=/backup,readonly" `
        yunnan-runtime:current `
        -c "cd /data && tar -xpf /backup/$archiveName"
    if ($LASTEXITCODE -ne 0) { throw "恢复验证失败: $archiveName" }

    $manifestCommand = "cd /data && { find . -mindepth 1 -printf '%y|%p|%U|%G|%m|%s\n'; find . -type f -exec sha256sum {} +; } | sort"
    $sourceManifest = docker run --rm --entrypoint /bin/sh `
        --mount "type=volume,source=$sourceVolume,target=/data,readonly,volume-nocopy" `
        yunnan-runtime:current `
        -c $manifestCommand
    if ($LASTEXITCODE -ne 0) { throw "源卷最终验收清单失败: $sourceVolume" }
    $restoredManifest = docker run --rm --entrypoint /bin/sh `
        --mount "type=volume,source=$verifyVolume,target=/data,readonly,volume-nocopy" `
        yunnan-runtime:current `
        -c $manifestCommand
    if ($LASTEXITCODE -ne 0) { throw "恢复卷最终验收清单失败: $verifyVolume" }
    if ($sourceManifest -ne $restoredManifest) {
        throw "卷最终验收清单不匹配（type/path/uid/gid/mode/length/content hash），保留验证卷供排查: $verifyVolume"
    }

    if (-not $verifyVolume.StartsWith("yunnan_verify_")) {
        throw "拒绝删除非验证卷: $verifyVolume"
    }
    docker volume rm $verifyVolume | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "删除验证卷失败: $verifyVolume" }
    [pscustomobject]@{ Source = $sourceVolume; Restored = "MATCH" }
}
```

Expected: seven `Restored=MATCH` results；每个精确的 `yunnan_verify_*` 临时卷均使用本次随机 label，并且仅在最终验收比较 `type/path/uid/gid/mode/length/content hash` 全部匹配后删除。

### Task 7: 完整目录最终验证与交付检查点

**Files:**
- Verify: `deploy_offline.ps1`
- Verify: `images/SHA256SUMS`
- Verify: `volumes/SHA256SUMS`
- Verify: updated documentation and tests

- [ ] **Step 1: 运行全部部署相关静态测试**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m unittest -v test_yunnan_offline_deployment.py
node --test frontend\test\backendUrl.test.mjs
Push-Location miner
try { npm test -- --test-reporter=spec } finally { Pop-Location }
```

Expected: deployment contract tests all pass, frontend test `1/1` passes, Miner tests `33/33` pass。

- [ ] **Step 2: 运行 Windows 离线包只校验模式**

Run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\deploy_offline.ps1 -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw "Windows 离线包验证失败" }
```

Expected: image and volume checksums all print `OK`; existing source机 `yunnan-*` containers/volumes only produce warnings; final line contains `VALIDATION OK`。

- [ ] **Step 3: 核对镜像平台、归档数量和停止状态**

Run:

```powershell
docker image inspect --format '{{index .RepoTags 0}} {{.Os}}/{{.Architecture}}' `
    yunnan-runtime:current `
    yunnan-inference-worker:current `
    registry.openanolis.cn/openanolis/mysql:8.0.30-8.6

$imageArchives = @(Get-ChildItem .\images -File -Filter *.tar)
$volumeArchives = @(Get-ChildItem .\volumes -File -Filter yunnan_*.tar)
if ($imageArchives.Count -ne 3) { throw "镜像归档数量错误: $($imageArchives.Count)" }
if ($volumeArchives.Count -ne 7) { throw "云南卷归档数量错误: $($volumeArchives.Count)" }
$running = @(docker ps --filter name=yunnan- --format '{{.Names}}')
if ($running.Count -gt 0) { throw "交付前仍有云南容器运行: $($running -join ', ')" }
[pscustomobject]@{
    ImageArchives = $imageArchives.Count
    VolumeArchives = $volumeArchives.Count
    YunnanContainersRunning = $running.Count
}
```

Expected: all three images report `linux/amd64`; archive counts are `3` and `7`; running count is `0`。

- [ ] **Step 4: 记录最终目录大小和目标机部署命令**

Run:

```powershell
$measure = Get-ChildItem -LiteralPath $PWD -Recurse -Force -File -ErrorAction Stop |
    Measure-Object -Property Length -Sum
[pscustomobject]@{
    Files = $measure.Count
    TotalGB = [math]::Round($measure.Sum / 1GB, 2)
    FreeGBRequiredOnTarget = [math]::Ceiling(($measure.Sum / 1GB) + 30)
}
```

Expected: command returns a concrete file count、package size and target free-space requirement。Final handoff must include:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\YunNan
.\deploy_offline.ps1 -ValidateOnly
.\deploy_offline.ps1
```

The handoff must also state that current-source runtime and archive restore were verified, while the first clean-machine end-to-end restore can only be completed on the target Windows computer.
