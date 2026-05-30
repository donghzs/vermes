# Vermes 全功能链路审查报告
> 2026-05-30 | Hermes 全面审查

---

## 📊 总览

| 模块 | 状态 | 严重 | 中等 | 低 |
|------|------|------|------|-----|
| 聊天核心 | ⚠️ | 4 | 3 | 0 |
| 会话管理 | ✅ | 0 | 1 | 3 |
| 设置+登录+配额 | ⚠️ | 1 | 1 | 2 |
| **合计** | | **5** | **5** | **5** |

---

## 🔴 严重问题（5项）

### 1. api.setToken() 不存在 — 桌面模式鉴权断链
- **位置**: chat.js:69 → api.js
- **问题**: `api.setToken(t)` 调用不存在的方法，桌面模式下 `X-Hermes-Session-Token` 永远为空
- **影响**: 所有 API 请求鉴权失败
- **修复**: api.js 加 `setToken(t) { token.value = t }`

### 2. regenerate 创建重复用户消息
- **位置**: MessageList.vue regenerate() → chat.js sendMessage()
- **问题**: regenerate 删除 assistant 消息后调 sendMessage，sendMessage 再次 push user 消息
- **影响**: 消息列表出现两条相同的 user 消息
- **修复**: regenerate 应标记"重新生成"让 sendMessage 跳过添加 user 消息

### 3. sendCompareMessage 模式下 stopGeneration 完全失效
- **位置**: chat.js:558 vs chat.js:626
- **问题**: 对比模式创建的 AbortController 是局部变量，未存入 `abortController.value`
- **影响**: 多模型对比时无法停止任何流
- **修复**: 存储所有 AbortController 到数组

### 4. stopGeneration 在线模式请求错误端点
- **位置**: chat.js:616
- **问题**: 硬编码 `/api/stop-generation`，在线模式应为 `/v1/stop-generation`
- **影响**: 在线模式停止按钮无效
- **修复**: 复用 request() 的 isOnline 前缀逻辑

### 5. _ENV_WRITE_ALLOWED_KEYS 白名单缺 12 个 key
- **位置**: web_server.py:1287-1292
- **问题**: xiaomi/gemini/groq/together/custom 等 12 个 provider 的 API Key 保存返回 403
- **影响**: 用户保存这些 key 时静默失败，实际调用时因缺少 key 报错
- **修复**: 将缺失的 key 加入白名单

---

## 🟡 中等问题（5项）

### 6. regenerate 不保留原始附件
- **位置**: MessageList.vue regenerate()
- **问题**: 只传文本，丢弃原始 user 消息的 attachments
- **修复**: 传递原始 attachments

### 7. stopGeneration 不持久化部分生成内容
- **位置**: chat.js stopGeneration()
- **问题**: 不调用 persistMessages()，刷新页面后部分回复丢失
- **修复**: 调用 persistMessages()

### 8. wechat.py except ImportError 应为 except Exception
- **位置**: blueprints/wechat.py:30, 80
- **问题**: 只捕获 ImportError，网络错误导致 500
- **修复**: 改为 except Exception

### 9. 导出会话丢失图片数据
- **位置**: chat.js exportSession()
- **问题**: 只读 localStorage 文本（"🖼️ 图片"占位符），不从 IndexedDB 取实际图片
- **修复**: 导出时从 IndexedDB 加载图片数据

### 10. 搜索性能无防抖
- **位置**: HistoryPanel.vue computed + chat.js searchAllMessages()
- **问题**: 每次按键触发全量 localStorage 扫描，50+ 会话时卡顿
- **修复**: 加 debounce 300ms

---

## 🟢 低优先级（5项）

### 11. 删除会话不清理 IndexedDB 图片
### 12. request() 重试逻辑会重试 POST SSE 请求
### 13. api.sendMessage 的 JSON.parse 异常被静默吞掉
### 14. vbit.top poll 响应是否含 openid 不可验证
### 15. 登录时未清理旧 openid 可能导致身份错位

---

## ✅ 确认正常的链路

| 功能 | 状态 |
|------|------|
| 消息发送 → SSE 流式 → 渲染 | ✅ |
| 工具卡片（tool_start/tool_end） | ✅ |
| thinking 事件 → 工具卡片 | ✅ |
| 流式状态条（⏳） | ✅ |
| 完成后紧凑时间线 | ✅ |
| 创建/切换/重命名/删除会话 | ✅ |
| 会话搜索（侧边栏+跨会话） | ✅ |
| 导入会话（JSON） | ✅ |
| 设置页 Provider 管理 | ✅ |
| 微信登录（QR+轮询+postMessage） | ✅ |
| 配额系统（三级检查） | ✅ |
| Blueprint 注册 | ✅ |
| 代码高亮 + 复制按钮 | ✅ |
| 消息编辑 | ✅ |
| 自更新前端+后端 | ✅ |
| PyInstaller 打包 | ✅ |

---

## 修复优先级建议

**立即修复（影响核心功能）：**
1. api.setToken() → 1行代码
2. regenerate 重复消息 → 3行代码
3. _ENV_WRITE_ALLOWED_KEYS 白名单 → 12行追加

**本周修复：**
4. sendCompareMessage AbortController 存储
5. stopGeneration 在线模式端点
6. wechat.py except 修复
7. stopGeneration persistMessages

**持续优化：**
8. 导出图片、搜索防抖、IndexedDB 清理等
