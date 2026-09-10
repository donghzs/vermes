#!/usr/bin/env bash
# prebuild-check.sh — 构建前完整性检查
# 确保前端构建产物已生成到 web_dist（vite outDir 已直出 ../vermes_cli/web_dist，不再产生 frontend/dist），
# 版本号一致，关键文件非空。
# 用法: bash scripts/prebuild-check.sh [--fix]
#   --fix  若 web_dist 缺失/不完整，自动 cd frontend && npm run build 重新生成（vite 直出 web_dist）

# 注意：不用 set -e。本脚本是"检查"脚本，应在遇到单个错误时继续报告其余检查项，
# 而非首错即退（否则会掩盖后续真实问题，且 set -e + 子命令失败会静默中断整条构建链）。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIX_MODE=false

[[ "${1:-}" == "--fix" ]] && FIX_MODE=true

ERRORS=0
WARNINGS=0

# ── 辅助函数 ──
err() { echo "❌ ERROR: $1" >&2; ERRORS=$((ERRORS + 1)); }
warn() { echo "⚠️  WARN: $1" >&2; WARNINGS=$((WARNINGS + 1)); }
ok() { echo "✅ $1"; }

# vite.config.js 的 build.outDir 已指向 ../vermes_cli/web_dist（09-09 理顺），
# 故 vite build 的产物直接落在 web_dist，frontend/dist 不再生成。
# "前端构建产物"检查的对象就是 web_dist 本身。
WEB_DIST="$ROOT_DIR/vermes_cli/web_dist"

# ── 1. 前端构建产物检查（web_dist 即 vite 直出）──
echo ""
echo "=== 1. 前端构建产物（web_dist，vite 直出）==="

# 1a. web_dist 必须存在
if [[ ! -d "$WEB_DIST" ]]; then
  err "vermes_cli/web_dist 不存在"
else
  ok "vermes_cli/web_dist 存在"
fi

# 1b. web_dist/index.html 必须存在且非空
if [[ ! -s "$WEB_DIST/index.html" ]]; then
  err "vermes_cli/web_dist/index.html 不存在或为空"
else
  ok "web_dist/index.html 非空 ($(wc -c < "$WEB_DIST/index.html") bytes)"
fi

# 1c. web_dist/assets/ 下必须有 .js 和 .css 文件
JS_COUNT=0; CSS_COUNT=0
if [[ -d "$WEB_DIST/assets" ]]; then
  JS_COUNT=$(find "$WEB_DIST/assets" -name "*.js" -type f 2>/dev/null | wc -l | tr -d ' ')
  CSS_COUNT=$(find "$WEB_DIST/assets" -name "*.css" -type f 2>/dev/null | wc -l | tr -d ' ')
fi
if [[ "$JS_COUNT" -eq 0 ]]; then
  err "vermes_cli/web_dist/assets/ 下没有 .js 文件 — 前端可能未构建"
else
  ok "web_dist/assets/ 有 ${JS_COUNT} 个 .js 文件"
fi
if [[ "$CSS_COUNT" -eq 0 ]]; then
  err "vermes_cli/web_dist/assets/ 下没有 .css 文件 — 前端可能未构建"
else
  ok "web_dist/assets/ 有 ${CSS_COUNT} 个 .css 文件"
fi

# ── 2. web_dist 同步状态检查（web_dist 即源，无需与 frontend/dist 比对）──
echo ""
echo "=== 2. web_dist 状态 ==="
if [[ -d "$WEB_DIST" ]]; then
  ok "vermes_cli/web_dist 存在（vite 直出，无需二次同步）"
else
  err "vermes_cli/web_dist 不存在 — 请先 cd frontend && npm run build"
fi

# ── 3. 版本号一致性检查 ──
echo ""
echo "=== 3. 版本号一致性 ==="

INIT_PY="$ROOT_DIR/vermes_cli/__init__.py"
if [[ -f "$INIT_PY" ]]; then
  VERSION=$(grep -oE '__version__[[:space:]]*=[[:space:]]*"[^"]*"' "$INIT_PY" 2>/dev/null | sed 's/.*"\([^"]*\)".*/\1/' || true)
  ok "__init__.py 版本: $VERSION"
else
  err "vermes_cli/__init__.py 不存在"
  VERSION=""
fi

# 检查 electron/version.txt
ELEC_VER=""
if [[ -f "$ROOT_DIR/electron/version.txt" ]]; then
  ELEC_VER=$(cat "$ROOT_DIR/electron/version.txt" | tr -d '[:space:]')
  if [[ "$ELEC_VER" != "$VERSION" ]]; then
    warn "electron/version.txt ($ELEC_VER) 与 __init__.py ($VERSION) 不一致"
  else
    ok "electron/version.txt: $ELEC_VER"
  fi
fi

# 检查 version.txt
ROOT_VER=""
if [[ -f "$ROOT_DIR/version.txt" ]]; then
  ROOT_VER=$(cat "$ROOT_DIR/version.txt" | tr -d '[:space:]')
  if [[ "$ROOT_VER" != "$VERSION" ]]; then
    warn "version.txt ($ROOT_VER) 与 __init__.py ($VERSION) 不一致"
  else
    ok "version.txt: $ROOT_VER"
  fi
fi

# 检查 pyproject.toml
if [[ -f "$ROOT_DIR/pyproject.toml" ]]; then
  PY_VER=$(grep '^version = ' "$ROOT_DIR/pyproject.toml" | head -1 | grep -o '"[^"]*"' | tr -d '"')
  if [[ -n "$PY_VER" && "$PY_VER" != "$VERSION" ]]; then
    warn "pyproject.toml ($PY_VER) 与 __init__.py ($VERSION) 不一致"
  elif [[ -n "$PY_VER" ]]; then
    ok "pyproject.toml: $PY_VER"
  fi
fi

# ── 4. PyInstaller spec 关键数据检查 ──
echo ""
echo "=== 4. 构建配置 ==="

# 检查 vermes-backend.spec 是否包含 web_dist
BACKEND_SPEC="$ROOT_DIR/vermes-backend.spec"
if [[ -f "$BACKEND_SPEC" ]]; then
  if grep -q "web_dist" "$BACKEND_SPEC"; then
    ok "vermes-backend.spec 包含 web_dist 引用"
  else
    err "vermes-backend.spec 缺少 web_dist 引用 — 打包后前端文件会丢失！"
  fi
else
  warn "vermes-backend.spec 不存在（可能用其他 spec 文件）"
fi

# 检查 electron/package.json extraResources 是否包含 web_dist
ELEC_PKG="$ROOT_DIR/electron/package.json"
if [[ -f "$ELEC_PKG" ]]; then
  if grep -q "web_dist" "$ELEC_PKG"; then
    ok "electron/package.json extraResources 包含 web_dist"
  else
    err "electron/package.json extraResources 缺少 web_dist — Electron 包会丢失前端！"
  fi
fi

# ── 5. 自动修复 ──
echo ""
if [[ "$ERRORS" -gt 0 ]] && $FIX_MODE; then
  echo "=== 自动修复：重新构建前端（vite 直出 web_dist）==="
  # 注意：vite outDir 已直出 web_dist，旧的 "rm -rf web_dist && cp frontend/dist" 在
  # 当前配置下会误删刚构建好的产物且 frontend/dist 已不存在 → 必须改用 npm run build 重建。
  (cd "$ROOT_DIR/frontend" && npm run build)
  # 重新验证
  if [[ -s "$WEB_DIST/index.html" ]] && [[ -d "$WEB_DIST/assets" ]]; then
    JS_AFTER=$(find "$WEB_DIST/assets" -name "*.js" -type f 2>/dev/null | wc -l | tr -d ' ')
    CSS_AFTER=$(find "$WEB_DIST/assets" -name "*.css" -type f 2>/dev/null | wc -l | tr -d ' ')
    ok "已重建 web_dist: ${JS_AFTER} .js + ${CSS_AFTER} .css 文件"
    ERRORS=0
  else
    err "npm run build 后仍缺失 web_dist，请手动排查 vite 配置"
  fi
elif [[ "$ERRORS" -gt 0 ]]; then
  warn "存在错误，使用 --fix 自动修复，或手动执行: cd frontend && npm run build"
fi

# ── 结果 ──
echo ""
echo "========================================"
if [[ "$ERRORS" -gt 0 ]]; then
  echo "❌ 检查失败: ${ERRORS} 个错误, ${WARNINGS} 个警告"
  echo "   请修复后再构建！"
  exit 1
elif [[ "$WARNINGS" -gt 0 ]]; then
  echo "⚠️  检查通过（有 ${WARNINGS} 个警告）"
  exit 0
else
  echo "✅ 全部通过，可以构建！"
  exit 0
fi
