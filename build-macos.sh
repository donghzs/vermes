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
    if [[ -f "vermes_cli/web_dist/logo-256.png" ]]; then
        sips -s format icns vermes_cli/web_dist/logo-256.png --out packaging/vermes.icns 2>/dev/null || true
    fi
fi

# ── 前端构建 + 同步到 web_dist（关键：dmg 不含前端改动的根因）──
# vite 默认输出到 frontend/dist，但运行时 web_server 从 vermes_cli/web_dist
# 读取静态文件（web_server.py: WEB_DIST = Path(__file__).parent / "web_dist"）。
# 若此处不显式 build + 同步，dmg 会用旧 web_dist，导致前端改动（如技能市场
# 生态接入）打不进安装包。故每次打 dmg 必须重编前端并同步。
echo "🎨 Building frontend (vite)..."
cd frontend
if [[ ! -d "node_modules" ]]; then
    echo "📦 Installing frontend deps..."
    npm install
fi
npm run build
cd ..

echo "🔄 Syncing frontend/dist → vermes_cli/web_dist..."
rm -rf vermes_cli/web_dist
cp -R frontend/dist vermes_cli/web_dist
echo "✅ web_dist synced (gitHash: $(cat vermes_cli/web_dist/frontend-build.json 2>/dev/null | grep gitHash | head -1))"

# Build
echo "🔨 Building Vermes.app (windowed)..."
$VENV_PYINSTALLER vermes-gui.spec --noconfirm

# Check result
if [[ -d "dist/Vermes.app" ]]; then
    echo ""
    echo "✅ Build successful!"
    echo "   Location: $(pwd)/dist/Vermes.app"
else
    echo "❌ Build failed. Check the output above."
    exit 1
fi

# ── 打包 DMG（用版本号命名，对齐下载页 version.json）──
# 读取版本号：优先 vermes_cli/__init__.py __version__，回退 version.json
VERSION=$(grep -o '__version__ = "[^"]*"' vermes_cli/__init__.py | sed 's/__version__ = "//;s/"//')
if [[ -z "$VERSION" ]]; then
    VERSION=$(grep '"version"' version.json | head -1 | sed 's/.*: *"//;s/".*//')
fi
DMG_NAME="Vermes-${VERSION}-arm64.dmg"
DMG_DIR="dist-electron"
mkdir -p "$DMG_DIR"

echo "💿 Creating DMG: $DMG_NAME (version $VERSION)"
# 卸载可能残留的旧挂载
for v in "Vermes ${VERSION}" "Vermes ${VERSION} 1" "Vermes ${VERSION} 2"; do
    hdiutil detach "/Volumes/$v" -force -quiet 2>/dev/null || true
done
rm -f "$DMG_DIR/$DMG_NAME"
hdiutil create -volname "Vermes ${VERSION}" -srcfolder dist/Vermes.app -ov -format UDZO "$DMG_DIR/$DMG_NAME"
echo "✅ DMG created: $DMG_DIR/$DMG_NAME ($(du -h "$DMG_DIR/$DMG_NAME" | cut -f1))"
echo "   SHA256: $(shasum -a 256 "$DMG_DIR/$DMG_NAME" | cut -d' ' -f1)"
