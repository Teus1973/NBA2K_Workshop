@echo off
setlocal EnableDelayedExpansion
title NBA2K26 Workshop - Launcher

rem ----------------------------------------------------------------------
rem  Self-healing launcher
rem    1. Verify venv + streamlit are importable (rebuild if not).
rem    2. Delegate to launcher.py which handles:
rem         - fast-path (already-running + healthy -> open browser)
rem         - stale-PID kill on port 8506
rem         - hidden Streamlit launch + browser open on first HTTP 200
rem ----------------------------------------------------------------------

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%venv\Scripts\python.exe"

pushd "%PROJECT_DIR%"

echo ============================================================
echo  NBA2K26 Rookie Rating Tool - Self-Healing Launcher
echo ============================================================
echo.

if not exist "%VENV_PYTHON%" goto rebuild_venv

"%VENV_PYTHON%" -c "import streamlit" >nul 2>&1
if errorlevel 1 goto rebuild_venv

goto launch

:rebuild_venv
echo [WARN] venv missing or broken. Rebuilding...
if exist "%PROJECT_DIR%venv" rmdir /s /q "%PROJECT_DIR%venv"

echo [INFO] Creating new virtual environment...
python -m venv "%PROJECT_DIR%venv"
if errorlevel 1 (
    echo [ERROR] Failed to create venv. Ensure Python 3.11+ is on PATH.
    pause
    popd
    endlocal
    exit /b 1
)

echo [INFO] Upgrading pip...
"%VENV_PYTHON%" -m pip install --upgrade pip --quiet

if exist "%PROJECT_DIR%requirements.txt" (
    echo [INFO] Installing from requirements.txt ^(this can take a minute^)...
    "%VENV_PYTHON%" -m pip install -r "%PROJECT_DIR%requirements.txt" --quiet
    if errorlevel 1 (
        echo [ERROR] Package installation failed.
        pause
        popd
        endlocal
        exit /b 1
    )
)
echo [INFO] Environment ready.
echo.

:launch
echo [INFO] Starting NBA2K26 Workshop...
"%VENV_PYTHON%" "%PROJECT_DIR%launcher.py"
set "RC=%ERRORLEVEL%"

popd
endlocal & exit /b %RC%
