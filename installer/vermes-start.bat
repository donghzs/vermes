@echo off
title Vermes - AI Agent v2.0.6
chcp 65001 >nul 2>&1
echo.
echo  ╔══════════════════════════════════════╗
echo  ║   Vermes - AI Agent v2.0.6          ║
echo  ║   桌面端开箱即用的 AI Agent          ║
echo  ╚══════════════════════════════════════╝
echo.

REM ── Find bundled Python ─────────────────────
set PYTHON=%~dp0python\python.exe
if not exist "%PYTHON%" (
    echo  [ERROR] Bundled Python not found at %PYTHON%
    echo  Please reinstall Vermes.
    pause
    exit /b 1
)

echo  Python: bundled v3.12
echo  Config: %USERPROFILE%\.vermes
echo.

REM ── Set environment ─────────────────────────
set HERMES_HOME=%USERPROFILE%\.vermes
if not exist "%HERMES_HOME%" mkdir "%HERMES_HOME%"

REM ── Check port ──────────────────────────────
netstat -ano | findstr ":9119.*LISTEN" >nul 2>&1
if not errorlevel 1 (
    echo  [WARN] Port 9119 in use. Another instance running?
    echo.
    set /p REPLY=  Kill and restart? [Y/n] 
    if /i not "%REPLY%"=="n" (
        for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":9119.*LISTEN"') do taskkill /PID %%P /F >nul 2>&1
        timeout /t 1 /nobreak >nul
    ) else (
        start http://127.0.0.1:9119
        pause
        exit /b 0
    )
)

REM ── Start backend + open native window ──────
echo  Starting Vermes...
echo  Close this window to stop.
echo.
"%PYTHON%" -m hermes_cli.gui_app --port 9119

echo.
echo  Vermes stopped.
pause
