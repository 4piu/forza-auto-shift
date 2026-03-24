param(
    [ValidateSet('dry-run', 'standalone', 'onefile')]
    [string]$Mode = 'standalone'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$deploy = Join-Path $repoRoot 'venv\Scripts\pyside6-deploy.exe'
if (-not (Test-Path $deploy)) {
    throw "pyside6-deploy not found at $deploy"
}

$entry = 'forza_auto_shift\__main__.py'
$commonArgs = @('--name', 'forza-auto-shift', $entry)

if ($Mode -eq 'dry-run') {
    & $deploy '--dry-run' '--mode' 'standalone' @commonArgs
    exit $LASTEXITCODE
}

if ($Mode -eq 'standalone') {
    & $deploy '--force' '--mode' 'standalone' @commonArgs
    exit $LASTEXITCODE
}

& $deploy '--force' '--mode' 'onefile' @commonArgs
exit $LASTEXITCODE
