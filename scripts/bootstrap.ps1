[CmdletBinding()]
param(
    [switch]$WebImages,
    [switch]$CollectPoseLineLibrary,
    [ValidateRange(1, 10)]
    [int]$PoseLinePages = 4,
    # Optional reference packs; POSE_ATLAS downloads the whole private Gitea atlas.
    [string[]]$WithReferences = @(),
    # Local copy for pack files that have no public URL (crops, expired links).
    [string]$ReferencesFromDir
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

& $python -m pip install -r (Join-Path $root 'requirements-dev.txt')
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
# Existing agent instructions survive updates; add only the portable entrypoint.
$startupMarker = '<!-- STORYART_REFERENCE_PACK_STARTUP -->'
$startupBlock = @"

$startupMarker
Do not offer or download pose references on project startup or updates.
Only when the user explicitly requests the atlas, read docs/REFERENCE_PACK_STARTUP.md
and download all 1439 images using the supported project command.
<!-- /STORYART_REFERENCE_PACK_STARTUP -->
"@
$agentInstructions = [string](Get-Content -LiteralPath $localAgents -Raw)
if ($agentInstructions.Contains($startupMarker)) {
    $agentInstructions = [regex]::Replace($agentInstructions, '(?s)<!-- STORYART_REFERENCE_PACK_STARTUP -->.*?<!-- /STORYART_REFERENCE_PACK_STARTUP -->', $startupBlock.Trim())
    Set-Content -LiteralPath $localAgents -Encoding utf8 -Value $agentInstructions
}
else {
    Add-Content -LiteralPath $localAgents -Encoding utf8 -Value $startupBlock
}

# The ChatGPT Web image skill is optional: without -WebImages nothing of it is
# installed or registered. setup-web-image.ps1 registers the agent after install.
if ($WebImages) {
    & (Join-Path $root 'scripts\setup-web-image.ps1') -PythonPath $python
}
else {
    Write-Output 'WEB_IMAGES=SKIPPED (.\scripts\bootstrap.ps1 -WebImages)'
}

$testFiles = @(Get-ChildItem -LiteralPath (Join-Path $root 'tests') -File -Filter 'test_*.py' | Sort-Object Name | ForEach-Object { $_.FullName })
if ($testFiles.Count -eq 0) {
    throw 'No project tests were found.'
}
# Pytest runs both unittest classes and pytest function tests. Pass test files
# explicitly so unrelated scratch directories are not part of collection.
& $python -m pytest @testFiles -q
Assert-NativeSuccess 'Running all project tests'
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
elseif ($poseStatus.status -eq 'REVIEW_REQUIRED') {
    Write-Output 'POSE_LINE_LIBRARY_NEXT_ACTION=Review is pending; continue only on explicit user request.'
}

$packsTool = Join-Path $root 'tools\reference_packs.py'
& $python $packsTool metadata --all
Assert-NativeSuccess 'Installing shipped reference catalogs without image downloads'
if ($WithReferences.Count -gt 0) {
    $packArgs = if ($WithReferences -contains 'all') { @('--all') } else { $WithReferences }
    if ($ReferencesFromDir) { $packArgs += @('--from-dir', $ReferencesFromDir) }
    & $python $packsTool install @packArgs
    # Exit 1 means some files had no reachable source; the rest stay installed.
    if ($LASTEXITCODE -eq 1) {
        Write-Output 'REFERENCE_PACKS=INCOMPLETE (POSE_ATLAS requires Git SSH access to Gitea; other missing files may use -ReferencesFromDir <folder>)'
        $global:LASTEXITCODE = 0
    }
    Assert-NativeSuccess 'Installing reference packs'
}
else {
    & $python $packsTool list
    Assert-NativeSuccess 'Listing reference packs'
}

& $python (Join-Path $root 'tools\reference_knowledge.py') build
Assert-NativeSuccess 'Building reference discovery database'
Write-Output 'STATUS=READY'
