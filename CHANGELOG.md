# Changelog

All notable changes to Vermes will be documented in this file.

## [v2.0.7] - 2026-06-03

### 上下文生命周期通道（status_callback 全链路）

后端 AIAgent 的 `_emit_status` / `_emit_warning` 一直在执行，但因 `status_callback=None` 全部丢弃。本次打通 SSE 通道，让用户看到压缩、生命周期等事件。

- **chat.py**: 新增 `status_callback(event_type, message)` 路由 lifecycle/warn → `_delta_queue` → SSE，赋值 `agent.status_callback`
- **api.js**: 新增 `onStatus` 参数 + SSE 循环处理 `lifecycle` / `warn` 事件类型
- **chat.js**: 新增 `statusMessages` ref + `lastTokenUsage` ref，onDone 保存 token 用量 + 清空状态，onError 同步清空
- **MessageList.vue**: 📦/⚠️ 状态消息条（fade-in 动画）+ `4.2K / 8.5K → 12.7K tokens` 底部用量显示

### 安全审查 P0/P1

- **P0-1** Agent 缓存 LRU: OrderedDict maxsize=20 + `pop_for_session()` 联动清理 (`dfb52f0`)
- **P0-2** 删除 TRIAL_EXPIRY 硬编码: 前后端全链路移除，免费模型无限免费 (`baf8afe`)
- **P0-4** GitHub Token 泄露: 吊销 + git filter-branch 重写历史
- **P1-5** `/api/env/reveal` 从公开白名单移除，改 session token 鉴权
- **P1-6** PROVIDERS 添加 cloud/free/recommended 字段 + `/api/config/cloud-models` 端点 + 前端动态拉取

### 安全修复（预发布审计 — 5 项）

- **P0-1/P0-6**: 全局异常处理器不再返回 `str(exc)`，改为通用 `"Internal server error"`，异常详情仅记日志
- **P0-4**: HistoryPanel.vue 搜索高亮先 HTML 实体转义再注入 `<mark>` 标签，防 XSS
- **P1-6**: SSE 错误消息入口 `stripHtml()` 防御纵深，剥离 HTML 标签
- **P1-7**: OAuth 窗口添加导航域白名单（vbit.top / weixin.qq.com 等7个），限制 `will-navigate/will-redirect`

### UI/UX 8 项优化

1. **微信登录直弹二维码**: WelcomeGuide 选微信→自动触发 WechatLogin 扫码→监听 `wechat-login-success`→自动领取
2. **消息气泡视觉区分**: AI 气泡 `border-l-[3px] border-green-400` 绿色左边框
3. **输入框空会话视觉强化**: placeholder "问我任何问题…" + 边框加深 + 空会话绿边
4. **模型选择器搜索+置顶**: 搜索框 + 最近 3 个置顶（localStorage `vermes-recent-models`）
5. **工具调用折叠**: >5 个工具默认折叠，"+N 更多"展开按钮
6. **侧边栏时间线/搜索**: 已有完整实现（无需改动）
7. **Settings Provider 搜索**: `providerSearch` 过滤推荐/中文/国际/自定义四组
8. **键盘快捷键**: ⌘K 聚焦 / ⌘N 新建 / ⌘B 侧边栏 / ⌘, 设置 / Esc 停止

### 额外修复

- **storage DB 路径**: `sessions/sessions.db` → `state.db`
- **会话 lastActive**: 新增字段，侧边栏按最后活跃时间排序分组
- **Settings 添加自定义提供商**: 底部"+ 添加自定义提供商"按钮
- **blueprints/__init__.py**: 补充 `storage` 模块导入
- **blueprints/storage.py**: `utils.config` → `hermes_cli.config`

### 更新概要展示

- `update.js`: 读取 `version.json` changelog 数组 → join 为 releaseNotes 字符串
- `App.vue`: 更新提示条增加"更新内容"按钮，展开显示逐条概要

### 项目清理

- 删除 `vermes-desktop/`（废弃 Tauri，3.2 GB）
- 删除 `vermes/`（原始项目，1.1 GB）
- 清理 dist-electron/mac/ + dist/ 缓存（~1.0 GB）
- **总计回收 ~5.4 GB**
- 唯一项目: `vermes-electron/`

### 判定不修（7 项）

SSE Queue 背压（桌面 localhost）、SSE 断线重连、stopGeneration 竞态、配额上报失败、web_server.py/auth.py 过大（P2 重构）、preload IPC 偶发不可用、CLOUD_MODELS 远程签名

---

## [v2.0.6] - 2026-06-02

### Electron 壳方案

- 替代 pywebview，解决 A11 Windows WebView2 崩溃
- Electron 仅作壳加载本地 SPA + 启动 Python 后端（uvicorn 子进程）
- 微信 OAuth BrowserWindow 子窗口 + will-redirect 监听 + 3 秒延迟关闭
- 全量删除 pywebview 代码，WechatLogin.vue 统一为 IPC 路径

### Agnes AI 接入

- 替换 DeepSeek/MiMo 为默认免费体验模型
- 文本/图片/视频三模态均正常
- One-API 渠道 id=3 Active

### Bug 修复

- DMG 黑屏: session.py 缺少 Request 导入 + PyInstaller 改用 venv Python 3.11
- 消息不保存: API 白名单补全
- 滚动失效: overflow-hidden → overflow-y-auto + min-h-0
- 微信登录白屏根因: `loadURL("数字")` → 修复为正确 OAuth URL
- 聊天 500: 旧后端无免费体验逻辑 + API Key 为空

### web_server.py 重构

- 6240→2830 行（-55%），拆出 blueprints 目录

### Windows 版本

- NSIS 安装包 `Vermes Setup 2.0.6.exe`（117MB）
- Python 3.12 后端（3.13 崩溃回退）
- perMachine: true（修复 WinRM 非交互问题）
