[CmdletBinding()]
param(
    [switch]$All,
    [string[]]$Pools = @(),
    [string]$FromDir
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$launcher = Join-Path $PSScriptRoot 'storyart.ps1'

# Catalogs are shipped in Git. This step never downloads images or grants approval.
& $launcher reference_packs metadata --all
if ($LASTEXITCODE -ne 0) { throw 'Reference metadata installation failed.' }
$incomplete = $false
if ($All -or $Pools.Count -gt 0) {
    $installArgs = if ($All) { @('--all') } else { @($Pools) }
    if ($FromDir) { $installArgs += @('--from-dir', $FromDir) }
    & $launcher reference_packs install @installArgs
    if ($LASTEXITCODE -eq 1) { $incomplete = $true }
    elseif ($LASTEXITCODE -ne 0) { throw 'Reference installation failed.' }
}
& $launcher reference_knowledge build
if ($LASTEXITCODE -ne 0) { throw 'Reference discovery rebuild failed.' }
if ($incomplete) {
    Write-Output 'STATUS=INCOMPLETE_REFERENCES (see NO_SOURCE/DOWNLOAD_FAILED and docs/REFERENCE_INSTALLATION.md)'
    exit 1
}
Write-Output 'STATUS=READY (optional images are downloaded only with -All or -Pools)'
