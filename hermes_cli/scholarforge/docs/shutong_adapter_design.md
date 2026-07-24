# shutong（书童图书馆）文献源适配器 — 设计文档

> 状态：**专用适配器已实现（登录 + SSO JWT 令牌获取已实证；真实检索端点为专有契约，需用户从浏览器 DevTools 抓取后配置 `search_url`）**
> 阶段目标：先摸清 shutong 的登录/检索流程并出设计；**已落地 `ShutongProvider`**（按 2026-07-24 用户拍板：核心产品修复一个 PR，shutong 专用适配器留后续单独 PR）。

---

## 1. shutong 是什么

shutong2.com（书童图书馆）是一个**第三方中文文献代理网关**，面向知网(CNKI)/万方/维普等中文库的会员级访问。它基于 **EmpireCMS（帝国CMS）** 搭建，登录与会员中心走帝国CMS 标准 `member` 体系；中文库检索（`/zhongwenku/`）是**会员级、登录态会话限定**的资源。

用户的诉求：把自购的 shutong 卡号+密码+网关网址，像 CNKI 卡密一样**粘贴识别并接入**，成为 ScholarForge 的可用文献源。

合规铁律：**仅支持用户自有合法凭证**。shutong 是用户自购的第三方网关，符合；拒绝任何共享/灰产知网卡密。

---

## 2. 调研结论（已实证）

### 2.1 登录流程（已确认可用通用 form 适配）

对 `http://3.shutong2.com/e/member/doaction.php` 做了只读探测：

- 登录入口：`POST /e/member/doaction.php`
- 表单字段（已用真实结构校正）：
  - `username` — 卡号/账号
  - `password` — 密码
  - `enews=login` — 帝国CMS 登录动作标识（必填隐藏域）
  - `ecmsfrom=/zhongwenku/` — 登录后回跳目标（带会话进入中文库）
  - `lifetime=0` — 会话持久化选项
  - `key` — **仅在一段被注释掉的旧表单里出现**，是“验证码/旧加密”字段；**当前活动登录表单并不强制 `key`**，也**未做客户端密码加密**（用假凭证探测返回“您输入的账号不存在”，证明服务端直接校验账号、未先卡验证码）。
- 结论：登录本身**不需要专用适配器**，通用 `CustomHttpProvider` 的 `form` 鉴权即可覆盖。

### 2.2 检索流程（已实证：两段式 SSO）

用真实会话做只读探测后确认 shutong 的检索是**两段式 SSO**，而非经典 CMS 搜索表单：

1. **EmpireCMS 表单登录**（见 2.1）—— 实测 `POST /e/member/doaction.php` 后页面返回“登录成功!”，并种下会员会话 cookie（`yiffamlauth` 等）。✅ 已实证可用。
2. **SSO 令牌交接**：登录态访问中文库入口页（如 `GET /l77.php`，需带 `Referer: <base>/zhongwenku/`），该页 JS 会 `location.href='https://api88.wenxian.shop/token?sign_key=...&rndtoken=1&sign_token=<JWT>'`。其中 `sign_token` 是一段 **JWT**，payload 形如 `{"domain":"shutong121.com","username":<数字ID>,"exp":<+1h>,...}`。✅ 该 JWT 的抽取已实现并单测覆盖。
3. **真实检索 API**：`api88.wenxian.shop` 是 Go 服务，其 search 接口路径/参数**无法靠盲探得到**（所有猜测路径 404，携带 JWT 的 Bearer/Cookie/Query 试探均不开出搜索端点）。这是 shutong 的**专有契约**。

> 结论：登录与 SSO JWT 获取两段均已实证并可自动完成；**唯独“用 JWT 调哪个 URL 做检索、返回什么结构”是专有契约，必须用户从浏览器 DevTools 抓一次站内检索请求来补齐**。因此 `ShutongProvider.search()` 在拿到 JWT 后调用**用户配置的 `search_url`**（默认 bearer 携带 JWT），解析走通用 tolerant JSON 解析器。

---

## 3. 架构设计

### 3.1 复用现有通用能力（已落地骨架）

凭据识别与通用 form 登录已经在 B+C 轮次实现，shutong 直接复用：

1. **粘贴识别**：`agent/literature_custom_store.parse_literature_credential_block()` 已识别“卡号/密码/网址/名称”中文块；对 shutong 增加预设：`source_id` 规则 + `auth=form` + `login_url=/e/member/doaction.php` + `login_user_field=username` + `login_password_field=password` + `login_extra_fields={enews:login, lifetime:0, ecmsfrom:/zhongwenku/}`。
2. **通用 form 登录**：`agent/literature_providers/_http.http_login_then_search()` 已做“登录→带会话检索”的 best-effort 串联；`CustomHttpProvider.search()` 的 `form` 分支已接通上述 `login_extra_fields`。
3. **落盘**：`register_source_from_credential_block()` 已把凭证写入 `~/.vermes/.env` 的 `LIT_<ID>_*`，不进代码、不回显（脱敏校验已做）。

> 也就是说：shutong 的**登录与接入骨架已经闭环**，本机已能用真实卡密接入 `source_id=s_3_shutong2_com`。缺口只在“登录后如何检索 + 如何解析结果”。

### 3.2 专用适配器（已实现：`agent/literature_providers/shutong.py`）

`ShutongProvider(CustomHttpProvider)` 已实现并单测覆盖：

```python
class ShutongProvider(CustomHttpProvider):
    def _acquire_sso_token(self) -> Dict[str, Any]:
        # 登录 → GET sso_url(默认 /l77.php, 带 Referer) → 从 JS 重定向抽 sign_token JWT
    def search(self, query, limit=10) -> Dict[str, Any]:
        # 1) 取 SSO JWT（已实证）
        # 2) 若未配置 search_url → 返回可执行的“请抓检索端点”提示（不静默失败）
        # 3) 否则按 token_scheme(bearer/cookie/query) 带 JWT 调 search_url，
        #    用通用 tolerant 解析器抽论文（api88 返回 JSON）
```

接入路由：`literature_registry.bootstrap_custom_providers()` 遇 `provider_type=="shutong"` 即实例化 `ShutongProvider`（其余自定义源仍走 `CustomHttpProvider`）。`register_source_from_credential_block()` 在 URL 命中 `shutong` 时自动标注 `provider_type="shutong"` 并预填 `login_extra_fields={enews:login, lifetime:0, ecmsfrom:/zhongwenku/}` + `sso_url`/`sso_referer`/`token_scheme`。

> 说明：初版设计以为结果页是 EmpireCMS HTML grid、可复用 `_parse_cnki_grid_html`；**实测更正**——shutong 真实检索走 `api88.wenxian.shop` 的 JSON API，故改用通用 tolerant JSON 解析器（`CustomHttpProvider._parse`），更准确。

检索端点发现策略（按优先级）：
1. **用户从浏览器 DevTools 抓一次 shutong 站内检索的请求 URL + 响应结构** → 填 `search_url` +（若返回 HTML 则需扩展解析器）；这是唯一可靠的拿到真实契约的方式。
2. 填好 `search_url` 后，`ShutongProvider` 自动带 JWT 调用，无需改代码。

---

## 4. 诚实边界 / 风险

| 项 | 状态 | 说明 |
|----|------|------|
| 登录表单适配 | ✅ 已实证 | 通用 form 可用，无客户端加密、未强制验证码（返回“登录成功!”） |
| 凭证粘贴识别 | ✅ 已闭环 | 解析器支持 shutong 块，自动标 `provider_type=shutong` |
| SSO JWT 获取 | ✅ 已实证+单测 | `/l77.php`→ 抽 `sign_token` JWT，payload 含 domain/username/exp |
| 真实检索端点 | ⚠️ 需用户配置 | `api88.wenxian.shop` search API 为专有契约，盲探不可得；用户须从 DevTools 抓 `search_url` 填入 |
| 结果解析 | ✅ JSON tolerant | `api88` 返回 JSON，通用解析器抽取；若某源返 HTML 则按需扩展 |

运行生效前提：改动需 dev 后端(:9120) 重启加载新 `custom.py`/`config.py`；桌面 app 需 `npm run build` 刷新 `web_dist` 才见设置页“粘贴凭证自动识别”框。

---

## 5. 落地步骤（适配器已就绪，仅差检索端点配置）

1. 用户在前端粘贴 shutong 卡密块 → `provider_type=shutong` 自动标注、`s_3_shutong2_com` 源已注册、`.env` 已写。
2. **用户从浏览器 DevTools 抓一次 shutong 站内检索**：复制 Request URL（形如 `https://api88.wenxian.shop/...search...`）→ 在设置页补该源的 `search_url`；若需非 bearer 携带 JWT，改 `token_scheme`。
3. 之后 `ShutongProvider` 自动：登录 → 取 JWT → 带 JWT 调 `search_url` → 解析返回。
4. 本适配器随 ScholarForge 论文模块改进一并提交（单独 PR，不与核心产品修复混）。

---

## 6. 测试策略（已实现）

- 单元：`tests/agent/test_shutong_provider.py` 覆盖——SSO JWT 抽取（正常/无重定向）、未配置 `search_url` 返回可执行提示、`search()` 带 Bearer 调配置端点并解析、`register_source_from_credential_block` 对 shutong 标 `provider_type` + 预填 `login_extra_fields`/`sso_url`（用 tmp store 避免污染 `~/.vermes`）。
- 集成（待用户配 `search_url` 后）：本机真实卡密接入，跑一次 `literature_matrix` / `research_map` 验证能拉到 shutong 源结果。

---

_最后更新：2026-07-24 — `ShutongProvider` 已实现：登录 + SSO JWT 获取实证可用；真实检索端点（api88.wenxian.shop）为专有契约，需用户从 DevTools 抓 `search_url` 填入。_
