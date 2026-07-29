#!/usr/bin/env bash
# prebuild-check.sh — 构建前完整性检查
# 确保前端构建产物已同步到 web_dist，版本号一致，关键文件非空
# 用法: bash scripts/prebuild-check.sh [--fix]
#   --fix  自动同步 frontend/dist → vermes_cli/web_dist（构建前修复）

set -euo pipefail

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

# ── 1. 前端构建产物检查 ──
FRONTEND_DIST="$ROOT_DIR/frontend/dist"
WEB_DIST="$ROOT_DIR/vermes_cli/web_dist"

echo ""
echo "=== 1. 前端构建产物 ==="

# 1a. frontend/dist 必须存在
if [[ ! -d "$FRONTEND_DIST" ]]; then
  err "frontend/dist 不存在，请先 cd frontend && npm run build"
else
  ok "frontend/dist 存在"
fi

# 1b. frontend/dist/index.html 必须存在且非空
if [[ ! -s "$FRONTEND_DIST/index.html" ]]; then
  err "frontend/dist/index.html 不存在或为空"
else
  ok "frontend/dist/index.html 非空 ($(wc -c < "$FRONTEND_DIST/index.html") bytes)"
fi

# 1c. frontend/dist/assets/ 下必须有 .js 和 .css 文件
JS_COUNT=0; CSS_COUNT=0
if [[ -d "$FRONTEND_DIST/assets" ]]; then
  JS_COUNT=$(find "$FRONTEND_DIST/assets" -name "*.js" -type f 2>/dev/null | wc -l | tr -d ' ')
  CSS_COUNT=$(find "$FRONTEND_DIST/assets" -name "*.css" -type f 2>/dev/null | wc -l | tr -d ' ')
fi
if [[ "$JS_COUNT" -eq 0 ]]; then
  err "frontend/dist/assets/ 下没有 .js 文件 — 构建可能失败"
else
  ok "frontend/dist/assets/ 有 ${JS_COUNT} 个 .js 文件"
fi
if [[ "$CSS_COUNT" -eq 0 ]]; then
  err "frontend/dist/assets/ 下没有 .css 文件 — 构建可能失败"
else
  ok "frontend/dist/assets/ 有 ${CSS_COUNT} 个 .css 文件"
fi

# ── 2. web_dist 同步状态检查 ──
echo ""
echo "=== 2. web_dist 同步状态 ==="

NEED_SYNC=false

# 2a. web_dist 必须存在
if [[ ! -d "$WEB_DIST" ]]; then
  err "vermes_cli/web_dist 不存在"
  NEED_SYNC=true
else
  ok "vermes_cli/web_dist 存在"
fi

# 2b. 对比 frontend/dist 和 web_dist 的 JS 文件
if [[ -d "$FRONTEND_DIST/assets" ]] && [[ -d "$WEB_DIST/assets" ]]; then
  SRC_JS=$(ls "$FRONTEND_DIST/assets/"*.js 2>/dev/null | xargs -I{} basename {} | sort || true)
  DST_JS=$(ls "$WEB_DIST/assets/"*.js 2>/dev/null | xargs -I{} basename {} | sort || true)
  if [[ "$SRC_JS" != "$DST_JS" ]]; then
    warn "web_dist/assets/ JS 文件与 frontend/dist/assets/ 不一致"
    echo "   源: $(echo $SRC_JS | tr '\n' ' ')"
    echo "   目标: $(echo $DST_JS | tr '\n' ' ')"
    NEED_SYNC=true
  else
    ok "web_dist/assets/ JS 文件与 frontend/dist 一致"
  fi

  # 2c. 对比文件大小（检测内容是否同步）
  for js in $FRONTEND_DIST/assets/*.js; do
    fname=$(basename "$js")
    dst="$WEB_DIST/assets/$fname"
    if [[ -f "$dst" ]]; then
      src_size=$(wc -c < "$js" | tr -d ' ')
      dst_size=$(wc -c < "$dst" | tr -d ' ')
      if [[ "$src_size" != "$dst_size" ]]; then
        warn "文件大小不一致: $fname (src=$src_size, dst=$dst_size)"
        NEED_SYNC=true
      fi
    fi
  done
else
  NEED_SYNC=true
fi

# 2d. index.html 对比
if [[ -f "$FRONTEND_DIST/index.html" && -f "$WEB_DIST/index.html" ]]; then
  if ! diff -q "$FRONTEND_DIST/index.html" "$WEB_DIST/index.html" >/dev/null 2>&1; then
    warn "web_dist/index.html 与 frontend/dist/index.html 内容不同"
    NEED_SYNC=true
  else
    ok "web_dist/index.html 与 frontend/dist 一致"
  fi
fi

# ── 3. 版本号一致性检查 ──
echo ""
echo "=== 3. 版本号一致性 ==="

INIT_PY="$ROOT_DIR/vermes_cli/__init__.py"
if [[ -f "$INIT_PY" ]]; then
  VERSION=$(grep -o '__version__\s*=\s*"[^"]*"' "$INIT_PY" | grep -o '"[^"]*"' | tr -d '"')
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
if $NEED_SYNC; then
  if $FIX_MODE; then
    echo "=== 自动同步 frontend/dist → vermes_cli/web_dist ==="
    # 删除旧 web_dist
    rm -rf "$WEB_DIST" 2>/dev/null || true
    # 拷贝 frontend/dist → web_dist
    cp -R "$FRONTEND_DIST" "$WEB_DIST"
    ok "已同步 frontend/dist → vermes_cli/web_dist"
    # 重新验证
    JS_AFTER=$(find "$WEB_DIST/assets" -name "*.js" -type f 2>/dev/null | wc -l | tr -d ' ')
    CSS_AFTER=$(find "$WEB_DIST/assets" -name "*.css" -type f 2>/dev/null | wc -l | tr -d ' ')
    ok "同步后: ${JS_AFTER} .js + ${CSS_AFTER} .css 文件"
    NEED_SYNC=false
    # 重置错误计数（因为已修复）
    ERRORS=0
  else
    warn "web_dist 未同步！使用 --fix 自动修复，或手动执行:"
    echo "   rm -rf vermes_cli/web_dist && cp -R frontend/dist vermes_cli/web_dist"
  fi
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
  if $NEED_SYNC; then
    echo "   ⚠️  web_dist 未同步，建议加 --fix 或手动同步"
    exit 1
  fi
  exit 0
else
  echo "✅ 全部通过，可以构建！"
  exit 0
fi
