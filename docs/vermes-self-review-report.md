# Vermes 自我审查与进化方案

> **审查时间**: 2026-05-29
> **审查对象**: Vermes AI Agent 全栈系统
> **源代码位置**: ~/Projects/vermes/
> **审查范围**: 前端 UI/UX、后端架构、Vermes 引擎、内置技能生态、非技术用户体验

---

## 目录

1. [系统架构总览](#一系统架构总览)
2. [前端 UI/UX 问题与改进](#二前端-uiux-问题与改进)
3. [前端架构问题与改进](#三前端架构问题与改进)
4. [后端架构问题与改进](#四后端架构问题与改进)
5. [Vermes 引擎评估](#五vermes-引擎评估)
6. [内置技能生态补充建议](#六内置技能生态补充建议)
7. [非技术用户体验核心改进](#七非技术用户体验核心改进)
8. [安全风险清单](#八安全风险清单)
9. [进化路线图](#九进化路线图)
10. [总结](#十总结)

---

## 一、系统架构总览

### 1.1 三层架构

```
┌─────────────────────────────────────────────────┐
│                   用户入口                        │
│  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │ Vue3 GUI │  │ CLI 终端  │  │ Gateway 多平台 │  │
│  │(前端SPA) │  │ (vermes) │  │(微信/飞书/钉钉)│  │
│  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │
│       │              │               │           │
│  ┌────▼──────────────▼───────────────▼────────┐  │
│  │          Web Server (Flask)                │  │
│  │          web_server.py (5,990行)            │  │
│  └──────────────────┬─────────────────────────┘  │
│                     │                             │
│  ┌──────────────────▼─────────────────────────┐  │
│  │          Vermes Agent 引擎                  │  │
│  │  run_agent.py + agent/ (80+模块)            │  │
│  │  ├── 对话管理    ├── 工具调度               │  │
│  │  ├── 上下文压缩  ├── 记忆系统               │  │
│  │  ├── Skill 系统  ├── Provider 适配          │  │
│  │  └── 定时任务    └── 插件系统               │  │
│  └─────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### 1.2 代码规模

| 维度 | 数量 | 说明 |
|------|------|------|
| Python 文件总数 | 2,732 | 包括 tools、agent、gateway 等 |
| 核心 Python 模块代码量 | 28,000+ 行 | 仅 top-level 核心文件（cli.py 14,515行 + web_server.py 5,990行 + run_agent.py 4,137行 + 其他） |
| Vue 组件 | 6 个 | App、ChatView、Settings、Sidebar、WelcomeGuide、index.html |
| 前端 JS Store | 2 个 | chat.js (664行)、settings.js (44行) |
| 内置 Skill | 30+ | 覆盖开发、文档、研究、营销等 |
| SkillHub 可安装 | 107 个 | 19 个分类 |
| 工具实现 | 80+ | terminal、browser、file、search 等 |
| Gateway 平台 | 10+ | 微信、飞书、钉钉、Telegram、Discord、WhatsApp、Signal、Matrix、Line、iMessage |
| 配置目录 | 2 个 | ~/.vermes/（Vermes 分发配置）+ ~/.vermes/（Vermes 引擎配置） |

### 1.3 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Vue 3 + Vite + Tailwind CSS + Pinia |
| 后端 | Python Flask |
| 引擎 | Python（自研 Agent 框架） |
| 打包 | PyInstaller（macOS .app） |
| 数据库 | SQLite（VERMES_state.py） |
| LLM 适配 | 多 Provider 架构（18+ 提供商） |

---

## 二、前端 UI/UX 问题与改进

### 2.1 P0 — 高优先级（直接影响用户体验）

#### F1: ChatView.vue 738行单文件

**问题**: 所有聊天逻辑（消息列表、输入框、模型选择器、配额弹窗、微信登录、文件上传、历史面板）全塞在一个文件里。

**影响**: 维护困难，修改一个功能可能影响其他功能；代码可读性差。

**改进方案**:
```
ChatView.vue (拆分为)
├── MessageList.vue       — 消息列表（支持虚拟滚动）
├── MessageBubble.vue     — 单条消息气泡（复制、重新生成）
├── InputArea.vue         — 输入区域（多行、文件上传、发送）
├── ModelSelector.vue     — 模型选择器
├── QuotaModal.vue        — 积分/配额弹窗
├── WechatLogin.vue       — 微信扫码登录
├── HistoryPanel.vue      — 搜索历史/匹配面板
└── WelcomeEmpty.vue      — 空会话欢迎状态
```

#### F2: 错误提示使用 alert()

**问题**: 多处使用浏览器原生 `alert()` 弹窗显示错误信息，阻塞交互、样式丑陋。

**当前代码示例**:
```javascript
// ChatView.vue:614
alert('❌ 发送失败：' + e.message);

// ChatView.vue:372
alert('❌ 粘贴图片失败：' + e.message);

// ChatView.vue:461
alert('❌ 图片上传失败：' + e.message);
```

**改进方案**: 引入 toast 通知组件（推荐 `vue-sonner` 或自研轻量 toast），替代所有 `alert()`：
- 成功 → 绿色 toast，自动 3 秒消失
- 警告 → 黄色 toast，自动 5 秒消失
- 错误 → 红色 toast，带"查看详情"展开按钮

#### F3: 错误信息不友好

**问题**: 错误信息直接抛出技术异常原文，非技术用户完全看不懂。

**改进方案** — 错误分类映射：

| 原始错误 | 用户看到的提示 |
|----------|---------------|
| `fetch failed` / `NetworkError` | "🌐 网络连接失败，请检查网络后重试" |
| `401 Unauthorized` | "🔑 API Key 无效或已过期，请到设置页重新配置" |
| `429 Too Many Requests` | "⏳ 请求太频繁，请稍后再试" |
| `402` / 配额不足 | "💰 免费额度已用完" + 自动弹出积分弹窗 |
| `500 Internal Server Error` | "⚠️ 服务暂时不可用，请切换其他模型或稍后重试" |
| `repetition_penalty` 超范围 | "⚙️ 模型参数配置异常，请检查设置" |
| 其他未知错误 | "❌ 出了点问题，请重试。如持续出现请联系客服" |

#### F4: 缺少消息复制按钮

**问题**: 用户想复制 AI 回复内容，只能手动鼠标选中文字，长消息尤其痛苦。

**改进方案**: 在每条 AI 消息的右下角（hover 时显示）添加操作按钮组：
- 📋 复制全文
- 🔄 重新生成
- 👍/👎 反馈

#### F5: Markdown 渲染 XSS 风险

**问题**: 使用 `v-html` 直接渲染 `markdown-it` 输出，无任何清理。

**当前代码**:
```html
<!-- ChatView.vue:343 -->
<div v-html="item.content" ...></div>
<!-- ChatView.vue:517 -->
<div v-html="renderMarkdown(item.displayContent)" ...></div>
```

**改进方案**: 使用 DOMPurify 清理：
```javascript
import DOMPurify from 'dompurify';
const safeHtml = DOMPurify.sanitize(renderMarkdown(content));
```

#### F6: 缺少"重新生成"按钮

**问题**: AI 回复不满意时，用户必须重新打字或手动复制再发。

**改进方案**: 当生成停止后（用户手动停止或自然结束），在最后一条 AI 消息旁显示"🔄 重新生成"按钮，点击后自动重新发送上一条用户消息。

### 2.2 P1 — 中优先级

#### F7: 顶部栏信息过载

**问题**: 一行之内塞了微信头像、用户名、分隔线、汉堡菜单、会话名、消息数、历史搜索按钮、配额信息、模型选择器……信息密度太高。

**改进方案** — 信息分层：

```
顶部栏（精简）:
  [≡] [会话名]                    [模型 ▾] [头像]

底部状态栏:
  消息数 | 今日请求 | 配额余额
```

- 核心信息（会话名 + 模型选择器 + 用户头像）放顶部
- 次要信息（消息数、配额余额）放底部状态栏或折叠菜单
- 汉堡菜单合并到侧边栏的展开按钮

#### F8: 消息列表无虚拟滚动

**问题**: 长会话消息全部渲染到 DOM，性能随消息数线性下降。

**改进方案**: 引入 `vue-virtual-scroller` 或手动实现 Intersection Observer 懒加载。

#### F9: 文件上传支持有限

**当前支持**: 图片（jpg/png/gif/webp）、PDF、txt、log、csv、json、md、xml、html、py、js、ts、java、c、cpp、h。

**缺失**: .docx、.xlsx、.pptx、.zip、.mp3、.mp4

**改进方案**: 扩展 accept 列表 + 后端增加对应解析器。

#### F10: 代码块无"复制"按钮

**问题**: AI 生成的代码无法一键复制。

**改进方案**: 在 `<pre>` 标签右上角固定显示"复制"按钮，点击后复制代码内容并显示"✅ 已复制"。

#### F11: 输入框不支持多行

**问题**: Enter 直接发送消息，无法输入换行。

**改进方案**: 
- `Enter` → 发送消息
- `Shift+Enter` → 换行
- 或提供设置项让用户自选

#### F12: 欢迎页重复

**问题**: `WelcomeGuide.vue` 和 `ChatView.vue` 各有一套欢迎页面逻辑，存在重复。

**改进方案**: 合并为一个统一的 `WelcomeEmpty.vue` 组件，由 ChatView 在无消息时渲染。

#### F13: 设置页信息过载

**问题**: 18 个提供商平铺展开，每个都要求填写 Base URL、API Key、Provider ID，术语多，小白用户完全懵。

**改进方案** — 分层展示：

```
设置页:
├── 🌟 推荐模式（默认展示）
│   ├── DeepSeek（国内推荐，性价比高）
│   ├── 本地模型（Ollama/vLLX，完全免费）
│   └── 微信扫码登录（体验最简单）
│
└── ⚙️ 高级模式（点击展开）
    ├── 🇨🇳 国产模型
    │   ├── 小米 MiMo
    │   ├── 百度文心
    │   ├── 讯飞星火
    │   ├── 智谱 ChatGLM
    │   └── ...
    ├── 🌍 国际模型
    │   ├── OpenAI GPT
    │   ├── Anthropic Claude
    │   ├── Google Gemini
    │   └── ...
    └── 🔧 自定义
        └── 添加自定义 Provider
```

每个提供商加一行通俗说明：
- "这个是什么" — 一句话介绍
- "怎么获取 Key" — 链接到申请页面

#### F14: 主题不跟随系统

**问题**: 保存主题后刷新才生效，首次访问不读 `prefers-color-scheme`。

**改进方案**: `init()` 时检测 `window.matchMedia('(prefers-color-scheme: dark)')`，无手动设置时跟随系统。

#### F15: 侧边栏收起后消失

**问题**: `w-0` 导致收起后侧边栏完全消失，用户不知道如何展开。

**改进方案**: 收起时保留 40px 窄边栏，只显示"展开"图标和新建会话按钮。

### 2.3 P2 — 低优先级

| 编号 | 问题 | 改进方案 |
|------|------|----------|
| F16 | 消息无时间戳 | hover 时在气泡旁显示时间 |
| F17 | 搜索高亮不准 | 搜索跳转后定位到具体消息并高亮关键词 |
| F18 | 无拖拽上传 | 支持拖拽文件到聊天区域上传 |
| F19 | 模型切换无提示 | 切换后在聊天区插入系统消息"已切换至 xxx" |
| F20 | 无键盘快捷键 | Ctrl+N 新会话、Ctrl+K 搜索、Esc 关闭弹窗 |

---

## 三、前端架构问题与改进

### A1: chat.js store 664行单文件

**问题**: 所有状态管理（会话、消息、认证、设置、WebSocket）混在一个 store 里。

**改进方案**:
```
stores/
├── sessionStore.js    — 会话列表管理（创建、删除、切换、搜索）
├── messageStore.js    — 消息发送/接收/流式处理
├── authStore.js       — 微信登录、配额、用户信息
├── settingsStore.js   — 模型选择、主题、Provider 配置
└── websocketStore.js  — WebSocket 连接管理
```

### A2: 事件通信靠 window.dispatchEvent

**问题**: 组件间通信使用 `window.dispatchEvent(new CustomEvent(...))`，字符串 key 无类型安全，容易拼写错误。

**改进方案**: 使用 mitt 轻量事件总线，或直接在 Pinia store 间互相调用。

### A3: localStorage 操作散落

**问题**: 大量 `localStorage.getItem/setItem` 直接调用，key 字符串散落在 20+ 处。

**改进方案**: 封装 `storageService.js`：
```javascript
// storageService.js
export const StorageKeys = {
  SESSION_ID: 'currentSessionId',
  THEME: 'vermes_theme',
  SELECTED_MODEL: 'vermes_selected_model',
  // ...
};

export function getStorage(key, defaultValue = null) { ... }
export function setStorage(key, value) { ... }
export function removeStorage(key) { ... }
```

### A4: 无 TypeScript

**问题**: 全 JavaScript，无类型检查，运行时才暴露类型错误。

**改进方案**: 至少对 stores 和 services 进行 TypeScript 迁移，逐步推进。

### A5: 无前端单元测试

**改进方案**: 引入 vitest，对 store 逻辑做单元测试。

---

## 四、后端架构问题与改进

### B1: web_server.py 5,990行单文件 🔴

**问题**: 所有 HTTP 路由（认证、聊天、配置、配额、Provider 管理、文件上传、WebSocket）全在一个文件里。

**改进方案** — 拆分为 Blueprint：
```
server/
├── __init__.py
├── app.py              — Flask app 工厂
├── routes/
│   ├── auth.py         — 微信登录 /api/wechat/*
│   ├── chat.py         — 聊天 SSE /api/chat, /api/chat/stop
│   ├── models.py       — 模型管理 /api/models
│   ├── config.py       — 配置管理 /api/env
│   ├── quota.py        — 积分/配额 /api/quota/*
│   ├── providers.py    — Provider CRUD /api/provider/*
│   ├── files.py        — 文件上传 /api/upload-image
│   └── system.py       — 系统信息 /api/health, /api/debug
├── services/
│   ├── wechat_service.py
│   ├── quota_service.py
│   ├── model_resolver.py
│   └── chat_service.py
└── middleware/
    ├── auth.py
    └── error_handler.py
```

### B2: cli.py 14,515行单文件 🔴

**问题**: CLI 入口文件极长，所有命令定义在同一文件。

**改进方案**: 按命令组拆分为子模块，cli.py 只做注册。

### B3: provider_aliases 硬编码 🟡

**问题**: `_resolve_model_provider()` 中的 `provider_aliases` 字典手动维护，添加新 Provider 时容易遗漏。

**改进方案**: 从 `PROVIDER_TEMPLATES` 配置自动推导 alias 映射，不再手动维护。

### B4: 配额逻辑分散 🟡

**问题**: 配额检查在前端（ChatView.vue 积分弹窗）和后端（web_server.py）都有逻辑。

**改进方案**: 配额逻辑统一到后端 API，前端只做展示。

### B5: 无 API 文档 🟡

**改进方案**: 添加 docstring + 考虑迁移到 FastAPI 自动生成 `/docs`。

### B6: /api/env PUT 直接写 .env 文件 🔴

**问题**: 任何人只要知道 API 地址就能修改 .env 配置。

**改进方案**: 
- 添加认证校验（仅管理员可调用）
- 限制可写的 key 范围白名单
- 记录操作日志

---

## 五、Vermes 引擎评估

### 5.1 优势（保持）

| 模块 | 评分 | 说明 |
|------|------|------|
| 工具生态 | ⭐⭐⭐⭐⭐ | 80+ 工具，覆盖终端、浏览器、文件、搜索、MCP、TTS、视频、图片等 |
| Skill 系统 | ⭐⭐⭐⭐⭐ | 30 内置 + 107 SkillHub，结构化加载、模板、脚本 |
| 多平台接入 | ⭐⭐⭐⭐⭐ | Gateway 支持 10+ 平台 |
| Provider 适配 | ⭐⭐⭐⭐ | 18+ 提供商，但 aliases 需维护 |
| 上下文管理 | ⭐⭐⭐⭐ | 压缩、摘要、记忆持久化 |
| 定时任务 | ⭐⭐⭐⭐ | cron 系统，支持后台运行 |

### 5.2 引擎层改进建议

| 编号 | 问题 | 改进方案 |
|------|------|----------|
| E1 | run_agent.py 4,137行 | 拆分为 AgentRunner、ContextManager、ToolDispatcher |
| E2 | tools/ 80+ 文件无分类 | 按领域分目录：tools/file/、tools/web/、tools/media/ |
| E3 | 错误恢复机制不够完善 | 添加 retry + fallback 策略，Provider 失败时自动切换 |
| E4 | 子代理结果验证弱 | delegate_task 返回的是 self-report，对外部副作用应自动验证 |

---

## 六、内置技能生态补充建议

### 6.1 现有技能覆盖评估

| 领域 | 已覆盖 | 评价 |
|------|--------|------|
| 编程开发 | TDD、调试、代码审查、Git PR、多 Agent 协作 | ✅ 优秀 |
| 文档处理 | PDF、Word、Excel、PPT | ✅ 完整 |
| 研究 | arXiv、Polymarket、博客监控、LLM Wiki | ✅ 丰富 |
| 社交媒体 | X/Twitter | ⚠️ 缺微博/抖音/小红书 |
| 内容营销 | 公众号、视频号创作模板 | ⚠️ 有框架但缺完整工作流 |
| 邮件 | Himalaya、IMAP/SMTP | ✅ 完整 |
| 图片/视频 | ComfyUI、Manim、ASCII Art | ✅ 不错 |
| 智能家居 | Hue | ⚠️ 单一 |
| 搜索 | 多引擎聚合 | ✅ 强 |

### 6.2 建议新增技能（面向非技术用户）

#### 🔴 P0 — 高优先级

| 技能 | 说明 | 价值 |
|------|------|------|
| **公众号文章全自动创作** | 选题→大纲→初稿→配图→排版→API发布，完整流水线 | 用户核心需求 |
| **PPT 自动生成** | 用户说"帮我做一个关于XX的PPT"→自动生成 .pptx | 高频需求 |
| **AI 图片/海报生成** | 集成 DALL-E/Midjourney/SVG，生成公众号封面、社交海报 | 内容创作必备 |
| **微信消息自动回复** | 接入公众号/企微消息接口，智能自动回复 | 商业价值高 |

#### 🟡 P1 — 中优先级

| 技能 | 说明 |
|------|------|
| **Excel 数据分析** | 上传 Excel → 自动分析、生成图表、写报告 |
| **日程/待办管理** | "提醒我明天开会" → 自动设定 |
| **语音输入/输出** | Whisper 转写 + TTS 回复 |
| **文档/合同审查** | 上传 PDF 合同 → AI 标注关键条款和风险 |
| **微博/小红书发布** | 国内社交平台内容分发 |

#### 🟢 P2 — 低优先级

| 技能 | 说明 |
|------|------|
| 简历生成 | 用户描述经历 → 生成专业简历 |
| 菜谱/营养建议 | 拍食材照片 → 推荐菜谱 |
| 旅行规划 | 输入目的地+天数 → 生成行程表 |
| 翻译润色 | 专业文档翻译 + 润色 |
| 学习助手 | 拍题解答、知识卡片生成 |

---

## 七、非技术用户体验核心改进

### 7.1 新手引导流程重做

**现状**: `WelcomeGuide.vue` 只有 2 个步骤（配置模型 → 体验），信息密度低，引导不充分。

**改进方案** — 3步引导向导：

```
Step 1: 选择使用方式
┌─────────────────────────────────────────┐
│  🎉 欢迎使用 Vermes AI 助手              │
│                                         │
│  选择一种方式开始：                       │
│                                         │
│  [🔵 微信扫码登录]  ← 推荐，最简单       │
│  [🟢 免费体验]      ← 无需注册           │
│  [⚙️ 配置自己的Key] ← 高级用户           │
│  [💻 使用本地模型]   ← 完全免费离线       │
└─────────────────────────────────────────┘

Step 2: 根据选择引导配置
  - 微信扫码 → 显示二维码
  - 免费体验 → 直接跳过
  - 自备Key → 展示简化的设置页
  - 本地模型 → 引导安装 Ollama/vLLX

Step 3: 发送第一条消息
┌─────────────────────────────────────────┐
│  🎊 一切就绪！                           │
│                                         │
│  试试对我说：                            │
│  · "帮我写一篇公众号文章"                │
│  · "分析这个 Excel 文件"                │
│  · "帮我做一个PPT"                       │
│                                         │
│  [开始对话 →]                            │
└─────────────────────────────────────────┘
```

### 7.2 设置页分层重做

**现状**: 18 个提供商全部平铺，术语多（Base URL、Provider ID），小白用户恐惧。

**改进方案**:

**默认 — 推荐模式**:
```
┌─────────────────────────────────────────┐
│  ⚙️ 设置                                │
│                                         │
│  🌟 推荐（点击切换）                     │
│  ┌─────────────────────────────────┐    │
│  │ 🔥 DeepSeek                     │    │
│  │ 国产高性价比，注册即送免费额度    │    │
│  │ [获取API Key]  [Key: ****]      │    │
│  └─────────────────────────────────┘    │
│  ┌─────────────────────────────────┐    │
│  │ 💻 本地模型                      │    │
│  │ 完全免费，数据不离开你的电脑      │    │
│  │ 支持 Ollama / vLLX              │    │
│  │ [自动检测]  ✓ 已检测到 vLLX      │    │
│  └─────────────────────────────────┘    │
│                                         │
│  [展开高级选项 ↓]                        │
└─────────────────────────────────────────┘
```

**高级模式（点击展开）**:
- 🇨🇳 国产模型（小米、百度、讯飞、智谱……）
- 🌍 国际模型（OpenAI、Anthropic、Google……）
- 🔧 自定义 Provider

### 7.3 全局错误处理层

**改进方案**: 创建统一错误处理模块：

```javascript
// errorHandler.js
const ERROR_MAP = {
  'NetworkError': { icon: '🌐', msg: '网络连接失败，请检查网络后重试', level: 'error' },
  'Unauthorized': { icon: '🔑', msg: 'API Key 无效，请到设置页重新配置', level: 'warning' },
  'RateLimited':  { icon: '⏳', msg: '请求太频繁，请稍后再试', level: 'warning' },
  'QuotaExhausted': { icon: '💰', msg: '免费额度已用完', level: 'info', action: 'showQuotaModal' },
  'ModelUnavailable': { icon: '⚠️', msg: '模型暂时不可用，请切换其他模型', level: 'warning' },
  'ServerError':  { icon: '❌', msg: '服务暂时出错，请重试', level: 'error' },
  'Unknown':      { icon: '❌', msg: '出了点问题，请重试', level: 'error' }
};

export function handleError(error) {
  const category = classifyError(error);
  const config = ERROR_MAP[category];
  showToast(config.icon + ' ' + config.msg, config.level);
  if (config.action) triggerAction(config.action);
}
```

---

## 八、安全风险清单

| 编号 | 风险 | 严重性 | 当前位置 | 修复方案 |
|------|------|--------|----------|----------|
| S1 | `/api/env` PUT 无认证直接写 .env | 🔴 高 | web_server.py | 添加认证 + key 白名单 |
| S2 | Markdown 渲染 XSS | 🔴 高 | ChatView.vue | DOMPurify 清理 |
| S3 | Provider API Key 明文存储在 localStorage | 🟡 中 | settings.js | 考虑加密存储或后端存储 |
| S4 | WebSocket 无认证 | 🟡 中 | web_server.py | 添加 token 校验 |
| S5 | 文件上传无大小限制检查 | 🟡 中 | web_server.py | 添加文件大小和类型校验 |
| S6 | 前端硬编码 localhost:5000 | 🟢 低 | ChatView.vue | 读取环境变量或配置 |

---

## 九、进化路线图

### Phase 1: 体验修复（1-2周）

**目标**: 零架构改动，纯 UX 修复，投入产出比最高。

- [ ] toast 通知替代所有 `alert()`
- [ ] 代码块添加"复制"按钮
- [ ] 消息添加"复制"和"重新生成"按钮
- [ ] 错误信息中文化 + 分类友好提示
- [ ] 输入框支持 Shift+Enter 多行
- [ ] DOMPurify 清理 Markdown 渲染
- [ ] 系统主题偏好检测

### Phase 2: 前端架构重构（2-4周）

**目标**: 可维护性提升，为后续功能迭代打基础。

- [ ] ChatView.vue 拆分为 6-8 个子组件
- [ ] chat.js store 拆分为 4-5 个独立 store
- [ ] Settings 页分层（推荐模式/高级模式）
- [ ] 侧边栏收起保留窄边栏
- [ ] storageService 封装
- [ ] 顶部栏信息分层精简

### Phase 3: 后端架构重构（3-6周）

**目标**: 可维护性和安全性提升。

- [ ] web_server.py 拆分为 Blueprint
- [ ] provider_aliases 自动生成
- [ ] 配额逻辑统一到后端
- [ ] `/api/env` 添加认证和白名单
- [ ] API 文档（docstring 或 FastAPI 迁移）
- [ ] WebSocket 认证

### Phase 4: 功能增强（1-2月）

**目标**: 功能丰富，提升用户粘性。

- [ ] 新手引导 3 步流程重做
- [ ] 公众号文章全自动创作 Skill
- [ ] PPT 自动生成 Skill
- [ ] AI 图片/海报生成 Skill
- [ ] 语音输入/输出
- [ ] 拖拽文件上传
- [ ] 消息虚拟滚动
- [ ] 搜索高亮跳转优化

### Phase 5: 生态完善（持续）

**目标**: 长期可维护性和扩展性。

- [ ] 前端 TypeScript 迁移（stores + services 优先）
- [ ] 前端单元测试（vitest）
- [ ] 性能监控
- [ ] run_agent.py 拆分
- [ ] tools/ 分类目录化
- [ ] 新增社交平台技能（微博/小红书/抖音）

---

## 十、总结

### 优势

Vermes 的核心引擎（Vermes）**能力非常强**：
- 107 个 Skill 覆盖 19 个领域
- 10+ 平台 Gateway 接入
- 80+ 工具实现
- 18+ LLM Provider 适配
- 完整的记忆、定时任务、子代理系统

这是一个**功能完备的 AI Agent 平台**。

### 最大短板

**前端是最大短板** — 对非技术用户来说：
1. 界面信息过载（顶部栏、设置页）
2. 错误提示不友好（`alert()` + 技术异常原文）
3. 关键操作缺失（复制、重新生成、多行输入）
4. 新手引导不足（2 步引导太简陋）

### 投入产出比最高的改进

**Phase 1 的 7 项体验修复**预计只需要 1-2 周工作量，但能显著提升：
- 用户留存率（减少因困惑流失）
- 日常使用效率（复制/重新生成）
- 品牌专业感（中文友好错误提示）

**建议立即启动 Phase 1。**

---

> 报告生成时间: 2026-05-29
> 生成者: Vermes AI Agent (MiMo-v2.5-Pro)
> 审查范围: ~/Projects/vermes/ 全栈代码
