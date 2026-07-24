# shutong（书童图书馆）文献源适配器 — 设计文档

> 状态：**设计阶段（已实现通用 form 适配骨架，检索端点待真机确认）**
> 阶段目标：先摸清 shutong 的登录/检索流程并出设计；**暂不落地专用适配器实现**（按 2026-07-24 用户拍板：核心产品修复一个 PR，shutong 专用适配器留后续单独 PR）。

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

### 2.2 检索流程（未知，待真机确认）

- 登录后的中文库检索端点（`/zhongwenku/` 下的 search action / 参数）**尚未探明**。
- 帝国CMS 检索通常返回 **HTML（grid 表格）**，当前 `custom.py` 的 tolerant JSON 解析器**不一定能抽到论文条目**。
- `cnki_fetcher._parse_cnki_grid_html` 已能解析 CNKI 结果网格 HTML，可作为 shutong 中文库结果页解析的**复用起点**。

---

## 3. 架构设计

### 3.1 复用现有通用能力（已落地骨架）

凭据识别与通用 form 登录已经在 B+C 轮次实现，shutong 直接复用：

1. **粘贴识别**：`agent/literature_custom_store.parse_literature_credential_block()` 已识别“卡号/密码/网址/名称”中文块；对 shutong 增加预设：`source_id` 规则 + `auth=form` + `login_url=/e/member/doaction.php` + `login_user_field=username` + `login_password_field=password` + `login_extra_fields={enews:login, lifetime:0, ecmsfrom:/zhongwenku/}`。
2. **通用 form 登录**：`agent/literature_providers/_http.http_login_then_search()` 已做“登录→带会话检索”的 best-effort 串联；`CustomHttpProvider.search()` 的 `form` 分支已接通上述 `login_extra_fields`。
3. **落盘**：`register_source_from_credential_block()` 已把凭证写入 `~/.vermes/.env` 的 `LIT_<ID>_*`，不进代码、不回显（脱敏校验已做）。

> 也就是说：shutong 的**登录与接入骨架已经闭环**，本机已能用真实卡密接入 `source_id=s_3_shutong2_com`。缺口只在“登录后如何检索 + 如何解析结果”。

### 3.2 专用适配器（设计，待实现）

当通用适配器在 shutong 上被拦（验证码/客户端加密/检索端点不明）时，新增专用子类：

```python
# agent/literature_providers/shutong.py（设计）
class ShutongProvider(CustomHttpProvider):
    """书童图书馆：EmpireCMS 登录 + 中文库检索。
    复用 _parse_cnki_grid_html 解析结果网格。"""
    # 1) 登录：覆盖 CustomHttpProvider 的 form 登录，必要时补 ecmsfrom/lifetime
    # 2) 检索：确认 search_url 与参数（待真机），复用 _parse_cnki_grid_html
    # 3) 解析：若结果页含客户端加密/验证码，加专用破解分支
```

检索端点发现策略（按优先级）：
1. 用户在设置页补充 `search_url` + 参数（最快，无需改代码）。
2. 用已登录会话抓 `/zhongwenku/` 页面，提取真实检索 form 的 action 与字段。
3. 仍失败 → `ShutongProvider` 专用适配，复用 CNKI grid 解析。

---

## 4. 诚实边界 / 风险

| 项 | 状态 | 说明 |
|----|------|------|
| 登录表单适配 | ✅ 已闭环 | 通用 form 可用，无客户端加密、未强制验证码 |
| 凭证粘贴识别 | ✅ 已闭环 | 解析器支持 shutong 块 |
| 会员会话检索 | ⚠️ 待确认 | search_url 与参数未知，需真机 |
| 结果 HTML 解析 | ⚠️ 待确认 | 帝国CMS 返回 HTML，tolerant 解析未必抽到；复用 `_parse_cnki_grid_html` |
| 验证码/加密拦截 | ⚠️ 潜在 | 若活动表单加验证码或客户端加密，需专用适配 |

运行生效前提：改动需 dev 后端(:9120) 重启加载新 `custom.py`/`config.py`；桌面 app 需 `npm run build` 刷新 `web_dist` 才见设置页“粘贴凭证自动识别”框。

---

## 5. 落地步骤（待用户绿灯）

1. 用户在前端粘贴 shutong 卡密块 → 确认 `s_3_shutong2_com` 源已注册、`.env` 已写。
2. 用真实会员会话探测 `/zhongwenku/` 检索端点（curl/浏览器抓包）→ 回填 `search_url`+参数，或交 `ShutongProvider` 专用适配。
3. 用 `_parse_cnki_grid_html` 接结果解析；补 `test_literature_providers` 用例（mock 登录会话 + 真实结构 HTML fixture）。
4. 单独 PR（不与核心产品修复混）。

---

## 6. 测试策略（沿用现有）

- 单元：`tests/agent/test_literature_credential_parser.py` 已覆盖 shutong 块的识别/告警/脱敏；新增 `ShutongProvider` 检索/解析用例（mock 会话 + HTML fixture）。
- 集成：本机真实卡密接入后，跑一次 `literature_matrix` / `research_map` 验证能拉到 shutong 源结果。

---

_最后更新：2026-07-24 — 设计定稿；通用 form 骨架已闭环，检索端点待真机确认。_
