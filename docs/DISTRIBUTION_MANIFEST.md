# DISTRIBUTION_MANIFEST.md — Vermes 作为 Hermes 上游发行版的契约

> 建立日期：2026-09-21 · 冻结锚：`888bf8a344`（非发版 tag；发版 tag `v2.5.1` 可移动，当前指向 `5142343cbd`）
> 定位：**Vermes = Hermes Agent 的中文发行版（distribution）**，不是平行分叉。
> 目标：**保持 Vermes 独特开发，同时让取长上游从「偶发人工考古」变成「常态化、可度量、低风险」。**

---

## 0. 一句话结论

- **冻结锚（freeze_ref）**：`888bf8a344` —— Vermes 侧“从这里开始算契约税”的不可移动锚点。**发版 tag `v2.5.1` 可以继续移动，但 freeze_ref 永久固定**，`upstream_watch.py boundary` 默认读它，不跟发版 tag。

发行版化的核心不是"改成能 merge 上游"（做不到，见 §1 硬约束），而是
**把「哪些是 Vermes 的、哪些是上游的」用机器可检查的方式固定下来**，
然后让每次取长都走同一条：雷达 → 闸门 → 登记 → 验收 → 回退。

---

## 1. 硬约束（实测，非推断）

| 事实 | 实测命令 | 结果 |
|---|---|---|
| 与上游**无共同祖先** | `git merge-base main upstream/main` | **空**（`--is-shallow-repository=false`，非 shallow 导致） |
| 上游领先 | `git rev-list --count main..upstream/main` | **39,490** commits |
| Vermes 自有 | `git rev-list --count upstream/main..main` | **1,758** commits（自 v1.0.3 起独立演化） |
| 上游速度 | `git describe --tags upstream/main` | `v2026.9.14-4787-g3b7eda0887`（一个 tag 周期后又走了 4,787 个 commit） |
| 同源相似度 | `scripts/diverge_metrics.py`（见 `reports/diverge-baseline-20260920.md`） | `toolsets.py` 0.09 / `model_tools.py` 0.12 / `utils.py` 0.15 —— 深度 diverge |

**推论（必须接受，不要反复尝试推翻）**：

1. ❌ `git merge upstream/main` —— 无共同祖先，不可用
2. ⚠️ `git cherry-pick <upstream-sha>` —— 可执行，但补丁上下文已 diverge，冲突率高；
   仅用于**单文件/单特性的能力级搬运**（`chore/upstream-sync` 分支已有成功先例：
   `a8cb8ebc7a` = per-platform prompt-hint overrides，取自上游 `3ead2bdd0`）
3. ✅ 正确形态：**能力级取长**——看懂上游实现 → 在 Vermes 侧重写/适配 → 契约测试兜底

---

## 2. 分区（机器可检查的归属）

脚本真源：`scripts/upstream_watch.py` 的 `ZONES`（本表与之一致，改一处需同步另一处）。

| 分区 | 路径 | 判定规则 |
|---|---|---|
| **own**（发行版自有·红线） | `vermes_cli/`、`agent/memory_fabric.py`、`agent/capability_evolver.py`、`agent/workflow_runtime.py`、`agent/compression_scheduler.py`、`gateway/platforms/`、`scholarforge/`、`acp_registry/`、`frontend/`、`electron/`、`installer/`、`locales/`、`scripts/build-*`、`scripts/sync-version.sh`、`docs/vermes/`、`scripts/vermes/` | 上游同名改动**禁止直接搬运**，只参考思路；Vermes 侧在此区改动是**正常开发** |
| **follow**（上游跟随区） | `plugins/`、`tools/`、`harness/`、`cron/`、`.github/`、`docs/`、`scripts/` | 优先跟随上游；Vermes 侧在此区改动 = **契约税**，需登记或外置为插件 |
| **core**（同源核心·已 diverge） | `agent/`、`gateway/`、`acp_adapter/`、`memory/`、`runner/`、`cli/` | 个案评估，需对照 `diverge_metrics` 度量 |

### 红线清单（Vermes 领先项，**任何取长都不得覆盖**）

| 能力 | 实现 | 规模 |
|---|---|---|
| 自进化 | `agent/capability_evolver.py` | 485 行 |
| 记忆织物 | `agent/memory_fabric.py` | 1,377 行 |
| 工作流 DAG | `agent/workflow_runtime.py`（+ scheduler/pipeline） | 291 行 |
| 上下文压缩调度 | `agent/compression_scheduler.py` | 656 行 |
| 中文平台 | `gateway/platforms/` | 40 文件 / 17 平台 |
| 运行时技能库 | `~/.vermes/skills/` | 245 个 SKILL.md |
| 双平台打包链 | `scripts/build-macos*.sh` / `build-windows*` | PyInstaller + DMG / NSIS |
| ScholarForge | `scholarforge/` | 论文写作 27+ 工具 |
| CAD-IR | 用户技能目录 `cad_ir_contract.py` | 27 KB 契约编译器 |

---

## 3. 闸门（每次取长/每次发版前跑）

| # | 闸门 | 命令 | 通过条件 |
|---|---|---|---|
| G1 | **跟随区契约税** | `.venv/bin/python scripts/upstream_watch.py boundary --since <tag>` | 0 处=PASS；≤10 处=WARN（逐条登记）；>10 处=FAIL（先外置） |
| G2 | 上游红线扫描 | `.venv/bin/python scripts/upstream_watch.py watch` | 红线命中项逐条人工判定，不得直接搬运 |
| G3 | 并存不变量 | `python3 scripts/vermes/check_coexistence.py --deep` | FAIL 0 |
| G4 | 目标测试 | `pytest <相关文件>` | 0 failed |
| G5 | 版本号一致 | `cat version.txt` + 四处 package.json/pyproject | 全部等于 `vermes_cli/__init__.py:__version__` |

> G1 首次实测（2026-09-21，`v2.4.9..main`）：**契约税 48 处 / 100 commits** → 当前 **FAIL** 档。
> 这是 S1 的主要工作：逐条判定「登记（有意偏离）」还是「外置为插件」。
>
> **G1 绿色边界（Hermes 2026-09-23）**：G1/boundary 的 PASS **只覆盖 follow 区**。
> core 区（`agent/`、`gateway/` 等）改动**不计税、不进 G1 绿**，改由 §7d `CORE_DIVERGE_LEDGER`
> 逐条登记（为什么改 core + 上游对应面）。**「未登记税=0」≠「离上游很近」**——
> core 登记率才是分叉距离的真读数，进月度复查点（roadmap §8.9）。

---

## 4. 取长登记 ledger（每个取长一个条目，写在本文件 §7）

```
### <日期> · <上游 hash 或 PR> · <一句话价值>
- 上游做什么：<...>
- Vermes 差异：<为什么不能直接搬 / 改了什么>
- 冲突面：<命中分区 / 涉及文件>
- 验收：<pytest 命令 + 数字>
- 回退：<开关名 或 revert commit>
```

**判定优先级**（避免无效搬运）：
1. 安全修复（`fix(security)`）→ 最高优先，即使要重写也做
2. 关键 bug（崩溃 / 数据丢失 / 凭据错误）→ 高
3. 上游新增能力但 Vermes 已有等价实现 → **不搬**（红线：不追 provider 适配、不追 UI 重构/TUI）
4. 纯上游品牌/英文文案 → 不搬

---

## 5. 工具

| 工具 | 作用 |
|---|---|
| `scripts/upstream_watch.py watch` | 上游雷达：基线以来上游改动 → 分区分类 → 取长候选 + 红线告警；报告 `reports/upstream-watch-<date>.md` |
| `scripts/upstream_watch.py boundary` | 边界闸门：Vermes 跟随区契约税；报告 `reports/dist-boundary-<date>.md` |
| `scripts/diverge_metrics.py` | 同源相似度度量（Jaccard），判断某个 core 文件还能不能搬 |
| `scripts/vermes/check_coexistence.py --deep` | 5 个安装并存不变量（12 项） |
| `reports/.upstream-baseline.json` | 雷达基线，下次只看增量 |

---

## 6. 路线（对齐 `reports/vermes-distribution-ROLLOUT-PLAN_20260920.md` 的 P0–P5，不另起炉灶）

| Sprint | 内容 | 对应 ROLLOUT | 验收（硬） | 工期 |
|---|---|---|---|---|
| **P0** | 冻结一版（**v2.5.1，本次完成**：版本真源对齐 + CHANGELOG + tag） | P0 | tag 存在 + CHANGELOG 完整 + release notes 含已知问题 | ✅ 完成 |
| **S1** | 制度层：雷达 + 闸门 + 本契约（本次落地）+ **上游 canary CI lane** | 贯穿规则 3 | G1 降到 WARN 档（契约税 ≤10 且逐条登记）；canary 周跑一次 | 3–5 天 |
| **S2** | P1 自进化插件化：静态块→`register_system_prompt_section`；动态块→`pre_llm_call`；工具后→`post_tool_call` | P1 | 注入文本与 v2.5.1 **逐字相同**；diff 仅落 `plugins/`；记录一次取长的人时 | 1–2 周 |
| **S3** | P3 记忆织物 → `MemoryProvider`（**先做召回 A/B**） | P3 | 同一批问题，v2.5.1 vs 新版召回并排；变差即停 | 2–3 周 |
| **S4** | P4 中文平台 → `plugins/platforms`（每平台必测断线重连不丢消息） | P4 | 一平台一验收 | 2–3 周 |
| **S5（可选）** | 形态 B：引擎作依赖、Vermes 只留 plugins+品牌+打包 | P5 | 追上游 = 版本号 +1 | 视情况 |

**拍板建议**：做 S1 + S2，**暂不做 S5（形态 B）**。
理由：形态 B 要重做整条打包链（PyInstaller 内嵌 → 依赖安装），且上游是否能作为 pip 依赖安装
**尚未核实**（不得凭假设推进）；而 S1+S2 已能把"取长成本"从考古降到可控，投入产出比更高。
S5 的前置核实项已登记为待办（§8）。

---

## 7. Ledger（取长登记）

### 待判定（来自 `reports/upstream-watch-20260921.md`，2026-09-21 首次扫描）

| 上游 hash | 主题 | 与 Vermes 的关联 | 建议 |
|---|---|---|---|
| `b534f4b8c8cd` | fix(security): match credential env names case-insensitively | **不是** v2.5.1 `key_env` 收口的同域问题（首轮推断有误，2026-09-21 读上游 diff 后更正）。真实作用域：凭据 env **屏蔽名单**在 Windows 上被大小写变体绕过（`openai_api_key` 注册被接受，随后 `os.getenv` 在 Windows 解析到真实 `OPENAI_API_KEY`），经 `env_passthrough` / `docker_forward_env` / `docker_extra_args` / `_filter_secret_env` / `_scrub_credentials` 注入 SSH、Docker exec 与兄弟 profile。Vermes 侧有对应机制：`tools/env_passthrough.py`、`tools/environments/docker.py`、`tools/session_env_registry.py` | 优先核实：Vermes 的屏蔽名单是否同样大小写敏感；若是 → 同类漏洞，取长 |
| `940c6109943a` | fix(security): keep `_HERMES_PROVIDER_ENV_BLOCKLIST` importable | 同一屏蔽名单的可导入性（防止重构后守卫失效） | 随 `b534f4b8c8cd` 一并评估 |

### 已合入

| 日期 | 来源 | 内容 |
|---|---|---|
| 2026-09（在途分支） | 上游 `3ead2bdd0` | per-platform prompt-hint overrides（`chore/upstream-sync` → `a8cb8ebc7a`） |
| 2026-09-21 | 上游 `b534f4b8c8cd` | **T3**：凭据 env 屏蔽名单大小写不敏感匹配（`_is_env_blocklisted` casefold）。落点 `tools/env_passthrough.py` + `tools/environments/local.py` + `tools/environments/docker.py`，验收 `tests/tools/test_env_passthrough.py`（19 passed），commit `a48811769d` |

---

## 7b. DIVERSION_LEDGER（有意偏离登记）

> 语义：**follow 区的 Vermes 改动，若已在本表登记 = 有意偏离，不算契约税**。
> 字段：`id` / `路径` / `偏离类型`（产品增强|品牌|适配|修复） / `登记日期` / `理由`。
> 脚本 `upstream_watch.py` 用固定格式解析本表（`<!--DIVERSION_LEDGER:START-->` 至 `END` 之间），
> 只把「follow 区改动 && 两账都未登记」算税。

<!--DIVERSION_LEDGER:START-->
| id | 路径 | 类型 | 登记日期 | 理由 |
|---|---|---|---|---|
| D-001 | `tools/env_passthrough.py`, `tools/environments/local.py`, `tools/environments/docker.py` | 修复 | 2026-09-21 | T3：凭据 env 屏蔽名单大小写不敏感（对齐上游 b534f4b8c8cd） |
| D-002 | `docs/DISTRIBUTION_MANIFEST.md` | 品牌/发行版 | 2026-09-21 | Vermes 独有发行版契约文档（上游无此文件） |
| D-003 | `tools/kanban_tools.py` | 适配 | 2026-09-22 | L-010：session_id reader 迁 `get_session_env`（自有 P0，见 TAKEALONG L-010/T13）；有意偏离，后续同文件大改仍需再登记 |
| D-004 | `cron/scheduler.py` | 修复 | 2026-09-22 | L-007：cron 标记改 contextvar（自有 P0，见 TAKEALONG L-007/T11）；有意偏离 |
| D-005 | `docs/plans/` | 品牌/发行版 | 2026-09-22 | Vermes 产品路线/报告（前端打扰治理等），上游无此树；目录级有意偏离 |
| D-006 | `tools/code_execution_tool.py` | 产品增强 | 2026-09-22 | UX 打扰治理 P0-5：沙箱产物 `intermediate` 标记 + delivery 过滤消费；有意偏离（须重打 DMG 对打包用户生效） |
| D-007 | `tools/send_message_tool.py` | 修复 | 2026-09-23 | `35532f29fb57`：QQBot target 解析——32 位 openid/数字群号识别为显式目标，避免 directory 命中后解析返回 None 被丢弃；自有产品修复，有意偏离（后续同文件大改仍需再登记） |
| D-008 | `tools/file_operations.py` | 修复 | 2026-09-23 | `018b4e761ade`（本文件部分；同 commit `code_execution_tool.py` 已在 D-006/L-013）：read_file 单行/长行截断不说谎——行内截断并入 `truncated`，`wc -l` 改 awk 正确数行；自有缺陷修复，有意偏离 |
| D-009 | `tools/file_tools.py` | 修复 | 2026-09-24 | `34bba18558`：write-deny 补 `/var/`+`/private/var/` 前缀（macOS 旁路，注释写了要挡但前缀表从未收录）；`/var/folders/` 临时目录白名单保留。自有安全修复，有意偏离 |
<!--DIVERSION_LEDGER:END-->

---

## 7d. CORE_DIVERGE_LEDGER（core 区有意分叉登记）

> 语义：**core 区**（`agent/`、`gateway/`、`acp_adapter/`、`memory/`、`runner/`、`cli/`）的
> Vermes 改动不计 G1 契约税，但**必须登记**——记「为什么改 core + 上游对应面是否有 + 为何不能走插件形态」。
> 这是 G1 绿色的盲区补丁（Hermes 2026-09-23）：`未登记税=0` 只反映 follow 区干净，
> core 分叉成本由本表反映。字段：`id` / `路径` / `为什么改 core` / `上游对应面` / `登记日期`。
> 脚本 `upstream_watch.py boundary` 解析本表（`<!--CORE_DIVERGE_LEDGER:START-->` 至 `END`），
> 报告 core 登记率；**core 登记率进月度复查点**。
> 同一文件后续大改仍需追加条目（或更新理由行），禁止「登一次就永久免税」。

<!--CORE_DIVERGE_LEDGER:START-->
| id | 路径 | 为什么改 core | 上游对应面 | 登记日期 |
|---|---|---|---|---|
| C-001 | `agent/system_prompt.py` | S2 walking skeleton：统一注入入口 `_resolve_section` + 15 注入点迁入。**不能走插件形态**——三层 prompt 组装与 cache 前缀稳定性是核心运行时（gold 17×3 逐字门钉住）；插件只能 `register_system_prompt_section` 注册段，不能替换组装路径 | 上游无三层组装等价物；上游 `register_system_prompt_section` 是注册面（S2.1 已 adapter 落 `plugins.py`），组装仍在 core | 2026-09-23 |
| C-002 | `agent/prompt_processor_loader.py` | S2 adapter 基座：YAML processor 加载/合并优先级（plugin < builtin < user）+ `compute_manifest_hash` canonical hash + `list_prompt_sections` 可发现性。**不能走插件形态**——插件是注册进本加载器的客户，不能自成加载路径（否则双源/双优先级） | 上游无 YAML processor 体系（Vermes 反向领先）；上游只有 session 级 section 快照 | 2026-09-23 |
| C-003 | `agent/agent_init.py` | L-010/T13：`VERMES_SESSION_ID` 改 task-local contextvar，修 `set_current_session_id` 缺失 | 上游 session 上下文机制不同；自有安全修复 | 2026-09-23 |
| C-004 | `agent/conversation_compression.py` | 同 C-003（L-010 会话上下文一致性） | 同上 | 2026-09-23 |
| C-005 | `agent/file_safety.py` | L-002：write-deny vault/browser-profile 密钥库（对齐上游 `1c0d95badbac`，重写） | 有（上游 `_WRITE_DENIED_SECRET_DIRS`）；已 TAKEALONG L-002 | 2026-09-23 |
| C-006 | `gateway/run.py` | L-008：gateway 启动 purge 残留 `VERMES_CRON_SESSION`，关 env 回落信任边界 | 无直接对应；自有安全修复（Electron spawn 边界） | 2026-09-23 |
| C-007 | `gateway/session_context.py` | L-007/L-010：`VERMES_CRON_SESSION`/`VERMES_SESSION_ID` 改 contextvar，修 gateway 内嵌 cron 污染真实用户审批 | 无直接对应；自有 P0 | 2026-09-23 |
| C-008 | `acp_adapter/server.py` | L-010：session_id 弃进程级 save/restore 改 setter + token reset（修并发串味）；同 C-003 族 | 无直接对应；自有安全修复 | 2026-09-23 |
| C-009 | `agent/prompt_builder.py` | 常量面：S2.4 四键以 YAML 为准回写 + `COMPUTER_USE_GUIDANCE` 惰性源；skill-routing 渠道门 + SkillRouter；W-L4/L5/M7 阈值相关常量。**不能走插件形态**——这些常量被 `system_prompt`/`codex_responses_adapter` 等 core 路径直引，插件只能注册段、不能替换常量真源 | 上游无等价常量面（Vermes 反向领先）；分叉点=内容与调度，不是文件存在性 | 2026-09-23 |
<!--CORE_DIVERGE_LEDGER:END-->

---

## 7c. TAKEALONG_LEDGER（上游取长登记）

> 语义：从上游搬进来的能力/修复，逐条登记（价值、来源、落点、验收、人时）。
> 与 §7「已合入」表互补：§7 记“搬了什么”，本节记“搬的成本与验收”。
> 字段：`id` / `上游 commit` / 落点 / 类型（移植|重写|拒绝） / 验收测试 / 人时 / 上游后续变更 / 状态。
> 脚本 `upstream_watch.py` 同样解析本表（`<!--TAKEALONG_LEDGER:START-->` 至 `END` 之间），
> 仅「真上游取长」类型落点参与 G1 免税（见下）。
>
> **ID 规则（2026-09-24 立）**：`L-xxx` 一经使用**不再回收、不再改指**。拒绝/暂缓/历史条目
> 也占号（保留行即可）。新条目取下一个未用号。ID 复用会让代码注释、commit 标题、工单
> 与账本指向两件不同的事——考古必错（L-014 曾被 file_safety 误用，已改 L-027）。
>
> **复查点规则（2026-09-24 立）**：上游后续变更 **≥5** 的取长条目，登记时**自动挂复查点**
> （在「状态」列写明 `复查点：<触发条件>`），下次再碰该文件/该能力前必须对照上游新变更重判。
> 先例 T8（file_safety）；L-032（churn 10）为第 4 个实例，从此不靠记性。
>
> **取长分流（2026-09-24 立，脚本强制 `is_product_face`）**：
> ① **引擎面**（与上游同名、非产品面）→ 默认跟，改动须登记（本表或 §7b）。
> ② **产品面**（自进化 / 记忆织物 / 中文平台 / workflow DAG / Electron 壳 / ScholarForge /
>    构建链路 / 品牌入口，及「同能力但产品语义」如 ContextEngine 产品逻辑）→ **默认不取长**；
>    要取必须先证明「不引入 Vermes 没有的上游抽象」，并在本表记一行。
> ③ intake 命中②直接标「产品面·不判」，L 表只留一行说明 —— 不必每轮重判。
>
> **免税边界（T15，脚本强制）**：落点列按「类型」决定是否进 G1 免税集：
> - **真上游取长**（类型含 `移植|重写|部分采纳`，L-001~L-006）：落点路径免税——有意跟进上游。
> - **自有 bugfix / 拒绝 / 待做**（`修复（自有缺陷）`、`拒绝/暂缓`、`待做`，L-007+）：
>   落点**只作审计、不免税**——避免 follow 区文件被永久免契约税。
>
> 后续若对同一 follow 文件有大范围有意偏离，另记 **DIVERSION §7b**（有意偏离才免税）。

<!--TAKEALONG_LEDGER:START-->
| id | 上游 commit | 落点 | 类型 | 验收 | 人时 | 上游后续变更 | 状态 |
|---|---|---|---|---|---|---|---|
| L-001 | `b534f4b8c8cd` | `tools/env_passthrough.py`, `tools/environments/local.py`, `tools/environments/docker.py` | 重写（Vermes 无 `_build_provider_env_blocklist`，用 `_is_env_blocklisted` casefold 等价实现） | `tests/tools/test_env_passthrough.py` 19 passed | ~0.5h | 0 | ✅ 已合入 `a48811769d` |
| L-002 | `1c0d95badbac` | `agent/file_safety.py` | 重写（Vermes `is_write_denied`/`get_read_block_error` 各自内联目录判定，无上游 `_WRITE_DENIED_SECRET_DIRS`/`_READ_DENIED_DIRS` 元组，新增 `_WRITE_DENIED_SECRET_DIRS` 常量 + 两处目录级 deny） | `tests/agent/test_file_safety_secret_stores.py` 3 passed | ~0.5h | 4（高 churn，见 T8 复查点） | ✅ 已合入 |
| L-003 | `1c0d95badbac` 部分 | — | **拒绝/暂缓**（有意分叉，非遗漏）：上游同 commit 把 `auth/google_oauth.json`、`cache/bws_cache.json` 也纳入 write-deny，且早前 #45947 已放松 control files（`auth.json`/`config.yaml`/`webhook_subscriptions.json` stay writable）。Vermes 未跟进这两层——control-file 语义是否放松是产品决策，google_oauth/bws_cache 是 read-denied 但未 write-denied，单独立项（见 T9） | —（无契约测试，未采纳） | — | — | ⏸ 暂缓 |
| L-004 | `2afb405337c3` | `tools/approval.py` | 部分采纳/重写（三层取一层）：上游修 approval 队列「pop 与 outcome 提交分裂」竞态（resolve/clear_session/unregister 在锁内 pop、锁外提交 `entry.result`+`event.set()`，waiter `_drop_entry` 锁内读 result 可能 pop-and-lose 用户已 acked 的选择成 timeout）。Vermes `tools/approval.py:581-582/1095-1104/552-554` 完全同构，已把三处提交移进同一临界区。第 1 层（on_result(None)→withdraw）依赖 server→client 往返协议（`server_requests`），Vermes 单向 `register_gateway_notify`+`_emit` 无等价面；第 3 层（settle 状态映射）依赖 request.cancel 通知机制，Vermes 无 settle——此两层拒绝 | `tests/tools/test_approval_lock_commit.py` 4 passed | ~0.6h | 0 | ✅ 已合入 |
| L-005 | `3ed40556cea1` | `tools/env_passthrough.py`, `vermes_cli/kanban_db.py`, `vermes_cli/gateway.py` | 部分采纳/重写（三 seam 取两）：上游 #113270 新增 `local_env_policy` 门控形状匹配 + `strip_launch_profile_env`/`served_profile_child_env`/`update_restart_recovery._child_environment` 三 seam。Vermes 无 `local_env_policy.py`/`web_server_gateway.py`/`update_restart_recovery.py`，但有两个真实 profile 子进程 seam：① `kanban_db._default_spawn`（`env=dict(os.environ)` 裸复制 spawn `-p <assignee>` worker）；② `gateway.launch_detached_profile_gateway_restart`（watcher respawn 不传 env，继承 CLI 进程 env）。已在 `env_passthrough` 新增 `is_profile_gate_env`/`strip_profile_gate_env`（形状匹配，永不匹配 `VERMES_*`/`_` 前缀），两 seam 在目标 profile ≠ 当前 VERMES_HOME 时剥离门控 env。第三个上游 seam（dashboard action env / cron worker env 经 `served_profile_child_env`）Vermes 无对应面——拒绝 | `tests/tools/test_profile_gate_env_isolation.py` 8 passed | ~0.5h | 0 | ✅ 已合入 |
| L-006 | `05e7e891751c` + `2a630671d7bc` | `tools/approval.py` | 部分采纳/重写（两个 cron-unattended 语义）：① 上游 `05e7e891751c` 修「unattended 时 honor pattern-key allowlist」——Vermes `check_all_command_guards` 的 cron deny 分支重新 `detect_dangerous_command` 后直接 block，未查 `is_approved`（permanent allowlist 被忽略），已改为 block 前查 `is_approved(get_current_session_key(), pattern_key)`；`check_dangerous_command` 早已在 cron 分支前 `is_approved` 早退，无此漏洞。② 上游 `2a630671d7bc` 修「cron context 永不交互，即使泄漏 presence env」（#110932，31 hangs/h）——Vermes `check_dangerous_command`/`check_all_command_guards` 内联 `is_cli=VERMES_INTERACTIVE`/`is_ask=VERMES_EXEC_ASK` 未对 cron 排除（`_is_gateway_approval_context` 已有 cron 排除但 is_cli/is_ask 没有），已加 `if VERMES_CRON_SESSION: is_cli = is_ask = False`。上游经 `_presence()` 统一处理 + `_is_permanently_approved`/`_unattended_deny` 规格抽象，Vermes 无此抽象故重写为内联判定 | `tests/tools/test_approval_unattended_allowlist.py` 7 passed | ~0.6h | 9/13 | ✅ 已合入 · **复查点（churn≥5）：下次碰 approval unattended 前对照两 hash 后续变更** |
| L-007 | —（**Vermes 自有 P0-BUG**，非上游取长；与 L-006 同族，若上游同构再交叉引用） | `gateway/session_context.py`, `cron/scheduler.py`, `tools/approval.py` | 修复（自有缺陷）：`VERMES_CRON_SESSION` 由进程级 `os.environ` 改为 task-local contextvar。根因：cron ticker 是 gateway 进程内后台线程，`os.environ["VERMES_CRON_SESSION"]="1"` 一旦设置永不清理 → gateway 跑过一次定时任务后，真实用户消息的危险命令审批被 `_is_gateway_approval_context()` 的 cron 排除误判为非交互 → 走 cron_mode deny 直接 BLOCKED（不弹卡片）。修法：`gateway.session_context` 新增 `_CRON_SESSION` ContextVar + `is_cron_session()`（contextvar 优先，env 回落）/`enter_cron_session()`/`leave_cron_session()`；`cron/scheduler.py` 弃用 `os.environ["VERMES_CRON_SESSION"]="1"`，改 `enter_cron_session()` 并在 finally `leave_cron_session(_cron_token)`；`tools/approval.py` 新增 `_is_cron_session()` 包装（延迟 import）并五处替换 | `tests/tools/test_cron_session_contextvar.py` 6 passed | ~0.6h | 0 | ✅ 已合入 |
| L-008 | —（**Vermes 自有缺陷**，非上游取长；L-007 的启动层收口） | `gateway/run.py` | 修复（自有缺陷）：`start_gateway()` 开头一次性 `sanitize_gateway_process_env()` 清掉 `VERMES_CRON_SESSION`。根因：桌面 `electron/main.js` spawn gateway 用 `env={...process.env}` 只覆盖 `VERMES_HOME`，父 shell 若 `export` 过 `VERMES_CRON_SESSION=1` 会透传进 gateway 子进程，经 `is_cron_session()` 的 env 回落把真实用户消息误判为 cron（L-007 用 contextvar 治了 gateway 内部串味，但没治「父进程把脏 env 带进来」这条边界）。修法：抽可测函数 `sanitize_gateway_process_env()` 在 `start_gateway` 最开头（单线程、adapter/cron 之前）pop，覆盖 Electron/systemd/手动 CLI 全部 6 条启动入口；env 回落仅剩独立 `vermes cron` 守护进程与旧测试两处合法来源 | `tests/tools/test_gateway_env_sanitize.py` 5 passed | ~0.3h | 0 | ✅ 已合入 |
| L-009 | —（**Vermes 自有缺陷**，非上游取长） | `gateway/run.py` | 待做（源头治理）：`gateway/run.py:770` module-level `os.environ["VERMES_EXEC_ASK"]="1"`。L-006 已在消费方防御（cron 分支 `is_ask=False`），但源头未治。注意：gateway 用户线程**需要** EXEC_ASK，正确形态是 reader cron-aware 或 contextvar，而非简单删 module-level。先出「reader 清单 + 是否还有非 approval 消费者」再动码 | — | — | — | ⏳ 待做 |
| L-010 | —（**Vermes 自有缺陷**，非上游取长） | `gateway/session_context.py`, `agent/agent_init.py`, `agent/conversation_compression.py`, `acp_adapter/server.py`, `tools/kanban_tools.py` | 修复（自有缺陷）：`set_current_session_id` 全仓 6 处调用 0 处定义 → writer 想走 contextvar 却 import 抛 ImportError、全部静默回落 `os.environ`；ACP 进程级 save/restore 并发串味；reader（`kanban_tools.py:125/688`）直读 os.environ。修法：① `session_context` 补 `set_current_session_id()`（写 `_SESSION_ID` ContextVar，返回 reset token，不写 os.environ）+ `reset_current_session_id()` + `get_current_session_id()`；② `agent/agent_init.py`/`agent/conversation_compression.py` 三处 writer 走 setter（仅 except 日志 + env 兑底，旋转/回滚语义保持）；③ `acp_adapter/server.py` 弃进程级 save/restore 改 setter+token reset；④ `kanban_tools` reader 改 `_get_session_id()`→`get_session_env`（返回 None 保持旧语义）。见 T13 | `tests/tools/test_session_id_contextvar.py` 7 passed | ~0.7h | 0 | ✅ 已合入 |
| L-011 | —（**Vermes 自有缺陷**，非上游取长） | `cron/scheduler.py` | 待做（最小修复，避免过度 contextvar 化）：`TERMINAL_CWD` cron↔用户线程串味。已证 tick 内 workdir/profile job 严格串行（`tick()` 分区 sequential vs parallel），但 cron 线程改进程级 `TERMINAL_CWD` 时用户会话 file_tools 等 reader 仍可能看到。优先低成本：消费方在 cron 上下文不读进程 cwd，或 job 用子进程 env；整表 contextvar 非必须 | — | — | — | ⏳ 待做 |
| L-013 | —（**Vermes 自有缺陷**，非上游取长；PyInstaller GUI 形态，官方无 frozen 专段） | `tools/code_execution_tool.py` | 修复（自有缺陷）：frozen 下 `sys.executable` 是 bootloader 非解释器，旧逻辑 fallback 后 RPC 空等 300s。修法（对齐官方「勿瞎 fallback」原则）：内嵌/旁路 CLI 优先 → venv/conda → PATH 全遍历 + well-known + **版本化名** `python3.11` + ABI major.minor；**找不到 raise `_NoChildPython`**（禁止 return sys.executable）；probe `stdin=DEVNULL` + 成功缓存/失败可重试；macOS 大小写不敏感导致 `Python` dylib 误判的防护 | `tests/tools/test_code_execution_frozen_resolver.py` + abi/modes/code_execution **117 passed**；冒烟：无匹配硬失败 <2ms；有 `python3.11` 则解析成功；venv `execute_code` e2e 成功 | ~0.8h | — | ✅ 已合入（**须重打 DMG 后真机生效**） |
| L-014 | `hermes_cli/plugins.py` 签名族（`max_chars`/id 校验/重复注册） | `agent/prompt_processor_loader.py`, `vermes_cli/plugins.py`, `agent/system_prompt.py` | 重写（三护栏：单片段 `max_chars` 上限 + id 格式校验 + 同 id 重复注册拒绝） | `tests/tools/test_l014_max_chars.py` + `tests/tools/test_s21_register_section.py` | ~0.7h | 3 | ✅ 已合入 `04f4604715`→`de92b3eb7e`（**历史 ID，代码注释/commit 标题大量引用；2026-09-24 找回本行，禁止再被他条复用**） |
| L-027 | `7c478ac257a3` | `agent/file_safety.py` | 重写（T8 复查点收口）：上游 `_guard_homes` 锚定「write 可能落入的每个 home」——process `~` 在 profile/容器下会指到 `{VERMES_HOME}/home`，真实用户 home 的 `~/.ssh`/`~/.aws` 等绝对路径写会漏防。Vermes 侧 `is_write_denied` 原先只锚 `expanduser("~")`，已重写为 `_guard_homes()`（process home + `get_real_home` + `get_subprocess_home` + profile/root + `~name/`）；表折叠/helper 抽取（后续 4 次重构）**不跟**。（**原误用 L-014，2026-09-24 换号**） | `tests/agent/test_file_safety_guard_homes.py` 5 passed + secret_stores 3 passed | ~0.5h | 2 | ✅ 已合入 `5436169b35` |
| L-015 | —（**Vermes 自有缺陷**，非上游取长） | `scripts/sync-version.sh` | 修复（自有缺陷）：T5 静默失败——旧脚本 `grep\|grep\|tr` 提取版本 + `set -euo pipefail`，WorkBuddy `grep` shim 假阴性 → EXIT=1 且零输出。重写为 Python `ast` 提取 + `json` 写版本（不经 shim）；每步失败都打 stderr | `tests/scripts/test_sync_version.py` 3 passed | ~0.4h | — | ✅ 已合入（本切片） |
| L-016 | `3ed40556cea1` 续期（T10） | `vermes_cli/kanban_db.py`, `vermes_cli/gateway.py` | 部分采纳/重写（L-005 三残留收口）：① 去掉 `profile == "default"` 豁免；② fail-closed（resolve 失败/缺 home 仍 strip）；③ 形状偏宽维持可接受 | `tests/tools/test_profile_gate_t10.py` 5 passed | ~0.4h | 0 | ✅ 已合入（本切片） |
| L-017 | `b6b7802447f4` 语义并入 + 自有纵深（T12①③） | `gateway/run.py`, `gateway/session_context.py`, `cron/scheduler.py`, `tools/file_tools.py` | 修复（自有缺陷）（T12①③ 是 Vermes 纵深项；`b6b7802447f4` 仅交叉引用语义，后续 27 禁照搬）：① sanitize 启动层剥 presence 四键；③ `TERMINAL_CWD` 改 contextvar，cron 不再写 `os.environ` | `tests/tools/test_t12_session_state.py` 4 passed | ~0.6h | 27 | ✅ 已合入（本切片）· **复查点（churn≥5）：presence/TERMINAL_CWD 再动前对照 `b6b7802447f4` 后续 27 变更** |
| L-018 | —（UX，T16 配额） | `frontend/src/stores/chat.js`, `ApprovalDialog.vue`, `tools/code_execution_tool.py`, `frontend/tests/*` | 修复（自有 UX）：① approval 按会话分片；③ 路径段匹配；④ 测试跟产品现名。② 重打 DMG 挂发版窗 | frontend **23 passed** | ~0.5h | — | ✅ 已合入（本切片；**用户可见配额 ≥1**） |
| L-019 | `c0362da9a6e9` | `cron/scheduler.py` | 重写（后续 4）：交付前强制脱敏 `_redact_cron_payload`（force=True；raise 则整段替换） | `tests/cron/test_delivery_redact.py` 3 passed | ~0.4h | 4 | ✅ 已合入（本切片） |
| L-020 | `2dfb795cb78f`+`1e2cb5797362`+`6332216384b7` | `tools/approval.py` | 重写（后续 6/7/8，家族合并）：未送达/未回答 = `cancelled` + 真实原因，不再写 `denied by user`；真 deny 才归用户 | `tests/tools/test_approval_cancelled_attribution.py` 4 passed | ~0.5h | 8 | ✅ 已合入（本切片）· **复查点（churn≥5）：approval 归因再动前对照三 hash 后续变更** |
| L-021 | `dd68d175674d` | — | 拒绝/暂缓（后续 5）：GUI terminal ask 预排属 UX 特征 | — | — | 5 | ⏸ 暂缓 |
| L-022 | `04fcf9159c18` | — | 拒绝/暂缓（后续 11）：api_server bridge 高 churn，等 L-020 后复查 | — | — | 11 | ⏸ 暂缓 |
| L-023 | `840c00c124be` | — | 拒绝（后续 33）：重构非缺陷 | — | — | 33 | ❌ 拒绝 |
| L-024 | `dcdbcb8a2b14` | — | 拒绝/暂缓（后续 10）：等 scope overlay 基建 | — | — | 10 | ⏸ 暂缓 |
| L-025 | `547fff75003a`+`802a9975d283`+`3fe8e5e443d1` | — | 拒绝/暂缓（后续 1/0/4）：缺 scope overlay 基建 | — | — | 4 | ⏸ → **L-028/029/030 已重写落地** |
| L-026 | `9e232a7ff5c1`+`3fc1a184f8c0` | — | 拒绝/暂缓（后续 3/4）：无 `terminal_env_registry.provider_flag` 同构 API | — | — | 4 | ⏸ 暂缓（无对应 API）→ **L-031 已重写落地** |
| L-028 | `547fff75003a` | `tools/environments/local.py`, `tools/code_execution_tool.py` | 重写（后续 1）：passthrough 探针 fail-closed——`except → lambda _: False` 会静默丢弃声明 secret；改为 `require_is_env_passthrough()` 失败即 raise | `tests/tools/test_takealong_r3.py` | ~0.3h | 1 | ✅ 已合入（取长第 3 轮） |
| L-029 | `802a9975d283` | `tools/env_passthrough.py`, `tools/environments/local.py`, `tools/code_execution_tool.py` | 重写（后续 0）：`scoped_passthrough_additions`——profile `.env` 声明但过滤后 env 缺失的名字补进子进程（Vermes 无 `agent.secret_scope`，用 `get_vermes_home()/.env` 等价实现） | `tests/tools/test_takealong_r3.py` | ~0.5h | 0 | ✅ 已合入（取长第 3 轮） |
| L-030 | `3fe8e5e443d1` | `tools/environments/local.py` | 重写（后续 4）：routed 子进程（target home ≠ 当前）剥离 launch-only 凭据后再叠 scope overlay | `tests/tools/test_takealong_r3.py` | ~0.4h | 4 | ✅ 已合入（取长第 3 轮） |
| L-031 | `3fc1a184f8c0`+`9e232a7ff5c1` | `tools/approval.py` | 重写（后续 3/4）：`register_provider_flag`/`provider_flag`/`_should_skip_container_guards`——plugin 容器分类生效；truthy 非 bool 强制 bool；未知 backend 默认保留门控 | `tests/tools/test_takealong_r3.py` | ~0.4h | 4 | ✅ 已合入（取长第 3 轮） |
| L-032 | `dcdbcb8a2b14` | `tools/env_passthrough.py` | 移植/重写（后续 10）：`source_supplied_names()` 拆出「用户/技能声明」面，与 runtime union 分离 | `tests/tools/test_takealong_r3.py` | ~0.1h | 10 | ✅ 已合入（取长第 3 轮）· **复查点：下次碰 env_passthrough 声明面前对照上游 `dcdbcb8a2b14` 后续 10 变更重判** |
| L-033 | `04fcf9159c18` | — | **拒绝/暂缓**（后续 11）：api_server approval bridge + cron 自调度——等 L-020 家族稳定后复查 | — | — | 11 | ⏸ 暂缓 |
| L-034 | `dd68d175674d` | — | **拒绝/暂缓**（后续 5）：GUI terminal ask 批量预排属 UX 特征（新模块 `terminal_approval_batch`），挂用户可见配额观察 | — | — | 5 | ⏸ 暂缓 |
| L-035 | —（**自有缺陷**，L3 进程卡死 / L4 观测） | `gateway/run.py`, `gateway/watchdog.py` | 修复（自有缺陷）+ 对齐上游防护：① 空异常 `debug+%s`→`warning+%r`（channel directory + cron tick）；② `fut.result(timeout=30)` 超时单独记 warning；③ **进程看门狗**（CPU>90%×3 或 :9120 失联×3 → `os._exit(70)` 喂 Electron 崩溃自愈） | `tests/gateway/test_process_watchdog.py` 6 passed | ~0.6h | — | ✅ 已合入（gateway 稳定性治本①） |
| L-036 | `qqbot` 躺平族（对照 Telegram 永不放弃模式） | `gateway/platforms/qqbot/constants.py`, `.../adapter.py` | 重写（对齐上游 Telegram 语义）：`MAX_RECONNECT_ATTEMPTS=0` 无上限 + 封顶 60s 退避 + 节流日志；三处「用尽上限就躺平」分支不再 return | `tests/gateway/test_channel_selfheal_l2.py` | ~0.3h | 100 | ✅ 已合入 · **复查点（churn≥5）** |
| L-037 | `14f20d142e` + `5743dbb703` | `gateway/platforms/feishu.py` | 重写（后续 2，高价值）：`_supervise_websocket_thread` — WS 线程死了自动带退避重建（封顶 60s），不再静默变聋直到网关重启 | `tests/gateway/test_channel_selfheal_l2.py` | ~0.5h | 2 | ✅ 已合入 |
| L-038 | `5743dbb703` 语义（ws_link_lost） | `gateway/platforms/feishu.py` | 重写：死链时状态写 `ws_link_lost`/`retrying`，**不再假绿 `connected`**（配合 L-037） | `tests/gateway/test_channel_selfheal_l2.py` | ~0.1h | — | ✅ 已合入 |
<!--TAKEALONG_LEDGER:END-->

---

## 8. 待办 / 未决

| # | 项 | 状态 |
|---|---|---|
| T1 | G1 契约税 48 处逐条判定（登记 vs 外置） | 待做（S1） |
| T2 | 上游 canary CI lane（周跑：fetch → 雷达 → 契约测试） | 待做（S1） |
| T6 | 上游意图级巡检脚本（月度：安全修复 → Vermes 对应物清单） | ✅ **已落地** `b25021d044`，口径拧紧 `c1e2d3248c`，红线精确覆盖修正 `（本 commit）`（首跑口径：`intake --max 8000` → 3154 commits / 220 候选 / GHSA 1 / fixsec 6 / 有对应物 21；报告名含 `--since`/`--max` 防覆盖，命令行写进报告头；own 区路径永判红线，不被精确映射放行） |
| T7 | walking skeleton 插件化评审（ContextEngine 试点） | ✅ **评审文档已出** `reports/vermes-plugin-walking-skeleton-review_20260921.md`（纸面，未写迁移代码） |
| T3 | 核实 Vermes 的凭据 env 屏蔽名单（`tools/env_passthrough.py` / `tools/environments/docker.py`）是否大小写敏感 —— 若是即与上游 `b534f4b8c8cd` 同类漏洞 | ✅ **已完成** `a48811769d`（`_is_env_blocklisted` casefold，已登记 DIVERSION_LEDGER D-001 + TAKEALONG_LEDGER L-001） |
| T4 | 形态 B（引擎作依赖）前置核实 | **已核实 2026-09-21**：`hermes-agent` **确实在 PyPI**（`https://pypi.org/pypi/hermes-agent`，作者 Nous Research，MIT，requires Python ≥3.11 <3.14，extras 覆盖 wecom/feishu/dingtalk/acp/mcp 等 40 项）。**但 PyPI 最新版 0.19.0 落后于 GitHub v0.21.3**（tag `v2026.9.14`）→ 形态 B 有路径，代价是**跟随版本落后上游 2 个小版本**，且需重做打包链 |
| T5 | `scripts/sync-version.sh` 在本机 shell shim 下静默失败（EXIT=1 无输出） | ✅ **已完成**（L-015：Python ast/json 重写，契约测 `tests/scripts/test_sync_version.py`） |
| T8 | `agent/file_safety.py` 后续取长复查点（`1c0d95badbac` 后 4 变更：表折叠×2 / helper / 去 suppress + `7c478ac257a3` multi-home） | ✅ **本轮收口**（L-027：`_guard_homes` multi-home 已重写；表折叠/helper 不跟） |
| T9 | #45947 control-file 语义分叉 + `auth/google_oauth.json`/`cache/bws_cache.json` 读拒写未拒 | ✅ **已拍板 2026-09-23：不跟**（产品取舍，维持 write-deny；非技术用户误操作面）— 记 DIVERSION/有意不跟，见 L-003、roadmap §8.6 |
| T10 | L-005 profile 门控三残留：① default 豁免 ② fail-open ③ 形状偏宽 | ✅ **已完成**（L-016：①② 已修 fail-closed；③ 维持并注记） |
| T11 | **P0-BUG（Vermes 自有缺陷，非遗漏/非取长）**：`VERMES_CRON_SESSION` 用 `os.environ`（进程级）标记 cron 会话，而 cron ticker 是 gateway 进程内后台线程（`gateway/run.py:4086`）→ 首个 cron job 跑过后环境永久污染、从不清理 → 真实用户消息危险命令审批被误判为 cron（走 cron_mode deny → 直接 BLOCKED，不弹审批卡片）。修法：cron 标记改 task-local contextvar（`gateway.session_context._CRON_SESSION` + `is_cron_session()/enter_cron_session()/leave_cron_session()`），`cron/scheduler.py` 弃用 `os.environ["VERMES_CRON_SESSION"]="1"`，`tools/approval.py` 五处 cron 判定改 `_is_cron_session()`（contextvar 优先，env 回落兼容独立 cron 进程/CLI/旧测试） | ✅ **已完成**（commit `92c6c4792a`，见 §7c L-007；验收 6+ 契约测试） |
| T12 | 进程级会话状态残留：① presence 剥离 ② CRON_SESSION pop（已随 L-008）③ TERMINAL_CWD 串味 | ✅ **①③ 已完成**（L-017）。TUI/ACP 深 contextvar 化留给 L-009 |
| T13 | **P0-BUG（Vermes 自有缺陷）**：`gateway.session_context.set_current_session_id` 全仓 6 处调用（`agent/agent_init.py:997/999`、`agent/conversation_compression.py:616/618/662/663`）但 **0 处定义**——writer 想走 contextvar（`_SESSION_ID` ContextVar + `_VAR_MAP` 已建），但 setter 缺失导致 import 抛 ImportError、全部静默回落 `os.environ`，与 ACP（`acp_adapter/server.py:1454`）的进程级 save/restore 形成并发串味。reader 侧（`tools/kanban_tools.py:125/688`）也直接读 `os.environ` 而非 `get_session_env()`。修法（L-010）：① `gateway.session_context` 补 `set_current_session_id()`（写 `_SESSION_ID` ContextVar）② reader 改 `get_session_env("VERMES_SESSION_ID")` ③ ACP 改调 setter 弃用进程级 save/restore | ✅ **已完成**（= L-010，见 §7c） |
| T14 | **进程级会话状态变量纸面审计**（VERMES_INTERACTIVE/VERMES_EXEC_ASK/VERMES_GATEWAY_SESSION/TERMINAL_CWD 的 writer/reader/泄漏路径分级） | ✅ **已完成**（`reports/vermes-t14-session-env-audit_20260921.md`，commit `c3b95ef9fd` 初版 + `b2f1d3aaa0` 三项查证闭环；输出 L-009~L-011 实施清单） |
| T15 | **类型免税（方案 2）**：TAKEALONG 落点仅「移植\|重写\|部分采纳」进 G1 免税集；自有 bugfix/拒绝/待做只审计不免税。有意偏离另记 DIVERSION（D-003/D-004） | ✅ **已完成**（本切片；`upstream_watch.py` + 机制测试 + §7b/§7c 边界注记） |
| T16 | UX 遗留：① approval 非 per-session ② 须重打 DMG ③ 路径子串匹配 ④ 测试旧文案 | ✅ **①③④ 已完成**（L-018 用户可见配额）；② 待发版窗重打 DMG |

---

## 9. 证据索引

| 结论 | 证据 |
|---|---|
| 无共同祖先 / divergence 数字 | `git merge-base --all main upstream/main`（空）· `git rev-list --count` · `git rev-parse --is-shallow-repository=false` |
| 上游 HEAD 与速度 | `git describe --tags upstream/main` = `v2026.9.14-4787-g3b7eda0887` |
| 契约税 48 处 | `reports/dist-boundary-20260921.md`（`v2.4.9..main`，100 commits） |
| 上游候选 300 commits / 810 文件 / 36 红线 | `reports/upstream-watch-20260921.md` |
| 分区定义真源 | `scripts/upstream_watch.py` 的 `ZONES` |
| 冻结基线 | 冻结锚 `888bf8a344`（非 tag）· 发版 tag `v2.5.1` @ `5142343cbd` · `CHANGELOG.md [2.5.1]` · `version.json` |
| 前序路线 | `reports/vermes-distribution-ROLLOUT-PLAN_20260920.md`（P0–P5） |
| 同步策略（旧） | `UPSTREAM_SYNC.md`（本文档为其工程化落地） |
