@echo off
chcp 65001 >nul
REM Vermes Build Script for Windows
REM Usage: build-windows.bat [--clean]

echo.
echo  ╔════════════════════════════════════════╗
echo  ║     Vermes Windows Build Script        ║
echo  ╚════════════════════════════════════════╝
echo.

cd /d "%~dp0"

REM Check Python
python --version 2>nul || (
    echo ❌ Python not found. Please install Python 3.11+
    exit /b 1
)

REM Install PyInstaller if needed
python -c "import PyInstaller" 2>nul || (
    echo 📦 Installing PyInstaller...
    pip install pyinstaller
)

REM Clean previous build
if "%1"=="--clean" (
    echo 🧹 Cleaning previous build...
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
)

REM Build — 使用 vermes-gui.spec（入口 gui_app.py，含 pywebview + web_server）
echo 🔨 Building Vermes.exe (GUI mode)...
pyinstaller vermes-gui.spec --noconfirm --clean

REM Check result
if exist "dist\Vermes\Vermes.exe" (
    echo.
    echo ✅ Build successful!
    echo    Location: %cd%\dist\Vermes\
    echo.
    echo    To test: dist\Vermes\Vermes.exe
    echo    To distribute: Create ZIP of dist\Vermes\ folder
    echo.
) else (
    echo ❌ Build failed. Check the output above.
    exit /b 1
)

pause
