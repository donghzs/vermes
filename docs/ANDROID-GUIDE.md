# Vermes on Android — 安装指南

在手机上跑完整 Vermes 引擎，跟桌面版一样。

## 原理

```
Android 手机
├── Termux (Linux 终端模拟器)     ← 装 Python
│   ├── Vermes 引擎               ← pip install
│   ├── TUI 界面 / CLI 命令行     ← 终端里跑
│   └── DeepSeek API 调用         ← 调云端
└── (可选) pywebview 原生窗口      ← 需要 Termux:X11
```

## 前置条件

- Android 8+ 手机
- 从 **F-Droid** 安装 Termux（不要从 Google Play，Play 版已停更）
- 网络能访问 DeepSeek API

## 安装步骤

### 1. 安装 Termux

下载 F-Droid: https://f-droid.org
搜索 Termux 安装

### 2. 打开 Termux，运行以下命令

```bash
# 更新包
pkg update -y && pkg upgrade -y

# 安装 Python + 编译工具
pkg install -y python python-pip clang binutils

# 配置 OpenSSL（Android 需要手动指定证书路径）
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
echo 'export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem' >> ~/.bashrc

# 下载 Vermes 源码
cd ~
curl -L -o vermes-src.tar.gz https://vbit.top/vermes/downloads/vermes-src-v2.0.4.tar.gz
tar xzf vermes-src.tar.gz
cd vermes

# 安装（过程中有些 optional 依赖可能报错，不影响核心功能）
pip install -e . 2>&1 | tail -10

# 配置 DeepSeek
mkdir -p ~/.vermes
cat > ~/.vermes/.env << 'EOF'
DEEPSEEK_API_KEY=REDACTED_API_KEY
DEEPSEEK_BASE_URL=https://api.deepseek.com
EOF

cat > ~/.vermes/config.yaml << 'EOF'
provider: deepseek
model: deepseek-chat
EOF

# 测试运行
vermes
```

### 3. 第一次运行

```
$ vermes
╭──────────────────────╮
│  Vermes v2.0.4       │
│  Model: deepseek-chat │
╰──────────────────────╯

You: 你好
Vermes: 你好！有什么我可以帮你的吗？
```

## 保持后台运行

Android 会杀后台进程，需要：

```bash
# 在 Termux 内运行
termux-wake-lock
vermes
```

或者在系统设置里：
- 设置 → 应用 → Termux → 电池优化 → 不优化
- 最近任务 → Termux → 锁定

## 性能

| 手机配置 | 体验 |
|---------|------|
| 8GB+ 内存 | 流畅，引擎全开 |
| 6GB 内存 | 良好，建议关闭其他 App |
| 4GB 内存 | 能用，但后台多任务会吃力 |

引擎本身只要 ~200MB 内存，主要是 LLM 调用走云端，不耗本地算力。

## 故障排除

**SSL 证书错误**
```bash
export SSL_CERT_FILE=/data/data/com.termux/files/usr/etc/tls/cert.pem
```

**psutil 编译失败**
```bash
pkg install python-build
```

**想用 GUI（pywebview）**
需要 Termux:X11 + 桌面环境，不推荐手机小屏用。

建议直接用 TUI（终端界面），全键盘操作，在手机上也很好用。
