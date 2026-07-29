@echo off
title Vermes v2.0.6
chcp 65001 >nul 2>&1
echo.
echo  ========================================
echo    Vermes - AI Agent v2.0.6
echo  ========================================
echo.

REM Set environment
set PYTHON=%~dp0python\python.exe
set PYTHONPATH=%~dp0;%PYTHONPATH%
set HERMES_HOME=%USERPROFILE%\.vermes
if not exist "%HERMES_HOME%" mkdir "%HERMES_HOME%"

REM Kill existing Gateway
for /f "tokens=5" %%%%P in ('netstat -ano 2^>nul ^| findstr ":9119.*LISTEN"') do (
    taskkill /PID %%%%P /F >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM Start Gateway as independent persistent process
echo  Starting Gateway on port 9119...
start "Vermes Gateway" /MIN "%PYTHON%" -m vermes_cli.web_server --port 9119

REM Wait for Gateway ready
:wait
timeout /t 1 /nobreak >nul
netstat -ano | findstr ":9119.*LISTEN" >nul 2>&1
if errorlevel 1 goto :wait

echo  Gateway ready. Opening native window...
echo.

REM Open native window (pywebview) - connects to Gateway
"%PYTHON%" -m vermes_cli.gui_app --no-server --port 9119

echo.
echo  Vermes closed. Gateway still running in background.
echo  Close "Vermes Gateway" window to stop Gateway.
pause
