[CmdletBinding()]
param(
    [switch]$ValidateOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RootDir = $PSScriptRoot
$ImageDir = Join-Path $RootDir 'images'
$VolumeDir = Join-Path $RootDir 'volumes'
$ComposeFile = Join-Path $RootDir 'docker-compose.prod.yml'
$EnvFile = Join-Path $RootDir '.env'
$OfflineMapDir = Join-Path $RootDir 'maps\dali'
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

$Images = @(
    @{ Tag = 'yunnan-runtime:current'; Archive = 'yunnan_runtime_current.tar' },
    @{ Tag = 'yunnan-inference-worker:current'; Archive = 'yunnan_inference_worker_current.tar' },
    @{ Tag = 'registry.openanolis.cn/openanolis/mysql:8.0.30-8.6'; Archive = 'mysql_8.0.30-8.6.tar' }
)

$Volumes = @(
    @{ Name = 'yunnan_mysql_data'; Archive = 'yunnan_mysql_data.tar' },
    @{ Name = 'yunnan_backend_static'; Archive = 'yunnan_backend_static.tar' },
    @{ Name = 'yunnan_hf_cache'; Archive = 'yunnan_hf_cache.tar' },
    @{ Name = 'yunnan_miner_outputs'; Archive = 'yunnan_miner_outputs.tar' },
    @{ Name = 'yunnan_miner_uploads'; Archive = 'yunnan_miner_uploads.tar' },
    @{ Name = 'yunnan_inference_runtime'; Archive = 'yunnan_inference_runtime.tar' },
    @{ Name = 'yunnan_miner_tiles'; Archive = 'yunnan_miner_tiles.tar' }
)

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

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$Capture
    )

    $commandText = 'docker ' + ($Arguments -join ' ')
    if ($Capture) {
        $output = & docker @Arguments 2>&1
    }
    else {
        & docker @Arguments
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed ($commandText) with exit code $LASTEXITCODE. Check Docker Desktop and the offline bundle."
    }

    if ($Capture) {
        return $output
    }
}

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Assert-Directory {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Required directory is missing: $Path"
    }
}

function Assert-NoReparsePath {
    param(
        [Parameter(Mandatory = $true)][string]$RootPath,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $rootFullPath = [System.IO.Path]::GetFullPath($RootPath).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )
    $rootItem = Get-Item -LiteralPath $rootFullPath -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Bundle root cannot be a reparse point: $rootFullPath"
    }

    $pathParts = @($RelativePath.Split(@(
        [System.IO.Path]::DirectorySeparatorChar,
        [System.IO.Path]::AltDirectorySeparatorChar
    )) | Where-Object { $_ })
    $currentPath = $rootFullPath
    foreach ($pathPart in $pathParts) {
        $currentPath = Join-Path $currentPath $pathPart
        if (-not (Test-Path -LiteralPath $currentPath)) {
            break
        }
        $item = Get-Item -LiteralPath $currentPath -Force -ErrorAction Stop
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Host input path contains a reparse point: $RelativePath"
        }
    }
}

function Get-SafeHostInputFiles {
    param(
        [Parameter(Mandatory = $true)][string]$DirectoryPath,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $pendingDirectories = New-Object 'System.Collections.Generic.Queue[string]'
    $files = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
    $pendingDirectories.Enqueue($DirectoryPath)
    while ($pendingDirectories.Count -gt 0) {
        $currentDirectory = $pendingDirectories.Dequeue()
        foreach ($item in @(Get-ChildItem -LiteralPath $currentDirectory -Force -ErrorAction Stop)) {
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Host input directory tree contains a reparse point: $RelativePath"
            }
            if ($item.PSIsContainer) {
                $pendingDirectories.Enqueue($item.FullName)
            }
            else {
                $files.Add($item)
            }
        }
    }
    return $files
}

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
        Assert-NoReparsePath -RootPath $rootFullPath -RelativePath $relativePath

        if (Test-Path -LiteralPath $fullPath -PathType Leaf) {
            $files = @((Get-Item -LiteralPath $fullPath -Force -ErrorAction Stop))
        }
        elseif (Test-Path -LiteralPath $fullPath -PathType Container) {
            $files = @(Get-SafeHostInputFiles -DirectoryPath $fullPath -RelativePath $relativePath)
        }
        else {
            throw "Required host input is missing: $relativePath"
        }

        foreach ($file in $files) {
            if (($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Host input file cannot be a reparse point: $($file.FullName)"
            }
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

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    Assert-File -Path $Path
    $values = @{}
    foreach ($rawLine in [System.IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith('#')) {
            continue
        }

        $separator = $line.IndexOf('=')
        if ($separator -le 0) {
            throw "Invalid .env entry in ${Path}: expected KEY=VALUE."
        }

        $key = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if ($key.Length -eq 0) {
            throw "Invalid .env entry in ${Path}: key is empty."
        }
        if ($value.Length -ge 2) {
            $firstCharacter = $value[0]
            $lastCharacter = $value[$value.Length - 1]
            if (($firstCharacter -eq '"' -and $lastCharacter -eq '"') -or ($firstCharacter -eq "'" -and $lastCharacter -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        $values[$key] = $value
    }
    return $values
}

function Assert-ChecksumFile {
    param(
        [Parameter(Mandatory = $true)][string]$ChecksumPath,
        [Parameter(Mandatory = $true)][string]$BaseDirectory,
        [Parameter(Mandatory = $true)][string[]]$ExpectedFileNames,
        [switch]$QuietEntries,
        [string]$SummaryLabel = 'Checksum files'
    )

    Assert-File -Path $ChecksumPath
    Assert-Directory -Path $BaseDirectory
    $entries = @([System.IO.File]::ReadAllLines($ChecksumPath) | Where-Object { $_.Trim().Length -gt 0 })
    if ($entries.Count -eq 0) {
        throw "Checksum manifest is empty: $ChecksumPath"
    }

    $expectedFileNameSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($expectedFileName in $ExpectedFileNames) {
        if (-not $expectedFileNameSet.Add($expectedFileName)) {
            throw "Duplicate expected checksum path: $expectedFileName"
        }
    }

    $manifestFileNames = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $baseFullPath = [System.IO.Path]::GetFullPath($BaseDirectory).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $basePrefix = $baseFullPath + [System.IO.Path]::DirectorySeparatorChar
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

    foreach ($expectedFileName in $ExpectedFileNames) {
        if (-not $manifestFileNames.Contains($expectedFileName)) {
            throw "Checksum manifest is missing required archive: $expectedFileName"
        }
    }

    if ($manifestFileNames.Count -ne $expectedFileNameSet.Count) {
        throw "Checksum manifest file count does not match the required input set: $ChecksumPath"
    }
    if ($QuietEntries) {
        Write-Host "$SummaryLabel OK ($($manifestFileNames.Count) files)"
    }
}

function Wait-ContainerStates {
    param([int]$TimeoutSeconds = 360)

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastPending = @('container state not inspected')
    while ([DateTime]::UtcNow -lt $deadline) {
        $existingContainers = @(Invoke-Docker -Arguments @('ps', '-a', '--format', '{{.Names}}') -Capture |
            ForEach-Object { $_.ToString().Trim() } |
            Where-Object { $_ })
        $missingContainers = @($ContainerNames | Where-Object { $existingContainers -notcontains $_ })
        if ($missingContainers.Count -gt 0) {
            $lastPending = @($missingContainers | ForEach-Object { "$($_)=missing" })
            Start-Sleep -Seconds 2
            continue
        }

        $arguments = @(
            'inspect',
            '--format',
            '{{.Name}}|{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}'
        ) + $ContainerNames
        try {
            $rows = @(Invoke-Docker -Arguments $arguments -Capture)
        }
        catch {
            $existingContainersAfterInspect = @(Invoke-Docker -Arguments @('ps', '-a', '--format', '{{.Names}}') -Capture |
                ForEach-Object { $_.ToString().Trim() } |
                Where-Object { $_ })
            $missingContainers = @($ContainerNames | Where-Object { $existingContainersAfterInspect -notcontains $_ })
            if ($missingContainers.Count -gt 0) {
                $lastPending = @($missingContainers | ForEach-Object { "$($_)=missing" })
                Start-Sleep -Seconds 2
                continue
            }
            throw
        }
        $states = @{}
        foreach ($row in $rows) {
            $parts = $row.ToString().Trim().Split('|')
            if ($parts.Count -ne 3) {
                throw 'Unexpected docker inspect state output.'
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

function Wait-Url {
    param([Parameter(Mandatory = $true)][string]$Url)

    $deadline = [DateTime]::UtcNow.AddSeconds(360)
    $lastError = 'No response received.'
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 10
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "Ready: $Url"
                return
            }
            $lastError = "HTTP status $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for $Url after 360 seconds. Last error: $lastError"
}

function Get-ConflictingResources {
    $existingContainers = @(Invoke-Docker -Arguments @("ps", "-a", "--format", "{{.Names}}") -Capture | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ })
    $existingVolumes = @(Invoke-Docker -Arguments @("volume", "ls", "--format", "{{.Name}}") -Capture | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ })
    $conflicts = @()
    $conflicts += $ContainerNames | Where-Object { $existingContainers -contains $_ } | ForEach-Object { "container=$_" }
    $conflicts += $Volumes | ForEach-Object { $_.Name } | Where-Object { $existingVolumes -contains $_ } | ForEach-Object { "volume=$_" }
    return $conflicts
}

Get-Command docker -ErrorAction Stop | Out-Null
$serverOs = (Invoke-Docker -Arguments @('version', '--format', '{{.Server.Os}}') -Capture | Out-String).Trim()
if ($serverOs -ne 'linux') {
    throw "Docker server must use Linux containers; detected '$serverOs'. Switch Docker Desktop to Linux containers."
}
Invoke-Docker -Arguments @('compose', 'version') | Out-Null

Assert-File -Path $ComposeFile
Assert-File -Path $EnvFile
Assert-Directory -Path (Join-Path $RootDir 'backend')
Assert-Directory -Path (Join-Path $RootDir 'frontend')
Assert-Directory -Path (Join-Path $RootDir 'miner')
Assert-Directory -Path (Join-Path $RootDir 'project_storage')
Assert-File -Path (Join-Path $RootDir 'backend\model\mmseg_config\model.inference.pth')
Assert-Directory -Path $OfflineMapDir
$offlineMapFiles = @(Get-ChildItem -LiteralPath $OfflineMapDir -Recurse -File -ErrorAction Stop |
    Where-Object { $_.Extension -in @('.tif', '.tiff') })
if ($offlineMapFiles.Count -eq 0) {
    throw 'Offline map directory does not contain a .tif or .tiff file.'
}
$env:OFFLINE_MAP_DIR = $OfflineMapDir
Assert-Directory -Path $ImageDir
Assert-Directory -Path $VolumeDir
foreach ($image in $Images) {
    Assert-File -Path (Join-Path $ImageDir $image.Archive)
}
foreach ($volume in $Volumes) {
    Assert-File -Path (Join-Path $VolumeDir $volume.Archive)
}
Assert-ChecksumFile -ChecksumPath (Join-Path $ImageDir "SHA256SUMS") -BaseDirectory $ImageDir -ExpectedFileNames @($Images | ForEach-Object { $_.Archive })
Assert-ChecksumFile -ChecksumPath (Join-Path $VolumeDir "SHA256SUMS") -BaseDirectory $VolumeDir -ExpectedFileNames @($Volumes | ForEach-Object { $_.Archive })
$hostInputFileNames = @(Get-HostInputFileNames)
Assert-ChecksumFile `
    -ChecksumPath $HostChecksumFile `
    -BaseDirectory $RootDir `
    -ExpectedFileNames $hostInputFileNames `
    -QuietEntries `
    -SummaryLabel 'Host data files'

$envValues = Read-DotEnv -Path $EnvFile
foreach ($requiredKey in @('ADMIN_USERNAME', 'ADMIN_PASSWORD', 'SECRET_KEY', 'MYSQL_PASSWORD', 'MYSQL_ROOT_PASSWORD')) {
    if (-not $envValues.ContainsKey($requiredKey) -or [string]::IsNullOrWhiteSpace([string]$envValues[$requiredKey])) {
        throw ".env requires a non-empty $requiredKey value."
    }
}

$occupiedPorts = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
    Where-Object { $_.Port -in @(3000, 4000, 5008, 8000) } |
    ForEach-Object { $_.Port } |
    Sort-Object -Unique
if ($occupiedPorts) {
    throw "Required ports are already occupied: $($occupiedPorts -join ', '). Stop the conflicting process before deployment."
}

$conflicts = @(Get-ConflictingResources)
if ($conflicts.Count -gt 0) {
    $conflictMessage = "目标电脑已有同名云南资源: $($conflicts -join ', ')"
    if ($ValidateOnly) {
        Write-Warning $conflictMessage
    }
    else {
        throw $conflictMessage
    }
}

Invoke-Docker -Arguments @("compose", "--env-file", $EnvFile, "-f", $ComposeFile, 'config', '--quiet') | Out-Null
$composeImages = @(Invoke-Docker -Arguments @("compose", "--env-file", $EnvFile, "-f", $ComposeFile, 'config', '--images') -Capture |
    ForEach-Object { $_.ToString().Trim() } |
    Where-Object { $_ } |
    Sort-Object -Unique)
$expectedComposeImages = @($Images | ForEach-Object { $_.Tag } | Sort-Object -Unique)
$unexpectedComposeImages = @($composeImages | Where-Object { $expectedComposeImages -notcontains $_ })
$missingComposeImages = @($expectedComposeImages | Where-Object { $composeImages -notcontains $_ })
if ($unexpectedComposeImages.Count -gt 0 -or $missingComposeImages.Count -gt 0) {
    throw 'Compose image configuration must contain only the three expected offline image tags.'
}

if ($ValidateOnly) {
    Write-Host 'VALIDATION OK'
    exit 0
}

foreach ($image in $Images) {
    $loadOutput = @(Invoke-Docker -Arguments @('load', '--input', (Join-Path $ImageDir $image.Archive)) -Capture |
        ForEach-Object { $_.ToString().Trim() } |
        Where-Object { $_ })
    $expectedLoadMessage = "Loaded image: $($image.Tag)"
    if ($loadOutput -notcontains $expectedLoadMessage) {
        throw "Docker load did not confirm the expected image tag: $($image.Tag)"
    }
    Invoke-Docker -Arguments @('image', 'inspect', $image.Tag) | Out-Null
}

$restoreRunId = [Guid]::NewGuid().ToString('N')
foreach ($volume in $Volumes) {
    Invoke-Docker -Arguments @('volume', 'create', "--label", "yunnan.offline.restore=$restoreRunId", $volume.Name) | Out-Null
    $volumeRestoreOwner = (Invoke-Docker -Arguments @('volume', 'inspect', '--format', '{{ index .Labels "yunnan.offline.restore" }}', $volume.Name) -Capture | Out-String).Trim()
    if ($volumeRestoreOwner -ne $restoreRunId) {
        throw "Volume restore ownership check failed for $($volume.Name); do not restore into an existing volume."
    }
    $restoreCommand = "cd /data && tar -xpf /backup/$($volume.Archive)"
    Invoke-Docker -Arguments @(
        'run', '--rm',
        '--mount', "type=volume,source=$($volume.Name),target=/data,volume-nocopy",
        '--mount', "type=bind,source=$VolumeDir,target=/backup,readonly",
        '--entrypoint', '/bin/sh',
        'yunnan-runtime:current', '-c', $restoreCommand
    )
}

Push-Location $RootDir
try {
    Invoke-Docker -Arguments @("compose", "--env-file", $EnvFile, "-f", $ComposeFile, "up", "-d")
}
finally {
    Pop-Location
}

Wait-ContainerStates -TimeoutSeconds 360

foreach ($url in @(
    'http://127.0.0.1:3000/',
    'http://127.0.0.1:4000/',
    'http://127.0.0.1:5008/api/auth/session',
    'http://127.0.0.1:8000/api/auth/session'
)) {
    Wait-Url -Url $url
}

Invoke-Docker -Arguments @('exec', 'yunnan-inference-worker', 'python', '/app/docker/check-inference-runtime.py')
Invoke-Docker -Arguments @("compose", "--env-file", $EnvFile, "-f", $ComposeFile, "ps")
Write-Host 'Frontend: http://127.0.0.1:3000/'
Write-Host 'Miner web: http://127.0.0.1:4000/'
Write-Host 'Backend: http://127.0.0.1:5008/api/auth/session'
Write-Host 'Miner API: http://127.0.0.1:8000/api/auth/session'
