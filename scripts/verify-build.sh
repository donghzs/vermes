#!/usr/bin/env bash
# verify-build.sh — 构建产物自检（P0.2）
# 用法: bash scripts/verify-build.sh [dmg_path]
# 检查 DMG 内是否包含关键模块/类，缺失则 exit 1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 定位 DMG ──
DMG="${1:-}"
if [ -z "$DMG" ]; then
  DMG=$(ls -t dist-electron/Vermes-*.dmg 2>/dev/null | head -1)
fi
if [ -z "$DMG" ] || [ ! -f "$DMG" ]; then
  echo "❌ ERROR: 未找到 DMG 文件"
  exit 1
fi
echo "🔍 检查 DMG: $DMG ($(du -h "$DMG" | cut -f1))"

# ── 挂载 DMG ──
MOUNT_POINT=$(mktemp -d)
hdiutil attach "$DMG" -nobrowse -mountpoint "$MOUNT_POINT" 2>/dev/null
APP_PATH="$MOUNT_POINT/Vermes.app"

if [ ! -d "$APP_PATH" ]; then
  echo "❌ ERROR: DMG 内未找到 Vermes.app"
  hdiutil detach "$MOUNT_POINT" 2>/dev/null || true
  exit 1
fi

ERRORS=0
ok()   { echo "✅ $1"; }
fail() { echo "❌ FAIL: $1"; ERRORS=$((ERRORS + 1)); }

# backend _internal 目录
INTERNAL="$APP_PATH/Contents/Resources/backend/_internal"

# ── 1. 前端产物 ──
echo ""
echo "=== 1. 前端产物 ==="
WEB_DIST="$APP_PATH/Contents/Resources/app/hermes_cli/web_dist"
if [ -f "$WEB_DIST/index.html" ] && [ -s "$WEB_DIST/index.html" ]; then
  ok "web_dist/index.html 存在且非空"
else
  fail "web_dist/index.html 缺失或为空"
fi
JS_COUNT=$(find "$WEB_DIST/assets" -name "*.js" 2>/dev/null | wc -l | tr -d ' ')
if [ "$JS_COUNT" -ge 2 ]; then
  ok "web_dist/assets/ 有 $JS_COUNT 个 JS 文件"
else
  fail "web_dist/assets/ JS 文件不足 ($JS_COUNT)"
fi

# ── 2. Harness 模块 ──
echo ""
echo "=== 2. Harness 模块 ==="
if [ -d "$INTERNAL/harness" ]; then
  ok "harness/ 目录存在"
  HARNESS_FILES=$(find "$INTERNAL/harness" -name "*.py" -o -name "*.pyc" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$HARNESS_FILES" -ge 3 ]; then
    ok "harness/ 有 $HARNESS_FILES 个文件"
  else
    fail "harness/ 文件不足 ($HARNESS_FILES, 期望 ≥3)"
  fi
  # grep 关键类
  if grep -rl "RecoverableFeedback" "$INTERNAL/harness" 2>/dev/null | grep -q .; then
    ok "RecoverableFeedback 类存在"
  else
    fail "RecoverableFeedback 类未找到"
  fi
  if grep -rl "StabilityReport" "$INTERNAL/harness" 2>/dev/null | grep -q .; then
    ok "StabilityReport 类存在"
  else
    fail "StabilityReport 类未找到"
  fi
  if grep -rl "ConstraintReport" "$INTERNAL/harness" 2>/dev/null | grep -q .; then
    ok "ConstraintReport 类存在"
  else
    fail "ConstraintReport 类未找到"
  fi
else
  fail "harness/ 目录未找到（spec datas 缺失？）"
fi

# ── 3. ScholarForge 工具 ──
echo ""
echo "=== 3. ScholarForge ==="
SF_DIR="$INTERNAL/hermes_cli/scholarforge"
if [ -d "$SF_DIR" ]; then
  ok "scholarforge/ 目录存在"
  SF_FILES=$(find "$SF_DIR" -name "*.py" -o -name "*.pyc" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$SF_FILES" -ge 10 ]; then
    ok "scholarforge/ 有 $SF_FILES 个文件"
  else
    fail "scholarforge/ 文件不足 ($SF_FILES, 期望 ≥10)"
  fi
else
  fail "scholarforge/ 目录未找到"
fi

# ── 4. Gateway Mixins ──
echo ""
echo "=== 4. Gateway Mixins ==="
GW_DIR="$INTERNAL/gateway"
if [ -d "$GW_DIR" ]; then
  ok "gateway/ 目录存在"
  MIXIN_COUNT=$(find "$GW_DIR" -name "*mixin*" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$MIXIN_COUNT" -ge 8 ]; then
    ok "gateway/ 有 $MIXIN_COUNT 个 mixin 文件"
  else
    fail "gateway/ mixin 文件不足 ($MIXIN_COUNT, 期望 ≥8)"
  fi
  if [ -d "$GW_DIR/slash_handlers" ]; then
    ok "slash_handlers/ 子包存在"
    SLASH_FILES=$(find "$GW_DIR/slash_handlers" -name "*.py" -o -name "*.pyc" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$SLASH_FILES" -ge 5 ]; then
      ok "slash_handlers/ 有 $SLASH_FILES 个文件"
    else
      fail "slash_handlers/ 文件不足 ($SLASH_FILES, 期望 ≥5)"
    fi
  else
    fail "slash_handlers/ 子包未找到"
  fi
else
  fail "gateway/ 目录未找到"
fi

# ── 5. splash.html (Electron) ──
echo ""
echo "=== 5. Electron 壳 ==="
ASAR="$APP_PATH/Contents/Resources/app.asar"
if [ -f "$ASAR" ]; then
  if command -v npx &> /dev/null; then
    if npx asar list "$ASAR" 2>/dev/null | grep -q '^/splash.html$'; then
      ok "splash.html 在 app.asar 中"
    else
      fail "splash.html 不在 app.asar 中"
    fi
  else
    ok "npx 不可用，跳过 asar 检查"
  fi
else
  fail "app.asar 不存在"
fi

# ── 6. 版本号一致性 ──
echo ""
echo "=== 6. 版本号 ==="
INIT_VER=$(grep '__version__' hermes_cli/__init__.py | grep -o '"[^"]*"' | tr -d '"')
DMG_VER=$(echo "$DMG" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
if [ "$INIT_VER" = "$DMG_VER" ]; then
  ok "版本号一致: $INIT_VER"
else
  fail "版本号不一致: __init__.py=$INIT_VER, DMG=$DMG_VER"
fi

# ── 清理 ──
hdiutil detach "$MOUNT_POINT" 2>/dev/null || true

echo ""
if [ "$ERRORS" -eq 0 ]; then
  echo "🎉 全部检查通过！"
  exit 0
else
  echo "❌ $ERRORS 项检查失败"
  exit 1
fi
