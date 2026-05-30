#!/data/data/com.termux/files/usr/bin/bash
# Vermes on Android — Termux 一键安装
# 在 Termux 里运行: curl -L https://vbit.top/vermes/downloads/vermes-android-setup.sh | bash

set -e

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Vermes on Android 一键安装${NC}"
echo -e "${GREEN}========================================${NC}"

# 1. 更新
echo -e "\n${YELLOW}▶ [1/5] 更新 Termux 包...${NC}"
pkg update -y -o Dpkg::Options::="--force-confnew" 2>&1 | tail -2
pkg upgrade -y -o Dpkg::Options::="--force-confnew" 2>&1 | tail -2

# 2. 安装 Python
echo -e "\n${YELLOW}▶ [2/5] 安装 Python 3...${NC}"
pkg install -y python python-pip clang binutils openssl-tool 2>&1 | tail -2

# SSL 证书路径
CERT_PATH="/data/data/com.termux/files/usr/etc/tls/cert.pem"
if [ -f "$CERT_PATH" ]; then
    export SSL_CERT_FILE="$CERT_PATH"
    grep -q "SSL_CERT_FILE" ~/.bashrc 2>/dev/null || echo "export SSL_CERT_FILE=$CERT_PATH" >> ~/.bashrc
    echo -e "  ${GREEN}✓${NC} SSL 证书已配置"
fi

# 3. 下载源码
echo -e "\n${YELLOW}▶ [3/5] 下载 Vermes 源码...${NC}"
cd ~
DOWNLOAD_URL="https://vbit.top/vermes/downloads/vermes-src-v2.0.4.tar.gz"
curl -L -o vermes-src.tar.gz "$DOWNLOAD_URL" --progress-bar 2>&1
tar xzf vermes-src.tar.gz 2>/dev/null
cd vermes
echo -e "  ${GREEN}✓${NC} 源码已解压 ($(find . -type f -not -path './.venv/*' | wc -l) 个文件)"

# 4. 安装 Vermes
echo -e "\n${YELLOW}▶ [4/5] 安装 Vermes 依赖（可能需要几分钟）...${NC}"
pip install --upgrade pip -q 2>&1 | tail -1

# 核心依赖
pip install openai python-dotenv httpx pydantic pyyaml prompt-toolkit rich jinja2 croniter psutil --quiet 2>&1 | tail -3

# 源码安装（跳过版本检测，因为 Termux 的 Python 版本可能不满足 >=3.11 的具体版本号）
python setup.py develop 2>/dev/null || pip install -e . 2>&1 | tail -5

# 5. 配置
echo -e "\n${YELLOW}▶ [5/5] 配置 DeepSeek...${NC}"
mkdir -p ~/.vermes
cat > ~/.vermes/.env << 'EOF'
DEEPSEEK_API_KEY=REDACTED_API_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
EOF

cat > ~/.vermes/config.yaml << 'EOF'
provider: deepseek
model: deepseek-chat
EOF

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ 安装完成！${NC}"
echo -e "${GREEN}  在 Termux 里输入 vermes 启动${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "首次运行: ${YELLOW}vermes${NC}"
echo -e "保持后台: ${YELLOW}termux-wake-lock && vermes${NC}"
echo -e "SSL 问题: ${YELLOW}export SSL_CERT_FILE=$CERT_PATH${NC}"
