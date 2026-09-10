#!/bin/bash
# build.sh — 一键构建 Vermes Mac 版 (Electron 壳 + Python 后端)
# 完整流程：前端构建 → web_dist 同步 → PyInstaller 后端 → Electron 打包 → DMG
set -e

cd "$(dirname "$0")"

echo "▶ 1/5 构建前端..."
cd frontend && npm run build && cd ..

echo "▶ 2/5 同步 web_dist（vite outDir 已直出 web_dist，无需 cp）..."
# 注：vite.config.js 的 build.outDir 指向 ../vermes_cli/web_dist，
# 第 1 步 npm run build 已把产物写入 web_dist 且 emptyOutDir 自动清空旧文件，
# 因此此处不再执行 rm/cp（旧逻辑 rm -rf web_dist/assets + cp frontend/dist/* 在
# 当前 vite 配置下会误删刚 build 好的产物，且 frontend/dist 已不存在）。

echo "▶ 3/5 PyInstaller 打包后端 (vermes-backend.spec)..."
.venv/bin/python -m PyInstaller vermes-backend.spec --noconfirm

echo "▶ 4/5 Electron 打包 (electron-builder --mac)..."
cd electron && npm run prebuild && npx electron-builder --mac && cd ..

echo "▶ 5/5 构建产物自检..."
DMG_FILE=$(ls -t dist-electron/*.dmg 2>/dev/null | head -1)
if [ -z "$DMG_FILE" ]; then
    echo "❌ 错误：未找到 DMG 文件"
    exit 1
fi

bash scripts/verify-build.sh "$DMG_FILE"
if [ $? -ne 0 ]; then
    echo "❌ 构建产物自检失败"
    exit 1
fi

echo ""
echo "DMG: $DMG_FILE ($(du -h "$DMG_FILE" | cut -f1))"

# ── sqlite_vec 运行时断言（T2）──
echo "▶ 6/6 sqlite_vec 运行时检查..."
.venv/bin/python -c "
import sqlite3, sqlite_vec
conn = sqlite3.connect(':memory:')
conn.enable_load_extension(True)
sqlite_vec.load(conn)
# sqlite_vec 无 version 属性，验证 load 不抛异常即正常
conn.close()
print('  sqlite_vec loaded OK')
" || { echo '❌ sqlite_vec 运行时检查失败 — vec0 扩展不可加载'; exit 1; }
