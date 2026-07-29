# API Key / 凭证泄露风险审计报告

**审计时间**: 2026-06-03  
**审计范围**: `/Users/dongzusheng/Projects/vermes-electron`  
**核心后端**: `vermes_cli/`, `agent/`, `gateway/`  

---

## P0 — 高危（立即修复）

### P0-1: 全局异常处理器泄露异常详情到 HTTP 响应
**文件**: `vermes_cli/web_server.py:90-94`  
**风险**: 全局 `@app.exception_handler(Exception)` 将 `str(exc)[:200]` 返回给客户端。如果异常信息包含 API key（如 Provider API 调用失败时返回的 auth header），会直接暴露。  
**代码**:
```python
_log.error(f"... {type(exc).__name__}: {exc}")   # 日志也记录了完整异常
content={"error": {"message": f"Internal server error: {type(exc).__name__}: {str(exc)[:200]}"}}
```
**修复建议**:
1. 移除 `str(exc)[:200]` 中的异常详情，只返回通用错误消息
2. 日志中 `{exc}` 虽然经过 `redact_sensitive_text` 过滤，但需要确认该 formatter 已挂载在这条路径上

### P0-2: Session Token 注入 SPA HTML，存在 XSS 窃取风险
**文件**: `vermes_cli/web_server.py:1577-1583`  
**风险**: 同一 `_SESSION_TOKEN` 同时用作 `window.__vermes_SESSION_TOKEN__` 和 `window.__OPENCLAW_SESSION_KEY__`。若 SPA 存在 XSS 漏洞，攻击者可窃取 token，随后通过 `/api/env/reveal` 暴露所有 API key。  
**代码**:
```python
f'window.__vermes_SESSION_TOKEN__=\"{_SESSION_TOKEN}\";'
f'window.__OPENCLAW_SESSION_KEY__=\"{_SESSION_TOKEN}\";'
```
**修复建议**:
1. `__OPENCLAW_SESSION_KEY__` 应使用独立的、更低权限的 token
2. 考虑 CSP header 限制 script 注入
3. `/api/env/reveal` 增加二次确认（如输入密码）

### P0-3: 自定义 Provider 的 API Key 同时存入 config.yaml（明文）
**文件**: `vermes_cli/blueprints/providers.py:194-199`, `vermes_cli/web_server.py:2668-2673`  
**风险**: 对于非模板的 provider（自定义），API key 被明文写入两个位置：
- `~/.vermes/.env`（安全，有 `.gitignore` 保护）
- `~/.vermes/config.yaml`（明文 API key 在 YAML 中，可能被误提交或备份泄露）
**修复建议**: 自定义 provider 的 API key 也应只写入 `.env`，不要写入 `config.yaml`

### P0-4: `/api/env` 在公开 API 列表中无需 Session Token
**文件**: `vermes_cli/web_server.py:216`  
**风险**: `/api/env`（GET/PUT/DELETE）在 `_PUBLIC_API_PATHS` 中，**不需要 session token** 即可访问。虽然 PUT 有 `_ENV_WRITE_ALLOWED_KEYS` 白名单保护，但任何能连接到 dashboard 端口的进程都可以读写环境变量（包括设置新的 API key）。  
**修复建议**: 将 `/api/env` 移出公开列表，或至少 GET 需要 session token 认证

---

## P1 — 中高危

### P1-1: `/api/provider/verify` 异常信息泄露 Exception 详情
**文件**: `vermes_cli/blueprints/providers.py:230-231`  
**风险**: Provider API Key 验证失败时，异常信息（可能包含 HTTP response body 中的 key 相关错误）被直接返回给客户端  
**代码**:
```python
except Exception as e:
    raise HTTPException(status_code=400, detail=f"Verification failed: {str(e)}")
```
**修复建议**: 返回通用错误消息，将 `str(e)` 记录到日志

### P1-2: `/api/provider/sync-models` 异常信息泄露
**文件**: `vermes_cli/blueprints/providers.py:293-294`  
**风险**: 同样的模式——`str(e)` 直接返回给前端  
**代码**:
```python
except Exception as e:
    return {"ok": False, "error": str(e)}
```
**修复建议**: 返回通用错误消息

### P1-3: `verify_provider` 函数异常信息泄露
**文件**: `vermes_cli/web_server.py:2702-2703`（旧版，与 blueprints 中重复）  
**风险**: 同上，异常信息直接返回  
**代码**:
```python
except Exception as e:
    raise HTTPException(status_code=400, detail=f"Verification failed: {str(e)}")
```
**修复建议**: 统一通过 blueprint 实现，返回通用错误消息

### P1-4: Session Token 通过 WebSocket URL 查询参数传输
**文件**: `vermes_cli/web_server.py:1269`, `vermes_cli/web_server.py:1296-1306`  
**风险**: `_SESSION_TOKEN` 作为 `?token=` 查询参数出现在 WebSocket URL 中，可能被记录在服务器日志、代理日志、Referer header 中  
**修复建议**: 考虑 WebSocket 升级前先通过 REST 获取一次性 token，或使用 Cookie 认证

### P1-5: `feishu.py` logger.debug 记录 card action token
**文件**: `gateway/platforms/feishu.py:2728`  
**风险**:  
```python
logger.debug("[Feishu] Dropping duplicate card action token: %s", token)
```
如果启用 DEBUG 日志级别，card action token 会被明文记录到日志  
**修复建议**: 对 token 值应用 `mask_secret()` 后再记录

---

## P2 — 中低风险

### P2-1: `config.yaml` 中 `providers[].api_key` 可能存储明文 Key
**文件**: `vermes_cli/blueprints/providers.py:257`, `vermes_cli/blueprints/chat.py:465`  
**风险**: 代码从 `config.yaml` 的 `providers.<id>.api_key` 字段读取 API key。虽然当前代码只对自定义 provider 写入 `config.yaml`，但未来若扩展，可能导致 API key 泄露。  
**修复建议**: 归档此风险点，未来所有 provider 的 API key 应统一通过 `.env` 管理

### P2-2: GATEWAY_ALLOW_ALL_USERS 配置项在 .env.example 中注释说明存在风险
**文件**: `.env.example:367-368`  
**风险**: `GATEWAY_ALLOW_ALL_USERS=false` 的注释说明存在开放访问风险，属设计层面的配置风险  
**修复建议**: 已经在文档中警告，但建议默认值时添加运行时检查

### P2-3: 前端 Provider API Key 明文通过 PUT /api/env 传输
**文件**: `frontend/src/components/Settings.vue:240-242`, `frontend/src/components/ProviderCard.vue:60`  
**风险**: API key 在 HTTP PUT body 中以明文传输（localhost 范围内风险较低，但若配置了远程 debug 则为高危）  
**修复建议**: 如服务部署在非 localhost 环境，应启用 HTTPS

### P2-4: Electron main.js 控制台输出后端 stderr
**文件**: `electron/main.js:81-84`  
**风险**: 后端 stderr 输出（可能包含 API key 或 token 错误）被直接 `console.error` 到 Electron 日志  
**修复建议**: 对 stderr 输出应用简单的正则过滤后再记录

### P2-5: Session Token 在同一名称下用于两种用途
**文件**: `vermes_cli/web_server.py:1580`  
**风险**: `__OPENCLAW_SESSION_KEY__` 与 `__vermes_SESSION_TOKEN__` 使用同一值，命名暗示了不同的用途，可能导致权限混淆  
**修复建议**: 使用独立的 token 值

### P2-6: `.gitignore` 中 `export*` 覆盖范围过宽
**文件**: `.gitignore:15`  
**风险**: `export*` 会忽略所有以 `export` 开头的文件，但同时也可能遗漏用户创建的 `export_*_keys.sh` 等脚本文件（如果命名不同）  
**修复建议**: 明确列出 `export*.sh` 或 `export*.env`

### P2-7: `.dockerignore` 排除了 `.env` 但未排除 `.env.*` 变体
**文件**: `.dockerignore:22`  
**风险**: `.env.example` 在构建上下文中可见（虽然示例不含真实密钥，但透露了密钥变量名）  
**修复建议**: 添加 `.env.*` 到 `.dockerignore`

---

## 已验证安全的配置

### ✅ `.gitignore`
正确排除了 `.env`, `.env.local`, `.env.development`, `.env.test`, `.env.production.local` 等文件（第8-14行）

### ✅ `.dockerignore`
正确排除了 `.env`（第22行）

### ✅ Preload 脚本（`electron/preload.js`）
IPC 通道限定在 `backend:status`, `wechat-login`, `shell:openExternal`, `update:*` 等安全操作，不暴露任何文件系统或 secret 读取能力

### ✅ 请求日志中间件（`web_server.py:124-167`）
正确地在日志中 redact `api_key`, `password`, `token`, `secret` 字段

### ✅ Redact 机制（`agent/redact.py`）
完善的正则匹配 + 前缀模式对已知 API key 格式（sk-, ghp_, xoxb-, AIza 等）进行 masking。`RedactingFormatter` 在 logging 层自动 redact

### ✅ `/api/env` GET 返回值使用 `redact_key()` 脱敏
`vermes_cli/blueprints/config.py:456-457` 中返回值使用 `redact_key(value)` 而非明文

### ✅ `/api/env/reveal` 有 Rate Limit + Session Token 保护
`vermes_cli/blueprints/config.py:493-517` 配置了每30秒最多5次 reveals

### ✅ 主机头验证中间件
`vermes_cli/web_server.py:312-339` 防止 DNS 重绑定攻击

### ✅ Provider 同步有 SSRF 保护
`vermes_cli/blueprints/providers.py:263-274` 限制仅已知 domain 可发送 API key

---

## 总结

| 优先级 | 数量 | 关键风险 |
|--------|------|----------|
| **P0** | 4 | 全局异常信息泄露、Token 注入 XSS、API Key 写入 config.yaml、/api/env 无需认证 |
| **P1** | 5 | 验证/同步端点异常泄露、WS query param 泄露、feishu debug 日志 |
| **P2** | 7 | 配置残留、传输链路、日志输出等 |

**最优先修复项**: P0-1（全局异常处理器）、P0-3（API key 写入 config.yaml）、P0-4（/api/env 公开访问）
