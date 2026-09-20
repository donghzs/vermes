# Vermes 跟进 Hermes 上游 —— 源码修复与取长补短路线方案

> ⚠️ **本版已被 `vermes-upstream-catchup-roadmap_FINAL_20260920.md` 合并取代**（含 errata E1–E7 的复核修正）。
> 唯一口径以 FINAL 为准，本文仅作历史留档，不再单独维护；其中的「13 个浏览器工具」「照抄上游两处 hunk」等表述已在 FINAL 中修正。

- 撰写：2026-09-19
- 取证基线：Hermes `5a0c2fb89e`（v0.21.3 / 2026.9.14）↔ Vermes `a5d265d8cf`（同日）
- 全部数字为当日实测；标注「判断」的为推论，标注「待核实」的需下一步验证
- 本方案不推翻既有的「锁定基线、白名单移植」决策，而是把它升级为：
  **契约对齐（制度层）+ 白名单移植（能力层）+ 度量驱动（防复发）三件套**

---

## 0. 结论先行

Vermes 追不上上游的原因，不是某个功能没搬，而是**制度层缺了一半**：

1. Hermes 有 12 个按目录写的 `AGENTS.md`，内容是「不可违反的 invariants + 贡献 rubric + 测试纪律」；Vermes 只有 1 个（讲 git 流程与 pre-commit）。→ 同类 bug 会在 Vermes 反复出现，因为没有任何东西规定「唯一口径」。
2. Vermes 的 `UPSTREAM_SYNC.md` **文档描述与实际不符且版本落后**：文档里写「上游最新 0.18.2+ / commit 4a69a6620」而实际上游已是 v0.21.3 / `5a0c2fb8`；文档把 upstream 记为 `github.com/donghzs/vermes` 并称作「官方 Vermes Agent」，但**实测 git 配置是正确的**（`upstream` / `upstream2` → `github.com/NousResearch/hermes-agent.git`）。→ 管道没坏，是文档烂掉了，同步流程因此不可执行。
3. CI 门禁数量差距不大（35 vs 14），但**类别**缺关键几类（桌面/安装端到端、多 OS 测试 lane、JS 测试、lockfile 差异门禁），而 `UPSTREAM_SYNC.md` 明确把「CI/CD 管道」列为**不同步类别** → 制度性放弃了上游最值钱的那部分资产。

直接证据（同一类问题的活体样本）：**home channel 在 Vermes 里有两套口径** —— 投递走结构化 config（14 个调用点），但提示判定与 cron 投递只读环境变量（3 处）。结果：只有恰好设过环境变量的 telegram 不提示，feishu / qqbot 以及**未来新接的任何平台**都会提示。这类 bug 不会因为「多写测试」被发现，只能靠「单一口径 + 契约测试」约束。

---

## 1. 基线实测

| 维度 | Hermes 上游 | Vermes | 说明 |
|---|---|---|---|
| 最新提交 | `5a0c2fb89e` | `a5d265d8cf` | 同日 |
| 近 30 天提交 | 13,513（fix/revert 5,595） | 574（fix/revert 183） | 迭代势能 23× |
| 总提交 | 37,857 | 1,702 | 两 repo 零共享祖先 |
| Python 文件 | 6,717 | 2,415 | |
| `agent/` | 294 文件 / 118,765 行 | 210 文件 / 107,829 行 | 规模接近，结构不同 |
| `tools/` | 314 / 109,483 | 104 / 73,171 | 上游工具更细粒度 |
| `cron/` | 30 / 16,286 | **3 / 3,415** | 调度健壮性差距最大处 |
| `gateway/` | 165 / 88,894 | 89 / 90,416 | 规模相当 |
| 测试文件 | 4,578 | 1,449 | |
| CI workflows | 35 | 14 | 类别差异见 §2.1 |
| 核心工具数 | 60 | 49 | 见 §2.3 |
| 运行时技能 | 173 | **246** | Vermes 领先 |
| `AGENTS.md` | **12**（按目录） | **1** | 制度层核心差距 |
| 同源文件重合度 | — | `model_tools` 0.11 / `toolsets` 0.09 / `utils` 0.14 / `context_compressor` 0.08 | 深度分歧 |
| `context_compressor.py` | 5,113 行 | 2,171 行 | 上下文治理覆盖度 |

---

## 2. 四类差距（分开处理，不要混）

### 2.1 制度层（最贵，最容易被忽略）

- 规范文档层：Hermes 12 个 `AGENTS.md` 覆盖 `agent/ tools/ gateway/ cron/ plugins/ web/ skills/ tui_gateway/ apps/desktop/` 等，写的是硬约束（prompt 缓存不得破、消息角色严格交替、facade+sibling 结构与行数上限、测试放置与「不写 change-detector」、profile scope 绑定规则）。
- Vermes 仅 1 个根 `AGENTS.md`，内容为 git 分支规范 + pre-commit 敏感信息检查。→ 没有 invariants，等价于「每次改动都靠人记」。
- CI 类别缺口（对照上游 workflow 名）：`install-e2e`（mac/win）、`tests-os` 多平台 lane、`windows-venv-e2e`、`js-tests`、`lockfile-diff`、`e2e-desktop`、`installer-tests`。
  - 判断：这几类正是「静默失败」类 bug 的拦网；Vermes 近 30 天提交里可见多条崩溃/静默/卡死类修复（如 CLI 输出完全静默 + TypeError、UnboundLocalError、多模态回合边界 TypeError、积木市场卡死后端）。
- `UPSTREAM_SYNC.md` 不可执行：版本落后、远程仓库指向自身、品牌混淆。

### 2.2 契约层（当前 bug 的根源）

样本：home channel 双口径。

| 口径 | 实现 | 调用点 | 实际行为 |
|---|---|---|---|
| A 结构化 | `gateway/config.py:569 get_home_channel()` | 14 处（`session.py:1429`、`session_mixin.py:534`、`lifecycle_mixin.py:539`、`run.py:2855`、`send_message_tool.py:271`、`webhook.py:927`、`discord.py:2561`、`cli.py:7149` …） | 认 config.yaml，且 env 经 bridge（`config.py:1150-1713`）进 config |
| B 环境变量 | `cron/scheduler.py:372 _get_home_target_chat_id()`；`gateway/message_handler_mixin.py:2087-2106` 提示判定 | 3 处 | 只读 env，看不见 config |

后果：提示判定与投递解析用两套口径 → 设了也不认、认了也不投递；`/sethome` 只写 env（`gateway/slash_handlers/config_handlers.py:865-888`），而桌面前端零处 home channel 概念（`frontend/src/components/Settings.vue` 的「移动接入」渠道页只有凭据字段）→ 傻瓜式用户根本没有设置入口。

**关键补充（已核实上游）**：这不是「要不要重新设计」的问题，而是 **Vermes 落后上游一整代**，上游两处都已统一：

| 位置 | 上游现状（实测） | Vermes 现状 |
|---|---|---|
| 提示判定 | `gateway/run_turn.py:1391-1422`：secret scope → `os.getenv` → `self.config.get_home_channel()` → 二级 profile config 四路兜底 | `gateway/message_handler_mixin.py:2087-2106`：仅 `os.getenv` |
| cron 投递 | `cron/scheduler_delivery.py:472-479`：env mirror → legacy env → **config.yaml `home_channel`**（`_get_config_home_channel`，注释明确写"env 只是 best-effort 镜像，只读 env 会丢投递"） | `cron/scheduler.py:372-382`：仅 env |

→ 结论：**回移上游实现**（判定层 + cron 层各一处），不需要自创方案；`get_home_channel()` 本身已能看见 env（env 经 bridge 进 config），无需再加回落。

**为什么用户感觉"每条都提示"**：判定条件含「该会话无 history」，源码语义是"每个新会话第一条"。Vermes 的会话重置策略是 `mode="both" / at_hour=4 / idle_minutes=1440`（`gateway/config.py::SessionResetPolicy`），叠加压缩轮换（会话可在对话中途轮换）与网关重启 —— **间歇使用的用户几乎每次回来都落在"新会话第一条"**。平台侧实测口径见过「67 条用户消息 / 12 次提示 = 12 个会话」，所以先做计数再下结论，不要凭印象断言"每条"。

判断：此类「同一语义两套解析」的问题在其他配置项上大概率同样存在（待核实：allowlist、platform enabled、notice delivery 的解析路径是否一致）。

### 2.3 能力层（功能有无）

- 工具面收敛：上游核心工具 60（浏览器已收敛为 `browser_exec` + 7 个 vault 工具）；Vermes 49 且浏览器仍是 13 个细粒度 `browser_click/snapshot/type/scroll…`。同等 LLM 下，schema 越大、选择错误率越高、token 越贵。
- 验证闭环：上游 `agent/verification_evidence.py`、`verification_stop.py`、`bounded_response.py`、`empty_response_guard.py`、`repetition_guard.py`；Vermes 侧对应物为 `claim_verifier.py`、`tool_guardrails.py`（不构成等价闭环）。
- 调度健壮性：`cron/` 30 文件 vs 3 文件（上游含投递队列、不可达重试、incidents、occurrences、preflight 等）。
- 上下文治理：`context_compressor.py` 5,113 vs 2,171 行（上游含两阶段压缩、冷却阶梯、auxiliary fallback chain）。

### 2.4 Vermes 领先项（跟进过程中必须保住）

`workflow_runtime/scheduler`（DAG 断点重试）、`memory_fabric/recall/reflection/budget`（记忆织物）、`capability_evolver` + `emergence_*`（自进化）、中文平台（微信/元宝/飞书评论）、246 个运行时技能、PyInstaller/Inno Setup 桌面分发链、ScholarForge 等垂直域。

> 红线：任何一次「跟进上游」都不得覆盖上述模块。移植前必须先跑迁移矩阵里的冲突评估。

---

## 3. 路线（三阶段）

### P0 — 止血 + 建立度量（1~2 周）

1. **home channel 单一口径修复 = 回移上游实现**（不是自创方案）
   - 判定层：`gateway/message_handler_mixin.py:2087-2106` → 照抄上游 `gateway/run_turn.py:1391-1422` 的兜底链（secret scope → env → `self.config.get_home_channel()`；二级 profile 那支 Vermes 单 profile 不适用，只搬 `get_home_channel` 一支）。
   - 投递层：`cron/scheduler.py:372-382 _get_home_target_chat_id()` → 照抄上游 `cron/scheduler_delivery.py:472-479`（env → legacy env → config.yaml home_channel）。
   - 上游改动定位：`cd ~/.hermes/hermes-agent && git log -S "Also honor in-memory / yaml home_channel" -- gateway/`，取 hunk 照抄。
   - 去重：同一平台只提示一次，标记落在已有的 `~/.vermes/gateway_state.json`（已含 `platforms` 键），不再拿 `not history` 当节流。
   - GUI 入口（傻瓜式）：`Settings.vue`「移动接入」渠道卡片增加「默认通知频道」；后端 `vermes_cli/gateway_channels.py` 落 `platforms.<p>.home_channel`；已授权用户首条 DM 自动设定 + 回执（带取消入口）。
2. **分歧度量脚本**（放 `scripts/`，CI 或本地月度跑）
   - 同源文件 Jaccard 表、契约清单 diff、工具 schema token 数、静默失败提交计数。
   - 输出 markdown 到 `reports/`，进版本管理。
3. **重写 `UPSTREAM_SYNC.md`**：修正 upstream remote 指向与版本基线，改成可执行清单（模块 + 价值 + 冲突风险 + 验收）。
4. **CI 补三条 lane**（性价比最高）：`js-tests`、`tests-os`（mac+linux）、`install-e2e`（至少 mac）。

**验收**：feishu / qqbot 首次会话不再提示（两次入站日志对比）；cron 用 config 里的 home channel 能真正投递到飞书；度量脚本产出第一份基线。

### P1 — 契约层（3~6 周）

1. **起草 Vermes 自己的 invariants 文档**，按目录分批，优先级 `agent/` → `gateway/` → `tools/` → `cron/`。至少先固化四条：
   - 配置解析单一入口（禁止 env-only 旁路）；
   - 禁止静默失败（工具/CLI 错误必须有可见出口）；
   - 会话与消息形状不变式（角色交替、单一缓存破点）；
   - 测试放置与反 change-detector 纪律。
2. **契约测试**：为上述每条写 1~2 个不变式测试（不是快照测试）。
3. **工具面收敛**：浏览器 13 个细粒度工具收敛为 `browser_exec` 式入口 + 少量专用工具；实测 schema token 降幅与工具选择错误率变化。
4. **配置口径普查**：把 §2.2 样本推全量（allowlist / platform enabled / notice delivery / approvals），一次修完同类。

### P2 — 能力层白名单移植（6~12 周）

按迁移矩阵逐条 cherry-pick + 适配；优先「纯逻辑、低 UI 耦合、高事故率」的模块。

---

## 4. 白名单移植矩阵（候选，需逐条评估冲突）

| 上游模块 | 价值 | 冲突风险 | 建议方式 | 验收 |
|---|---|---|---|---|
| `agent/verification_evidence.py`、`verification_stop.py` | 验证证据链，压制「声称完成」 | 中 | 移植 + 适配 Vermes 的 agent 循环 | 构造反例：无证据的完成声明被拦 |
| `agent/bounded_response.py`、`empty_response_guard.py`、`repetition_guard.py` | 静默失败/空回复/重复守卫 | 低 | cherry-pick | 三个反例各拦一次 |
| `cron/` 健壮性（投递队列、不可达重试、occurrences、preflight） | 定时任务可靠性（当前 3 文件 vs 30 文件） | 中高 | 分模块移植，逐模块回归 | 断网/平台不可用时投递不丢 |
| `context_compressor` 冷却阶梯 + auxiliary fallback | 长会话不退化 | 高（与 Vermes 自有压缩共存） | 只搬策略，不搬实现 | 长会话压缩失败有确定性兜底 |
| gateway profile scope 绑定规则 | 多 profile 不串数据 | 高 | 先文档化，再按需移植 | A→B→A 双 profile E2E |
| CI：`install-e2e`、`tests-os`、`js-tests`、`lockfile-diff` | 拦静默失败 | 低 | 直接搬 workflow 结构 | lane 在 PR 上生效 |
| 工具 schema 收敛实现（`browser_exec` 模式） | token/准确率 | 中 | 参考实现，Vermes 自写 | token 降幅实测 |
| `AGENTS.md` 目录化规范体系 | 制度层 | 低 | 按 Vermes 实际结构调整后落地 | 每条 invariants 有对应测试 |

---

## 5. 防复发机制（三层，缺一不可）

1. **规则层**：目录化 `AGENTS.md`，每条规则一句话 + 为什么。
2. **测试层**：每条高风险规则至少一个不变式测试，跑在 CI。
3. **度量层**：分歧度量脚本月度产出 —— 同源重合度、工具 schema 规模、静默失败提交数、契约清单覆盖。指标变差即视为回归。

---

## 6. 明确不做（避免范围爆炸）

- 不全量 `merge` 上游（分叉 ~5,743 文件、+238K/−1.2M 行，不可控）。
- 不追上游 provider 适配（`openai_compat` 已覆盖）。
- 不搬前端 UI 重构与 TUI 增强。
- 不改 Vermes 独立版本编号规则（`v2.x.y`）。
- 不在一次改动里同时动「制度层 + 能力层」。

---

## 7. 验收口径（可量化）

| 指标 | 现状 | 目标 |
|---|---|---|
| `AGENTS.md`（目录化规范） | 1 | ≥ 4（agent/gateway/tools/cron） |
| 契约测试条数（不变式类） | 待核实 | ≥ 8 |
| CI lane 类别覆盖 | 缺 4 类 | 补 3 类 |
| 工具 schema token（浏览器集） | 13 个细粒度工具 | 收敛后实测降幅 |
| 近 30 天静默/崩溃类修复提交 | 6+ | 逐月下降 |
| 同源文件重合度（度量基线） | `model_tools` 0.11 / `toolsets` 0.09 | 作为监控曲线，不设硬目标 |

---

## 8. 立刻可开工的前三个 PR

1. `fix(gateway): unify home-channel resolution across notice, cron and config`（含 3 处改动 + 2 个契约测试）
2. `chore(ci): add js-tests / tests-os / install-e2e lanes`
3. `docs: rewrite UPSTREAM_SYNC.md as an executable移植清单 + 新增 scripts/diverge_metrics.py`

---

## 附：本次取证的原始命令族

- 规模/迭代：`git log --since="30 days ago" --oneline | wc -l`、`find … -name '*.py' | wc -l`
- 同源重合度：`comm -12 <(sort -u A) <(sort -u B)` + Jaccard
- 契约口径：`grep -rn "get_home_channel(" --include=*.py`、`grep -rn "_get_home_target_chat_id" --include=*.py`
- 制度层：`find -name AGENTS.md`、`ls .github/workflows`
- 运行时核实：`lsof`、`~/.vermes/logs/gateway.log`、`~/.vermes/sessions/sessions.json`、`~/.vermes/gateway_state.json`
