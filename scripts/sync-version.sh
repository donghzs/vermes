#!/usr/bin/env bash
# sync-version.sh — 从 vermes_cli/__init__.py 提取版本号，同步到所有需要的位置
# 用法: bash scripts/sync-version.sh
#
# T5（2026-09-24）：旧版依赖 `grep | grep | tr` 提取版本，在 WorkBuddy 等
# shell shim 下 `grep` 假阴性 → pipeline 非零 + `set -euo pipefail` → EXIT=1
# 且无任何输出。改为 Python 提取/写 JSON（不经过 shim），每步失败都有 stderr。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

INIT_PY="$ROOT_DIR/vermes_cli/__init__.py"
if [ ! -f "$INIT_PY" ]; then
  echo "ERROR: $INIT_PY not found" >&2
  exit 1
fi

# Prefer the real interpreter; fall back to python3. Never use a shimmed grep.
PY=""
for candidate in "${MIMO_PYTHON:-}" python3.11 python3.12 python3 /usr/bin/python3; do
  if [ -n "$candidate" ] && [ -x "$candidate" ] || command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "ERROR: no Python >= 3.9 found to extract/sync version" >&2
  exit 1
fi

VERSION="$("$PY" - "$INIT_PY" <<'PY'
import ast, sys
path = sys.argv[1]
try:
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
except Exception as e:
    print(f"ERROR: cannot parse {path}: {e}", file=sys.stderr)
    sys.exit(1)
for node in tree.body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "__version__":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    print(node.value.value)
                    sys.exit(0)
print(f"ERROR: __version__ string not found in {path}", file=sys.stderr)
sys.exit(1)
PY
)" || {
  echo "ERROR: version extraction failed" >&2
  exit 1
}

if [ -z "$VERSION" ]; then
  echo "ERROR: empty __version__ extracted from $INIT_PY" >&2
  exit 1
fi

echo "🔖 Version: $VERSION"

sync_json_version() {
  local pkg="$1"
  local label="$2"
  if [ ! -f "$pkg" ]; then
    echo "ERROR: $pkg not found" >&2
    exit 1
  fi
  if ! "$PY" - "$pkg" "$VERSION" <<'PY'
import json, sys
path, version = sys.argv[1], sys.argv[2]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception as e:
    print(f"ERROR: cannot parse JSON {path}: {e}", file=sys.stderr)
    sys.exit(1)
data["version"] = version
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
  then
    echo "ERROR: failed to sync version into $pkg" >&2
    exit 1
  fi
  echo "  ✅ $label"
}

# 1. electron/version.txt
echo "$VERSION" > "$ROOT_DIR/electron/version.txt"
echo "  ✅ electron/version.txt"

# 2–3b. package.json (electron / frontend / root)
sync_json_version "$ROOT_DIR/electron/package.json" "electron/package.json"
sync_json_version "$ROOT_DIR/frontend/package.json" "frontend/package.json"
sync_json_version "$ROOT_DIR/package.json" "package.json (根目录)"

# 4. version.txt
echo "$VERSION" > "$ROOT_DIR/version.txt"
echo "  ✅ version.txt"

# 5. pyproject.toml
PYPROJECT="$ROOT_DIR/pyproject.toml"
if [ -f "$PYPROJECT" ]; then
  if ! "$PY" - "$PYPROJECT" "$VERSION" <<'PY'
import re, sys
path, version = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
new, n = re.subn(r'(?m)^version\s*=\s*"[^"]*"', f'version = "{version}"', text, count=1)
if n != 1:
    print(f"ERROR: version key not found in {path}", file=sys.stderr)
    sys.exit(1)
open(path, "w", encoding="utf-8").write(new)
PY
  then
    echo "ERROR: failed to sync version into $PYPROJECT" >&2
    exit 1
  fi
  echo "  ✅ pyproject.toml"
fi

echo "Done — all version files synced to $VERSION"
