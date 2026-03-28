param(
    [ValidateSet('onedir', 'onefile')]
    [string]$Mode = 'onedir',
    [string]$IconPath = 'forza_auto_shift\assets\icon.ico',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot 'venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
} else {
    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCmd) {
        throw "python not found on PATH. Install Python or activate your environment first."
    }
    $pythonExe = $pythonCmd.Source
}

& $pythonExe -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed for $pythonExe. Install it with: $pythonExe -m pip install pyinstaller"
}

$entry = 'run.py'
$resolvedIconPath = Join-Path $repoRoot $IconPath

$args = @(
    '--noconfirm',
    '--clean',
    '--windowed',
    '--name', 'forza-auto-shift',
    '--hidden-import', 'pynput.keyboard._win32',
    '--add-data', 'forza_auto_shift\i18n;forza_auto_shift\i18n',
    '--add-data', 'forza_auto_shift\assets;forza_auto_shift\assets'
)

if (Test-Path $resolvedIconPath) {
    $args += @('--icon', $resolvedIconPath)
} else {
    Write-Warning "Icon file not found: $resolvedIconPath. Building without custom exe icon."
}

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
    Write-Host "$pythonExe -m PyInstaller $($args -join ' ')"
    exit 0
}

& $pythonExe -m PyInstaller @args
exit $LASTEXITCODE
