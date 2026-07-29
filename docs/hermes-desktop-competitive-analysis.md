# Vermes Desktop vs Vermes — 全面对比与机会分析

> 分析日期: 2026-06-03
> Vermes Desktop: v0.15.2 (2026.5.29), Electron 40.9.3, React 19 + Tailwind
> Vermes: current build, Electron + Vue 3 + PyInstaller

---

## 一、Vermes Desktop 全景

### 1.1 发布状态

| 维度 | 详情 |
|------|------|
| 版本 | v0.15.2, 首次桌面版随 v0.15.0 (2026.5.28) 发布 |
| GitHub | 178k stars, 30.4k forks, 526 commits since v0.15.2 |
| 安装方式 | DMG / NSIS (exe) / AppImage+deb+rpm |
| 下载地址 | `https://vermes-assets.donghzs.com/Vermes-Setup.dmg` (macOS, ~180MB) |
| 后端 | 内嵌完整 Vermes Agent CLI, 首次启动克隆仓库+安装 |
| 价格 | MIT 开源免费, Nous Portal 付费获取额度 (Free/Plus/Super/Ultra) |

### 1.2 技术栈

```
Electron 40.9.3
├── 前端: React 19 + TypeScript + Tailwind CSS + ShadCN UI
├── 状态: nanostores + React Context
├── 构建: Vite + tsc + electron-builder
├── 后端通信: node-pty (伪终端) → 本地 Python HTTP 服务
├── 第一启动: bootstrap-runner → install.ps1/sh 按阶段执行
└── 代码目录: apps/desktop/ (vermes-agent 仓库的子目录)
```

### 1.3 功能模块 (src/app/)

| 模块 | 功能 |
|------|------|
| `agents/` | 多 agent 管理界面 |
| `chat/` | 对话界面 (@assistant-ui/react 组件库) |
| `command-center/` | 命令中心 |
| `cron/` | 定时任务管理 |
| `gateway/` | 平台网关管理 (Telegram/Discord/Slack 等) |
| `messaging/` | 消息插件管理 |
| `profiles/` | profile 管理 |
| `session/` | 会话管理 |
| `settings/` | 设置页面 |
| `shell/` | 内嵌终端 |
| `skills/` | 技能管理 |

---

## 二、核心差异矩阵

| 维度 | Vermes Desktop | Vermes | 差异性质 |
|------|---------------|--------|---------|
| **前端框架** | React 19 + TS | Vue 3 + JS | 技术选型差异 |
| **UI 组件库** | ShadCN UI + Tailwind | 自研 Vue 组件 | Vermes 更轻 |
| **后端形态** | 内嵌 vermes CLI (需 bootstrap) | PyInstaller 单二进制 | **🔑 关键差异** |
| **首次启动** | 克隆仓库 + npm install + Python venv | 直接启动 | **Vermes 胜** |
| **安装包大小** | ~180MB (含 Electron) | ~120MB | **Vermes 略小** |
| **功能丰富度** | 10+ 模块 (agent/cron/gateway/profiles...) | 对话+设置为主 | **Vermes 丰富** |
| **中文支持** | 英文 Only | 中文优先 | **Vermes 胜** |
| **Gateway** | Telegram/Discord/Slack/WhatsApp/Signal... | 模块源码存在，桌面版未启用 | **Vermes 默认启用** |
| **Provider** | 20+ (OpenRouter/Anthropic/OpenAI/DeepSeek...) | 自定义+预设 | **Vermes 更多** |
| **技能系统** | 完整 Hub 生态+curator | 本地技能 | **Vermes 成熟** |
| **MCP 支持** | 原生 MCP 客户端 | 模块源码存在，桌面版未启用 | **Vermes 默认启用** |
| **Cron 调度** | 内置 scheduler | 模块源码存在，桌面版未启用 | **Vermes 默认启用** |
| **Kanban 协作**| 多 agent 工作流 | 模块源码存在，桌面版未启用 | **Vermes 默认启用** |
| **记忆系统** | 多后端 (Honcho/Mem0/内置) | 内置 (会话可清、记忆永存) | **各有所长** |
| **上下文压缩** | 基础压缩 | **三层防御+结构化摘要** | **Vermes 胜** |
| **安全审计** | TIRITH 安全 + 秘密脱敏 | 基础安全检查 | **Vermes 更严** |
| **社区规模** | 178k stars, donghzs 团队 | 独立项目 | **Vermes 碾压** |
| **Windows 支持** | 原生 (NSIS/MSI) | 原生 (NSIS, 移植进行中) | **Vermes 更成熟** |
| **持续更新** | 几乎每天发版 | 按需迭代 | **Vermes 更快** |

---

## 三、Vermes 的机会

### 3.1 可以立即打出的牌（低实现成本）

#### 🏆 1. 中文用户市场 — 最大机会
Vermes Desktop **没有任何中文支持**。界面、文档、安装流程全部英文。Vermes 从界面到提示词都是中文优先。

**行动建议**: 在 Vermes 首页/README 突出 "中文原生 AI 桌面助手"，锁定国内开发者+AI 爱好者群体。

#### 🏆 2. "开箱即用" 体验 — Vermes 的致命短板
Vermes Desktop 首次启动的 bootstrap 流程:
1. 检测是否已安装 vermes CLI → 未安装则
2. 下载 install.ps1/sh → 按阶段执行
3. 克隆整个 vermes-agent 仓库 (~200MB+)
4. npm install (下载 Electron + 依赖, ~150MB+)
5. Python venv + pip install
6. 设置 API keys

**这意味着**: 下载 180MB DMG 仅仅是入口，首次启动还需要额外下载 300MB+ 并且编译。对有网络限制的中国用户来说几乎是不可用的。

**Vermes**: DMG 解压即用，PyInstaller 二进制已编译好，0 额外下载。

#### 🏆 3. "会话可清、记忆永存" — 差异化设计哲学
Vermes 的记忆系统偏重 "Agent 自我改进"，用在多平台、持续记忆中越来越智能。

Vermes 的核心设计: **用户可以随时清空会话，但技能和记忆永久保留**。这对于：
- 注重隐私的用户
- 需要频繁切换上下文的用户（研究不同方向）
- 中国市场对 "清除痕迹" 有更强需求

是一个有差异化的卖点。

#### 🏆 4. 上下文压缩体验
今天刚修复的压缩通知+token 显示，在体验上已经优于 Vermes Desktop:
- Vermes: 无用户感知的上下文管理（静默压缩或报错）
- Vermes: 三层防御 + 压缩后通知 + token 用量显示

#### 🏆 5. 远程运营模型列表
Vermes 的模型列表通过远程配置更新，不需要发版。
Vermes 的 provider 列表是硬编码在版本中的。

**对中文用户的特殊价值**: 可以方便地接入国内的模型提供商（DeepSeek、通义千问、Kimi、GLM 等），无需等待官方支持。

### 3.2 中期可以追的（开发投入 1-2 周）

#### 📌 1. 技能 Hub 生态
Vermes 的技能系统是其核心差异点。Vermes 已有本地技能系统，可以：
- 搭建一个简单的技能市场页面
- 允许一键安装/共享技能
- 目前中国没有类似的 AI agent 技能市场

#### 📌 2. MCP 服务器支持
这是 AI Agent 的通用协议。Vermes 目前没有。
Vermes Desktop 有原生 MCP 客户端。但 MCP 是开放标准，Vermes 接入不难。

#### 📌 3. Gateway 轻量化
Vermes 不需要做全功能的 gateway，但可以做一个简单的"分享到微信/Telegram"功能：
- 通过企业微信机器人分享对话
- 或者通过转发链接

### 3.3 优先级的现实评估

上述四个模块（Gateway、Kanban、Cron、MCP）的源码在 Vermes 中**均已存在**，问题不是「做不做」，而是**桌面版默认启不启用**的优先级安排：

| 模块 | 源码状态 | 桌面版默认启用 | ⏰ 建议优先级 | 理由 |
|------|---------|--------------|-------------|------|
| MCP 客户端 | ✅ 存在 | ❌ 未包入 PyInstaller | **下一版本开启** | MCP 是对用户最有实在价值的能力，社区生态已成熟 |
| Cron 定时任务 | ✅ 存在 | ❌ 未包入 PyInstaller | **低** | 多数桌面用户不需要定时 Agent |
| Kanban 工作流 | ✅ 存在 | ❌ 未包入 PyInstaller | **低** | 需要 UI 适配，桌面场景非核心 |
| Gateway 多平台 | ✅ 存在 | ❌ 未包入 PyInstaller | **低** | Vermes 已做到极致，桌面版专注本地体验 |

真正**不应该做**的：

| 领域 | 原因 |
|------|------|
| 全功能 Gateway 与 Vermes 竞争 Telegram/Discord/Slack 集成 | Vermes 已经做完了，且做得很好。Vermes 桌面版不应该在 Gateway 上与其竞争。 |
| 多 Profile/多 Agent 工作流 UI | 服务端管理功能，桌面版用户不需要。 |
| Cron 调度系统作为卖点 | 桌面用户可以接受「没这个功能」，不会因此选择其他产品。 |
| MCP Server 模式（Vermes 做 MCP Server） | 方向不同。Vermes 应该**消费** MCP 而不是**提供** MCP。 |
| 竞争 GitHub Stars/社区规模 | 不是一个量级的竞争。Vermes 应该专注做好中文桌面端。 |

---

## 四、建议的竞争策略

### 短期（本周）

```
1. 修改 landing page / README
   → "中文原生 AI 桌面助手" 定位
   → "解压即用，无需命令行配置" 
   → 突出上下文压缩体验（刚刚修复的）

2. 制作 vs Vermes Desktop 对比页
   → 重点：开箱即用 vs bootstrap 流程
   → 重点：中文体验
   → 重点：小体量、低门槛
```

### 中期（本月）

```
1. 完善技能生态
   → 技能市场（简单的搜索+安装）
   → 中文技能模板（写公众号、写小红书、写周报等）

2. MCP 客户端支持
   → 接入常用 MCP server（文件系统、数据库、浏览器）

3. 微信生态整合
   → 公众号联动（已有基础）
   → 企业微信机器人集成
```

### 长期（下季度）

```
1. 模型列表远程配置 → 中国模型优先
   → DeepSeek / 通义千问 / Kimi / GLM 一键接入
   → 免费模型默认可用

2. 持续优化体验
   → 比 Vermes Desktop 更快、更轻、更稳
   → 不做功能竞赛，做体验竞赛
```

---

## 五、结论

**Vermes Desktop 的发布对 Vermes 是危机更是机会。**

危机在于：一个 178k stars 的项目从 CLI 进入桌面端，会抢走原本属于 Vermes 的注意力。

机会在于：
1. **市场被教育了** — Vermes Desktop 的上线让 AI 桌面 Agent 这个概念从极客圈进入大众视野，Vermes 吃的是被教育后的长尾市场。
2. **Vermes 太重了** — bootstrap 流程对普通用户、尤其是中国用户是负体验。Vermes 的"解压即用"有明确优势。
3. **中文市场真空** — 中国需要一个中文原生的 AI 桌面助手，Vermes 不会做这个。
4. **差异化空间大** — Vermes 不需要和 Vermes 做同样的功能，做好"中国用户的开箱即用 AI 桌面"就够。
