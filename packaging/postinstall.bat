@echo off
chcp 65001 >nul
title Vermes

echo.
echo  ╔══════════════════════════════════════╗
echo  ║         Vermes 正在启动...           ║
echo  ╚══════════════════════════════════════╝
echo.

REM Start Vermes gateway in background
start /b "" "%~dp0vermes.exe" gateway start 2>nul

REM Wait for server to be ready
echo  等待服务就绪...
timeout /t 3 /nobreak >nul

REM Open browser
echo  正在打开浏览器...
start http://localhost:9119

echo.
echo  Vermes 已在后台运行。
echo  浏览器窗口已打开，如果没有自动打开，请访问：
echo  http://localhost:9119
echo.
echo  关闭此窗口不会停止 Vermes 服务。
echo.
pause
