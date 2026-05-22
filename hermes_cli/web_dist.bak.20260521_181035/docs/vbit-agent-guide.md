# vbit Agent 使用文档

> 基于 Nous Research 官方 Hermes Agent 构建，由 vbit.top 定制部署。
> 版本：v1.0 | 更新：2026-05-20

---

## 目录

1. [产品简介](#1-产品简介)
2. [快速开始](#2-快速开始)
3. [模型配置](#3-模型配置)
4. [Web 聊天界面](#4-web-聊天界面)
5. [技能系统](#5-技能系统)
6. [工具与权限](#6-工具与权限)
7. [定时任务](#7-定时任务)
8. [CLI 命令参考](#8-cli-命令参考)
9. [消息平台接入](#9-消息平台接入)
10. [故障排查](#10-故障排查)

---

## 1. 产品简介

vbit Agent 是一个**自进化 AI 助手**，具备以下核心能力：

| 能力 | 说明 |
|------|------|
| 💬 多模型对话 | 支持 DeepSeek、OpenRouter、Ollama 等 20+ 提供商 |
| 🔧 工具调用 | 40+ 内置工具（文件操作、代码执行、浏览器自动化、搜索等） |
| 🧠 记忆系统 | 自动保存对话上下文，跨会话记忆用户信息 |
| 🎯 技能系统 | 从对话中自动创建和改进技能，支持 SkillHub 社区安装 |
| ⏰ 定时任务 | 内置 cron 调度器，支持自然语言定时提醒 |
| 📱 多平台 | Web、Telegram、Discord、Slack 等多端接入 |

**技术架构：**

```
用户消息
  └─→ Web UI (React + Vite)  ← 当前页面
        └─→ Gateway (Python asyncio)
              ├─→ Session Store (SQLite)
              ├─→ AIAgent (LLM 调用 + 工具循环)
              │     ├─→ LLM API (DeepSeek / OpenRouter / ...)
              │     └─→ Tools (文件/代码/浏览器/搜索...)
              └─→ Delivery (消息平台适配器)
```

---

## 2. 快速开始

### 2.1 安装

```bash
# macOS / Linux
curl -fsSL https://vbit.top/install.sh | bash

# 或手动安装
git clone https://github.com/NousResearch/hermes-agent.git ~/.hermes/hermes-agent
cd ~/.hermes/hermes-agent
pip install -e ".[all]"
```

### 2.2 初始化配置

```bash
hermes setup
```

交互式向导会引导你完成：
1. 选择 LLM 提供商（推荐：DeepSeek / OpenRouter）
2. 填写 API Key
3. 选择默认模型
4. 配置工具权限

### 2.3 启动 Web 界面

```bash
hermes gateway start
```

打开浏览器访问：`http://localhost:3579`

---

## 3. 模型配置

### 3.1 支持的提供商

| 提供商 | 环境变量 | 获取地址 |
|--------|----------|----------|
| DeepSeek | `DEEPSEEK_API_KEY` | https://platform.deepseek.com |
| OpenRouter | `OPENROUTER_API_KEY` | https://openrouter.ai/keys |
| Noui Portal | `PORTAL_API_KEY` | https://portal.nousresearch.com |
| Ollama 本地 | `OLLAMA_API_KEY`（可选） | 本地 http://localhost:11434 |
| 智谱 GLM | `GLM_API_KEY` | https://open.bigmodel.cn |
| Kimi | `KIMI_API_KEY` | https://platform.kimi.ai |
| MiniMax | `MINIMAX_API_KEY` | https://www.minimax.io |
| 小米 MiMo | `XIAOMI_API_KEY` | https://platform.xiaomimimo.com |

### 3.2 配置方法

**方式一：命令行（推荐）**

```bash
# 设置提供商
hermes config set model.provider deepseek

# 设置默认模型
hermes config set model.default deepseek-chat

# 查看当前配置
hermes config get model
```

**方式二：Web 界面**

访问 `http://localhost:3579/config`，在页面中修改：
- Model Provider → 选择提供商
- Default Model → 选择模型
- 点击 Save 保存

**方式三：直接编辑文件**

> ⚠️ 注意：直接编辑 `~/.hermes/config.yaml` 可能被 `hermes setup` 覆盖，建议用方式一或二。

```yaml
# ~/.hermes/config.yaml
model:
  default: deepseek-chat
  provider: deepseek
  api_mode: chat_completions
```

### 3.3 切换模型

在任何聊天界面中，使用斜杠命令切换：

```
/model deepseek:deepseek-chat
/model openrouter:anthropic/claude-opus-4
```

---

## 4. Web 聊天界面

### 4.1 界面布局

```
┌─────────────────────────────────────────────┐
│  📋 会话列表  │  主聊天区域                    │
│                │                              │
│  ＋ 新对话    │  （消息气泡）                 │
│                │                              │
│  > 对话1      │  ┌──────────────────────────┐ │
│    对话2       │  │ ▤ 输入框                 │ │
│    对话3       │  │  支持附件、换行、停止生成 │ │
│                │  └──────────────────────────┘ │
└─────────────────────────────────────────────┘
```

### 4.2 发送消息

- **发送**：输入完成后按 `Enter`
- **换行**：`Shift + Enter`
- **停止生成**：点击红色 ⏹ 按钮

### 4.3 附件功能

支持拖拽或点击 📎 按钮添加附件：

| 类型 | 格式 | 限制 |
|------|------|------|
| 图片 | jpg/png/gif/webp | 单文件 ≤ 2MB，最多 5 个 |
| 文本 | txt/csv/json/md/py/js/ts/html/css | 单文件 ≤ 2MB |

附件会在消息中显示预览，图片以缩略图展示，点击可放大。

### 4.4 斜杠命令

在输入框中输入 `/` 可查看所有可用命令：

| 命令 | 说明 |
|------|------|
| `/new` | 开始新对话 |
| `/reset` | 重置当前对话 |
| `/model [provider:]model` | 切换模型 |
| `/personality [name]` | 切换 AI 人格 |
| `/skills` | 查看已安装技能 |
| `/compress` | 压缩上下文 |
| `/usage` | 查看用量统计 |
| `/stop` | 停止当前生成 |
| `/retry` | 重新生成上一条回复 |

### 4.5 多会话管理

- 左侧边栏显示所有会话，点击切换
- 点击 🗑️ 图标删除会话
- 新对话自动以第一条消息内容命名

---

## 5. 技能系统

### 5.1 什么是技能

技能是 Hermes 的**过程性记忆**——它从对话中自动学习可复用的工作流程，并在后续对话中自动调用。

### 5.2 查看已安装技能

**Web 界面**：访问 `http://localhost:3579/skills`

**命令行**：
```bash
hermes skills list
```

### 5.3 安装新技能

**从 SkillHub 安装：**
```bash
hermes skills install <skill-name>
```

**示例：**
```bash
# 安装计算器技能
hermes skills install calculator

# 安装网页搜索技能
hermes skills install duckduckgo-search

# 安装 PPT 生成技能
hermes skills install pptx
```

### 5.4 调用技能

在对话中直接描述需求，Hermes 会自动识别并调用对应技能：

```
用户：帮我搜索一下 Hermes Agent 的最新文档
→ Hermes 自动调用 duckduckgo-search 技能

用户：把这个做成 PPT
→ Hermes 自动调用 pptx 技能
```

也可使用斜杠命令直接调用：
```
/skills          # 查看所有技能
/<skill-name>    # 直接调用某个技能
```

### 5.5 自动技能创建

当 Hermes 完成一个复杂任务后，会自动问你是否要将此过程保存为技能：

```
🤖 Hermes：我刚帮你完成了 XX 任务，要不要把这个过程保存为技能，
      下次可以直接调用？
```

---

## 6. 工具与权限

### 6.1 内置工具（40+）

| 分类 | 工具 |
|------|------|
| 📁 文件操作 | read_file, write_file, edit_file, list_files |
| 💻 代码执行 | run_python, run_node, run_shell |
| 🌐 网页浏览 | web_search, web_fetch, browser_automation |
| 🖼️ 图像生成 | generate_image (fal.ai) |
| 🎤 语音 | speech_to_text, text_to_speech |
| 📊 数据分析 | analyze_csv, generate_chart |

### 6.2 配置工具权限

**Web 界面**：`http://localhost:3579/tools`

可配置：
- 启用/禁用某个工具
- 设置工具调用需要审批
- 配置沙箱执行环境

**命令行**：
```bash
# 配置启用的工具集
hermes tools

# 查看当前工具配置
hermes config get toolsets
```

### 6.3 安全建议

- 首次使用时，将敏感工具（如 `run_shell`）设为**需要审批**
- 使用 Docker 后端运行代码工具，实现沙箱隔离
- 定期审查 `~/.hermes/logs/` 中的执行日志

---

## 7. 定时任务

### 7.1 创建定时任务

**自然语言方式（推荐）：**

在任意聊天中告诉 Hermes：

```
用户：每天早上 9 点提醒我查看邮件
用户：每周一下午 3 点执行数据备份
```

Hermes 会自动创建 cron 任务。

**命令行方式：**

```bash
# 查看所有定时任务
hermes cron list

# 手动创建任务
hermes cron add "0 9 * * *" "提醒：查看邮件"
```

### 7.2 管理定时任务

**Web 界面**：`http://localhost:3579/cron`

可操作：
- 查看所有任务
- 启用/暂停/删除任务
- 查看执行历史
- 立即触发执行

---

## 8. CLI 命令参考

### 8.1 常用命令

| 命令 | 说明 |
|------|------|
| `hermes` | 启动终端交互界面 |
| `hermes setup` | 运行配置向导 |
| `hermes model` | 切换/配置模型 |
| `hermes tools` | 配置工具权限 |
| `hermes config set <key> <value>` | 设置配置项 |
| `hermes config get [key]` | 查看配置 |
| `hermes gateway start/stop/restart` | 网关服务管理 |
| `hermes gateway status` | 查看网关状态 |
| `hermes skills list/install/search` | 技能管理 |
| `hermes cron list/add/remove` | 定时任务管理 |
| `hermes update` | 更新到最新版本 |
| `hermes doctor` | 诊断环境问题 |

### 8.2 终端界面快捷键

| 快捷键 | 说明 |
|--------|------|
| `Enter` | 发送消息 |
| `Shift + Enter` | 换行 |
| `Ctrl + C` | 停止生成 / 退出 |
| `Ctrl + L` | 清空屏幕 |
| `↑ / ↓` | 浏览历史消息 |
| `Tab` | 命令/技能名补全 |

---

## 9. 消息平台接入

### 9.1 Telegram

```bash
# 1. 找 @BotFather 创建 Bot，获取 Token
# 2. 配置
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_ALLOWED_USERS="your-telegram-id"

# 3. 启动网关
hermes gateway start
```

### 9.2 Discord

```bash
# 1. 在 Discord Developer Portal 创建 Application → Bot
# 2. 配置
export DISCORD_BOT_TOKEN="your-bot-token"
export DISCORD_ALLOWED_USERS="your-discord-id"

# 3. 启动网关
hermes gateway start
```

### 9.3 其他平台

支持的平台：Slack、WhatsApp、Signal、Microsoft Teams、Google Chat、WeChat（企业微信）

配置方式类似，详见各平台向导：
```bash
hermes gateway setup
```

---

## 10. 故障排查

### 10.1 模型调用失败

**症状**：聊天窗口显示 `LLM PROVIDER NOT CONFIGURED`

**原因**：`config.yaml` 中 `provider` 设置不正确，或 `.env` 中 API Key 缺失

**解决**：
```bash
# 1. 检查配置
hermes config get model

# 2. 重新设置
hermes config set model.provider deepseek
hermes config set model.default deepseek-chat

# 3. 确认 .env 中有对应 Key
grep DEEPSEEK_API_KEY ~/.hermes/.env

# 4. 重启网关
hermes gateway restart
```

### 10.2 端口被占用

**症状**：`hermes gateway start` 报端口 3579 被占用

**解决**：
```bash
# 查看占用进程
lsof -i :3579

# 杀掉进程后重启
kill -9 <PID>
hermes gateway start
```

### 10.3 技能安装失败

**症状**：`hermes skills install` 超时或报错

**原因**：网络问题或 Python 依赖安装失败

**解决**：
```bash
# 使用国内镜像
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 重新安装
hermes skills install <skill-name>
```

### 10.4 日志位置

| 日志类型 | 路径 |
|----------|------|
| 网关日志 | `~/.hermes/logs/gateway.log` |
| 会话轨迹 | `~/.hermes/logs/session_*.json` |
| 工具执行 | `~/.hermes/logs/tools.log` |

---

## 附录：配置文件说明

### A. `~/.hermes/config.yaml`

主配置文件，控制模型、工具、网关等行为。

**重要**：修改后需执行 `hermes gateway restart` 生效。

### B. `~/.hermes/.env`

API Key 和敏感配置。此文件不提交到 Git。

**格式**：
```bash
DEEPSEEK_API_KEY=sk-xxxxxxxx
OPENROUTER_API_KEY=sk-or-xxxxxxxx
```

---

*文档版本 v1.0 | 如有问题请联系 vbit.top 技术支持*
