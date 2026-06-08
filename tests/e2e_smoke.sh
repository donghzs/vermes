#!/usr/bin/env bash
# Vermes E2E Smoke Test — v2.0.9
# 覆盖核心路径: health / chat / agent_run / evolution / cache / claim
# 用法: bash tests/e2e_smoke.sh [BASE_URL]

set -euo pipefail

BASE="${1:-http://127.0.0.1:9119}"
PASS=0
FAIL=0
TIMEOUT=30

green() { printf '\033[32m%s\033[0m\n' "$1"; }
red()   { printf '\033[31m%s\033[0m\n' "$1"; }
dim()   { printf '\033[2m%s\033[0m' "$1"; }

check() {
  local label="$1" url="$2" expected_code="${3:-200}"
  dim "  $label ... "
  local http_code
  http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "000")
  if [ "$http_code" = "$expected_code" ]; then
    green "PASS ($http_code)"
    PASS=$((PASS + 1))
  else
    red "FAIL (got $http_code, expected $expected_code)"
    FAIL=$((FAIL + 1))
  fi
}

check_json() {
  local label="$1" url="$2" field="$3"
  dim "  $label ... "
  local body
  body=$(curl -s --max-time "$TIMEOUT" "$url" 2>/dev/null || echo "{}")
  local val
  val=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo "")
  if [ -n "$val" ] && [ "$val" != "None" ]; then
    green "PASS ($field=$val)"
    PASS=$((PASS + 1))
  else
    red "FAIL ($field 为空或 None)"
    FAIL=$((FAIL + 1))
  fi
}

check_post_json() {
  local label="$1" url="$2" body_data="$3" field="$4"
  dim "  $label ... "
  local body
  body=$(curl -s -X POST "$url" \
    -H "Content-Type: application/json" \
    -d "$body_data" \
    --max-time "$TIMEOUT" 2>/dev/null || echo "{}")
  local val
  val=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo "")
  if [ -n "$val" ] && [ "$val" != "None" ] && [ "$val" != "False" ]; then
    green "PASS ($field=$val)"
    PASS=$((PASS + 1))
  else
    red "FAIL ($field=$val)"
    FAIL=$((FAIL + 1))
  fi
}

echo ""
echo "🔍 Vermes E2E Smoke Test"
echo "   Base: $BASE"
echo ""

# ── 1. 基础健康 ──
echo "📋 1. 基础健康"
check      "GET /health"              "$BASE/health"
check_json "GET /health → version"    "$BASE/health" "version"

# ── 2. 聊天 Agent ──
echo "📋 2. 聊天 Agent"
check_post_json "POST /api/chat/completions" \
  "$BASE/api/chat/completions" \
  '{"messages":[{"role":"user","content":"ok"}],"model":"agnes-2.0-flash","stream":false}' \
  "id"

# ── 3. Agent API ──
echo "📋 3. Agent API (curl/cron)"
check_post_json "POST /api/agent/run" \
  "$BASE/api/agent/run" \
  '{"task":"回复 ok","model":"agnes-2.0-flash"}' \
  "ok"

# ── 4. 进化系统 ──
echo "📋 4. 进化系统"
check_json "GET /api/evolution/status" "$BASE/api/evolution/status" "active"

# ── 5. Agent 缓存 ──
echo "📋 5. Agent 缓存"
check_json "GET /api/cache/metrics"    "$BASE/api/cache/metrics" "hit_rate"

# ── 6. Provider ──
echo "📋 6. Provider & 模型"
check      "GET /api/chat/models"         "$BASE/api/chat/models"
check      "GET /api/providers/templates" "$BASE/api/providers/templates"

# ── 7. 微信 ──
echo "📋 7. 微信登录"
check      "GET /api/wechat/qrurl"    "$BASE/api/wechat/qrurl" "404"  # no WeChat config → 404 (正常)

# ── 8. 配置 & 状态 ──
echo "📋 8. 配置 & 状态"
check      "GET /api/sessions"         "$BASE/api/sessions"
check      "GET /api/storage/usage"    "$BASE/api/storage/usage"
check      "GET /api/status"           "$BASE/api/status"         # 无 auth 的状态端点
check      "GET /health → version"     "$BASE/health" "200"

# ── 9. 前端静态资源 ──
echo "📋 9. 前端"
check      "GET /"                     "$BASE/"

# ── 结果 ──
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
printf "   通过: %d / 失败: %d / 总计: %d\n" "$PASS" "$FAIL" $((PASS + FAIL))
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ "$FAIL" -gt 0 ]; then
  red "❌ E2E Smoke Test FAILED"
  exit 1
else
  green "✅ E2E Smoke Test PASSED"
  exit 0
fi
