#!/bin/bash
# build.sh — 一键构建 Vermes Mac 版 (Electron 壳 + Python 后端)
# 完整流程：前端构建 → web_dist 同步 → PyInstaller 后端 → Electron 打包 → DMG
set -e

cd "$(dirname "$0")"

echo "▶ 1/5 构建前端..."
cd frontend && npm run build && cd ..

echo "▶ 2/5 同步 web_dist..."
# 清理旧文件（防止堆积多个版本）
rm -rf hermes_cli/web_dist/assets
cp -R frontend/dist/* hermes_cli/web_dist/

echo "▶ 3/5 PyInstaller 打包后端 (vermes-backend.spec)..."
.venv/bin/python -m PyInstaller vermes-backend.spec --noconfirm

echo "▶ 4/5 Electron 打包 (electron-builder --mac)..."
cd electron && npm run prebuild && npx electron-builder --mac && cd ..

echo "▶ 5/5 验证..."
# 验证 Electron DMG 中的 splash.html 存在
DMG_FILE=$(ls -t dist-electron/*.dmg 2>/dev/null | head -1)
if [ -z "$DMG_FILE" ]; then
    echo "❌ 错误：未找到 DMG 文件"
    exit 1
fi

# 挂载验证
MOUNT_POINT=$(mktemp -d)
hdiutil attach "$DMG_FILE" -nobrowse -mountpoint "$MOUNT_POINT" 2>/dev/null

# splash.html 在 app.asar 内，用 npx asar 检查
HAS_SPLASH=$(npx asar list "$MOUNT_POINT/Vermes.app/Contents/Resources/app.asar" 2>/dev/null | grep -q '^/splash.html$' && echo "yes" || echo "no")
# web_dist 在 extraResources/app/hermes_cli/web_dist/
APP_JS=$(cat "$MOUNT_POINT/Vermes.app/Contents/Resources/app/hermes_cli/web_dist/index.html" 2>/dev/null | grep -o 'index-[A-Za-z0-9_-]*\.js' || echo "")
SRC_JS=$(cat hermes_cli/web_dist/index.html | grep -o 'index-[A-Za-z0-9_-]*\.js')

hdiutil detach "$MOUNT_POINT" 2>/dev/null

if [ "$HAS_SPLASH" = "yes" ] && [ "$APP_JS" = "$SRC_JS" ]; then
    echo "✅ 构建成功！"
    echo "   DMG: $DMG_FILE ($(du -h "$DMG_FILE" | cut -f1))"
    echo "   前端: $APP_JS"
    echo "   Splash: ✅"
else
    echo "❌ 验证失败：splash=$HAS_SPLASH, app_js=$APP_JS, src_js=$SRC_JS"
    exit 1
fi
