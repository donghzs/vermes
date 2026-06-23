#!/bin/bash
# build.sh — 一键构建 Vermes Mac 版
# 确保 frontend/dist → hermes_cli/web_dist 同步后再 PyInstaller 打包
set -e

cd "$(dirname "$0")"

echo "▶ 1/4 构建前端..."
cd frontend && npm run build && cd ..

echo "▶ 2/4 同步 web_dist..."
# 清理旧文件（防止堆积多个版本）
rm -rf hermes_cli/web_dist/assets
cp -R frontend/dist/* hermes_cli/web_dist/

echo "▶ 3/4 PyInstaller 打包..."
python3.11 -m PyInstaller vermes.spec --noconfirm

echo "▶ 4/4 验证..."
APP_JS=$(cat dist/Vermes.app/Contents/Resources/hermes_cli/web_dist/index.html | grep -o 'index-[A-Za-z0-9_-]*\.js')
SRC_JS=$(cat hermes_cli/web_dist/index.html | grep -o 'index-[A-Za-z0-9_-]*\.js')
if [ "$APP_JS" = "$SRC_JS" ]; then
    echo "✅ 构建成功！前端版本一致: $APP_JS"
    ls -lh dist/Vermes.app
else
    echo "❌ 错误：前端版本不一致！ app=$APP_JS src=$SRC_JS"
    exit 1
fi
