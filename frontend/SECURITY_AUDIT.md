# 前端安全审计报告

**项目:** /Users/dongzusheng/Projects/vermes-electron/frontend  
**日期:** 2026-06-03  
**范围:** XSS、敏感信息泄露、SSE 事件处理、Electron 安全配置

---

## P0 — 高危 (Critical)

### P0-1: v-html + highlightText 未做 HTML 转义
- **文件:** `src/components/HistoryPanel.vue`
- **行号:** 24-29 (函数定义), 79 (调用)
- **风险描述:** `highlightText()` 函数通过 `v-html` 渲染搜索结果片段。函数将用户**原始消息内容**中匹配关键词的部分用 `<mark>...</mark>` 包裹后直接插入 DOM，期间未对 `text` 做 HTML 转义。如果用户发送的消息中包含 HTML 标签（如 `<img src=x onerror=alert(1)>`），且搜索关键词匹配到该部分，则恶意 HTML 会被渲染执行。
- **修复建议:**
  ```js
  // 使用 DOMPurify 或 escapeHtml 对 text 做转义后再注入 mark 标签
  function highlightText(text, keyword) {
    if (!keyword || !text) return text
    const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(`(${escaped})`, 'gi')
    const safeText = String(text).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    return safeText.replace(regex, '<mark class="bg-yellow-200 dark:bg-yellow-800 ...">$1</mark>')
  }
  ```

### P0-2: localStorage 明文存储敏感凭据
- **文件:** `src/components/WechatLogin.vue`
- **行号:** 141-144
- **风险描述:** WeChat OAuth token (`vermes_wechat_token`)、OpenID (`vermes_wechat_openid`)、用户名 (`vermes_wechat_name`)、头像 URL (`vermes_wechat_avatar`) 全部以明文存储在 localStorage 中。Electron 的 localStorage 存储在磁盘上未加密的文件中，任何有磁盘访问权限的进程都可以读取。
- **修复建议:**
  1. 在储存前使用 Electron `safeStorage.encryptString()`（主进程）加密 token
  2. 或使用 session cookie（非持久化）替代 localStorage 存储敏感 token
  3. 至少对 `vermes_token` 和 `vermes_wechat_token` 实施加密存储

### P0-3: 微信登录 token 同时通过 localStorage 和 POST body 泄露
- **文件:** `src/services/api.js`
- **行号:** 145-146, 264-265
- **风险描述:**
  - `buildHeaders()` (L145-146) 从 localStorage 读取 `vermes_wechat_token` 并直接放入 `Authorization: Bearer <token>` 请求头
  - `sendMessage()` (L264-265) 将 `vermes_wechat_openid` 明文放入 POST body 发送到远端
- **修复建议:** 确保所有 API 请求均通过 HTTPS 传输（Electron 主窗口中访问 `127.0.0.1` 是本地环回）。对于在线模式，考虑使用短期 session token 替代持久化的 Bearer token。

### P0-4: SSE 事件中未转义的错误消息直接传递到 UI
- **文件:** `src/services/api.js`
- **行号:** 338-340
- **风险描述:** SSE 流中 `json.error` 中的 `message` 字段未做任何清理直接传入 `onError`。这些错误消息可能来自上游 AI 服务端（如 One-API），如果上游返回恶意响应，错误消息中的 HTML/JS 代码可能通过 `onError` 回调的 `am.content` 赋值最终渲染到页面上。
- **修复建议:** 在 `onError` 回调（`chat.js` L416-428）中对 `err.message` 使用 `escapeHtml()` 或通过 DOMPurify 过滤后再赋值到 `am.content`。

---

## P1 — 中危 (High)

### P1-1: 前端 console.warn/error 可能泄露 API 响应详情
- **文件:**
  - `src/services/api.js` L46, L72, L308, L390, L405, L413
  - `src/stores/chat.js` L418
  - `src/main.js` L17, L31
- **风险描述:** 多个错误处理路径中将完整的错误消息、API 响应文本写入 `console` 日志。这些日志可能包含服务器返回的敏感信息（如配额详情、API key 错误提示）。在现代 Electron 中，用户可通过 DevTools 查看控制台输出。
- **修复建议:** 生产构建时 strip console 调用（通过 Terser/Vite），或确保错误日志中不包含敏感 payload。

### P1-2: SSE 工具调用结果 (tool_end) 可能泄露敏感数据
- **文件:** `src/services/api.js` L377
- **行号:** 377
- **风险描述:** `tool_end` 事件的 `result_preview` 字段直接从 SSE 数据解析后存储到 Pinia store 的 `toolInvocations` 中，然后通过 `MessageList.vue` 的 `formatToolPreview()` 渲染。虽然 Vue 的 `{{ }}` 文本插值本身安全，但 `result_preview` 可能包含用户文件内容、代码、服务器路径等敏感信息。
- **修复建议:** 在渲染工具结果时，对过长或可能包含敏感信息的结果内容进行截断（当前已有 500 字符裁剪，但建议在前端渲染层也做限制）。

### P1-3: OAuth 窗口禁用 webSecurity
- **文件:** `electron/main.js`
- **行号:** 266
- **风险描述:** 微信 OAuth 窗口设置了 `webSecurity: false`，这会禁用同源策略。虽然注释说明是为了连接 `localhost.weixin.qq.com`（微信桌面本地服务），但这个配置使得 OAuth 窗口可以轻易被 XSS 攻击，攻击者可以读取跨域资源。
- **修复建议:**
  1. 评估是否真正需要 `webSecurity: false`
  2. 如果可以优先使用 `preload.js` + IPC 通信方案替代直接加载微信 OAuth URL
  3. 至少为 OAuth 窗口设置 `nativeWindowOpen: true` 和严格 CSP

### P1-4: 聊天消息内容中有 base64 图片数据持久化
- **文件:**
  - `src/stores/chat.js` L119-126
  - `src/stores/chat-storage.js` (全文)
- **风险描述:** 用户发送的 base64 图片数据在 `beforeunload` 事件中被序列化到 `localStorage`（`vermes-msgs-*` key）。大 base64 数据占用大量 localStorage 空间，且图片内容可能包含敏感信息（截图、文档等）。消息也存储在 IndexedDB 中。
- **修复建议:** IndexedDB 存储图片的方式优于 localStorage，已实现了 `stripBase64FromContent` 分离图片和文本。但建议确保迁移完成后彻底清除 localStorage 中的 base64 数据。

### P1-5: HTML 页面中嵌入的 Session Token 可通过前端 JS 提取
- **文件:** `src/stores/chat.js`
- **行号:** 133-143 (`fetchToken()`)
- **风险描述:** `fetchToken()` 请求首页 HTML 并用正则提取 `window.__vermes_SESSION_TOKEN__` 或 `window.__OPENCLAW_SESSION_KEY__`。该 token 随后通过 `api.setToken()` 存储并在 `X-Vermes-Session-Token` 请求头中发送。如果攻击者可以读取服务器 HTML（通过 XSS 或 MitM），就可以窃取该 token。
- **修复建议:** 考虑使用更安全的 token 交换机制，如短期 token + refresh token，或通过 `meta` 标签的 `http-equiv` CSP 限制。

### P1-6: console.error 中输出完整错误对象
- **文件:** `src/main.js`
- **行号:** 17, 31
- **风险描述:** `app.config.errorHandler` 和 `window.onerror` 将完整错误信息（包括 `err.stack`）输出到控制台和页面。虽然使用了 `escapeHtml()` 页面渲染，但控制台输出未受保护。
- **修复建议:** 生产环境移除 console 调用或限制错误输出的详细程度。

---

## P2 — 低危 (Medium)

### P2-1: localStorage 持久化大量 API 提供商配置
- **文件:** `src/components/Settings.vue`
- **行号:** 220-228
- **风险描述:** `saveProvidersToStorage()` 将所有提供商配置（包括 baseUrl、模型列表）持久化到 `vermes-providers` key 中。API keys 在存储时已替换为 `'***saved***'` 占位符（L225），因此 key 本身未暴露。但提供商配置信息仍可能泄露后端架构信息。
- **修复建议:** 当前设计已较安全，API keys 存储在内存中而非 localStorage。确保 `'***saved***'` 模式在所有路径一致。

### P2-2: 用户头像 URL 可能为 data: URI 导致 XSS
- **文件:** `src/components/ChatHeader.vue`
- **行号:** 122-123
- **风险描述:** `<img :src="userAvatar">` 直接渲染用户头像 URL。如果 `userAvatar` 被篡改为 `javascript:` 或 `data:text/html` 等 URI，浏览器可能解析执行。但 `<img>` 标签的 `src` 属性通常不执行脚本，风险较低。
- **修复建议:** 验证 `userAvatar` 仅允许 `https://` 协议或以 `data:image/` 开头的安全 URI。

### P2-3: stopGeneration 中 token 通过 URL 查询参数发送
- **文件:** `src/stores/chat.js`
- **行号:** 571-578
- **风险描述:** `stopGeneration()` 函数从 localStorage 读取 token 并放在 `X-Vermes-Session-Token` 请求头中发送到 `/api/stop-generation`。尽管是本地环回地址，token 在网络层仍然可见。
- **修复建议:** 安全性可接受（本地网络），但建议使用服务端 session 验证替代每次请求都携带完整 token。

### P2-4: markdown-it 配置允许链接自动转换为可点击链接
- **文件:** `src/components/MessageList.vue`
- **行号:** 141-169
- **风险描述:** markdown-it 配置了 `linkify: true`，这意味着任何 URL 格式的文本都会自动转换为可点击的 `<a>` 标签。虽然这些链接经过 `DOMPurify.sanitize()` 过滤，并且通过 `renderMd` 设置了 `rel="noopener noreferrer"` 和 `target="_blank"`，但用户仍可能被诱导点击外部链接。
- **修复建议:** 当前已有良好的保护措施（DOMPurify + noopener），但建议考虑添加安全配置：`linkify: false` 或仅允许白名单域名可点击。

### P2-5: 搜索功能可能在 URL 参数中暴露搜索词
- **文件:** `src/components/HistoryPanel.vue`
- **行号:** 8-21 (搜索逻辑)
- **风险描述:** 搜索结果高亮功能（`highlightText`）仅在客户端侧进行，搜索词不通过 URL 参数传递。但搜索词存储在 Pinia store 的 `chat.searchAllMessages()` 调用中，如果未来添加 URL 搜索参数功能需注意。
- **修复建议:** 当前无风险，仅为未来开发的提醒。

---

## 风险统计

| 级别 | 数量 | 关键风险项 |
|------|------|-----------|
| P0   | 4    | v-html XSS、明文 Token、敏感数据在请求体中、SSE 错误消息无过滤 |
| P1   | 6    | console 泄露、工具结果敏感信息、OAuth webSecurity、base64 持久化、Session Token 提取、错误输出 |
| P2   | 5    | Provider 配置泄露、头像 URI、Token 在请求头、linkify 风险、搜索词暴露 |

---

## 关键修复优先级

1. **最高优先级** — HistoryPanel.vue `highlightText` XSS 修复（P0-1）
2. **高优先级** — localStorage 敏感数据加密存储（P0-2）
3. **高优先级** — SSE `json.error` 消息安全处理（P0-4）
4. **中优先级** — OAuth 窗口 `webSecurity: false` 评估修复（P1-3）
5. **中优先级** — Console 日志清理（P1-1）
