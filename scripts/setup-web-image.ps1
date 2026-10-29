param([string]$ProjectUrl,[string]$ProjectName,[string]$ProfileDir,[string]$NodePath,[string]$PythonPath,[switch]$CheckOnly,[switch]$Login)
$ErrorActionPreference='Stop'
$workspace=Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath=(Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $PythonPath) { $PythonPath=Get-ChildItem "$env:USERPROFILE/.cache/codex-runtimes/*/dependencies/python/python.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName }
if (-not $PythonPath) { throw 'Agent must provide bundled Python through -PythonPath.' }
$mode=if($CheckOnly){'doctor'}elseif($Login){'login'}else{'install'}
$arguments=@("$workspace/tools/web_image_setup.py",$mode,'--workspace',$workspace)
foreach($pair in @(@('--project-url',$ProjectUrl),@('--project-name',$ProjectName),@('--profile-dir',$ProfileDir),@('--node-path',$NodePath))) { if($pair[1]) { $arguments += $pair } }
& $PythonPath @arguments
if($LASTEXITCODE -ne 0){throw "Web image setup failed: $LASTEXITCODE"}
if(-not $CheckOnly -and -not $Login){ & "$PSScriptRoot/register-web-image-agent.ps1" -Workspace $workspace }
