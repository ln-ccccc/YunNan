[CmdletBinding()]
param(
    [string]$CandidateImage = "yunnan-inference-worker:candidate",
    [string]$CanonicalImage = $(if ($env:INFERENCE_IMAGE) { $env:INFERENCE_IMAGE } else { "yunnan-inference-worker:current" }),
    [string]$CheckpointPath = "backend/model/mmseg_config/model.inference.pth",
    [string]$DockerCommand = "docker"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Dockerfile = Join-Path $RepoRoot "docker/Dockerfile.inference-gpu"

function Normalize-ImageReference([string]$Reference) {
    $normalized = $Reference.Trim()
    $normalized = $normalized -replace '^(?i:docker\.io/|index\.docker\.io/)', ''
    $normalized = $normalized -replace '^(?i:library/)', ''
    $finalComponent = ($normalized -split '/')[-1]
    if (-not $finalComponent.Contains(':') -and -not $normalized.Contains('@')) {
        $normalized = "${normalized}:latest"
    }
    return $normalized
}

if ([string]::IsNullOrWhiteSpace($CandidateImage) -or
    [string]::IsNullOrWhiteSpace($CanonicalImage) -or
    (Normalize-ImageReference $CandidateImage) -ceq
        (Normalize-ImageReference $CanonicalImage)) {
    throw "INFERENCE_IMAGE_TAG_CONFLICT: candidate and canonical image must differ"
}

if ([System.IO.Path]::IsPathRooted($CheckpointPath)) {
    $CheckpointCandidate = $CheckpointPath
}
else {
    $CheckpointCandidate = Join-Path $RepoRoot $CheckpointPath
}
$CheckpointItem = Get-Item -LiteralPath $CheckpointCandidate -ErrorAction SilentlyContinue
if ($null -eq $CheckpointItem -or $CheckpointItem.PSIsContainer) {
    throw "MODEL_CHECKPOINT_MISSING"
}
$Checkpoint = $CheckpointItem.FullName

function Assert-DockerSucceeded([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "Docker step failed: $Step (exit $LASTEXITCODE)"
    }
}

Push-Location $RepoRoot
try {
    & $DockerCommand build --progress=plain --file $Dockerfile --target inference-worker --tag $CandidateImage $RepoRoot
    Assert-DockerSucceeded "build final inference-worker target"

    & $DockerCommand run --rm --entrypoint python $CandidateImage /app/docker/check-inference-image.py
    Assert-DockerSucceeded "image/content contract check"

    & $DockerCommand run --rm --entrypoint python `
        --env INFERENCE_ACCELERATOR=cpu `
        --env INFERENCE_CPU_FALLBACK=true `
        --volume "${Checkpoint}:/app/backend/model/mmseg_config/model.inference.pth:ro" `
        $CandidateImage /app/docker/check-inference-runtime.py
    Assert-DockerSucceeded "runtime check"

    $ImageMetadata = & $DockerCommand image inspect --format '{{.Id}} {{.Size}}' $CandidateImage
    Assert-DockerSucceeded "inspect candidate image ID and size"
    Write-Output "Verified candidate image: $ImageMetadata"

    & $DockerCommand image tag $CandidateImage $CanonicalImage
    Assert-DockerSucceeded "assign canonical image tag"
    Write-Output "Canonical image tagged after verification: $CanonicalImage"
}
finally {
    Pop-Location
}
