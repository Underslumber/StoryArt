[CmdletBinding()]
param(
    [switch]$CollectPoseLineLibrary,
    [ValidateRange(1, 10)]
    [int]$PoseLinePages = 4
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Assert-NativeSuccess {
    param([Parameter(Mandatory = $true)][string]$Step)

    if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
    Assert-NativeSuccess 'Creating the virtual environment with py'
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
    Assert-NativeSuccess 'Creating the virtual environment with python'
}
else {
    throw 'Python 3 was not found. Install Python 3 and rerun this script.'
}

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual-environment Python was not created: $python"
}

& $python -m pip install -r (Join-Path $root 'requirements.txt')
Assert-NativeSuccess 'Installing Python requirements'

$projectConfigTemplate = Join-Path $root 'config\codex.project.example.toml'
$localConfig = Join-Path $root '.codex\config.toml'
if (Test-Path -LiteralPath $localConfig) {
    Write-Output 'CODEX_CONFIG=SKIPPED_EXISTING'
}
else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $localConfig) -Force | Out-Null
    Copy-Item -LiteralPath $projectConfigTemplate -Destination $localConfig
    Write-Output 'CODEX_CONFIG=CREATED_FROM_TEMPLATE'
}

$localAgents = Join-Path $root 'AGENTS.md'
if (-not (Test-Path -LiteralPath $localAgents)) {
    Copy-Item -LiteralPath (Join-Path $root 'AGENTS.example.md') -Destination $localAgents
}

& $python -m unittest discover -s (Join-Path $root 'tests') -v
Assert-NativeSuccess 'Running unit tests'
& $python (Join-Path $root 'tools\style_pack_manager.py') list-styles --json
Assert-NativeSuccess 'Listing styles'

$poseManager = Join-Path $root 'tools\pose_line_library_manager.py'
$poseStatusJson = (& $python $poseManager status --json)
Assert-NativeSuccess 'Reading pose-line library status'
$poseStatusJson = $poseStatusJson -join "`n"
$poseStatus = $poseStatusJson | ConvertFrom-Json
Write-Output "POSE_LINE_LIBRARY_STATUS=$($poseStatus.status)"

if ($CollectPoseLineLibrary) {
    if ($poseStatus.status -eq 'READY') {
        & $python $poseManager validate
        Assert-NativeSuccess 'Validating the pose-line library'
    }
    else {
        & $python $poseManager collect --pages $PoseLinePages
        Assert-NativeSuccess 'Collecting pose-line candidates'
        & $python $poseManager build-contact-sheets
        Assert-NativeSuccess 'Building pose-line contact sheets'
        Write-Output 'POSE_LINE_LIBRARY_NEXT_ACTION=Visually review every contact sheet, fill POSE_LINE_REVIEW.csv, then run apply-review.'
    }
}
elseif ($poseStatus.status -eq 'NOT_BUILT') {
    Write-Output 'POSE_LINE_LIBRARY_OFFER=Ask the user whether to build a local 200+ female-focused pose-line library.'
    Write-Output 'POSE_LINE_LIBRARY_COLLECT_COMMAND=.\scripts\bootstrap.ps1 -CollectPoseLineLibrary'
}
elseif ($poseStatus.status -eq 'REVIEW_REQUIRED') {
    Write-Output 'POSE_LINE_LIBRARY_NEXT_ACTION=Offer to continue visual review and apply POSE_LINE_REVIEW.csv.'
}

Write-Output 'STATUS=READY'
