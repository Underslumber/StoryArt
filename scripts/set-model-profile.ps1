[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('6.1', '5.6', '6')]
    [string]$Profile,

    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# Explicit profile switches are opt-in; experiments are never fallback routes.
$routesPath = Join-Path $root 'config\model_routes.json'
$routes = [System.IO.File]::ReadAllText($routesPath) | ConvertFrom-Json
if ($routes.schema_version -ne 1 -or $routes.default_profile -ne '6.1') {
    throw 'Model routes require schema_version=1 and default_profile=6.1.'
}
function Assert-RouteKeys($Object, [string[]]$Expected, [string]$Name) {
    if ($null -eq $Object -or $Object -isnot [pscustomobject]) {
        throw "Invalid model routes object: $Name"
    }
    $actual = @($Object.PSObject.Properties.Name | Sort-Object)
    if (($actual -join ',') -ne (($Expected | Sort-Object) -join ',')) {
        throw "Invalid model routes keys: $Name"
    }
}
Assert-RouteKeys $routes.profiles @('6.1', '5.6', '6') 'profiles'
Assert-RouteKeys $routes.roles @('ROOT', 'DEFAULT_WORKER', 'STYLE_LIBRARIAN', 'IDENTITY_CURATOR', 'CALL_PLANNER', 'VISUAL_QA', 'ESCALATION_ORCHESTRATOR', 'CODE_IMPLEMENTER', 'CODE_REVIEW') 'roles'
Assert-RouteKeys $routes.experiments @('ROUTINE_ROOT', 'MECHANICAL_WORKER') 'experiments'
$routeSettings = @()
foreach ($entry in $routes.profiles.PSObject.Properties) {
    Assert-RouteKeys $entry.Value @('root', 'default_agent') "profiles.$($entry.Name)"
    $routeSettings += $entry.Value.root, $entry.Value.default_agent
}
$routeSettings += @($routes.roles.PSObject.Properties | ForEach-Object { $_.Value })
$routeSettings += @($routes.experiments.PSObject.Properties | ForEach-Object { $_.Value })
foreach ($setting in $routeSettings) {
    Assert-RouteKeys $setting @('model', 'reasoning_effort') 'model/effort'
    if ($setting.model -isnot [string] -or $setting.model -cnotmatch '^gpt-[a-z0-9.-]+$' -or $setting.reasoning_effort -isnot [string] -or $setting.reasoning_effort -cnotin @('low', 'medium', 'high')) {
        throw 'Invalid model/effort object in model routes.'
    }
}
if ([string]::IsNullOrWhiteSpace($Profile)) { $Profile = $routes.default_profile }
$selected = $routes.profiles.PSObject.Properties[$Profile].Value
$profileSettings = @{
    RootModel = $selected.root.model
    RootEffort = $selected.root.reasoning_effort
    AgentModel = $selected.default_agent.model
    AgentEffort = $selected.default_agent.reasoning_effort
}

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $root '.codex\config.toml'
}

$configPathResolved = [System.IO.Path]::GetFullPath($ConfigPath)
$templatePath = Join-Path $root 'config\codex.project.example.toml'

$configExists = Test-Path -LiteralPath $configPathResolved -PathType Leaf
if (-not $configExists) {
    if ($configPathResolved -ne [System.IO.Path]::GetFullPath((Join-Path $root '.codex\config.toml'))) {
        throw "Config file does not exist: $configPathResolved"
    }
}

$content = if ($configExists) { [System.IO.File]::ReadAllText($configPathResolved) } else { [System.IO.File]::ReadAllText($templatePath) }
$replacements = @(
    @{ Pattern = '(?m)^model\s*=\s*"[^"]*"\s*$'; Value = "model = `"$($profileSettings.RootModel)`""; Name = 'root model' },
    @{ Pattern = '(?m)^model_reasoning_effort\s*=\s*"[^"]*"\s*$'; Value = "model_reasoning_effort = `"$($profileSettings.RootEffort)`""; Name = 'root reasoning effort' },
    @{ Pattern = '(?m)^default_subagent_model\s*=\s*"[^"]*"\s*$'; Value = "default_subagent_model = `"$($profileSettings.AgentModel)`""; Name = 'default agent model' },
    @{ Pattern = '(?m)^default_subagent_reasoning_effort\s*=\s*"[^"]*"\s*$'; Value = "default_subagent_reasoning_effort = `"$($profileSettings.AgentEffort)`""; Name = 'default agent reasoning effort' }
)

foreach ($replacement in $replacements) {
    $matches = [regex]::Matches($content, $replacement.Pattern)
    if ($matches.Count -ne 1) {
        throw "Expected exactly one $($replacement.Name) setting in $configPathResolved; found $($matches.Count). No changes were written."
    }
    $content = ([regex]::new($replacement.Pattern)).Replace($content, $replacement.Value, 1)
}

$temporaryPath = "$configPathResolved.$([guid]::NewGuid().ToString('N')).tmp"
try {
    New-Item -ItemType Directory -Path (Split-Path -Parent $configPathResolved) -Force | Out-Null
    [System.IO.File]::WriteAllText($temporaryPath, $content, [System.Text.UTF8Encoding]::new($false))
    if ($configExists) {
        [System.IO.File]::Replace($temporaryPath, $configPathResolved, [NullString]::Value)
    }
    else {
        [System.IO.File]::Move($temporaryPath, $configPathResolved)
    }
}
finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath -Force
    }
}

Write-Output "PROFILE=$Profile"
Write-Output "ROOT_MODEL=$($profileSettings.RootModel) ($($profileSettings.RootEffort))"
Write-Output "DEFAULT_AGENT_MODEL=$($profileSettings.AgentModel) ($($profileSettings.AgentEffort))"
Write-Output "CONFIG=$configPathResolved"
Write-Output 'APPLIES=New project sessions and agents started after this change; running sessions are unchanged.'
