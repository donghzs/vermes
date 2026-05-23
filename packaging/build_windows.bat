@echo off
chcp 65001 >nul
title Vermes Windows Builder
echo ========================================
echo  Vermes Windows Builder v1.0
echo ========================================
echo.

:: 杀掉已有进程
taskkill /f /im Vermes.exe 2>nul
taskkill /f /im python.exe 2>nul

:: 清理旧构建
echo [1/4] 清理旧构建...
rmdir /s /q "C:\Users\administrator\Desktop\Vermes\dist3" 2>nul
rmdir /s /q "C:\Users\administrator\Desktop\Vermes\build3" 2>nul

:: 构建
echo [2/4] 开始 PyInstaller 打包...
pyinstaller --onedir --windowed --name Vermes --noconfirm ^
  --distpath "C:\Users\administrator\Desktop\Vermes\dist3" ^
  --workpath "C:\Users\administrator\Desktop\Vermes\build3" ^
  --paths "C:\Users\administrator\Desktop\Vermes\backend" ^
  --add-data "C:\Users\administrator\Desktop\Vermes\backend\hermes_cli\web_dist;hermes_cli\web_dist" ^
  --add-data "C:\Users\administrator\Desktop\Vermes\backend\locales;locales" ^
  --collect-all hermes_cli ^
  --collect-all agent ^
  --hidden-import hermes_constants ^
  --hidden-import hermes_logging ^
  --hidden-import hermes_state ^
  --hidden-import hermes_cli.web_server ^
  --hidden-import run_agent ^
  --hidden-import webview ^
  --hidden-import webview.window ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols.http.auto ^
  --exclude-module torch ^
  --exclude-module torchvision ^
  --exclude-module torchaudio ^
  --exclude-module triton ^
  --exclude-module transformers ^
  --exclude-module sentencepiece ^
  --exclude-module onnx ^
  --exclude-module onnxruntime ^
  --exclude-module keras ^
  --exclude-module tensorflow ^
  --exclude-module matplotlib ^
  --exclude-module scipy ^
  --exclude-module numba ^
  "C:\Users\administrator\Desktop\Vermes\backend\hermes_cli\gui_app.py"

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] 打包失败！
    pause
    exit /b 1
)

echo [3/4] 打包成功！验证文件...
dir "C:\Users\administrator\Desktop\Vermes\dist3\Vermes\Vermes.exe" | findstr File
dir /s "C:\Users\administrator\Desktop\Vermes\dist3\Vermes\_internal\hermes_cli\*.py" 2>nul | findstr /c:".py"

echo.
echo [4/4] 创建 ZIP 安装包...
tar -a -c -f "C:\Users\administrator\Desktop\Vermes\Vermes-v1.0.0-windows-x64.zip" ^
  -C "C:\Users\administrator\Desktop\Vermes\dist3" Vermes

echo.
echo ========================================
echo  完成！安装包位于：
echo  C:\Users\administrator\Desktop\Vermes\Vermes-v1.0.0-windows-x64.zip
echo ========================================
pause
