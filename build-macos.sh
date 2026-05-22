#!/usr/bin/env bash
# Vermes Build Script for macOS
# Usage: ./build-macos.sh [--clean]

set -e

echo "╔════════════════════════════════════════╗"
echo "║     Vermes macOS Build Script         ║"
echo "╚════════════════════════════════════════╝"
echo ""

cd "$(dirname "$0")"

# Use venv Python 3.11 (has all deps including dotenv)
VENV_PY=".venv/bin/python"
VENV_PYINSTALLER=".venv/bin/python -m PyInstaller"

# Check Python version
PYTHON_VERSION=$($VENV_PY -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✅ Using Python $PYTHON_VERSION from venv"

# Install PyInstaller if needed
if ! $VENV_PY -c "import PyInstaller" 2>/dev/null; then
    echo "📦 Installing PyInstaller in venv..."
    .venv/bin/pip3 install pyinstaller
fi

# Clean previous build
if [[ "$1" == "--clean" ]]; then
    echo "🧹 Cleaning previous build..."
    rm -rf build/ dist/
fi

# Create icon if not exists
if [[ ! -f "packaging/vermes.icns" ]]; then
    echo "🎨 Creating app icon..."
    mkdir -p packaging
    # Create a simple icon using sips (macOS built-in)
    # For production, replace with proper .icns file
    if [[ -f "hermes_cli/web_dist/logo-256.png" ]]; then
        sips -s format icns hermes_cli/web_dist/logo-256.png --out packaging/vermes.icns 2>/dev/null || true
    fi
fi

# Build
echo "🔨 Building Vermes.app (windowed)..."
$VENV_PYINSTALLER vermes-gui.spec --noconfirm

# Check result
if [[ -d "dist/Vermes.app" ]]; then
    echo ""
    echo "✅ Build successful!"
    echo "   Location: $(pwd)/dist/Vermes.app"
    echo ""
    echo "   To test: open dist/Vermes.app"
    echo "   To distribute: zip -r Vermes-macos.zip dist/Vermes.app"
else
    echo "❌ Build failed. Check the output above."
    exit 1
fi
