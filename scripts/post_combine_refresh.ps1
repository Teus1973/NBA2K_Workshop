# NBA2K26 Workshop -- one-shot post-combine refresh.
# Intended to be run manually (or via a one-shot Task Scheduler entry) the day
# after the combine finishes (May 10, 2026). Pulls the new combine data,
# re-runs calibration (so combine-override coefficients are fresh), and
# recomputes every prospect's ratings.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

Write-Host "[NBA2K Workshop] Post-combine refresh"
& $Python -m src.cli refresh-combine
& $Python -m src.cli refit
& $Python -m src.cli recalc
& $Python -m src.cli export-excel
