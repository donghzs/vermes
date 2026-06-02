@echo off
title Vermes - AI Agent v2.0.6
chcp 65001 >nul 2>&1
echo.
echo  ╔══════════════════════════════════════╗
echo  ║   Vermes - AI Agent v2.0.6          ║
echo  ║   桌面端开箱即用的 AI Agent          ║
echo  ╚══════════════════════════════════════╝
echo.

REM ── 1. Find Python ──────────────────────────
set PYTHON=
for %%P in (python3.12 python3.11 python3 python) do (
    where %%P >nul 2>&1 && (
        for /f "tokens=*" %%V in ('%%P --version 2^>^&1') do set PYVER=%%V
        set PYTHON=%%P
        goto :found_python
    )
)
echo  [ERROR] Python not found!
echo  Please install Python 3.11+ from https://python.org
echo.
pause
exit /b 1

:found_python
echo  Python: %PYVER%

REM ── 2. Check dependencies ───────────────────
%PYTHON% -c "import fastapi, uvicorn, httpx" >nul 2>&1
if errorlevel 1 (
    echo  [WARN] Dependencies missing, installing...
    %PYTHON% -m pip install fastapi uvicorn httpx pydantic yaml openai anthropic -q
    echo  Done.
)

REM ── 3. Set config path ──────────────────────
if not defined HERMES_HOME (
    set HERMES_HOME=%USERPROFILE%\.vermes
)
if not exist "%HERMES_HOME%" mkdir "%HERMES_HOME%"
echo  Config: %HERMES_HOME%

REM ── 4. Check port availability ──────────────
netstat -ano | findstr ":9119.*LISTEN" >nul 2>&1
if not errorlevel 1 (
    echo  [WARN] Port 9119 already in use
    echo  Another instance may be running.
    echo.
    set /p REPLY=  Kill it and restart? [Y/n] 
    if /i "%REPLY%"=="n" (
        start http://127.0.0.1:9119
        pause
        exit /b 0
    )
    for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":9119.*LISTEN"') do (
        taskkill /PID %%P /F >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
)

REM ── 5. Start backend ────────────────────────
echo.
echo  Starting Vermes backend on port 9119...
echo  Close this window to stop.
echo.

REM Start browser after 3 second delay
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:9119"

REM Run backend (blocking - keeps CMD open)
%PYTHON% -m hermes_cli.web_server --port 9119

echo.
echo  Vermes stopped.
pause
