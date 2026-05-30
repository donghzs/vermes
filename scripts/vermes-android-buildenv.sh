#!/bin/bash
# Vermes Android APK 构建环境 — 在 vbit 服务器执行
# 基于 Buildozer + python-for-android
# 环境: Ubuntu 22.04 (腾讯云轻量服务器 2C4G)

set -e

GREEN='\033[32m'; YELLOW='\033[33m'; NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Vermes Android APK 构建环境安装${NC}"
echo -e "${GREEN}========================================${NC}"

# 1. 系统依赖
echo -e "\n${YELLOW}[1/6] 安装系统依赖...${NC}"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git zip unzip openjdk-17-jdk \
    python3-pip python3-dev \
    autoconf libtool pkg-config \
    libffi-dev libssl-dev \
    curl wget ccache \
    2>&1 | tail -3

# 2. Python 工具
echo -e "\n${YELLOW}[2/6] 安装 Buildozer...${NC}"
pip3 install --upgrade pip -q
pip3 install buildozer cython --quiet 2>&1 | tail -3

# 3. Android SDK 命令行工具
echo -e "\n${YELLOW}[3/6] 下载 Android SDK...${NC}"
cd ~
SDK_DIR="$HOME/android-sdk"
mkdir -p $SDK_DIR

if [ ! -f "$SDK_DIR/cmdline-tools/bin/sdkmanager" ]; then
    CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
    curl -L -o cmdline-tools.zip "$CMDLINE_TOOLS_URL" --progress-bar
    unzip -q cmdline-tools.zip -d $SDK_DIR/
    rm cmdline-tools.zip
    mkdir -p $SDK_DIR/cmdline-tools/latest
    mv $SDK_DIR/cmdline-tools/* $SDK_DIR/cmdline-tools/latest/ 2>/dev/null || true
    echo "  SDK 工具已下载"
fi

# 4. 安装 SDK 组件
echo -e "\n${YELLOW}[4/6] 安装 Android SDK 组件...${NC}"
export ANDROID_HOME="$SDK_DIR"
yes | $SDK_DIR/cmdline-tools/latest/bin/sdkmanager \
    "platforms;android-34" \
    "build-tools;34.0.0" \
    "ndk;27.0.12077973" \
    "platform-tools" \
    2>&1 | tail -5

# 5. 配置环境变量
echo -e "\n${YELLOW}[5/6] 配置环境变量...${NC}"
cat >> ~/.bashrc << 'EOF'
export ANDROID_HOME=$HOME/android-sdk
export PATH=$PATH:$ANDROID_HOME/cmdline-tools/latest/bin
export PATH=$PATH:$ANDROID_HOME/platform-tools
export PATH=$PATH:$ANDROID_HOME/ndk/27.0.12077973
EOF

source ~/.bashrc

# 6. 准备 Vermes 源码
echo -e "\n${YELLOW}[6/6] 准备 Vermes 源码...${NC}"
cd ~
if [ ! -d "~/vermes" ]; then
    curl -L -o vermes-src.tar.gz https://vbit.top/vermes/downloads/vermes-src-v2.0.4.tar.gz
    tar xzf vermes-src.tar.gz
    mv vermes-src vermes 2>/dev/null || true
fi

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  ✅ 构建环境就绪${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "下一步: cd ~/vermes && buildozer init"
echo "然后编辑 buildozer.spec，运行 buildozer android debug"
echo ""
echo "首次构建需要下载很多依赖，预计 20-40 分钟"
echo "建议在 tmux 或 screen 里运行，防止 SSH 断开"
