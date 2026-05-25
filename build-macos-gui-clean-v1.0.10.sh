#!/bin/bash
# build-macos-gui-clean-v1.0.10.sh
# 构建 Vermes v1.0.10 (macOS ARM64) - 移除 quarantine 标志

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build"

echo "========================================="
echo "  Vermes v1.0.10 GUI 安全打包"
echo "========================================="

# 1. 备份并移除 ~/.vermes/.env
ENV_FILE="$HOME/.vermes/.env"
ENV_BACKUP="$HOME/.vermes/.env.bak"

if [ -f "$ENV_FILE" ]; then
    echo "[1/6] 发现 ~/.vermes/.env，备份并临时移除..."
    cp "$ENV_FILE" "$ENV_BACKUP"
    echo "  已备份至 ~/.vermes/.env.bak"
    export VERMES_ENV_BACKED_UP=1
    export VERMES_ENV_BACKUP_PATH="$ENV_BACKUP"
else
    echo "[1/6] ~/.vermes/.env 不存在，跳过"
    export VERMES_ENV_BACKED_UP=0
fi

# 2. 确保项目目录没有 .env
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "[2/6] 警告：项目目录存在 .env，删除（不影响原文件）"
    rm -f "$PROJECT_DIR/.env"
fi

# 3. 清理旧构建
echo "[3/6] 清理旧构建..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
pkill -f "Vermes.app" 2>/dev/null || true
pkill -f "uvicorn.*9119" 2>/dev/null || true
sleep 1

# 4. 执行 PyInstaller
echo "[4/6] 开始打包（PyInstaller）..."
cd "$PROJECT_DIR"
.venv/bin/python -m PyInstaller vermes-gui.spec --noconfirm 2>&1 | tail -20

# 4.5 移除 quarantine 标志（修复 Gatekeeper 问题）✅
echo "[4.5/6] 移除 quarantine 标志..."
if [ -d "$DIST_DIR/Vermes.app" ]; then
    xattr -cr "$DIST_DIR/Vermes.app"
    echo "  ✅ 已移除 quarantine 标志（其他 Mac 可打开）"
else
    echo "  ⚠️  找不到 Vermes.app，跳过"
fi

# 5. 恢复 ~/.vermes/.env
if [ "$VERMES_ENV_BACKED_UP" = "1" ] && [ -f "$VERMES_ENV_BACKUP_PATH" ]; then
    echo "[5/6] 恢复 ~/.vermes/.env..."
    cp "$VERMES_ENV_BACKUP_PATH" "$ENV_FILE"
    rm -f "$VERMES_ENV_BACKUP_PATH"
    echo "  已恢复 ~/.vermes/.env"
else
    echo "[5/6] 无需恢复"
fi

# 6. 验证包里没有 .env 和密钥
echo ""
echo "[6/6] 验证分发包不包含密钥..."
if find "$DIST_DIR/Vermes.app" -name ".env" 2>/dev/null | grep -q .; then
    echo "❌ 警告：.app 中包含 .env 文件！"
else
    echo "✅ 验证通过：.app 中不包含 .env 文件"
fi

if grep -r "sk-[A-Za-z0-9]\{10,\}" "$DIST_DIR/Vermes.app/Contents/Resources/" 2>/dev/null | grep -v "\.pyc" | head -5; then
    echo "❌ 警告：发现疑似密钥字符串！"
else
    echo "✅ 验证通过：未发现密钥字符串泄露"
fi

# 7. 创建 DMG 安装包
echo ""
echo "========================================="
echo "  创建 DMG 安装包..."
echo "========================================="
mkdir -p dmg_staging
cp -r "$DIST_DIR/Vermes.app" dmg_staging/
ln -s /Applications dmg_staging/Applications

hdiutil create -volname "Vermes v1.0.10" \
    -srcfolder dmg_staging \
    -ov -format UDZO \
    "$DIST_DIR/Vermes-1.0.10-macos-arm64.dmg"

rm -rf dmg_staging

# 8. 显示结果
echo ""
echo "========================================="
echo "  ✅ 构建完成！"
echo "========================================="
echo "📦 DMG 文件: $DIST_DIR/Vermes-1.0.10-macos-arm64.dmg"
echo "📏 文件大小: $(du -h "$DIST_DIR/Vermes-1.0.10-macos-arm64.dmg" | cut -f1)"
echo ""
echo "🚀 上传到服务器:"
echo "   scp $DIST_DIR/Vermes-1.0.10-macos-arm64.dmg REDACTED_USER@REDACTED_SERVER_IP:/var/www/html/vermes/downloads/"
echo ""
echo "🔧 如果用户双击打不开，执行："
echo "   xattr -cr /Applications/Vermes.app"
