# Builds dist\Naretta.exe. Run from the project folder in an activated virtual environment:
#   .\.venv\Scripts\Activate.ps1
#   pip install -r requirements-dev.txt
#   .\build.ps1
# PyInstaller logs progress to stderr, so check its exit code rather than using
# $ErrorActionPreference = "Stop" (which Windows PowerShell 5.1 would trip on).

pyinstaller --noconfirm --clean --onefile --windowed `
  --name Naretta `
  --hidden-import keyring.backends.Windows `
  --collect-submodules win32ctypes `
  --collect-all livekit `
  --exclude-module PySide6.QtWebEngineCore `
  --exclude-module PySide6.QtWebEngineWidgets `
  --exclude-module PySide6.Qt3DCore `
  --exclude-module tkinter `
  main.py

if ($LASTEXITCODE -ne 0) {
  Write-Error "PyInstaller failed (exit code $LASTEXITCODE)"
  exit $LASTEXITCODE
}
Write-Host "Built dist\Naretta.exe"
