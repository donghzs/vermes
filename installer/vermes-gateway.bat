@echo off
title Vermes Gateway v2.0.6
chcp 65001 >nul 2>&1
echo.
echo  ========================================
echo    Vermes Gateway v2.0.6
echo    Persistent Backend Service
echo  ========================================
echo.

REM Find bundled Python
set PYTHON=%~dp0python\python.exe
if not exist "%PYTHON%" (
    echo  [ERROR] Bundled Python not found
    pause
    exit /b 1
)

REM Set PYTHONPATH
set PYTHONPATH=%~dp0;%PYTHONPATH%

REM Set environment
set HERMES_HOME=%USERPROFILE%\.vermes
if not exist "%HERMES_HOME%" mkdir "%HERMES_HOME%"

echo  Python: %PYTHON%
echo  Config: %HERMES_HOME%
echo.

REM Kill existing Gateway if running
for /f "tokens=5" %%%%P in ('netstat -ano 2^>nul ^| findstr ":9119.*LISTEN"') do (
    echo  Stopping old Gateway (PID %%%%P)...
    taskkill /PID %%%%P /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM Start Gateway as persistent daemon
echo  Starting Gateway on port 9119...
echo  DO NOT CLOSE THIS WINDOW - Gateway runs here.
echo.
"%PYTHON%" -m vermes_cli.web_server --port 9119
set EXIT_CODE=%ERRORLEVEL%

echo.
echo  Gateway stopped (exit code: %EXIT_CODE%)
echo  Restart this file to restart Gateway.
pause
