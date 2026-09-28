[CmdletBinding()]
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Tool,

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$ToolArguments = @()
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolPath = Join-Path (Join-Path $projectRoot 'tools') ($Tool + '.py')
if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) {
    [Console]::Error.WriteLine("StoryArt tool not found: tools\$Tool.py")
    exit 2
}

# Discover only the project's venv, Python commands already on PATH, and the
# normal per-user Windows Python install directory. Never install or configure.
$candidates = [System.Collections.Generic.List[string]]::new()
$venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $candidates.Add($venvPython)
}

foreach ($command in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
    if ($command.CommandType -ne 'Application' -or -not $command.Source) { continue }
    if ($command.Source -match '(?i)\\WindowsApps\\') { continue }
    $candidates.Add($command.Source)
}

$localPrograms = Join-Path $env:LOCALAPPDATA 'Programs\Python'
if (Test-Path -LiteralPath $localPrograms -PathType Container) {
    foreach ($install in @(Get-ChildItem -LiteralPath $localPrograms -Directory | Sort-Object Name -Descending)) {
        $pythonExe = Join-Path $install.FullName 'python.exe'
        if (Test-Path -LiteralPath $pythonExe -PathType Leaf) { $candidates.Add($pythonExe) }
    }
}

$seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
$selectedPython = $null
foreach ($candidate in $candidates) {
    try { $resolvedCandidate = (Resolve-Path -LiteralPath $candidate -ErrorAction Stop).Path }
    catch { continue }
    if (-not $seen.Add($resolvedCandidate)) { continue }

    # Test the two runtime dependencies in one bounded probe per candidate.
    & $resolvedCandidate -X utf8 -c 'import yaml, PIL' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $selectedPython = $resolvedCandidate
        break
    }
}

if (-not $selectedPython) {
    [Console]::Error.WriteLine('No usable StoryArt Python runtime found. Checked .venv, Python on PATH, and %LOCALAPPDATA%\Programs\Python; a candidate must import both yaml and PIL.')
    exit 3
}

function ConvertTo-WindowsCommandLineArgument {
    param([string]$Value)
    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }

    $builder = [System.Text.StringBuilder]::new()
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') {
            $backslashes++
        }
        elseif ($character -eq '"') {
            [void]$builder.Append(('\' * (2 * $backslashes + 1)))
            [void]$builder.Append('"')
            $backslashes = 0
        }
        else {
            if ($backslashes -gt 0) { [void]$builder.Append(('\' * $backslashes)) }
            [void]$builder.Append($character)
            $backslashes = 0
        }
    }
    if ($backslashes -gt 0) { [void]$builder.Append(('\' * (2 * $backslashes))) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

# Copy raw UTF-8 bytes around Windows PowerShell's OEM-code-page decoder.
$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $selectedPython
$startInfo.Arguments = (@('-X', 'utf8', $toolPath) + $ToolArguments | ForEach-Object { ConvertTo-WindowsCommandLineArgument ([string]$_) }) -join ' '
$startInfo.UseShellExecute = $false
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$process = [System.Diagnostics.Process]::new()
$process.StartInfo = $startInfo
[void]$process.Start()
$stdoutTask = $process.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
$stderrTask = $process.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
$process.WaitForExit()
[System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask))
exit $process.ExitCode
