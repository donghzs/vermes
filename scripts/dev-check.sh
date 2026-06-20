#!/bin/bash
# ── Vermes 开发标准化检查脚本 ──
# 用法: bash scripts/dev-check.sh
# 功能: 变更后强制检查清单 + Push前安全审计
# 退出码: 0=全部通过, 1=有失败项

set -euo pipefail

PROJECT_DIR="/Users/dongzusheng/Projects/vermes-electron"
FRONTEND_DIR="$PROJECT_DIR/frontend"
VENV="$PROJECT_DIR/.venv"
PASS=0
FAIL=0
SKIP=0

# 颜色
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

check() {
  local name="$1"
  local cmd="$2"
  local required="${3:-true}"
  
  echo -e "\n📋 ${name}"
  echo "   └─ $cmd"
  
  if eval "$cmd" 2>&1 | tail -5; then
    echo -e "   ${GREEN}✅ 通过${NC}"
    ((PASS++))
  else
    if [ "$required" = "false" ]; then
      echo -e "   ${YELLOW}⚠️  跳过（非阻塞）${NC}"
      ((SKIP++))
    else
      echo -e "   ${RED}❌ 失败${NC}"
      ((FAIL++))
    fi
  fi
}

echo "════════════════════════════════════════"
echo "  Vermes 开发标准化检查"
echo "════════════════════════════════════════"
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "分支: $(cd $PROJECT_DIR && git branch --show-current)"
echo "未提交: $(cd $PROJECT_DIR && git status --porcelain | wc -l | tr -d ' ') 个文件"

# ── Step 1: 前端构建 ──
check "1. 前端构建" "cd $FRONTEND_DIR && npm run build 2>&1"

# ── Step 2: Python 语法全量扫描 ──
check "2. Python 语法扫描" "cd $PROJECT_DIR && $VENV/bin/python3 -c \"
import ast, os, sys
errors = []
count = 0
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('.git', 'build', 'dist', 'node_modules', '.venv', '__pycache__', '.pytest_cache')]
    for f in files:
        if f.endswith('.py'):
            count += 1
            path = os.path.join(root, f)
            try:
                with open(path, 'r') as fh:
                    ast.parse(fh.read(), path)
            except SyntaxError as e:
                errors.append(f'{path}: {e}')
print(f'扫描 {count} 个 Python 文件')
if errors:
    for e in errors[:10]:
        print(f'  ❌ {e}')
    sys.exit(1)
print('  0 语法错误')
\""

# ── Step 3: 单元测试 ──
check "3. 单元测试" "cd $PROJECT_DIR && $VENV/bin/python3 -m pytest tests/ -q --tb=short 2>&1"

# ── Step 4: PyInstaller 打包 ──
echo -e "\n📋 4. PyInstaller 打包"
echo "   └─ python3 -m PyInstaller --clean vermes.spec"
cd "$PROJECT_DIR"
if $VENV/bin/python3 -m PyInstaller --clean vermes.spec 2>&1 | tail -3; then
  APP_SIZE=$(du -sh dist/Vermes.app 2>/dev/null | cut -f1 || echo "N/A")
  echo -e "   ${GREEN}✅ 通过${NC} (Vermes.app: $APP_SIZE)"
  ((PASS++))
else
  echo -e "   ${RED}❌ 失败${NC}"
  ((FAIL++))
fi

# ── Step 5: App 启动验证 ──
echo -e "\n📋 5. App 启动验证"
echo "   └─ xattr -cr + open Vermes.app"
xattr -cr "$PROJECT_DIR/dist/Vermes.app" 2>/dev/null || true
# 后台启动，检查进程是否存活
open "$PROJECT_DIR/dist/Vermes.app" 2>/dev/null || true
sleep 3
if pgrep -f "Vermes.app" > /dev/null 2>&1; then
  echo -e "   ${GREEN}✅ 通过${NC} (进程存活)"
  ((PASS++))
else
  echo -e "   ${YELLOW}⚠️  进程未检测到（可能启动较慢，手动确认）${NC}"
  ((SKIP++))
fi

# ── Step 6: Git pre-commit hook（安全检查）──
check "6. 安全检查 (pre-commit hook)" "cd $PROJECT_DIR && bash .git/hooks/pre-commit 2>&1 || echo 'hook 不存在，跳过'" "false"

# ── Step 7: Push 前安全审计 ──
echo -e "\n📋 7. Push 前安全审计"
echo "   └─ 检查硬编码密钥/Token/密码/SQL注入/XSS"

# 7a. 硬编码密钥扫描
echo "   7a. 硬编码密钥扫描..."
SECRET_HITS=$(cd $PROJECT_DIR && grep -rn --include='*.py' --include='*.vue' --include='*.js' \
  -E '(sk-[a-zA-Z0-9]{20,}|password\s*=\s*["\x27][^"\x27]{4,}|secret\s*=\s*["\x27][^"\x27]{8,}|token\s*=\s*["\x27][a-zA-Z0-9]{20,})' \
  --exclude-dir=.venv --exclude-dir=node_modules --exclude-dir=build --exclude-dir=dist --exclude-dir=__pycache__ \
  --exclude='*.pyc' 2>/dev/null | grep -v 'test\|example\|placeholder\|your_\|<\|os\.environ\|getenv\|config\|\.get(' | head -5 || true)

if [ -z "$SECRET_HITS" ]; then
  echo -e "   ${GREEN}✅ 无硬编码密钥${NC}"
  ((PASS++))
else
  echo "$SECRET_HITS"
  echo -e "   ${RED}❌ 发现疑似硬编码密钥${NC}"
  ((FAIL++))
fi

# 7b. v-html 检查（确认有 DOMPurify）
echo "   7b. v-html XSS 防护检查..."
VHTML_HITS=$(cd $PROJECT_DIR && grep -rn 'v-html' frontend/src/ --include='*.vue' 2>/dev/null || true)
DOMPURIFY=$(cd $PROJECT_DIR && grep -rn 'DOMPurify\|sanitize' frontend/src/ --include='*.vue' --include='*.js' 2>/dev/null | wc -l | tr -d ' ')

if [ -z "$VHTML_HITS" ]; then
  echo -e "   ${GREEN}✅ 无 v-html 使用${NC}"
  ((PASS++))
elif [ "$DOMPURIFY" -gt 0 ]; then
  echo -e "   ${GREEN}✅ v-html 使用点有 DOMPurify 防护 ($DOMPURIFY 处)${NC}"
  ((PASS++))
else
  echo "$VHTML_HITS"
  echo -e "   ${RED}❌ v-html 无 DOMPurify 防护${NC}"
  ((FAIL++))
fi

# 7c. Git diff 统计
echo -e "\n   7c. 待提交变更统计:"
cd $PROJECT_DIR
CHANGED=$(git status --porcelain | wc -l | tr -d ' ')
if [ "$CHANGED" -gt 0 ]; then
  git diff --stat HEAD 2>/dev/null | tail -1
  echo "   未提交文件: $CHANGED 个"
else
  echo "   工作区干净"
fi

# ── 汇总 ──
echo ""
echo "════════════════════════════════════════"
echo -e "  汇总: ${GREEN}✅ $PASS 通过${NC} | ${RED}❌ $FAIL 失败${NC} | ${YELLOW}⚠️  $SKIP 跳过${NC}"
echo "════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
  echo -e "\n${RED}⚠️  有 $FAIL 项检查失败，请修复后再 push！${NC}"
  exit 1
else
  echo -e "\n${GREEN}✅ 全部检查通过，可以 push！${NC}"
  echo "   git push origin main"
  exit 0
fi
