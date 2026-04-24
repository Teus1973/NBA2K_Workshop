# NBA2K26 Workshop -- weekly refresh helper.
# Prefers the local venv (.venv) python if it exists, otherwise falls back
# to `python` on PATH.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host "[NBA2K Workshop] Weekly refresh"
& $Python -m src.cli refresh

Write-Host "[NBA2K Workshop] Weekly Excel export"
& $Python -m src.cli export-excel
