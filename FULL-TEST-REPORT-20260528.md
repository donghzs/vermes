# Vermes 全链路测试报告

> **测试时间**：2026-05-28 04:22 CST  
> **测试范围**：前端 + 后端 + 云服务器 + One-API + 微信OAuth + 积分系统 + SSL/安全头  
> **测试方式**：端到端模拟用户场景，覆盖所有模块  

---

## 测试环境

| 项目 | 值 |
|------|-----|
| 服务器 | `82.156.45.139` (ubuntu@82.156.45.139 / Cluster@2026) |
| 前端产品页 | https://vbit.top/vermes/ |
| API 后端 | https://vbit.top/api/ (proxy→127.0.0.1:3001) |
| One-API | https://api.vbit.top/ (proxy→127.0.0.1:8083) |
| PostgreSQL | postgresql://vbit:vbit_Secure2026@localhost:5432/vbit_auth |
| One-API Admin Token | `6fbe226e03f04d5aa1fe320918a0c0c9` |

---

## 测试结果总览

| 模块 | 测试用例数 | 通过 | 失败 | 警告 |
|--------|-----------|------|------|------|
| 服务器健康检查 | 1 | ✅ 1 | 0 | 0 |
| 前端页面 | 3 | ✅ 3 | 0 | 0 |
| 下载文件 | 2 | ✅ 2 | 0 | 0 |
| One-API 渠道 | 2 | ✅ 2 | 0 | 0 |
| One-API 模型能力表 | 2 | ✅ 2 | 0 | 0 |
| 免费Token | 10 | ✅ 10 | 0 | 0 |
| 微信OAuth | 1 | ✅ 1 | 0 | 0 |
| 积分系统 check | 1 | ✅ 1 | 0 | 0 |
| 积分系统 spend | 1 | ✅ 1 | 0 | 0 |
| 推荐码 | 2 | ✅ 2 | 0 | 0 |
| claim接口 | 2 | ✅ 2 | 0 | 0 |
| 数据库表 | 4 | ✅ 4 | 0 | 0 |
| SSL证书 | 1 | ✅ 1 | 0 | 0 |
| 备案信息 | 1 | ✅ 1 | 0 | 0 |
| **安全头** | **3** | **1** | **1** | **1** |
| **前端部署** | **1** | **0** | **1** | **0** |
| **CORS配置** | **1** | **0** | **1** | **0** |
| **环境变量** | **2** | **1** | **0** | **1** |

---

## ✅ 通过的测试

### 1. 服务器健康检查
```json
GET https://vbit.top/api/health → 200 OK
{
  "status": "ok",
  "checks": { "database": "ok", "redis": "ok" }
}
```

### 2. 前端页面
- ✅ `https://vbit.top/vermes/` → 200 OK (608 bytes)
- ✅ `https://vbit.top/vermes/downloads/` → 200 OK (2064 bytes)
- ✅ `https://vbit.top/vermes/version.json` → `{"version":"1.1.4", ...}`

### 3. 下载文件
- ✅ DMG: 57.5MB HTTP 200
- ✅ ZIP: 75.1MB HTTP 200

### 4. One-API 渠道
- ✅ CH1 DeepSeek: status=1, models=deepseek-chat
- ✅ CH2 MiMo: status=1, models=mimo-v2.5

### 5. 模型能力表
- ✅ abilities 表有且仅有 2 条记录，channel_id 正确

### 6. 免费体验 Token
- ✅ 10 个 token 全部有效，quota 正常（500000 或已消费）

### 7. 微信 OAuth 流程
- ✅ `/api/wechat/qrurl` → 正确生成授权 URL + state
- ✅ callback 页面不再内嵌用户数据（XSS 修复验证通过）
- ✅ 前端轮询 `/api/wechat/poll` 正常

### 8. 积分系统
- ✅ `GET /api/quota/check?wechat_openid=test123` → 正确返回剩余积分
- ✅ `POST /api/quota/spend` 带正确密钥 → 成功
- ✅ `POST /api/quota/spend` 无密钥 → 鉴权失败（预期行为）
- ✅ 推荐码生成 `/api/quota/referral/code` → 正常
- ✅ 推荐码绑定 `/api/quota/referral/bind` → 无效码正确拒绝

### 9. claim 接口
- ✅ 无 openid → `require_login:true`（预期行为）
- ✅ 24h IP 限频 → `rate_limited:true`（预期行为）

### 10. 数据库
- ✅ 13 张表全部存在
- ✅ `device_claims`: 23 条记录
- ✅ `daily_spend`: 4 条记录
- ✅ `referral_codes`: 4 条记录
- ✅ `referral_rewards`: 4 条记录

### 11. 硬编码检查
- ✅ `web_server.py`: 无硬编码密钥
- ✅ `wechat.js`: 无硬编码密钥（已从环境变量读取）
- ✅ `claim.js`: 无硬编码密钥
- ✅ 前端代码: 无硬编码密钥（仅有 UI 占位符）

### 12. One-API 模型调用
- ✅ `deepseek-v4-flash` → 正常返回
- ✅ `mimo-v2.5` → 正常返回

---

## ❌ 发现的问题

### 问题 1（🔴 高）：CORS `Allow-Origin: *` + `Allow-Credentials: true` 冲突

**位置**：`/api/` 响应头

**现象**：
```
Access-Control-Allow-Origin: *
Access-Control-Allow-Credentials: true
```

**影响**：浏览器会拒绝该响应（规范不允许 `*` + `credentials` 同时出现）。前端如果从浏览器调用 API 可能失败。

**修复**：在 nginx 配置中统一 CORS 头，或后端统一设置具体的 origin。

**状态**：⚠️ 待修复

---

### 问题 2（🟡 中）：`X-Frame-Options` 重复发送

**位置**：nginx 响应头

**现象**：
```
X-Frame-Options: SAMEORIGIN    ← 来自后端 Express
X-Frame-Options: DENY         ← 来自 nginx 配置
```

**影响**：重复头可能导致某些浏览器行为异常（虽然大多数浏览器取第一个）。

**修复**：nginx 配置中删除 `add_header X-Frame-Options DENY;`，或后端不再发送该头。

**状态**：⚠️ 待修复

---

### 问题 3（🟡 中）：服务器前端未部署最新版

**位置**：`/var/www/html/vermes/`

**现象**：
- 服务器前端 JS：`index-LgG1PsKy.js`（旧版）
- 本地最新构建：`index-hjKaDR6g.js`（审计修复后新版）

**影响**：用户从官网下载的 DMG 安装后，前端仍是未修复审计问题的版本。

**修复**：
```bash
cd ~/Projects/vermes/frontend && npx vite build
rsync -avz dist/ ubuntu@82.156.45.139:/var/www/html/vermes/
```

**状态**：⚠️ 待部署

---

### 问题 4（🟢 低）：`WECHAT_APPID` 仍有 fallback

**位置**：`/opt/vbit/src/routes/wechat.js` 第 18 行

**现象**：
```js
const WECHAT_APPID = process.env.WECHAT_APPID || 'wxfd680141e93226be'
```

**影响**：AppID 是公开信息（在 OAuth URL 中可见），不影响安全。但为保持一致性（S2/S3 修复精神），应移除 fallback。

**修复**：改为 `process.env.WECHAT_APPID`（无 fallback），未配置则 `process.exit(1)`。

**状态**：⚠️ 待修复（低优先级）

---

### 问题 5（🟢 低）：`.env` 中 `VERMES_INTERNAL_SECRET` 重复定义

**位置**：`/opt/vbit/.env` 第 40 行 + 第 50 行

**现象**：
```
40:VERMES_INTERNAL_SECRET=vermes_quota_secret_2026
50:VERMES_INTERNAL_SECRET=vermes_quota_secret_2026
```

**影响**：dotenv 取第一个值，功能无影响。但维护时容易混淆。

**修复**：删除第 50 行的重复定义。

**状态**：⚠️ 待修复（低优先级）

---

### 问题 6（🟢 低）：缺少 HSTS 安全头

**位置**：HTTPS 响应头

**现象**：`Strict-Transport-Security` 头未发送。

**影响**：用户可能通过 HTTP 访问（虽然后端有 301 跳转），HSTS 可强制浏览器记住"只走 HTTPS"。

**修复**：nginx 配置中添加：
```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
```

**状态**：⚠️ 待修复（低优先级）

---

## ℹ️ 用户决定不修

| 编号 | 问题 | 原因 |
|------|------|------|
| C1 | `markdown-it` `html:true` + `v-html` XSS | 单用户桌面应用，无跨用户风险 |
| M1 | `/api/env` 可写 | 桌面本机，外部无法访问 |

---

## 修复建议优先级

| 优先级 | 问题 | 预估工时 |
|--------|------|----------|
| **P0** | 问题 3：部署最新前端到服务器 | 5 分钟 |
| **P1** | 问题 1：修复 CORS 头冲突 | 15 分钟 |
| **P2** | 问题 2：修复 X-Frame-Options 重复 | 5 分钟 |
| **P3** | 问题 6：添加 HSTS 头 | 5 分钟 |
| **P4** | 问题 4+5：清理 .env 和 wechat.js 一致性 | 5 分钟 |

---

## 下一步行动

- [ ] **P0**：部署最新前端 `dist/` 到 `/var/www/html/vermes/`
- [ ] **P1**：修复 nginx CORS 配置（`/etc/nginx/sites-enabled/vbit.top.conf`）
- [ ] **P2**：修复 X-Frame-Options 重复
- [ ] **P3**：添加 HSTS 头
- [ ] **P4**：清理 .env 重复定义 + WECHAT_APPID fallback
- [ ] Git commit 所有本地改动
- [ ] Git push（用户要求构建发布时再一起 push）
- [ ] 打包新版本 DMG
- [ ] Windows 构建

---

*报告生成时间：2026-05-28 04:22 CST*
