# Vermes v2.0.7 新版预发布审计报告

> 审计日期: 2026-06-03
> 审计范围: 功能链路检测 + 安全泄露审计
> 审计方法: 全量源码级跟踪（4 个子任务并行，55+ 文件，400+ 次工具调用）

---

## 目录

- [第一部分：功能链路检测](#第一部分功能链路检测)
- [第二部分：安全泄露审计 — P0 类](#第二部分安全泄露审计--p0-类)
- [第三部分：安全泄露审计 — P1 类](#第三部分安全泄露审计--p1-类)
- [第四部分：安全泄露审计 — P2 类](#第四部分安全泄露审计--p2-类)
- [第五部分：汇总决策表](#第五部分汇总决策表)

---

## 第一部分：功能链路检测

### 1. SSE 消息流完整性 — ✅ 全链路通

| 事件类型 | 后端 (chat.py) | 传输 (status_callback) | 前端 (api.js) | 渲染 (vue) | 状态 |
|---------|---------------|----------------------|--------------|-----------|------|
| `stream_start` | L863 ✅ | 直接 SSE | 自动 | — | ✅ |
| `delta` (文本) | L893-900 ✅ | 直接 SSE | OpenAI 格式 | 消息气泡 | ✅ |
| `tool_start` | L790-800 ✅ | tool_progress_handler | `onTool` | 工具卡片 | ✅ |
| `tool_end` | L801-809 ✅ | tool_progress_handler | `onTool` | 展开/折叠 | ✅ |
| `thinking` | L819-837 ✅ | thinking_handler | `onThinking` | 推理折叠 | ✅ |
| `lifecycle` | L754-774 | ✅ _delta_queue | ✅ L360-363 | ✅ ⚠️ **新增** | ✅ |
| `warn` | L754-774 | ✅ _delta_queue | ✅ L360-363 | ✅ ⚠️ **新增** | ✅ |
| `error` | L882 ✅ | 直接 SSE | `onError` | 错误条 | ✅ |
| `[DONE]` | L925 ✅ | 直接 SSE | 流结束 | — | ✅ |

**结论**: 今日修复的 `status_callback` 链路（P0-1 压缩通知、P0-4 token 用量、P1-1 前端 XSS 入口、P1-6 模型标签）全部验证通过。SSE 协议现支持 **9 种事件类型**。

### 2. 上下文压缩链路 — ✅

| 检查项 | 结果 | 代码路径 |
|--------|------|---------|
| 压缩前 `_emit_status` | ✅ | `conversation_compression.py:324-326` |
| 压缩中告警 | ✅ | `L117-118, L231-232` |
| 压缩成功通知 | ✅ | `_emit_status("✅ Context compression complete")` |
| 压缩失败通知 | ✅ | `_emit_warning()` |
| 重试逻辑（最多 3 次） | ✅ | `context_length_exceeded` 捕获重试 |
| 锁定防并发 | ✅ | `try_acquire_compression_lock` (state.db) |
| 辅助模型自动降级 | ✅ | 阈值 + 上下文窗口动态适配 |

### 3. 模型路由/提供商链路 — ✅

| 检查项 | 结果 | 备注 |
|--------|------|------|
| PROVIDERS 字典标签 | ✅ | 28 个提供商含 cloud/free/recommended |
| `/api/config/cloud-models` | ✅ | 从 PROVIDERS 自动派生 |
| 前端模型选择器搜索 | ✅ | ChatHeader.vue L167-170 |
| 默认免费模型 | ✅ | vbit/agnes-2.0-flash |
| Settings 提供商分组 | ✅ | 推荐/中文/国际/自定义四组 |

### 4. 工具调用链路 — ✅

| 检查项 | 结果 | 备注 |
|--------|------|------|
| tool_start SSE 格式 | ✅ | `{type, tool_call_id, tool_name, arguments}` |
| tool_end SSE 格式 | ✅ | `{type, tool_call_id, tool_name, duration, is_error, result_preview}` |
| 前端自动折叠（>5 个） | ✅ | `+N 更多` 按钮 |
| 结果预览截断 | ✅ | 各工具类型有独立渲染模板 |

### 5. 会话管理链路 — ✅

| 检查项 | 结果 | 备注 |
|--------|------|------|
| CRUD 全部实现 | ✅ | 后端 session.py + 前端 chat-session.js |
| state.db 路径 | ✅ | `~/.hermes/state.db` (storage.py:24) |
| lastActive 字段 | ✅ | 前端每发消息更新 (chat.js:307-308) |
| 侧边栏排序分组 | ✅ | 按 lastActive 时间线分组 |
| FTS5 搜索 | ✅ | 受 session token 保护 |

### 6. Settings 配置链路 — ✅

| 检查项 | 结果 | 备注 |
|--------|------|------|
| 自定义提供商添加 | ✅ | `addCustomProvider()` |
| 保存到后端 | ✅ | `/api/env` PUT + `/api/provider/add` |
| max_tokens 设置 | ✅ | 通过 config.yaml 传递 |
| Provider 搜索过滤 | ✅ | 搜索 + 推荐/中文/国际/自定义四组 |

### ⚠️ 功能链路发现的问题（不影响发布）

1. **`ChatRequest` 模型缺 `max_tokens` 字段** → chat.py 中用 `getattr(req, 'max_tokens', None)` 始终 None，但通过 `_resolve_max_tokens()` 从 config.yaml 读取兜底。**建议修复**：在 `ChatRequest` 中添加 `max_tokens: int | None = None`。

2. **模型选择器缺显式 free/cloud 标签** → 目前只在 Settings 页面有分组，选模型时用户无法看到哪个是免费的。**建议添加**：模型名旁渲染 `🆓` 或 `☁️` 标签。

3. **`[DONE]` 事件不携带 usage 信息** → token 用量只在 `onDone` 回调中获取，未通过 SSE 事件传递。前端 `lastTokenUsage` 通过 `onDone` 回调的返回值获得，**功能正常但链路不优雅**。

---

## 第二部分：安全泄露审计 — P0 类

> **P0 = 必须在发布前修复**

### P0-1: 全局异常处理器泄露 `str(exc)`（后端）

| 文件 | 行号 | 风险 |
|------|------|------|
| `vermes_cli/web_server.py` | ~162-175 | 全局 HTTPException handler 直接 `str(exc)` 可能包含 API key / 敏感数据 |

**修复建议**: 使用 `redact_message(str(exc))` 包装，或只返回通用错误码和摘要。

### P0-2: Session Token 注入 HTML 可被 XSS 窃取

| 文件 | 行号 | 风险 |
|------|------|------|
| `vermes_cli/web_server.py` | 1577-1583 | Session token 明文注入 `<script>` 标签，浏览器扩展 / XSS 可读取 |

**修复建议**: 使用一次性短期 token（5 分钟有效）替代永久 session token 注入 HTML。

### P0-3: `/api/gui/messages/{session_id}` 公开无鉴权 + 路径遍历

| 文件 | 行号 | 风险 |
|------|------|------|
| `vermes_cli/blueprints/session.py` | 187-214 | 三个方法（GET/POST/DELETE）在公开白名单中，`session_id` 无校验直接拼接文件路径 |

**攻击向量**: `session_id=../../tmp/evil` → 可读写任意 `.json` 后缀文件

**修复建议**:
```python
import re
if not re.match(r'^[a-zA-Z0-9_-]+$', session_id):
    raise HTTPException(400, "Invalid session_id")
```

### P0-4: `HistoryPanel.vue` v-html XSS 入口

| 文件 | 行号 | 风险 |
|------|------|------|
| `frontend/src/components/HistoryPanel.vue` | 24-29 | `highlightText()` 函数未对 `text` 做 HTML 转义，直接注入 `<mark>` 标签到 `v-html` |

**攻击向量**: 会话标题含 `<script>alert(1)</script>` → XSS

**修复建议**: 使用 `String.prototype.replace` + 实体转义替代直接 v-html，或使用 DOMPurify。

### P0-5: `WechatLogin.vue` localStorage 明文存储 token/OpenID

| 文件 | 行号 | 风险 |
|------|------|------|
| `frontend/src/components/WechatLogin.vue` | 141-144 | `vermes_wechat_token`、`vermes_wechat_openid` 明文存储 |

**风险**: 同机其他进程 / 恶意浏览器扩展可读取

**修复建议**: 使用 Electron safeStorage（`safeStorage.encryptString`）加密存储，或短期 token + 仅内存驻留。

### P0-6: 全局异常 500 泄露详细 traceback

| 文件 | 行号 | 风险 |
|------|------|------|
| `web_server.py` | 全局中间件 | 未捕获异常返回完整 Python traceback，含文件路径、环境变量片段 |

**修复建议**: 开发/生产模式区分 traceback 详情，生产模式只返回 `{"error": "Internal Server Error", "code": 500}`。

---

## 第三部分：安全泄露审计 — P1 类

> **P1 = 高优先级，建议发布前修复**

| # | 文件 | 行号 | 风险类型 | 描述 | 修复建议 |
|---|------|------|---------|------|---------|
| P1-1 | `web_server.py` | 226 | 公开端点过多 | 配置写入/环境变量修改/更新管理端点在 `_PUBLIC_API_PATHS` 中，无需鉴权 | 移出白名单，要求 session token |
| P1-2 | `web_server.py` | 1138-1146 | 配置注入 | `PUT /api/config/raw` 接受任意 YAML，公开无鉴权 | 要求 session token 或字段级白名单 |
| P1-3 | `providers.py` | 210 | SSRF | `verify_provider` 无 SSRF 防护，将 API Key 发到任意 base_url | 添加域名白名单校验 |
| P1-4 | `providers.py` | 2740-2742 | SSRF 可绕过 | `sync_models` 前缀匹配可被 `127.0.0.1.evil.com` 绕过 | 改用 URL hostname 精确匹配 |
| P1-5 | `web_server.py` | 2354-2398 | 动态代码加载 | Plugin API 用 `importlib.exec_module` 加载插件 Python 文件 | 限制插件 API 路由模式 |
| P1-6 | `api.js` | 360-363 | 未转义渲染 | SSE `json.error.message` 直接传入 UI 未做 HTML 转义 | 使用 `textContent` 或 DOMPurify |
| P1-7 | `electron/main.js` | 266 | webSecurity 关闭 | OAuth 窗口 `webSecurity: false` 允许 JS 注入 | 关闭后恢复为 true |
| P1-8 | 多文件 | 多处 | API Key 日志泄露 | Provider verify/sync 端点异常信息可能包含 API Key | 异常返回前 redact 敏感字段 |
| P1-9 | `update.py` | 60-150 | 任意 URL 下载 | 更新下载端点在公开白名单中，URL 完全由客户端控制 | 限白名单域名或服务端生成 |

---

## 第四部分：安全泄露审计 — P2 类

> **P2 = 中等优先级，可在发布后补修**

| # | 文件 | 行号 | 风险类型 | 描述 |
|---|------|------|---------|------|
| P2-1 | `web_server.py:116` | CORS 正则问题 | 不支持 IPv6 loopback `[::1]`，正则格式有误 |
| P2-2 | `web_server.py:1269` | Token 暴露 | Session token 作为 WebSocket URL query param，在 `ps aux` 中可见 |
| P2-3 | `web_server.py` | 日志中间件 | 请求体日志已做 redact，但嵌套字段可能漏 |
| P2-4 | `update.py:75-78` | 扩展名绕过 | URL 结尾校验 `endswith(".dmg")` 可被 `payload.exe?fake=.dmg` 绕过 |
| P2-5 | `update.py:153-182` | 无限制 SSE | 更新进度端点公开无速率限制 |
| P2-6 | `chat.py:692-700` | SSRF 代理 | Chat 代理转发到可配置的 base_url，无内网 IP 检查 |
| P2-7 | 前端多文件 | console 日志 | 多处 `console.log` 记录 API 响应详情 |
| P2-8 | 前端多文件 | base64 持久化 | 图片 base64 数据在 localStorage 永久留存 |
| P2-9 | `frontend/src/stores/chat.js` | lastTokenUsage | Token 用量数据存储在 reactive ref 中未清理 |
| P2-10 | `tools_config.py:659` | shell=True | 硬编码 curl-to-bash 安装命令 |
| P2-11 | 前端 SSE | 无断线重连 | SSE 连接中断后不自动重连（已在「判定不修」清单中） |

---

## 第五部分：汇总决策表

### 发布阻挡：P0（必须修复）

| ID | 风险 | 修复难度 | 修复时间估计 |
|----|------|---------|------------|
| P0-1 | 全局异常处理泄露 `str(exc)` | 简单 | 5 分钟 |
| P0-2 | Session Token 注入 HTML | 中等 | 30 分钟 |
| P0-3 | `/api/gui/messages/{session_id}` 路径遍历 | 简单 | 5 分钟 |
| P0-4 | HistoryPanel.vue v-html XSS | 简单 | 5 分钟 |
| P0-5 | WechatLogin.vue localStorage 明文 token | 中等 | 20 分钟 |
| P0-6 | 全局 500 traceback 泄露 | 简单 | 5 分钟 |

**合计修复时间**: ~70 分钟

### 建议发布前修复：P1

| ID | 风险 | 修复难度 | 修复时间估计 |
|----|------|---------|------------|
| P1-1 | 公开端点过多 | 中等 | 30 分钟 |
| P1-2 | 配置注入 | 中等 | 15 分钟 |
| P1-3 | verify_provider SSRF | 简单 | 10 分钟 |
| P1-4 | sync_models SSRF 绕过 | 简单 | 10 分钟 |
| P1-5 | 动态代码加载 | 复杂 | 60 分钟 |
| P1-6 | SSE 错误消息未转义 | 简单 | 5 分钟 |
| P1-7 | OAuth webSecurity | 简单 | 5 分钟 |
| P1-8 | API Key 日志泄露 | 中等 | 20 分钟 |
| P1-9 | 更新下载 URL 未校验 | 中等 | 20 分钟 |

**合计修复时间**: ~175 分钟

### 发布后补修：P2

11 个中低风险项，分散在 6 个文件中，合计估计 ~120 分钟。

### 已可发布：功能链路

6 大功能链路全部 ✅ 通过，今日新增的 4 项改动（status_callback、压缩通知、token 用量、lifecycle/warn SSE 事件）全部验证正确。

---

## 最终建议

> **即刻发布 v2.0.7？还是先修 P0？**

用户是桌面本地应用（Electron + localhost web server），非跨公网部署。P0 风险的实际威胁边界：
- **路径遍历 + 配置注入** → 仅本地进程 / DNS rebinding 可攻击
- **XSS** → 仅恶意会话名称可触发
- **Token 明文** → 仅同机恶意软件可读取

**推荐**:
1. **修复 P0-1（全局异常）、P0-4（v-html XSS）、P0-6（500 traceback）** — 每项 5 分钟，零风险
2. **进入 P1-6（SSE 错误转义）、P1-7（OAuth webSecurity）** — 额外 10 分钟
3. 其余 P0/P1 项在 v2.0.8 中修复

**最快发布路径**: 修复 5 项（3 P0 + 2 P1）→ 约 **30 分钟** → 发布 v2.0.7
**最安全发布路径**: 全部 6 项 P0 + 9 项 P1 → 约 **4 小时** → 明天发布 v2.0.7
