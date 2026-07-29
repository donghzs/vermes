#!/usr/bin/env bash
# sync-version.sh — 从 vermes_cli/__init__.py 提取版本号，同步到所有需要的位置
# 用法: bash scripts/sync-version.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# 从 Python __init__.py 读取版本号
INIT_PY="$ROOT_DIR/vermes_cli/__init__.py"
if [ ! -f "$INIT_PY" ]; then
  echo "ERROR: $INIT_PY not found" >&2
  exit 1
fi

VERSION=$(grep -o '__version__\s*=\s*"[^"]*"' "$INIT_PY" | grep -o '"[^"]*"' | tr -d '"')
if [ -z "$VERSION" ]; then
  echo "ERROR: could not extract __version__ from $INIT_PY" >&2
  exit 1
fi

echo "🔖 Version: $VERSION"

# 1. 写入 electron/version.txt（Electron 打包时作为 extraResources）
echo "$VERSION" > "$ROOT_DIR/electron/version.txt"
echo "  ✅ electron/version.txt"

# 2. 同步 electron/package.json 的 version 字段
ELECTRON_PKG="$ROOT_DIR/electron/package.json"
if command -v jq &> /dev/null; then
  jq --arg v "$VERSION" '.version = $v' "$ELECTRON_PKG" > "${ELECTRON_PKG}.tmp" && mv "${ELECTRON_PKG}.tmp" "$ELECTRON_PKG"
else
  # fallback: sed
  sed -i.bak "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION\"/" "$ELECTRON_PKG" && rm -f "${ELECTRON_PKG}.bak"
fi
echo "  ✅ electron/package.json"

# 3. 同步 frontend/package.json 的 version 字段
FRONTEND_PKG="$ROOT_DIR/frontend/package.json"
if command -v jq &> /dev/null; then
  jq --arg v "$VERSION" '.version = $v' "$FRONTEND_PKG" > "${FRONTEND_PKG}.tmp" && mv "${FRONTEND_PKG}.tmp" "$FRONTEND_PKG"
else
  sed -i.bak "s/\"version\": \"[^\"]*\"/\"version\": \"$VERSION\"/" "$FRONTEND_PKG" && rm -f "${FRONTEND_PKG}.bak"
fi
echo "  ✅ frontend/package.json"

# 4. 同步根目录 version.txt
ROOT_VERSION_TXT="$ROOT_DIR/version.txt"
echo "$VERSION" > "$ROOT_VERSION_TXT"
echo "  ✅ version.txt"

# 5. 同步 pyproject.toml 版本号
PYPROJECT="$ROOT_DIR/pyproject.toml"
if [ -f "$PYPROJECT" ]; then
  sed -i.bak "s/^version = \"[^\"]*\"/version = \"$VERSION\"/" "$PYPROJECT" && rm -f "${PYPROJECT}.bak"
  echo "  ✅ pyproject.toml"
fi

echo "Done — all version files synced to $VERSION"
