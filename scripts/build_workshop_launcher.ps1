# Build NBA2KWorkshop.exe in the project root (real Windows PE, not a .vbs).
# The .exe is written directly into this repo so Norton file exclusions apply.
# If AV still deletes it, exclude the whole project folder or pause protection for the build.
# Prerequisites: venv +  pip install pyinstaller
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv not found; create it and pip install -r requirements.txt" }
& $py -m pip install "pyinstaller>=6.0" --quiet
# Python driver patches PyInstaller Windows PE post-steps (see script docstring)
& $py (Join-Path $root "scripts\build_workshop_launcher.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
