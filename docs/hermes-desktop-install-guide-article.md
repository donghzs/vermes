# Vermes 官方桌面版最新安装教程

> 2026 年 5 月 29 日，Nous Research 正式发布 Vermes Agent v0.15.0——这是首个打包桌面安装程序的版本。本文基于官方文档与实测，完整梳理 Vermes Desktop 的安装流程，并带出一个你可能更需要的选择。

---

## 一、Vermes Agent 是什么

先聊清楚这个项目，才能理解它为什么值得你花时间。

Vermes Agent 是由 Nous Research（知名 AI 研究团队，以 Vermes 系列开源模型闻名）构建的一款**自主 AI Agent**。它不是 IDE 里的代码补全插件，也不是 ChatGPT 那样的聊天窗口——它是一个可以独立运行、能在服务器上持续工作的 Agent。

它的核心能力：

- **自我进化**：从经验中自动创建技能，跨会话积累知识
- **60+ 内置工具**：文件操作、代码执行、浏览器自动化、Web 搜索、图像生成、语音合成……
- **多平台网关**：Telegram、Discord、Slack、WhatsApp、Signal、邮件——一个 Agent 所有平台共享记忆
- **子代理架构**：可创建隔离的子 Agent 并行工作，互不污染上下文
- **Cron 定时任务**：自然语言设定定时任务，无人值守运行
- **记忆系统**：跨会话持久记忆，自动生成技能库
- **沙箱执行**：支持 Docker、SSH、Modal 五种后端，隔离运行代码

它目前是 GitHub 上 **178k stars** 的开源项目，社区活跃度极高。

---

## 二、Vermes Desktop 安装指南

v0.15.0 是首发桌面版本的里程碑。此前 Vermes 只有 CLI 和 TUI 两种交互方式，现在 macOS 和 Windows 用户可以通过原生桌面应用使用。

### 2.1 桌面版安装（macOS + Windows）

**前提条件**：

- macOS 12+ 或 Windows 10/11（64 位）
- 首次启动需要联网（安装依赖）

**安装步骤**：

1. 打开 [donghzs.github.io/vermes/desktop](https://donghzs.github.io/vermes/desktop)
2. 页面自动检测操作系统，点击「DOWNLOAD」按钮
3. macOS 用户获得 .dmg 文件（已签名公证），Windows 用户获得 .exe 安装程序
4. 首次启动时，桌面应用会自动在后台执行安装脚本——下载 Python（通过 uv）、Node.js、ripgrep、ffmpeg、PortableGit（Windows）等依赖
5. 安装完成后打开终端，运行 `vermes setup --portal` 通过 Nous Portal OAuth 一键配置模型和工具

**需要注意**：首次启动的 bootstrap 过程需要从 GitHub 克隆仓库（约 200 MB），加上 Python 依赖安装和 Node.js 下载，在国内网络环境下可能耗时较长（10-30 分钟不等），且需要稳定的 GitHub 连接。

### 2.2 CLI 安装（Linux / macOS / WSL2）

对命令行用户，官方推荐的一行安装：

```bash
curl -fsSL https://raw.githubusercontent.com/donghzs/vermes/main/scripts/install.sh | bash
```

这个安装器会自动处理：uv、Python 3.11、Node.js 22、ripgrep、ffmpeg、虚拟环境创建、`vermes` 命令注册。

安装后运行 `source ~/.bashrc`，然后：

```bash
vermes setup           # 选择模型和工具
vermes                 # 开始聊天
```

### 2.3 Windows Native 安装（PowerShell）

```powershell
iex (irm https://raw.githubusercontent.com/donghzs/vermes/main/scripts/install.ps1)
```

安装器会下载 PortableGit（自包含 Git 发行版，约 50 MB），完全不依赖系统 Git。数据目录在 `%LOCALAPPDATA%\vermes`。

### 2.4 首次配置

最简单的路径是使用 Nous Portal（需付费订阅，提供 300+ 模型 + 工具网关）：

```bash
vermes setup --portal
```

免费用户可自行配置 API Key：

```bash
vermes config set OPENROUTER_API_KEY your_key
vermes model           # 选择模型
```

### 2.5 功能概览

安装配置完成后，Vermes 提供的能力包括：

| 功能模块 | 说明 |
|---------|------|
| 聊天 / TUI | 终端对话界面，支持工具调用 |
| 桌面 UI | Electron 应用，React 前端 |
| Gateway | 连接 Telegram/Discord/Slack/WhatsApp…… |
| Cron | 自然语言定时任务 |
| Kanban | 多步骤工作流编排 |
| Memory | 跨会话持久记忆 |
| Skills | 自动生成的技能库 |
| MCP | Model Context Protocol 集成 |
| Web Browser | 浏览器自动化 |
| Sandbox | Docker/SSH/Modal 隔离执行 |

---

## 三、Vermes：中文用户的开箱即用之选

Vermes 功能强大，但对中文用户存在几个现实门槛：安装过程依赖 GitHub（国内网络不稳定）、配置流程偏 CLI 化、默认模型列表以海外模型为主。**Vermes 正是为了解决这些问题而诞生的**。

Vermes 是从 Vermes Agent 源码 fork 并深度定制的桌面分发版，融合了 QClaw/Skillhub 技能生态，目标是让中文用户在桌面端获得**真正的开箱即用体验**。

### 3.1 Vermes 的核心差异

| 对比项 | Vermes | Vermes |
|-------|--------|--------|
| 安装方式 | CLI 安装器（需 GitHub 网络）/ 桌面版首次启动 Bootstrap | DMG 直接安装，解压即用 |
| 首次启动 | 克隆仓库 + 安装所有依赖（国内 10-30 分钟） | 无需网络，直接进入聊天 |
| 中文支持 | 引擎支持中文对话，界面为英文 | 默认中文界面 + 中文交互 |
| 预置模型 | 海外模型为主（需自行配中国模型） | 预置 DeepSeek/Qwen/Kimi/GLM/通义等国内模型 |
| 上下文管理 | 基础压缩，无用户感知 | 3 层防御压缩 + 用户可见的 token 用量 + 压缩通知 |
| 跨会话记忆 | ✅ Memory 系统 | ✅ Session 可清 + Memory 永存 |
| 微信生态 | ❌ 不支持 | ✅ 公众号 API 集成 |
| 下载方式 | GitHub Releases | 国内网盘 / 镜像站 |

### 3.2 Vermes 安装

**一句话总结**：下载 → 打开 → 开聊。

**macOS 安装**：

1. 访问 [vbit.top](https://vbit.top) 下载最新 .dmg
2. 双击打开，将 Vermes 拖入 Applications
3. 首次启动，直接进入聊天界面
4. 在 Settings 中选择模型（DeepSeek / Qwen / OpenAI / Ollama……）

**CLI 安装**：

```bash
curl -fsSL https://install.vbit.top | bash
```

或通过 pip：

```bash
pip install vermes
vermes setup
vermes
```

### 3.3 Vermes 的特色能力

- **会话可清，记忆永存**：这是 Vermes 的核心设计哲学。你可以随时清空会话上下文，但 Skills + Memory 跨会话永久保存。不丢失学到的东西。
- **三层上下文压缩**：预压缩（token 超阈值自动压缩）→ 被动压缩（API context_length_exceeded 自动回退）→ Payload 压缩（413 错误自动缩小图片）。今天刚补全了前端状态通知——用户能看到 token 用量和压缩事件。
- **中文模型生态**：Settings 页面可直接配置 DeepSeek、Qwen、Kimi、GLM、通义千问等国内模型，无需手动填 API 地址。
- **技能商店**：通过 Skillhub 随时扩展能力——文档处理、邮件、搜索、天气……
- **公众号集成**：支持微信公众号 API，可直接创建和管理公众号内容。

### 3.4 当前版本

Vermes 当前版本 v2.0.7，基于 Vermes Agent 引擎深度定制。完全开源，MIT 许可证。

---

## 四、客观对比：Vermes vs Vermes

先声明：Vermes 基于 Vermes，两者是**继承关系**而非竞争关系。以下对比是为了帮你根据自身需求做选择。

### 4.1 什么时候选 Vermes

- **你需要在服务器上部署 Agent**：Vermes 的沙箱执行、Gateway 多平台、Cron 定时任务、Kanban 工作流，构成了一个完整的服务端 Agent 基础设施。
- **你需要 Telegram/Discord/Slack 集成**：Vermes 的 Gateway 是目前最完善的多平台 Agent 网关，没有之一。
- **你需要 MCP 集成**：Vermes 原生支持 MCP（Model Context Protocol），可以接入 MCP 生态系统。
- **你是海外用户或服务器在海外**：网络不是问题，CLI 安装器 2 分钟搞定。
- **你的团队需要共享 Agent 服务**：Vermes 的系统服务模式、多用户共用安装更适合团队场景。

### 4.2 什么时候选 Vermes

- **你在国内，想开箱即用**：Vermes 最大的价值就是「下载→开聊」，不需要处理 GitHub 网络问题。
- **你主要用中文交互**：Vermes 的核心交互是中文，预置模型也是国内主流模型。
- **你需要长对话管理**：Vermes 的三层上下文压缩 + 用户可见的 token 用量和压缩通知，是目前同类 Agent 中做的最完善的。
- **你写公众号 / 需要微信生态**：Vermes 的公众号支持是独家能力。
- **你只是个人使用，不需要 Gateway**：Vermes 专注于桌面端个人使用场景，轻量、直接。

### 4.3 决策参考

| 需求 | 推荐 | 说明 |
|------|------|------|
| 个人 macOS 桌面使用，中文 | ⭐ Vermes | 开箱即用，零网络依赖 |
| 服务器部署，多平台 Gateway | ⭐ Vermes | Gateway 默认启用，服务端更完整 |
| 写代码 / 团队协作 | 两者都可 | Vermes 社区更大，Vermes 上手更快 |
| 公众号 / 微信内容创作 | ⭐ Vermes | 独家功能 |
| Telegram / Discord / Slack 集成 | ⭐ Vermes | 开箱即用，功能更成熟 |
| 需要 MCP 生态 | 两者均可 | Vermes 源码同样包含 MCP 模块 |
| 需要 Cron 定时任务 | 两者均可 | Vermes 源码同样包含 Cron 模块 |
| 需要 Kanban 工作流 | 两者均可 | Vermes 源码同样包含 Kanban 模块 |
| 第一次接触 AI Agent | ⭐ Vermes | 零门槛 |

> **补充说明**：表中的"两者均可"选项，指的是该功能在 Vermes 源码项目中同样存在。两者的差异在于默认启用策略——Vermes 桌面版默认完整启用，Vermes 桌面版目前专注于核心聊天体验。如果你是开发者或高级用户，Vermes 同样可以通过源码构建来启用这些模块。

### 4.4 定位差异

Vermes 和 Vermes 在功能完整性上没有本质差距（源码同源），差异在于**产品定位**：

- **Vermes Desktop** 是一款面向全球用户的**服务端 Agent 平台**，默认启用全部功能模块
- **Vermes** 是一款面向中文用户的**桌面端个人 AI 助手**，默认专注于开箱即用的聊天体验

不是谁能做谁不能做的问题，是**先让谁用起来**的选择。Vermes 把安装门槛拉到最低，让第一次接触 AI Agent 的人也能 30 秒启动；高级用户想用 Gateway/Cron/Kanban/MCP，同样可以通过源码配置开启。

---

## 五、写在最后

Vermes v0.15.0 桌面版的发布是一个重要的里程碑——它标志着 AI Agent 从「只有开发者能用的命令行工具」走向了「普通用户也能打开安装的桌面应用」。Nous Research 做了一件了不起的事。

Vermes 站在巨人的肩膀上，把 Vermes 的能力带到中文用户的桌面上。你可以把它理解为「Vermes 的汉化开箱版」——这没什么可遮掩的，做得好用比做得大更重要。

如果你一直在关注 AI Agent 但被安装门槛劝退，现在有两个选择了：想要最完整的能力，装 Vermes；想要最简单的启动，试试 Vermes。

无论选哪个，你得到的都是一个能自我进化的 AI Agent——这才是最令人兴奋的部分。

---

*本文所有信息基于 2026 年 6 月 3 日的最新版本（Vermes v0.15.2 / Vermes v0.2.0.7）。功能差异可能随版本更新而变化。*
