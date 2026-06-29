$ErrorActionPreference = "Stop"

$ScriptPath = Join-Path $PSScriptRoot "droste_codex_mcp.py"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path

if (-not $env:DROSTE_MEMORY_ROOT) {
    $env:DROSTE_MEMORY_ROOT = $RepoRoot
}

if (-not $env:DROSTE_VISUALIZER_URL) {
    $env:DROSTE_VISUALIZER_URL = "http://127.0.0.1:5000"
}

$pythonPathEntries = New-Object System.Collections.Generic.List[string]
$LocalPackages = Join-Path $env:DROSTE_MEMORY_ROOT ".python-packages"
if (Test-Path $LocalPackages) {
    $pythonPathEntries.Add($LocalPackages)
}
$pythonPathEntries.Add($env:DROSTE_MEMORY_ROOT)
if ($env:PYTHONPATH) {
    $pythonPathEntries.Add($env:PYTHONPATH)
}
$env:PYTHONPATH = [string]::Join([System.IO.Path]::PathSeparator, $pythonPathEntries)
$env:PYTHONUNBUFFERED = "1"

$pythonCandidates = New-Object System.Collections.Generic.List[string]
if ($env:DROSTE_PYTHON) {
    $pythonCandidates.Add($env:DROSTE_PYTHON)
}

$CodexRuntimePython = Join-Path $HOME ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if (Test-Path $CodexRuntimePython) {
    $pythonCandidates.Add($CodexRuntimePython)
}

$PythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($PythonCommand) {
    $pythonCandidates.Add($PythonCommand.Source)
}

foreach ($Candidate in $pythonCandidates) {
    if (-not $Candidate) {
        continue
    }
    if (Test-Path $Candidate) {
        $ResolvedCandidate = $Candidate
    } else {
        $CommandCandidate = Get-Command $Candidate -ErrorAction SilentlyContinue
        if (-not $CommandCandidate) {
            continue
        }
        $ResolvedCandidate = $CommandCandidate.Source
    }
    & $ResolvedCandidate $ScriptPath
    exit $LASTEXITCODE
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
    & $PyLauncher.Source -3 $ScriptPath
    exit $LASTEXITCODE
}

Write-Error "No Python runtime found for Droste-Memory MCP. Set DROSTE_PYTHON to a python.exe path."
exit 127
