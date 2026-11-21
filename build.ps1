# Builds dist\Naretta.exe. Run from the project folder after installing the dev requirements:
#   py -m venv .venv
#   .\.venv\Scripts\pip install -r requirements-dev.txt
#   .\build.ps1
# PyInstaller logs progress to stderr, so check its exit code rather than using
# $ErrorActionPreference = "Stop" (which Windows PowerShell 5.1 would trip on).

# Use the project's venv even when it isn't activated; fall back to Python on PATH.
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name Naretta `
  --hidden-import keyring.backends.Windows `
  --collect-submodules win32ctypes `
  --collect-all livekit `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.Qt3DCore `
  --exclude-module tkinter `
  main.py

# A command that couldn't start leaves no exit code, so treat that as a failure too.
$code = $LASTEXITCODE
if ($null -eq $code -or $code -ne 0) {
  Write-Error "PyInstaller failed (exit code $code)"
  exit 1
}
Write-Host "Built dist\Naretta.exe"
