@echo off
title Vermes - AI Agent v2.0.6
echo ========================================
echo   Vermes - AI Agent v2.0.6
echo   桌面端开箱即用的 AI Agent
echo ========================================
echo.

REM Find Python
set PYTHON=
for %%P in (python3.11 python3.12 python3 python) do (
    where %%P >nul 2>&1 && (
        set PYTHON=%%P
        goto :found
    )
)
echo [ERROR] Python not found. Please install Python 3.11+
pause
exit /b 1

:found
echo Using: %PYTHON%
%PYTHON% --version
echo.

REM Set HERMES_HOME
if not defined HERMES_HOME (
    set HERMES_HOME=%USERPROFILE%\.vermes
)
echo Config: %HERMES_HOME%
echo.

REM Check if vermes_cli is installed
%PYTHON% -c "import vermes_cli" >nul 2>&1
if errorlevel 1 (
    echo [WARN] vermes_cli not installed, trying pip install...
    %PYTHON% -m pip install vermes 2>nul
)

REM Start backend
echo Starting backend on port 9119...
echo Close this window to stop Vermes.
echo.
%PYTHON% -m vermes_cli.web_server --port 9119 & start http://127.0.0.1:9119

pause
