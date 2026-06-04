#!/usr/bin/env bash
# publish-release.sh — 一键发布 Vermes 新版本
#
# 功能：上传安装包 + 自动生成 version.json + electron-updater yml + 部署到 vbit.top
#
# 用法:
#   bash scripts/publish-release.sh <version> <mac-dmg> <win-exe> [--changelog "条目1\n条目2"]
#
# 示例:
#   bash scripts/publish-release.sh 2.0.8 ./dist/Vermes-2.0.8-arm64.dmg ./dist/Vermes-Setup-2.0.8.exe
#   bash scripts/publish-release.sh 2.0.8 ./dist/Vermes-2.0.8-arm64.dmg ./dist/Vermes-Setup-2.0.8.exe --changelog "修复XX\n新增YY"
#
# 或者分平台发布:
#   bash scripts/publish-release.sh 2.0.8 ./dist/Vermes-2.0.8-arm64.dmg ""        # 只发 Mac
#   bash scripts/publish-release.sh 2.0.8 "" ./dist/Vermes-Setup-2.0.8.exe        # 只发 Windows
#
set -euo pipefail

# ── 配置 ──
SERVER="REDACTED_USER@REDACTED_SERVER_IP"
REMOTE_BASE="/var/www/html/vermes"
DOWNLOADS_DIR="$REMOTE_BASE/downloads"
UPDATES_DIR="$REMOTE_BASE/updates"
SCP_OPTS="-o ConnectTimeout=10"

# ── 参数解析 ──
if [ $# -lt 3 ]; then
  echo "用法: $0 <version> <mac-dmg-path|\"\"> <win-exe-path|\"\"> [--changelog \"条目1\\n条目2\"]"
  echo "  version     : 版本号，如 2.0.8 (不带 v 前缀)"
  echo "  mac-dmg-path: macOS DMG 文件本地路径，空字符串跳过"
  echo "  win-exe-path: Windows EXE 安装包本地路径，空字符串跳过"
  exit 1
fi

VERSION="$1"
MAC_DMG="$2"
WIN_EXE="$3"
CHANGELOG=""

# 解析可选参数
shift 3
while [ $# -gt 0 ]; do
  case "$1" in
    --changelog) shift; CHANGELOG="$1" ;;
  esac
  shift
done

# 去掉版本号的 v 前缀（防止手滑）
VERSION="${VERSION#v}"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Vermes 发布 v${VERSION}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── 验证文件存在 ──
if [ -n "$MAC_DMG" ] && [ ! -f "$MAC_DMG" ]; then
  echo "❌ Mac DMG 不存在: $MAC_DMG"
  exit 1
fi
if [ -n "$WIN_EXE" ] && [ ! -f "$WIN_EXE" ]; then
  echo "❌ Windows EXE 不存在: $WIN_EXE"
  exit 1
fi

# ── 检测 SSH 连接 ──
echo "📡 检查服务器连接..."
if ! ssh $SCP_OPTS $SERVER "echo ok" > /dev/null 2>&1; then
  echo "❌ 无法连接服务器 $SERVER"
  exit 1
fi
echo "  ✅ 服务器连接正常"

# ── 上传文件 ──
MAC_DMG_REMOTE=""
WIN_EXE_REMOTE=""

if [ -n "$MAC_DMG" ]; then
  MAC_DMG_FILENAME=$(basename "$MAC_DMG")
  echo "⬆️  上传 Mac DMG: $MAC_DMG_FILENAME ($(du -h "$MAC_DMG" | cut -f1))"
  scp $SCP_OPTS "$MAC_DMG" "$SERVER:$DOWNLOADS_DIR/$MAC_DMG_FILENAME"
  MAC_DMG_REMOTE="/vermes/downloads/$MAC_DMG_FILENAME"
  echo "  ✅ 已上传"
fi

if [ -n "$WIN_EXE" ]; then
  WIN_EXE_FILENAME=$(basename "$WIN_EXE")
  echo "⬆️  上传 Windows EXE: $WIN_EXE_FILENAME ($(du -h "$WIN_EXE" | cut -f1))"
  scp $SCP_OPTS "$WIN_EXE" "$SERVER:$DOWNLOADS_DIR/$WIN_EXE_FILENAME"
  # Windows 文件名可能有空格，URL encode
  WIN_EXE_REMOTE="/vermes/downloads/$(python3 -c "import urllib.parse; print(urllib.parse.quote('$WIN_EXE_FILENAME'))")"
  echo "  ✅ 已上传"
fi

# ── 计算校验和 ──
echo "🔍 计算校验和..."

MAC_SHA512_B64=""
MAC_FILE_SIZE=0
if [ -n "$MAC_DMG" ]; then
  MAC_SHA512_B64=$(shasum -a 512 "$MAC_DMG" | awk '{print $1}' | xxd -r -p | base64)
  MAC_FILE_SIZE=$(stat -f%z "$MAC_DMG" 2>/dev/null || stat --printf="%s" "$MAC_DMG")
  echo "  Mac DMG: sha512=${MAC_SHA512_B64:0:20}... size=${MAC_FILE_SIZE}"
fi

WIN_SHA512_B64=""
WIN_FILE_SIZE=0
if [ -n "$WIN_EXE" ]; then
  WIN_SHA512_B64=$(shasum -a 512 "$WIN_EXE" | awk '{print $1}' | xxd -r -p | base64)
  WIN_FILE_SIZE=$(stat -f%z "$WIN_EXE" 2>/dev/null || stat --printf="%s" "$WIN_EXE")
  echo "  Win EXE: sha512=${WIN_SHA512_B64:0:20}... size=${WIN_FILE_SIZE}"
fi

# ── 生成 version.json ──
echo "📝 生成 version.json..."

# 读取 CHANGELOG.md 中当前版本的内容作为默认 changelog
if [ -z "$CHANGELOG" ]; then
  # 从 CHANGELOG.md 提取当前版本的条目
  CHANGELOG_FILE="$(dirname "$0")/../CHANGELOG.md"
  if [ -f "$CHANGELOG_FILE" ]; then
    # 提取 [v2.0.8] 或 [2.0.8] 到下一个版本标题之间的内容
    CHANGELOG=$(awk "/^## \\[${VERSION}\\]|^## \\[v${VERSION}\\]/{found=1; next} /^## \\[/{found=0} found && /^- /{gsub(/^[- ]+/, \"\"); print}" "$CHANGELOG_FILE" | head -10 | python3 -c "
import sys, json
lines = [l.strip() for l in sys.stdin if l.strip()]
print(json.dumps(lines, ensure_ascii=False))
")
  fi
  if [ -z "$CHANGELOG" ] || [ "$CHANGELOG" = "[]" ]; then
    CHANGELOG='["版本更新"]'
  fi
fi

# 如果 CHANGELOG 不是 JSON 数组格式，转换它
if ! echo "$CHANGELOG" | python3 -c "import sys,json; json.loads(sys.stdin.read())" 2>/dev/null; then
  CHANGELOG=$(echo "$CHANGELOG" | python3 -c "
import sys, json
lines = [l.strip() for l in sys.stdin.read().split('\\n') if l.strip()]
print(json.dumps(lines, ensure_ascii=False))
")
fi

MAC_SIZE_HUMAN=""
if [ -n "$MAC_DMG" ] && [ "$MAC_FILE_SIZE" -gt 0 ]; then
  MAC_SIZE_HUMAN="$(python3 -c "print(f'{$MAC_FILE_SIZE / 1024 / 1024:.0f} MB')")"
fi

WIN_SIZE_HUMAN=""
if [ -n "$WIN_EXE" ] && [ "$WIN_FILE_SIZE" -gt 0 ]; then
  WIN_SIZE_HUMAN="$(python3 -c "print(f'{$WIN_FILE_SIZE / 1024 / 1024:.0f} MB')")"
fi

BUILD_DATE=$(date +%Y-%m-%d)

# 生成 version.json
python3 -c "
import json, sys

data = {
    'version': '${VERSION}',
    'buildDate': '${BUILD_DATE}',
    'download_url': {},
    'sha256': {},
    'macOS': {},
    'windows': {},
    'changelog': json.loads('${CHANGELOG}')
}

if '${MAC_DMG_REMOTE}':
    data['download_url']['macos_dmg'] = 'https://vbit.top${MAC_DMG_REMOTE}'
    data['macOS'] = {
        'dmg': '${MAC_DMG_REMOTE}',
        'size': '${MAC_SIZE_HUMAN}',
        'arch': 'arm64 (Apple Silicon)'
    }

if '${WIN_EXE_REMOTE}':
    data['download_url']['windows_exe'] = 'https://vbit.top${WIN_EXE_REMOTE}'
    data['windows'] = {
        'exe': '${WIN_EXE_REMOTE}',
        'size': '${WIN_SIZE_HUMAN}'
    }

print(json.dumps(data, indent=2, ensure_ascii=False))
" > /tmp/vermes-version.json

cat /tmp/vermes-version.json
echo "  ✅ version.json 已生成"

# ── 上传 version.json ──
echo "⬆️  上传 version.json"
scp $SCP_OPTS /tmp/vermes-version.json "$SERVER:$REMOTE_BASE/version.json"
echo "  ✅ 已上传"

# ── 生成 electron-updater yml ──
echo "📝 生成 electron-updater yml..."

# macOS arm64
if [ -n "$MAC_DMG" ]; then
  MAC_DMG_YML_NAME=$(basename "$MAC_DMG")
  cat > /tmp/vermes-latest-mac.yml << YMLEOF
version: ${VERSION}
files:
  - url: ${MAC_DMG_YML_NAME}
    sha512: ${MAC_SHA512_B64}
    size: ${MAC_FILE_SIZE}
path: ${MAC_DMG_YML_NAME}
sha512: ${MAC_SHA512_B64}
releaseDate: "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
YMLEOF

  # 上传 DMG 到 updates 目录 + yml
  echo "⬆️  上传 Mac 更新文件"
  ssh $SCP_OPTS $SERVER "mkdir -p $UPDATES_DIR/mac/arm64"
  scp $SCP_OPTS "$MAC_DMG" "$SERVER:$UPDATES_DIR/mac/arm64/$MAC_DMG_YML_NAME"
  scp $SCP_OPTS /tmp/vermes-latest-mac.yml "$SERVER:$UPDATES_DIR/mac/arm64/latest-mac.yml"
  scp $SCP_OPTS /tmp/vermes-latest-mac.yml "$SERVER:$UPDATES_DIR/mac/arm64/latest.yml"
  echo "  ✅ Mac arm64 更新文件已部署"
fi

# Windows x64
if [ -n "$WIN_EXE" ]; then
  WIN_EXE_YML_NAME=$(basename "$WIN_EXE" | sed 's/ /-/g')
  cat > /tmp/vermes-latest.yml << YMLEOF
version: ${VERSION}
files:
  - url: ${WIN_EXE_YML_NAME}
    sha512: ${WIN_SHA512_B64}
    size: ${WIN_FILE_SIZE}
path: ${WIN_EXE_YML_NAME}
sha512: ${WIN_SHA512_B64}
releaseDate: "$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"
YMLEOF

  echo "⬆️  上传 Windows 更新文件"
  ssh $SCP_OPTS $SERVER "mkdir -p $UPDATES_DIR/win/x64"
  scp $SCP_OPTS "$WIN_EXE" "$SERVER:$UPDATES_DIR/win/x64/$WIN_EXE_YML_NAME"
  scp $SCP_OPTS /tmp/vermes-latest.yml "$SERVER:$UPDATES_DIR/win/x64/latest.yml"
  echo "  ✅ Windows x64 更新文件已部署"
fi

# ── 验证 ──
echo ""
echo "🔎 验证部署..."

ERRORS=0

# 验证 version.json
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://vbit.top/vermes/version.json")
if [ "$HTTP_CODE" = "200" ]; then
  echo "  ✅ version.json 可访问 (HTTP 200)"
else
  echo "  ❌ version.json 不可访问 (HTTP $HTTP_CODE)"
  ERRORS=$((ERRORS + 1))
fi

# 验证 version.json 版本号
REMOTE_VER=$(curl -s "https://vbit.top/vermes/version.json" | python3 -c "import sys,json; print(json.load(sys.stdin)['version'])" 2>/dev/null)
if [ "$REMOTE_VER" = "$VERSION" ]; then
  echo "  ✅ version.json 版本号正确: $VERSION"
else
  echo "  ❌ version.json 版本号不匹配: 远程=$REMOTE_VER 本地=$VERSION"
  ERRORS=$((ERRORS + 1))
fi

# 验证 Mac 更新 yml
if [ -n "$MAC_DMG" ]; then
  YML_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://vbit.top/vermes/updates/mac/arm64/latest-mac.yml")
  if [ "$YML_CODE" = "200" ]; then
    echo "  ✅ latest-mac.yml 可访问 (HTTP 200)"
  else
    echo "  ❌ latest-mac.yml 不可访问 (HTTP $YML_CODE)"
    ERRORS=$((ERRORS + 1))
  fi

  # 验证 DMG 下载
  DMG_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://vbit.top/vermes/updates/mac/arm64/$MAC_DMG_YML_NAME")
  if [ "$DMG_CODE" = "200" ]; then
    echo "  ✅ Mac DMG 可下载 (HTTP 200)"
  else
    echo "  ❌ Mac DMG 不可下载 (HTTP $DMG_CODE)"
    ERRORS=$((ERRORS + 1))
  fi
fi

# 验证 Windows 更新 yml
if [ -n "$WIN_EXE" ]; then
  YML_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://vbit.top/vermes/updates/win/x64/latest.yml")
  if [ "$YML_CODE" = "200" ]; then
    echo "  ✅ Windows latest.yml 可访问 (HTTP 200)"
  else
    echo "  ❌ Windows latest.yml 不可访问 (HTTP $YML_CODE)"
    ERRORS=$((ERRORS + 1))
  fi
fi

echo ""
if [ $ERRORS -eq 0 ]; then
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ✅ v${VERSION} 发布成功！"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  产品页: https://vbit.top/vermes/"
  echo "  下载:   https://vbit.top/vermes/#downloads"
else
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ⚠️  v${VERSION} 发布完成，但有 ${ERRORS} 个验证失败"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
