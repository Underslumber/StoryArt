[CmdletBinding()]
param(
    [switch]$CollectPoseLineLibrary,
    [ValidateRange(1, 10)]
    [int]$PoseLinePages = 4
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -m venv .venv
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python -m venv .venv
}
else {
    throw 'Python 3 was not found. Install Python 3 and rerun this script.'
}

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual-environment Python was not created: $python"
}

& $python -m pip install -r (Join-Path $root 'requirements.txt')

$localAgents = Join-Path $root 'AGENTS.md'
if (-not (Test-Path -LiteralPath $localAgents)) {
    Copy-Item -LiteralPath (Join-Path $root 'AGENTS.example.md') -Destination $localAgents
}

& $python -m unittest discover -s (Join-Path $root 'tests') -v
& $python (Join-Path $root 'tools\style_pack_manager.py') list-styles --json

$poseManager = Join-Path $root 'tools\pose_line_library_manager.py'
$poseStatusJson = (& $python $poseManager status --json) -join "`n"
$poseStatus = $poseStatusJson | ConvertFrom-Json
Write-Output "POSE_LINE_LIBRARY_STATUS=$($poseStatus.status)"

if ($CollectPoseLineLibrary) {
    if ($poseStatus.status -eq 'READY') {
        & $python $poseManager validate
    }
    else {
        & $python $poseManager collect --pages $PoseLinePages
        & $python $poseManager build-contact-sheets
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
