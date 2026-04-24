@echo off
setlocal EnableDelayedExpansion
title NBA2K26 Workshop - Troubleshoot (visible console)

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%venv\Scripts\python.exe"
set "PORT=8506"

pushd "%PROJECT_DIR%"

echo ============================================================
echo  NBA2K26 Workshop - Troubleshoot
echo    - Kills any stale process on port %PORT%
echo    - Runs Streamlit in this console so you can read errors
echo ============================================================
echo.

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Missing venv: %VENV_PYTHON%
    echo Run LaunchNBA2KWorkshop.bat once to create the environment.
    pause
    popd
    exit /b 1
)

echo [INFO] Stopping any process LISTENING on port %PORT% ...
for /f "tokens=5" %%P in ('netstat -aon ^| findstr ":%PORT%.*LISTENING"') do (
    echo [INFO] taskkill /F /PID %%P
    taskkill /F /PID %%P >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo.
echo [INFO] Starting Streamlit (console stays open; Ctrl+C to stop).
echo [INFO] When you see "You can now view", open: http://localhost:%PORT%
echo.

"%VENV_PYTHON%" -m streamlit run "%PROJECT_DIR%app.py" --server.port %PORT% --server.headless true

echo.
echo [INFO] Streamlit exited with code %ERRORLEVEL%
pause
popd
endlocal
