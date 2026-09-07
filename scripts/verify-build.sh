#!/usr/bin/env bash
# verify-build.sh — 构建产物自检（P0.2）
# 用法: bash scripts/verify-build.sh [dmg_path]
# 检查 DMG 内是否包含关键模块/类，缺失则 exit 1
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

# ── 定位目标（支持直接传 .app，避免 hdiutil 挂载）──
TARGET="${1:-}"
if [ -z "$TARGET" ]; then
  TARGET=$(ls -t dist-electron/Vermes-*.dmg 2>/dev/null | head -1)
fi
if [ -z "$TARGET" ]; then
  echo "❌ ERROR: 未找到 DMG 或 .app 文件"
  exit 1
fi

if [[ "$TARGET" == *.app ]]; then
  APP_PATH="$TARGET"
  MOUNT_POINT=""
  echo "🔍 检查 .app: $APP_PATH"
else
  DMG="$TARGET"
  if [ ! -f "$DMG" ]; then
    echo "❌ ERROR: 未找到 DMG 文件: $DMG"
    exit 1
  fi
  echo "🔍 检查 DMG: $DMG ($(du -h "$DMG" | cut -f1))"
  MOUNT_POINT=$(mktemp -d)
  hdiutil attach "$DMG" -nobrowse -mountpoint "$MOUNT_POINT" 2>/dev/null
  APP_PATH="$MOUNT_POINT/Vermes.app"
  if [ ! -d "$APP_PATH" ]; then
    echo "❌ ERROR: DMG 内未找到 Vermes.app"
    hdiutil detach "$MOUNT_POINT" 2>/dev/null || true
    exit 1
  fi
fi

ERRORS=0
ok()   { echo "✅ $1"; }
fail() { echo "❌ FAIL: $1"; ERRORS=$((ERRORS + 1)); }

# ── 布局检测：Electron（app.asar + backend/_internal）vs PyInstaller（onedir，模块平铺在 Resources）──
if [ -f "$APP_PATH/Contents/Resources/app.asar" ]; then
  INTERNAL="$APP_PATH/Contents/Resources/backend/_internal"
  WEB_DIST="$APP_PATH/Contents/Resources/app/vermes_cli/web_dist"
else
  # PyInstaller onedir：运行时模块直接平铺在 Contents/Resources，无 backend/_internal、无 app.asar
  INTERNAL="$APP_PATH/Contents/Resources"
  WEB_DIST="$APP_PATH/Contents/Resources/vermes_cli/web_dist"
fi

# ── 1. 前端产物 ──
echo ""
echo "=== 1. 前端产物 ==="
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
  # grep 关键类（用 -q 直接判存在，避免 pipefail + grep -q 的脆弱管道）
  if grep -rq "RecoverableFeedback" "$INTERNAL/harness" 2>/dev/null; then
    ok "RecoverableFeedback 类存在"
  else
    fail "RecoverableFeedback 类未找到"
  fi
  if grep -rq "StabilityReport" "$INTERNAL/harness" 2>/dev/null; then
    ok "StabilityReport 类存在"
  else
    fail "StabilityReport 类未找到"
  fi
  if grep -rq "ConstraintReport" "$INTERNAL/harness" 2>/dev/null; then
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
SF_DIR="$INTERNAL/vermes_cli/scholarforge"
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

# ── 4.5 a2a 食谱 + botmode（⑭ 请神收尾必需，曾因 gui spec 漏 datas 导致 41 recipe 全丢）──
echo ""
echo "=== 4.5 a2a 食谱 + botmode ==="
A2A_DIR="$INTERNAL/vermes_cli/a2a"
if [ -d "$A2A_DIR" ]; then
  ok "a2a/ 目录存在"
  RECIPE_COUNT=$(find "$A2A_DIR/recipes" -name "*.yaml" 2>/dev/null | wc -l | tr -d ' ')
  if [ "$RECIPE_COUNT" -ge 40 ]; then
    ok "recipes/ 有 $RECIPE_COUNT 个 yaml（≥40）"
  else
    fail "recipes/ yaml 不足 ($RECIPE_COUNT, 期望 ≥40 — 封神榜登堂会 404)"
  fi
else
  fail "a2a/ 目录未找到（spec datas 缺 ('vermes_cli/a2a', ...)）"
fi
if [ -d "$INTERNAL/vermes_cli/botmode" ]; then
  ok "botmode/ 目录存在"
else
  fail "botmode/ 目录未找到（spec datas 缺 ('vermes_cli/botmode', ...)）"
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
  # PyInstaller 构建（本仓库路径）无 app.asar —— splash 由 web_dist 承载，跳过
  ok "PyInstaller 构建无 app.asar，跳过 Electron 壳检查"
fi

# ── 6. 版本号一致性 ──
echo ""
echo "=== 6. 版本号 ==="
INIT_VER=$(grep '__version__' vermes_cli/__init__.py | grep -o '"[^"]*"' | tr -d '"')
if [[ "$TARGET" == *.app ]]; then
  # .app 直传：从包内 vermes_cli/__init__.py 取版本（最权威）
  DMG_VER=$(grep '__version__' "$APP_PATH/Contents/Resources/vermes_cli/__init__.py" 2>/dev/null | grep -o '"[^"]*"' | tr -d '"')
else
  DMG_VER=$(echo "$TARGET" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
fi
if [ -n "$DMG_VER" ] && [ "$INIT_VER" = "$DMG_VER" ]; then
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
