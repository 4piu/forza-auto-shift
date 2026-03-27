param(
    [ValidateSet('onedir', 'onefile')]
    [string]$Mode = 'onedir',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pyInstaller = Join-Path $repoRoot 'venv\Scripts\pyinstaller.exe'
if (-not (Test-Path $pyInstaller)) {
    throw "pyinstaller not found at $pyInstaller. Install it with: venv\\Scripts\\python.exe -m pip install pyinstaller"
}

$entry = 'run.py'

$args = @(
    '--noconfirm',
    '--clean',
    '--windowed',
    '--name', 'forza-auto-shift',
    '--hidden-import', 'pynput.keyboard._win32',
    '--add-data', 'forza_auto_shift\i18n;forza_auto_shift\i18n',
    '--add-data', 'forza_auto_shift\assets;forza_auto_shift\assets'
)

# Keep bundle lean: rely on import analysis and explicitly exclude heavy Qt stacks
# this app does not use.
$args += @(
    '--exclude-module', 'PySide6.QtWebEngineCore',
    '--exclude-module', 'PySide6.QtWebEngineWidgets',
    '--exclude-module', 'PySide6.QtWebEngineQuick',
    '--exclude-module', 'PySide6.QtQml',
    '--exclude-module', 'PySide6.QtQuick',
    '--exclude-module', 'PySide6.QtQuickWidgets',
    '--exclude-module', 'PySide6.QtQuick3D',
    '--exclude-module', 'PySide6.QtPdf',
    '--exclude-module', 'PySide6.QtPdfWidgets',
    '--exclude-module', 'PySide6.Qt3DCore',
    '--exclude-module', 'PySide6.Qt3DRender',
    '--exclude-module', 'PySide6.Qt3DInput',
    '--exclude-module', 'PySide6.Qt3DExtras',
    '--exclude-module', 'PySide6.Qt3DAnimation'
)

if ($Mode -eq 'onefile') {
    $args += '--onefile'
} else {
    $args += '--onedir'
}

$args += $entry

if ($DryRun) {
    Write-Host "$pyInstaller $($args -join ' ')"
    exit 0
}

& $pyInstaller @args
exit $LASTEXITCODE
