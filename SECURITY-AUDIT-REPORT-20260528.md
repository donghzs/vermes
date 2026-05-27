# 🔍 Vermes 安全协同审计报告

> **日期**：2026-05-28  
> **审计方**：QClaw（主审计）+ Hermes（协同复审）  
> **范围**：前端 8 文件 + 后端 web_server.py + 服务端 claim.js/quota.js/wechat.js  
> **仓库**：`~/Projects/vermes/`（本地）/ `donghzs/vermes`（GitHub）  
> **服务器**：`ubuntu@82.156.45.139`  

---

## 📊 审计总览

| 分类 | 数量 | 已修 | 不修 | 确认无问题 |
|------|------|------|------|-----------|
| 🔴 严重/安全 | 4 | 4 | 0 | 0 |
| 🟡 功能问题 | 8 | 8 | 0 | 0 |
| 🟢 代码质量 | 9 | 6 | 1 | 2 |
| ℹ️ 不修 | 4 | — | 4 | — |
| **合计** | **25** | **18** | **5** | **2** |

---

## 🔴 严重/安全漏洞（全部已修）

### S1 — wechat.js callback XSS 注入
- **问题**：微信 OAuth 回调页 `userName` 只转义单引号，未转义 `<>"&`。恶意微信昵称如 `</script><script>alert(1)</script>` 可执行任意 JS，盗取 token/openid
- **修复**：callback 页面不再内嵌任何用户数据，删除 postMessage 逻辑，前端完全依靠 `/api/wechat/poll` 轮询获取登录结果
- **影响**：用户体验零变化（弹窗扫码 → 自动关闭 → 主窗口自动登录）
- **文件**：`/opt/vbit/src/routes/wechat.js`（服务端已部署）

### S2 — wechat.js 硬编码密钥 fallback
- **问题**：第14-16行 `WECHAT_SECRET` 和 `ONEAPI_KEY` 有硬编码 fallback 值，源码泄露 = 密钥泄露
- **修复**：移除 fallback，未配置环境变量则 `process.exit(1)`
- **文件**：`/opt/vbit/src/routes/wechat.js`（服务端已部署）

### S3 — web_server.py 硬编码密钥 fallback
- **问题**：`VERMES_INTERNAL_SECRET` 默认值 `vermes_quota_secret_2026`，源码泄露 = 密钥泄露
- **修复**：移除 fallback，未配置环境变量则 `raise RuntimeError`
- **同时修复**：硬编码 IP `82.156.45.139:8083` 改为 `os.environ.get("ONEAPI_URL", "http://127.0.0.1:8083")`
- **文件**：`~/Projects/vermes/hermes_cli/web_server.py`（本地已改，未部署到服务器——这是桌面端代码）

### C4 — 弱内部密钥（合入 S3 修复）
- `vermes_quota_secret_2026` 已写入 `/opt/vbit/.env`，移除代码 fallback 即可
- ⚠️ **Hermes 注意**：建议后续轮换此密钥，当前保留是因为已有线上 token 在用

---

## 🟡 功能问题（全部已修）

### H1 — 事件监听器未清理
- **问题**：`model-changed`、`quota-updated`、`postMessage` 监听器在组件销毁时未 removeEventListener → 内存泄漏
- **修复**：`onUnmounted` 中统一清理三个监听器
- **文件**：`ChatView.vue`、`Settings.vue`

### H2/M11 — ID 碰撞
- **问题**：`Date.now().toString()` 作为 messageId/sessionId/attachmentId，高频操作时可能碰撞
- **修复**：新增 `uid()` 函数 = `Date.now().toString(36) + Math.random().toString(36).slice(2, 8)`
- **文件**：`chat.js`

### H4 — createSession 未 await switchSession
- **问题**：`createSession()` 调用 `switchSession()` 但未 await，导致状态更新可能延迟
- **修复**：`createSession` 和 `deleteSession` 改为 async，内部 await 所有异步调用
- **文件**：`chat.js`（第162、202-206、458-468行）

### H5 — 双重配额扣减
- **问题**：`checkQuota()` 本地 `useWechatQuota(1)` 和服务端 `/spend` 各扣一次积分
- **修复**：删除 `checkQuota()` 内的本地扣减，只保留服务端精确扣减路径
- **文件**：`chat.js`

### H7/M6 — sync-models SSRF
- **问题**：`/api/sync-models` 接受任意 base_url，可探测内网服务
- **修复**：强制 https + 从 PROVIDER_TEMPLATES 白名单解析 provider_id，未知域名不发送 API Key
- **文件**：`web_server.py`

### H8 — ObjectURL 未 revoke
- **问题**：图片上传创建 `URL.createObjectURL` 但从不清除 → 内存泄漏
- **修复**：三处 revoke 清理
  - `removeFile()` — 单个删除时立即 revoke
  - `send()` — 发送后清空 uploadedFiles 前先 revoke 所有
  - `onUnmounted` — 组件销毁时清理残留
- **文件**：`ChatView.vue`

### M7 — pollTimer 竞态
- **问题**：并发 fetch 回调可能多次触发 `onWeChatLogin`
- **修复**：新增 `isPollingActive` guard，fetch 成功后立即置 false 防止重复触发
- **文件**：`ChatView.vue`

### M2 — token 创建参数不一致
- **问题**：wechat.js 创建 `remain_quota:3500000` vs web_server.py fallback `remain_quota:500`
- **修复**：统一为 `remain_quota:3500000, models:'deepseek-v4-flash,mimo-v2.5'`
- **文件**：`web_server.py`、`wechat.js`

---

## 🟢 代码质量（6已修 + 1不修 + 2确认无问题）

### L1 — 死代码清理 ✅
- 删除 `loadQR()` 函数（仅转发到 openWeChatQR）
- 删除 `qrCodeDataUrl` ref（无人设值）
- 删除 `reportQuotaSpend()` 函数及 import（不再被调用）
- **文件**：`ChatView.vue`、`api.js`、`chat.js`

### L3 — router base path ✅
- `createWebHistory('/vermes/')` → `createWebHistory('/')`
- FastAPI 后端在根路径 serve SPA，无需前缀
- **文件**：`frontend/src/router/index.js`

### L4 — wechatSessions 内存泄漏 ✅
- 新增 `setInterval` 每分钟清理 >5 分钟的 session
- **文件**：`/opt/vbit/src/routes/wechat.js`（服务端已部署）

### M3/M4 — 无用依赖 ✅
- `npm uninstall axios marked`（均未在 src/ 中使用）
- 减少 25 个包，JS bundle 基本持平（tree-shaking 已处理）
- **文件**：`package.json`、`package-lock.json`

### L2 — 小米 base_url 不一致 ℹ️ 不修
- Settings.vue 用 `https://api.xiaomimimo.com/v1` vs 后端用 `https://token-plan-cn.xiaomimimo.com`
- 用户决定不修，不影响功能

### L5 — claim.js IP限频查询 ✅ 确认无问题
- 审计时误以为查 `daily_spend` 表，实际查 `device_claims` 表按 `ip_address` 过滤，逻辑正确

### M1 — /api/env 公开写入 ✅ 确认无问题
- 桌面本机应用，非远程暴露

---

## ℹ️ 用户决定不修（4项）

| 编号 | 问题 | 原因 |
|------|------|------|
| C1 | markdown-it html:true + v-html XSS | 单用户桌面应用，无跨用户风险 |
| C2 | /api/env 公开可写 | 桌面本机，外部无法访问 |
| M1 | 试用日期硬编码 | 功能正常，改了反可能出 bug |
| L2 | provider 命名不一致 | 不影响功能 |

---

## 🔧 未提交的改动（commit c32c539 之后）

```
frontend/dist/index.html             |   4 +-
frontend/package-lock.json           | 322 +----
frontend/package.json                |   2 -
frontend/src/components/ChatView.vue |  25 ++-
frontend/src/router/index.js         |   2 +-
frontend/src/services/api.js         |  12 --
frontend/src/stores/chat.js          |  14 +-
7 files changed, 31 insertions(+), 350 deletions(-)
```

### 具体改动清单
1. **chat.js**：移除 reportQuotaSpend import；createSession/deleteSession 改 async + await；init() 中 createSession 加 await
2. **ChatView.vue**：删除 loadQR()、qrCodeDataUrl；添加 isPollingActive guard；ObjectURL 三处 revoke 清理；onUnmounted 清理残留 ObjectURL
3. **api.js**：删除 reportQuotaSpend 函数定义
4. **router/index.js**：`/vermes/` → `/`
5. **package.json**：移除 axios、marked
6. **package-lock.json**：同步更新（减 25 包）
7. **dist/**：重新构建

---

## 📋 Hermes 协同审计要点

请 Hermes 重点关注以下方面：

### 1. 确认修复完整性
- 上述修复是否覆盖了 Hermes 审计中发现的所有 CRITICAL 和 HIGH 问题？
- 是否有 Hermes 发现但 QClaw 审计未覆盖的问题？

### 2. 交叉验证
- `web_server.py` 的硬编码移除是否影响了桌面端正常功能？
- `chat.js` 的 async/await 改动是否与 Hermes 的流式输出修改兼容？
- ObjectURL 清理逻辑是否会误清理正在使用的 URL？

### 3. 服务端一致性
- 服务端 wechat.js 的修复已部署并验证（健康检查通过）
- `web_server.py` 是桌面端代码，只在用户本地运行，**服务器不需要部署此文件**
- 确认 `/opt/vbit/.env` 中的密钥配置是否完整（WECHAT_SECRET、ONEAPI_KEY、VERMES_INTERNAL_SECRET）

### 4. 前端构建
- 当前构建产物：251.92KB JS / 105.62KB gzip / 27.72KB CSS
- dist/ 已构建但**未部署到服务器**（`/var/www/html/vermes/`）
- 建议 Hermes 验证 `npx vite build` 无错误后部署

---

## 🗂 关键文件位置

| 文件 | 路径 | 修改方 |
|------|------|--------|
| ChatView.vue | `~/Projects/vermes/frontend/src/components/ChatView.vue` | QClaw |
| chat.js | `~/Projects/vermes/frontend/src/stores/chat.js` | QClaw |
| api.js | `~/Projects/vermes/frontend/src/services/api.js` | QClaw |
| Settings.vue | `~/Projects/vermes/frontend/src/components/Settings.vue` | QClaw |
| router/index.js | `~/Projects/vermes/frontend/src/router/index.js` | QClaw |
| web_server.py | `~/Projects/vermes/hermes_cli/web_server.py` | QClaw |
| package.json | `~/Projects/vermes/frontend/package.json` | QClaw |
| wechat.js | `/opt/vbit/src/routes/wechat.js` | QClaw（服务端已部署） |
| claim.js | `/opt/vbit/src/routes/claim.js` | QClaw（服务端已部署） |
| quota.js | `/opt/vbit/src/routes/quota.js` | QClaw（服务端已部署） |

---

## 🔑 关键凭证

| 项目 | 值 |
|------|-----|
| SSH | `ubuntu@82.156.45.139` / `Cluster@2026` |
| One-API 内部 | `127.0.0.1:8083` |
| One-API Admin Token | `6fbe226e03f04d5aa1fe320918a0c0c9` |
| PostgreSQL | `postgresql://vbit:vbit_Secure2026@localhost:5432/vbit_auth` |
| VERMES_INTERNAL_SECRET | 在 `/opt/vbit/.env` 中（已移除代码 fallback） |
| WECHAT_SECRET | 在 `/opt/vbit/.env` 中（已移除代码 fallback） |
| ONEAPI_KEY | 在 `/opt/vbit/.env` 中（已移除代码 fallback） |

---

## 📌 下一步

- [ ] Hermes 协同审计：交叉验证修复完整性
- [ ] Git commit 未提交的改动（H4/L1/L3/M3/M4/M7/H8）
- [ ] 前端 dist 部署到服务器
- [ ] 打包新版本 DMG
- [ ] Windows 构建（需在 Windows 环境执行）
- [ ] Git push（用户要求构建发布时再一起 push）

---

*报告生成时间：2026-05-28 03:59 CST*
