[CmdletBinding()]
param([string]$Workspace = (Split-Path -Parent $PSScriptRoot))

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath $Workspace).Path
$roleSource = Join-Path $root 'config\web_image_agent.toml'
$roleTarget = Join-Path $root '.codex\agents\web_image.toml'
$skillSource = Join-Path $root 'skills\storyart-web-image\SKILL.md'
$skillTarget = Join-Path $root '.agents\skills\storyart-web-image\SKILL.md'

foreach ($pair in @(@($roleSource, $roleTarget), @($skillSource, $skillTarget))) {
    $source = [string]$pair[0]
    $target = [string]$pair[1]
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Missing project source: $source" }
    if (Test-Path -LiteralPath $target -PathType Leaf) {
        if ((Get-FileHash -LiteralPath $source).Hash -ne (Get-FileHash -LiteralPath $target).Hash) {
            throw "Project agent/skill differs from its source; preserve and review the local edit: $target"
        }
    }
    else {
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $target -ErrorAction Stop
    }
}
Write-Output 'WEB_IMAGE_AGENT=REGISTERED_PROJECT_LOCAL'
Write-Output 'PERSONAL_AND_PROJECT_CONFIG=UNCHANGED'
