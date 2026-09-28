[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('5.6', '6')]
    [string]$Profile,

    [string]$ConfigPath
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($ConfigPath)) {
    $ConfigPath = Join-Path $root '.codex\config.toml'
}

$configPathResolved = [System.IO.Path]::GetFullPath($ConfigPath)
$templatePath = Join-Path $root 'config\codex.project.example.toml'

if (-not (Test-Path -LiteralPath $configPathResolved -PathType Leaf)) {
    if ($configPathResolved -ne [System.IO.Path]::GetFullPath((Join-Path $root '.codex\config.toml'))) {
        throw "Config file does not exist: $configPathResolved"
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $configPathResolved) -Force | Out-Null
    Copy-Item -LiteralPath $templatePath -Destination $configPathResolved
}

$profileSettings = switch ($Profile) {
    '5.6' {
        @{
            RootModel = 'gpt-5.6-luna'
            RootEffort = 'high'
            AgentModel = 'gpt-5.6-luna'
            AgentEffort = 'high'
        }
    }
    '6' {
        @{
            RootModel = 'gpt-6-luna'
            RootEffort = 'high'
            AgentModel = 'gpt-6-luna'
            AgentEffort = 'high'
        }
    }
}

$content = [System.IO.File]::ReadAllText($configPathResolved)
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
    [System.IO.File]::WriteAllText($temporaryPath, $content, [System.Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $temporaryPath -Destination $configPathResolved -Force
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
