[CmdletBinding()]
param()

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

Write-Output 'STATUS=READY'
