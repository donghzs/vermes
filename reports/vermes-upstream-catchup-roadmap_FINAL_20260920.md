# Vermes 跟进上游 Hermes —— 三案合并核实版（终版路线）

- 撰写：2026-09-20
- 取证对象：本报告 = 对三份既有方案的**独立复核 + 合并**，不是第四份独立调研
  - 方案 A：`reports/vermes-upstream-catchup-roadmap_20260919.md`（制度层视角，189 行）
  - 方案 B：`Vermes_取长上游Hermes_方案路线.md`（v0.21.x 功能面差距）
  - 方案 C：state.db / delegate 源码实证 + 执行方案（A/B/C 件套）
- 取证方式：GitHub tree API（上游 15,485 条目实测）+ raw 源码拉取 + 本地 `git`/`find`/Grep 工具实测
- **所有标注「实测」的数字均为本次亲自跑出；标注「待复核」的为我未能实证，不得用作决策依据**
- 纪律声明：本版**不推翻**「锁定基线、白名单移植」既有决策，只做三案合并与纠错
- 📌 **复核修正记录见文末「附录 E · Errata」**（E1–E10：3 处修正 + 7 条补充，正文对应位置已同步改掉，不留两份真相）

---

## 0.1 执行状态（2026-09-20 更新）

| 项 | 状态 | 改动 | 证据 |
|---|---|---|---|
| **A1** home channel 提示判定单一口径 | ✅ 已落地 | `gateway/message_handler_mixin.py:2088-2090` 改用 `resolve_home_channel_chat_id(platform_name, config=self.config)` | 见 §0.2 |
| **A2** cron 投递加 config 回落 | ✅ 已落地 | `cron/scheduler.py:_get_home_target_chat_id` 增加 config 支（用 `load_config()` 传字典，避免依赖 `gateway.run`） | 见 §0.2 |
| 共享解析入口 | ✅ 新建 | `gateway/gateway_utils.py`：`config_home_channel_chat_id()` + `resolve_home_channel_chat_id()`（env → legacy env → config） | 单一口径落点 |
| 契约测试 | ✅ 新建 | `tests/vermes_cli/test_home_channel_resolution.py`（12 项） | 12 passed；变异测试 4 红 |
| **提交固定** | ✅ 已 commit | `b7305707d4`（4 文件 +283/−5，**未 push**）：3 源文件 + 契约测试 | 2026-09-20 08:12，防在途改动被覆盖 |
| **E1 注释订正** | ✅ 已落地 | `gateway/message_handler_mixin.py:2089-2097` 根因注释按 E1 重写 | 与 A1/A2 同 commit |
| **13 条已知失败** | ✅ 已证实 + 已出清单 | 对照实验：回退到 `eaa63411a2` 后**同样 13 failed** → pre-existing 已证明；清单见 `reports/known-failures-gateway-20260920.md` | 含 1 条**疑真 bug 不得隔离**（email self-message 过滤） |
| A3~A6 | ⏸ 未动 | 去重 / 度量脚本 / 文档重写 / CI lane | 待本轮审后 |
| A7 GUI 入口 | ⏸ 未动 | `Settings.vue` + `gateway_channels.py`（后端落点已备，33,217 字节） | 见 §2 A7 详情 |
| P0-B state.db 六件套 | ⏸ 未动 | — | 等 2.5 收口 |

### 0.2 A1/A2 的根因（比"照抄上游"更贴切）

**（本节已按 errata E1 修正，原表述见文末 E1）**

`/sethome` 写 `~/.vermes/.env` 并同步内存 config（`config_handlers.py:882`）。两条必读事实：

- **`/sethome` 之后同进程的 env 检查是能命中的** —— `save_env_value` 结尾就是 `os.environ[key] = value` + `invalidate_env_cache()`（`vermes_cli/config.py:5144`），所以它**不是**误报来源；
- 网关启动时**确实**加载 .env（`gateway/run.py:543-546` 调 `load_vermes_dotenv`，语义为 user .env 覆盖 shell 导出值），但**只在启动时加载一次** → 从进程外改 .env（手工编辑/脚本写入）对运行中的网关不可见。

因此 env-only 判定的真实误报来源是 **config-only 路径不可见**（home channel 只写在 config.yaml：GUI、手写 YAML、未来 GUI 字段）+ 外部改 .env 后未重启。共享解析器（env → legacy env → config）同时覆盖这两条。

> ⚠️ 该错误根因已被写进生产代码注释 `gateway/message_handler_mixin.py:2089-2092`，需一并订正（见 errata E1 连带待办）。

### 0.3 已知取舍（需拍板）

解析顺序取 **env → legacy env → config**（与上游一致，保证显式 env 覆盖不被推翻）。副作用：若用户在网关启动后改了 home channel，进程内旧 env 仍优先，cron **投递目标**要等重启才更新（提示判定已不受影响）。要修就得改成 config 优先或让 `/sethome` 同时落 config.yaml —— 属语义变更。**建议（errata E4，待董董拍板）**：让 `/sethome` **双写** —— env 保留「运维覆盖」语义（与上游一致），`config.yaml` 的 `home_channel` 作结构化真源（GUI / 手写 / 未来 GUI 字段都落这里），判定与投递统一走共享解析器。这样「改完即时生效」与「重启后仍生效」都不再依赖进程环境的时效性；比「config 优先」更安全（不推翻显式 env 覆盖）。

---

## 0. 结论先行

**三份方案的方向都对，但都各自踩了同一类坑：搜索不完备导致的"缺失"误判。** 本版核掉 4 处，其中 2 处直接影响工作量估算。

| # | 结论 | 影响 |
|---|---|---|
| 1 | 方案 A 的核心洞察（**制度层缺一半**）成立且是本轮最有价值的发现；12 vs 1 `AGENTS.md`、35 vs 14 CI **实测完全属实** | 采纳为终版主线 |
| 2 | 方案 A 的原意是「口径 A 结构化 14 处消费 vs 口径 B env-only 3 处」，**不是**「Vermes 全局缺失 config 层」；本地确有完整 config 级 home_channel（`HomeChannel` + `get_home_channel()` + YAML 往返 + 15 平台 env→config bridge）。本版首轮表述易被误读，按 errata E2 澄清 | 修法从「补一整层」降为「改两个旁路点」（结论不变） |
| 3 | 方案 A 称「照抄上游两处 hunk」——**第一支（secret scope）抄不了**：本地 `secret_scope`/`get_secret` 全仓零命中，且 `gateway/run_turn.py`、`cron/scheduler_delivery.py` 两个上游文件本地均不存在 | P0 只能做最小版（0.5~1 天），完整对齐降级 P1 |
| 4 | 方案 B/C 的 P0-B「本地已有 RoutingIdentity 雏形」**是错的**（全仓 `.py` 零命中）；另 `list_background_tasks()` 已存在、`MAX_DEPTH=1` 是设计选择 | P0-B 从"收口 1-2 天"改判 **greenfield 3-5 天** |
| 5 | **我上一轮声称落盘的 `UPSTREAM_ADOPT_ROADMAP.md` 实际不存在**（`find ~ -maxdepth 5` 零命中，仓库根 md 列表无此项） | 如实披露；本版重新落盘并回读验证 |

---

## 1. 方案 A 核实结果（逐条实测）

### 1.1 ✅ 成立项（我亲自跑出，可放心用）

| 方案 A 论断 | 我的实测 | 判定 |
|---|---|---|
| 上游 12 个 `AGENTS.md`（按目录） | GitHub tree API 实测 12 个：`AGENTS.md` / `agent/` / `apps/desktop/` / `apps/desktop/src/` / `cron/` / `gateway/` / `hermes_cli/` / `plugins/` / `skills/` / `tools/` / `tui_gateway/` / `web/` | ✅ 精确 |
| Vermes 仅 1 个 `AGENTS.md` | `find` 实测 1 个（根目录） | ✅ 精确 |
| 上游 CI 35 个 / Vermes 14 个 | tree API 35 个 workflow、本地 `ls .github/workflows` 14 个 | ✅ 精确 |
| 缺 `install-e2e` / `tests-os` / `js-tests` / `lockfile-diff` 四类 | 上游独有 27 个中含 `install-e2e.yml`(+mac/win run)、`installer-tests.yml`、`tests-os.yml`、`js-tests.yml`、`lockfile-diff.yml` | ✅ 成立 |
| `git remote` 实际是正确的（文档烂，管道没坏） | `upstream` / `upstream2` → `https://github.com/NousResearch/hermes-agent.git`；`origin` → `donghzs/vermes` | ✅ 成立 |
| `UPSTREAM_SYNC.md` 把「CI/CD 管道」列为不同步 | `UPSTREAM_SYNC.md:50` 原文「**CI/CD 管道** — Vermes 有自己的 GitHub Actions」 | ✅ 成立 |
| home channel 存在双口径 | A 结构化 `gateway/config.py:569 get_home_channel()`（14 处消费）；B env-only：`gateway/message_handler_mixin.py:2087-2106`（提示判定）、`cron/scheduler.py:372-382`（cron 投递） | ✅ 成立 |
| 上游两处锚点真实存在且有四路兜底 | 拉取 raw 源码：`gateway/run_turn.py`（4,261 行）`_hmwa_first_contact_notes` 内 secret scope → `os.getenv` → `config.get_home_channel()` → 二级 profile；`cron/scheduler_delivery.py`（2,018 行）`_get_home_target_chat_id` = env → legacy env → `_get_config_home_channel` | ✅ 成立 |
| 会话重置语义是「该会话无 history」（非每条） | `message_handler_mixin.py:2083` 条件 `if not history and ...`；`SessionResetPolicy` = `mode="both"` / `at_hour=4` / `idle_minutes=1440`（`gateway/config.py:255-257`） | ✅ 成立，采纳其"按平台侧计数验收"的口径 |

**规模类数字复核（Vermes 侧，全部吻合，差异来自持续提交漂移）**：

| 指标 | 方案 A | 我实测 | 判定 |
|---|---|---|---|
| `tools/` | 104 文件 / 73,171 行 | 104 / 73,171 | ✅ 精确 |
| `cron/` | 3 / 3,415 | 3 / 3,415 | ✅ 精确 |
| `gateway/` | 89 / 90,416 | 89 / 90,416 | ✅ 精确 |
| `context_compressor.py` | 2,171 行 | 2,171 行 | ✅ 精确 |
| `agent/` | 210 / 107,829 | 211 / 108,191 | ✅ 漂移 +1 文件 |
| Python 文件 | 2,415 | 2,419 | ✅ 漂移 |
| 总提交 / 近 30 天 | 1,702 / 574 | 1,706 / 578 | ✅ 漂移 |
| 运行时技能 246 | — | `~/.vermes/skills` 实测 **245** 个 `SKILL.md` | ✅ 口径基本一致（差 1） |

### 1.2 ❌ 必须修正项

#### 修正 1（措辞，非事实）：两个 env-only 旁路点 ≠ 全局缺失

> 澄清（errata E2）：方案 A 原文即「口径 A 结构化 14 处消费 / 口径 B env-only 3 处」，从未主张 Vermes 没有 config 层。此处澄清只为防止「方案 A 说 Vermes 缺 config 层」被当成结论传播；下面的核实内容不变。

本地**已具备完整 config 级 home_channel**：

- `HomeChannel` 数据类：`gateway/config.py:210`（platform / chat_id / name / thread_id，含 `to_dict`/`from_dict` YAML 往返）
- `get_home_channel()`：`gateway/config.py:569`
- 消费点 14 处：`run.py:2855`（启动通知）、`webhook.py:927`、`discord.py:2561`、`yuanbao.py:4612`、`config_handlers.py:882` 等
- **env→config bridge 覆盖 15 个平台**（`config.py` 实测行号）：telegram:1153、discord:1170、whatsapp:1199、slack:1227、signal:1248、mattermost:1268、sms:1350、dingtalk:1469、**feishu:1497**、**wecom:1520**、**weixin:1579**、bluebubbles:1603、**qqbot:1630**、yuanbao:1676、line:1713

→ **修法不是"补一整层"，而是把两个 env-only 旁路点改走 `get_home_channel()`。**

#### 修正 2：「只有 telegram 不提示」是**本机实测状态**，不是平台级通则

> 前提（errata E2）：该句来自对董董本机 `~/.vermes/.env` 的实测（只有 `TELEGRAM_HOME_CHANNEL` 有值）。下面是通化后的三种失败模式，保留其价值。

bridge 覆盖了 feishu/qqbot/wecom/weixin，所以「只有 telegram」不成立。真实失败模式：

| 模式 | 机制 | 证据 |
|---|---|---|
| (a) 平台不在 `config.platforms` | bridge 有门：`if telegram_home and Platform.TELEGRAM in config.platforms` | `config.py:1154` |
| (b) home 只写在 config.yaml（未写 .env） | 两个 env-only 点看不见 → 提示误报 + cron 不投递 | `message_handler_mixin.py:2087`、`cron/scheduler.py:372` |
| (c) `/sethome` 落盘路径 | 写 `.env`（`save_env_value`）+ 同步**内存** config（`config_handlers.py:882`），**不落 config.yaml** → 重启后靠 env bridge 兜住 | 方案 A 说「只写 env」不完整，漏了内存同步段 |

→ 单 profile 下 `/sethome` 后两条路通常都通；**真正的爆点在多 profile / multiplex**（见修正 3）。

#### 修正 3：「照抄两处 hunk」→ 第一支抄不了，P0 只能做最小版

- 上游兜底链第一优先级是 `from agent.secret_scope import get_secret`（profile 级 secret scope）。本地 **全仓 grep `secret_scope` / `def get_secret` 零命中** → 该层不存在。
- 上游两个文件本地均无对应物：`gateway/run_turn.py`（本地是 `gateway/message_handler_mixin.py`）、`cron/scheduler_delivery.py`（本地是 `cron/scheduler.py`），且本地 `cron/` 仅 3 文件 vs 上游 30 文件。

→ **工作量重估**：
- 最小可行（单 profile）：两处改走 `self.config.get_home_channel()` + cron 加 config 回落 ≈ **0.5~1 天**，不需要 secret scope
- 完整对齐上游（含 profile scope）：需先建 secret scope 层 ≈ **额外 2~3 天**，降级 P1（与 RoutingIdentity 同族）

#### 修正 4（重写，errata E3）：浏览器工具口径两边都错，「上游已收敛」论点作废

正确口径 = `toolsets.py` 的**核心工具列表**（模型每轮真正看到的集合），实测：

| 侧 | 核心列表 `browser_*` | 明细 |
|---|---|---|
| Vermes | **12** | navigate / snapshot / click / type / scroll / back / press / get_images / vision / console / cdp / dialog |
| 上游 | **19** | 上述 12 个**照留** + 7 个 `browser_vault_*` + `browser_exec` + `browser-use` |

→ 方案 A 的「13」应为 12；本版首轮的「5 个注册名」取自 `agent/tool_guardrails.py`，口径偏离、低估一半。
→ **需更正一个更强的结论**：上游**并未收敛**细粒度浏览器工具 —— 它是「同一套细粒度照留 + 多 7 个凭据类 + exec」，工具面比 Vermes 更大。因此「工具面收敛度是 Vermes 的短板」**不成立**，不得写进结论或验收。

### 1.3 ⚠️ 未能实证项（不得用作决策依据）

- 上游侧规模数字（13,513 近 30 天提交 / 37,857 总提交 / 6,717 py / 4,578 测试文件）：**本地 `refs/remotes/` 只有 `origin/*`，未 fetch 上游**，无法复核。需 `git fetch upstream` 后验。
- 方案 A 基线 `5a0c2fb89e`（上游）/ `a5d265d8cf`（Vermes）：同上，未复核。本地 HEAD 已前进至 `1f7d6105df`。
- 同源重合度 Jaccard（`model_tools` 0.11 等）：未复核。

---

## 2. 终版路线（三案合并，四层分离）

设计原则：**制度层与能力层不同时动**；能力层内部按「是否与 2.5 在途改动抢文件」分流。

### P0-A · 契约止血 + 制度度量（**可立即开工，不碰 `vermes_state.py`，与 2.5 并行**）｜1~2 周

| # | 动作 | 落点 | 工作量 | 验收（可量化） |
|---|---|---|---|---|
| A1 | home channel 提示判定改走 `get_home_channel()` | `gateway/message_handler_mixin.py:2087-2106` | 0.25d | feishu/qqbot 仅写 config.yaml 时不误报 |
| A2 | cron 投递加 config 回落（env → legacy env → config） | `cron/scheduler.py:372-382` | 0.25d | 仅 config.yaml 设 home 时 cron 真投递到飞书 |
| A3 | 提示去重写进 `~/.vermes/gateway_state.json`（已有 `platforms` 键），不再拿 `not history` 当节流 | gateway 提示层 | 0.5d | 同一平台只提示一次 |
| A4 | 分歧度量脚本 `scripts/diverge_metrics.py` | `scripts/` + `reports/` | ~~0.5d~~ → **0.25d**（**已有实现**：`~/.hermes/skills/hermes-vermes-architecture/scripts/diverge_metrics.py`，2026-09-20 实测发现，只需搬入仓库并适配路径） | 产出第一份基线（同源 Jaccard / 工具 schema token / 静默失败提交数） |
| A5 | 重写 `UPSTREAM_SYNC.md`（修 remote 指向 + 版本基线 + 改可执行清单） | 仓库根 | 0.5d | 文档内的 upstream URL、版本号与实测一致 |
| A6 | CI 补 3 条 lane：`js-tests` / `tests-os`(mac+linux) / `install-e2e`(mac) | `.github/workflows/` | 1d | lane 在 PR 上真实生效 |
| **A7** | **GUI 设置入口 + 首条 DM 自动设定**（新增项，见下） | `frontend/src/components/Settings.vue` + `vermes_cli/gateway_channels.py` | 1~1.5d | 前端能设能改；首条 DM 自动设定后带取消入口的回执 |

> **secret scope 支（上游兜底链第一优先级）不在 P0**：本地无此层，属新建，降级 P1-B。

#### A7 详情（2026-09-20 补，来自方案 A §P0.1 去重/GUI 段，本次实测确认属实）

A1/A2 修掉的是「**设了能被认**」，但没解决「**用户根本没地方设**」：

| 实测 | 结果 |
|---|---|
| `grep -rn "home_channel\|homeChannel\|默认通知" frontend/src` | **零命中** → 桌面前端确实没有任何 home channel 概念 |
| `Settings.vue`「移动接入」渠道页 | 只有凭据字段（渠道卡片 = `channelsData` / `channelForms`，无通知频道字段） |
| `vermes_cli/gateway_channels.py` | **存在**（33,217 字节）→ 后端落点已备，接前端即可 |

→ 傻瓜式用户（只用桌面 GUI、不会 `/sethome`）**永远走不到 A1/A2 修好的那条路**。A7 动作：渠道卡片加「默认通知频道」字段 + 落 `platforms.<p>.home_channel` + 已授权用户首条 DM 自动设定并回执（带取消入口）。

> ⚠️ 与 E4「`/sethome` 双写」是同一件事的两个入口：CLI/GUI 都要能落到 `config.yaml` 的结构化真源上，否则 A1/A2 的收益只对命令行用户生效。

### P0-B · state.db 可靠性 6 件套（**必须等 2.5 主线 A2A/kanban 联调收口后**）｜3~4 天

**前置闸门（不可跳过）**：2.5 的 B4 `272caee7fa` + B8 `204f96d9cd` 已改 `vermes_state.py`（route_ledger 新表），与上游 `fix(state)` 系列同文件同层 → 直接 cherry-pick 必冲突。

| # | 移植内容 | 落点 | 验收 |
|---|---|---|---|
| B1 | 跨 VM FS（virtiofs/9p）主动拒 WAL | `vermes_state.py` `_ensure_wal` 前置 | virtiofs 挂载下走 DELETE 模式无 corruption |
| B2 | write-handle 注册表（长驻进程不再 mint 重复写句柄） | `vermes_state.py` SessionDB | gateway 跑 24h 后 `ls /proc/$(pid)/fd` 中写句柄 ≤ 1 |
| B3 | WAL 锁守卫（骑 SQLite descriptor + 同进程 refcount） | `vermes_state.py` 锁层 | 多 handle 并发写不触发 WAL 锁竞争 |
| B4 | 已删除 WAL sidecar 持有者枚举（macOS libproc） | 独立 doctor 诊断 | `kill -9` 后能列出真正持有者 |
| B5 | 外 profile 只读打开 | 跨 profile 路径 | 跨 profile 查询 fd 带 O_RDONLY |
| B6 | 二进程维护态拒外部 holder + 符号链接 home 解析 | 维护/doctor 路径 | 符号链接 home 场景 doctor 不误报 |

**冲突预警（写进执行说明）**：`vermes_state.py` 顶部注释即声明 WAL for gateway multi-platform；B2/B3 与 B4 建表同文件，**必须先重跑 B8 测试（`5964fa2408` 的 DEFAULT_DB_PATH 隔离测试）**；本地 WAL fallback 有 3 个月实战积累，B1 只能插入式扩展；**6 条单独 cherry-pick + `git log --follow` 验证，禁止批量合并**。

### P1 · 契约层 + 路由收口（2.5 后）｜4~7 周

| # | 动作 | 说明 | 工作量 |
|---|---|---|---|
| C1 | **目录化 invariants 文档** | 按 `agent/` → `gateway/` → `tools/` → `cron/` 分批；先固化 4 条：配置解析单一入口（禁 env-only 旁路）、禁静默失败、会话/消息形状不变式、测试放置与反 change-detector | 2w |
| C2 | **契约测试** | 每条高风险规则 ≥1 个不变式测试（非快照） | 1w |
| C3 | **配置口径普查** | 把 home channel 样本推全量：allowlist / platform enabled / notice delivery / approvals | 1w |
| C4 | **RoutingIdentity + 单一 session_key seam** | ⚠️ **greenfield 新建非收口**（全仓 `.py` 零 `RoutingIdentity`；`gateway/platform_registry.py` 是平台适配器注册表，与路由身份无关） | 3-5d |
| C5 | **secret scope 层**（home channel 完整对齐的前置） | 本地零命中，新建 | 2-3d |
| C6 | **cron 记忆化** `continuity=true` + 持久记忆/记事本 | 与 P0-B 不同文件，**可与 P0-B 并行** | 1-2d |

### P2 · 能力层白名单移植 ｜ 6~12 周

按迁移矩阵逐条 cherry-pick + 适配（上游模块 / 价值 / 冲突风险 / 方式 / 验收）：

| 上游模块 | 价值 | 冲突风险 | 建议方式 | 验收 |
|---|---|---|---|---|
| `agent/verification_evidence.py`、`verification_stop.py` | 验证证据链，压制"声称完成" | 中 | 移植+适配 | 构造反例被拦 |
| `agent/bounded_response.py`、`empty_response_guard.py`、`repetition_guard.py` | 静默/空回复/重复守卫 | 低 | cherry-pick | 三反例各拦一次 |
| `cron/` 健壮性（投递队列、不可达重试、occurrences、preflight） | 定时可靠性（3 vs 30 文件） | 中高 | 分模块移植 | 断网/不可用时投递不丢 |
| `context_compressor` 冷却阶梯 + auxiliary fallback | 长会话不退化 | 高（与自有压缩共存） | 只搬策略 | 长会话压缩失败有确定性兜底 |
| `delegate_task` 实时编排（steering / early-stop 保留部分结果 / 子输出 schema 校验） | 子 agent 可控 | 中 | 拆 3 commit | 三项各有反例 |
| CI 剩余 lane（`lockfile-diff`、`e2e-desktop`、`installer-tests`） | 拦静默失败 | 低 | 直接搬结构 | lane 生效 |

**delegate 对齐的两处 nuance（勿照抄上游）**：`list_background_tasks()`（`delegate_tool.py:174`）**已存在**，"缺 listing"说法夸大；`MAX_DEPTH = 1`（`:134`）是**主动拒孙辈**的设计选择，不是纯缺失——不要盲目"加回孙辈"。

### 防复发机制（三层，缺一不可）｜贯穿 P0~P2

> 来源：方案 A §5，本次全盘采纳——它是「制度层缺一半」这个核心洞察的落地形态。

| 层 | 载体 | 对应本路线条目 | 缺了会怎样 |
|---|---|---|---|
| 1 规则层 | 目录化 `AGENTS.md`（`agent/` → `gateway/` → `tools/` → `cron/`），每条规则一句话 + 为什么 | C1 | 唯一口径只存在于人脑里，同类 bug 反复出现 |
| 2 测试层 | 每条高风险规则 ≥1 个**不变式**测试（非快照/非 change-detector），跑在 CI | C2 | 规则写了没人守，改回去也没人知道 |
| 3 度量层 | `scripts/diverge_metrics.py` 月度产出：同源重合度 / 工具 schema token / 静默失败提交数 / 契约清单覆盖 | A4 | 分歧是变好还是变坏，无数据可判 |

三层与 P0~P2 的映射：**A4 出第一份基线**（P0 内）→ **C1/C2 把规则与测试立起来**（P1）→ 度量曲线持续监控（长期）。指标变差即视为回归。

### 明确不做（防范围爆炸）

- 不全量 merge 上游（分叉 ~5,743 文件 / +238K −1.2M 行）
- 不追上游 provider 适配（`openai_compat` 已覆盖）
- 不搬前端 UI 重构与 TUI 增强
- **HEIF/HEIC/AVIF**：判定为 **❌ 缺失**（方案 B 误标 ⚠️；`vision_tools.py`/`image_routing.py` 在 `vermes_cli` 内不存在，全仓 `heif|avif` 零命中）→ 列入"按需再议"，非本轮
- 不改 Vermes 独立版本编号规则（`v2.x.y`）

### 红线（任何一次跟进都不得覆盖）

`workflow_runtime/scheduler`（DAG 断点重试）、`memory_fabric/*`（记忆织物）、`capability_evolver` + `emergence_*`（自进化）、中文平台（微信/元宝/飞书评论）、245 个运行时技能、PyInstaller/Inno Setup 桌面分发链、ScholarForge、cadir_build 契约链。
> 移植前必须先跑迁移矩阵的冲突评估。

---

## 3. 时序图

```
现在（可与 2.5 并行，不碰 vermes_state.py）
  ├─ P0-A 契约止血 + 制度度量（1~2 周）
  │     A1/A2 home channel 单一口径（0.5~1d，先做）
  │     A3 去重 → A4 度量 → A5 文档 → A6 CI lanes
  │
2.5 主线完成（A2A/kanban 联调收口）
  ├─ P0-B state.db 6 件套（3~4d，过 route_ledger 回归闸门）
  ├─ C6 cron 记忆化（可与 P0-B 并行，不同文件）
  ├─ C4 RoutingIdentity（3~5d，greenfield）
  └─ C5 secret scope（2~3d）→ home channel 完整对齐
  └─ C1/C2/C3 契约层 invariants + 契约测试 + 口径普查
        └─ P2 白名单移植（6~12 周）
```

---

## 4. 验收口径（可量化）

| 指标 | 现状（实测） | 目标 |
|---|---|---|
| `AGENTS.md` 目录化 | 1 | ≥ 4（agent/gateway/tools/cron） |
| CI lane 类别 | 缺 install-e2e / tests-os / js-tests / lockfile-diff | 本轮补 3 类 |
| home channel 解析口径 | 2 个 env-only 旁路 | 0（全走 `get_home_channel()`） |
| 契约测试（不变式类） | 待复核 | ≥ 8 |
| 浏览器工具 schema token | 已核实：Vermes 核心 12 个 `browser_*` / 上游 19 个（12 + 7 vault + exec + browser-use）→「上游更收敛」**不成立** | 若决定收缩浏览器工具集，以 schema token 实测降幅为准（**产品取舍，非跟进项**） |
| 同源重合度基线 | 待 fetch upstream 后测 | 作监控曲线，不设硬目标 |
| 7×24 gateway state.db 写句柄 | 待实测 | ≤ 1 |

---

## 5. 待复核清单（下一步必须先做）

1. ~~`git fetch upstream` 后复核方案 A 的上游侧数字~~ → **已本地收口（errata E5）**：上游 git 检出就在本机 `~/.hermes/hermes-agent`（remote `fork` → `donghzs/hermes-agent.git`，HEAD `5a0c2fb89e`，总提交 37,857；近 30 天 13,436 为滚动值）。方案 A 的上游侧数字即由它测出，可复现，无需 fetch。
2. ~~复核浏览器工具真实注册数（方案 A 的 13 无实证）~~ → **已核实（errata E3）**：核心列表口径 Vermes 12 / 上游 19，「上游更收敛」作废。
3. `vermes_state.py` 专项审计：确认上游 `fix(state)` 6 条在 Vermes `SessionDB` 架构下是否**同形**（不同形则 P0-B 部分 N/A）
4. SessionDB 连接模型与 `route_ledger` 新表的交互回归面

---

## 附：本次取证命令族

- 上游：`curl api.github.com/repos/NousResearch/hermes-agent/git/trees/main?recursive=1`（15,485 条目）；`raw.githubusercontent.com/.../gateway/run_turn.py`、`cron/scheduler_delivery.py`
- 本地：`git remote -v`、`git rev-list --count HEAD`、`git log --oneline --since='30 days ago'`、`find -name AGENTS.md`、`ls .github/workflows`、`find <dir> -name '*.py' | wc -l` + `cat | wc -l`
- 契约：`sed -n '2083,2106p' gateway/message_handler_mixin.py`、`sed -n '372,382p' cron/scheduler.py`、`grep -n '_HOME_CHANNEL"' gateway/config.py`
- 缺失判定：`grep -rn "secret_scope\|def get_secret" --include=*.py`（零命中）、`grep -rn "RoutingIdentity" --include=*.py`（零命中）

---

## 附录 E · Errata（2026-09-20 独立复核修正，本段与正文同源，不留两份真相）

对本文档的独立复核结果：**正文对应位置已同步改掉**，此段记录「改了什么 / 证据 / 影响 / 连带待办」。

### 三处修正

| # | 正文位置 | 原表述 | 修正后 | 证据（本次实测） |
|---|---|---|---|---|
| E1 | §0.2 根因 | 「运行中的网关**不重读 .env**（`gateway/run.py` 无 dotenv）→ 进程内 `os.environ` 是启动旧值 → env-only 判定每新会话误报」 | 误报来源是 **config-only 路径不可见** + 外部改 .env 后未重启；`/sethome` 走 `save_env_value`，**同进程当场生效**，不是误报来源 | `vermes_cli/config.py:5144` `os.environ[key] = value` + `invalidate_env_cache()`；`gateway/run.py:543-546` **确有** `load_vermes_dotenv(...)`（「无 dotenv」不成立），语义为 user .env 覆盖 shell 导出值 |
| E2 | §0 表 row2、§1.2 修正 1 / 修正 2 | 「方案 A 称『Vermes 仅 `os.getenv`』**是错的**」；「只有 telegram 不提示」 | 前者是**误读**（方案 A 原文即「口径 A 结构化 14 处消费 vs 口径 B env-only 3 处」，从未主张全局缺失）；后者是**本机实测状态**，非平台级通则 | 方案 A 原文 §2.2 口径表；本机 `~/.vermes/.env` 实测（仅 `TELEGRAM_HOME_CHANNEL` 有值） |
| E3 | §1.2 修正 4、§4 表 | 「Vermes 13 个细粒度工具」「实测只数到 **5 个** 注册名」 | 核心列表口径（`toolsets.py` 的 `_vermes_CORE_TOOLS` / `_HERMES_CORE_TOOLS`）：**Vermes 12 / 上游 19**；且**上游并未收敛**（12 个照留 + 7 vault + `browser_exec` + `browser-use`）→「工具面收敛度是 Vermes 短板」**作废** | 两侧 `toolsets.py` 提取核心列表做差集实测 |

### 七条补充（E4–E7 为第一轮，E8–E10 为第二轮合并补）

| # | 位置 | 内容 | 为什么必须写进来 |
|---|---|---|---|
| E4 | §0.3 | `/sethome` **双写**建议（env 保留运维覆盖语义 + `config.yaml` 作结构化真源），待拍板 | 不双写则「改完即时生效」与「重启后仍生效」永远缺一条，A2 的语义分支悬空 |
| E5 | §5 #1、#2 | 上游检出就在本机 `~/.hermes/hermes-agent`（HEAD `5a0c2fb89e`、总提交 37,857）；浏览器数已核实 → 两项收口 | 省掉一次大仓 fetch，把「待复核」变成可决策项 |
| E6 | §4 验收（新增陷阱） | **提示文本不进 `logs/gateway.log`**（只走 `_deliver_platform_notice → adapter.send`），拿日志验收 A1 会永远显示「通过」；**必须用平台侧历史计数** —— 脚本**已存在**：`~/.hermes/skills/hermes-vermes-architecture/scripts/feishu_chat_timeline.py`（2026-09-20 实测确认，可直接使用） | 不写这条，A1 实质不可验收（拿 gateway.log 验收会永远显示「通过」） |
| E7 | §2 P0-A 的 A3 落点 | `gateway/status.py:542 write_runtime_status` 是**读-改-写**（`read → setdefault("platforms") → 更新已知键`）→ 塞标记**不会被冲掉**，但该文件是运行时健康诊断文件，UX 状态混入会污染 doctor/诊断语义 → 建议独立 `notices` 域或单独文件 | A3 按原落点能跑通，但会让诊断文件语义变浑 |
| **E8** | §2 P0-A 新增 **A7**、§2 新增「防复发机制（三层）」 | 第二轮通读方案 A 全文（178 行，非仅其摘要）后补入两项本版遗漏：①**GUI 设置入口 + 首条 DM 自动设定**；②**防复发三层**（规则 / 测试 / 度量）。方案 A 的 §4 移植矩阵、§7 验收、§8 前三个 PR 本版已覆盖，无遗漏 | 实测 `grep -rn "home_channel\|homeChannel\|默认通知" frontend/src` **零命中**、`vermes_cli/gateway_channels.py` 存在（33,217 字节）→ 傻瓜式用户无设置入口属实。**A1/A2 只修「设了能被认」，不修「没地方设」**；不做 A7 则本轮收益只对命令行用户生效 |
| **E9** | 落盘口径澄清（**归因已订正**） | 「找不到本文件（FINAL_20260920）」的说法 → **实测存在**：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`，与方案 A 自身文件（15,252 字节 / 178 行）**并列同一目录** | ①**归因订正（2026-09-20 08:10）**：该「找不到」反馈**并非本会话方案 A 的判断** —— 方案 A 本会话是用绝对路径 read 打开并逐行引用过本文档（§0.1、§1.2 修正 1–4），还写入了 E1–E7。该反馈应来自**另一个 Hermes 会话（飞书侧）或转述**，本条不再挂在方案 A 名下。②本机 `~/projects` 是 `~/Projects` 的软链（`pwd -P` 验证同一物理目录）。③保留的教训：**否定性结论不得建立在 `find ~` 这类易超时/截断的搜索上**（本会话已多次遇 137 截断） |
| **E10** | skill 挂载（**结论已修正**） | 初版记「实测不存在」→ **修正为：存在**，只是不在已搜的 roots 里 —— `~/.hermes/skills/hermes-vermes-architecture/SKILL.md`（**workbuddy 08:10 独立实测：13,893 字节，mtime 08:07**；方案 A 记录为 13,596 字节 / 240 行 @07:32，期间它又改过一次），含 `references/gateway-divergence-home-channel.md`（7,010 字节） | 硬证据：`ls -la ~/.hermes/skills/hermes-vermes-architecture/` 实测（2026-09-20 08:10）。**skill root 各引擎独立是设计，不是缺失**；「三处 skills 根目录」未含 Hermes root 才导致误判 —— 与 E9 同一教训（否定性结论前先确认搜索覆盖面）。**决定建议：单一来源 = 本仓库 `reports/` 文档；各引擎 skill root 只放「指针 skill」（写明仓库文档绝对路径 + 3 行摘要），不复制正文**，否则即两份真相 |

### E11 · 对照实验补做（2026-09-20 08:15，把「定性」升级为「已证明」）

首轮报告里我自设了限制：「对照实验未完成，13 条定性依据是失败原因 + 零引用，别当已证明」。本轮 `index.lock` 清除后已补做：

| 步骤 | 命令 | 结果 |
|---|---|---|
| 1 先固定改动 | `git add <4 路径>` + commit → `b7305707d4` | 有 commit 兜底后才敢折腾工作树（上一轮就是没兜底才险些丢改动） |
| 2 回退源码 | `git checkout eaa63411a2 -- gateway/gateway_utils.py gateway/message_handler_mixin.py cron/scheduler.py` | `resolve_home_channel_chat_id` 计数归 0（确为改动前版本） |
| 3 复跑同批 13 条 | pytest 指定 13 个 node id | **13 failed in 5.43s** —— 与改动后完全一致 |
| 4 恢复 | `git checkout HEAD -- <3 路径>` | `git status` 干净；契约测试 12 passed |

→ **13 条 pre-existing 已用对照实验坐实**，不再是「定性推测」。明细与分类见 `reports/known-failures-gateway-20260920.md`（含 1 条**疑真 bug：email self-message 过滤失效，禁止当噪声隔离**）。

### 复核后依然成立的结论（保留）

12 vs 1 `AGENTS.md`；CI 35 vs 14 且缺 `install-e2e` / `tests-os` / `js-tests` / `lockfile-diff`；`git remote` 正确而 `UPSTREAM_SYNC.md` 文档烂；home channel 双口径；会话重置语义（`not history` + `mode="both"` / `at_hour=4` / `idle_minutes=1440`）；**A1/A2 已落地**（本轮独立复跑 `tests/vermes_cli/test_home_channel_resolution.py` → **12 passed in 14.43s**）。

### 连带待办（错因已进生产代码，必须跟）

1. 订正 `gateway/message_handler_mixin.py:2089-2092` 的根因注释（按 E1 口径重写）。
2. 方案 A（`vermes-upstream-catchup-roadmap_20260919.md`）已并入本文档，其文首已标注 superseded，不再单独维护。
3. 复核脚本已跑通的前提：核心工具列表口径要用 `toolsets.py`，**不要**用 `agent/tool_guardrails.py` 的注册名或全仓标识符计数（E3 的教训）。
