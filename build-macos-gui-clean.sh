#!/bin/bash
# build-macos-gui-clean.sh
# 打包前自动清理个人密钥，确保分发包不包含任何敏感信息
set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$PROJECT_DIR/dist"
BUILD_DIR="$PROJECT_DIR/build"

echo "========================================="
echo "  Vermes GUI 安全打包（清理密钥）"
echo "========================================="

# 1. 备份并移除 ~/.vermes/.env（打包时不携带个人密钥）
ENV_FILE="$HOME/.vermes/.env"
ENV_BACKUP="$HOME/.vermes/.env.bak"

if [ -f "$ENV_FILE" ]; then
    echo "[1/5] 发现 ~/.vermes/.env，备份并临时移除..."
    cp "$ENV_FILE" "$ENV_BACKUP"
    echo "  已备份至 ~/.vermes/.env.bak"
    export VERMES_ENV_BACKED_UP=1
    export VERMES_ENV_BACKUP_PATH="$ENV_BACKUP"
else
    echo "[1/5] ~/.vermes/.env 不存在，跳过"
    export VERMES_ENV_BACKED_UP=0
fi

# 2. 确保项目目录没有 .env
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "[2/5] 警告：项目目录存在 .env，删除（不影响原文件）"
    rm -f "$PROJECT_DIR/.env"
fi

# 3. 清理旧构建
echo "[3/5] 清理旧构建..."
rm -rf "$DIST_DIR" "$BUILD_DIR"
# 杀掉可能占用 .app 的进程
pkill -f "Vermes.app" 2>/dev/null || true
pkill -f "uvicorn.*9119" 2>/dev/null || true
sleep 1

# 4. 执行 PyInstaller
echo "[4/5] 开始打包（PyInstaller）..."
cd "$PROJECT_DIR"
.venv/bin/python -m PyInstaller vermes-gui.spec --noconfirm 2>&1 | tail -20

# 5. 恢复 ~/.vermes/.env
if [ "$VERMES_ENV_BACKED_UP" = "1" ] && [ -f "$VERMES_ENV_BACKUP_PATH" ]; then
    echo "[5/5] 恢复 ~/.vermes/.env..."
    cp "$VERMES_ENV_BACKUP_PATH" "$ENV_FILE"
    rm -f "$VERMES_ENV_BACKUP_PATH"
    echo "  已恢复 ~/.vermes/.env"
else
    echo "[5/5] 无需恢复"
fi

# 6. 验证包里没有 .env
echo ""
echo "========================================="
echo "  验证分发包不包含密钥"
echo "========================================="
if find "$DIST_DIR/Vermes.app" -name ".env" 2>/dev/null | grep -q .; then
    echo "❌ 警告：.app 中包含 .env 文件！"
    find "$DIST_DIR/Vermes.app" -name ".env"
    echo "请检查 vermes-gui.spec 的 datas 配置"
else
    echo "✅ 验证通过：.app 中不包含 .env 文件"
fi

# 7. 验证包里没有 sk- 开头的密钥字符串
echo ""
echo "扫描打包产物中是否泄露密钥字符串（sk- / sk-ant-）..."
if grep -r "sk-[A-Za-z0-9]\{10,\}" "$DIST_DIR/Vermes.app/Contents/Resources/" 2>/dev/null | grep -v "\.pyc" | head -5; then
    echo "❌ 警告：发现疑似密钥字符串！"
else
    echo "✅ 验证通过：未发现密钥字符串泄露"
fi

echo ""
echo "========================================="
echo "  打包完成！"
echo "  产物："
echo "  $DIST_DIR/Vermes.app"
echo "  $DIST_DIR/Vermes-1.0.0-macos-arm64.zip"
echo "========================================="
