# Vermes 发行版化 · 下一阶段路线图（待董董审）

> 日期：2026-09-22 · 基线：`main` @ `26416408b4`
> 真源交叉：`docs/DISTRIBUTION_MANIFEST.md` · 前序：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`
> 状态：**纸面提案，未开工**。通过后再动码。

---

## 0. 目标（不变）

**Vermes = Hermes 的中文发行版**：独立开发特色（红线资产），同时把「取长官方更新」从偶发考古变成 **雷达 → 判定 → 重写/适配 → 契约测试 → 账本** 的常态化流水线。

硬约束（不要反复推翻）：

1. 与上游 **无共同祖先**，`git merge` 不可用；正确形态是 **能力级取长**。
2. 分区：`own` 红线 / `follow` 跟随 / `core` 个案。
3. G1 只认「未登记 follow 税」；TAKEALONG 仅「移植|重写|部分采纳」落点免税（T15）。
4. 形态 B（引擎作 pip 依赖）**仍暂缓**（PyPI 落后 + 打包链重做）。

---

## 1. 已完成（本阶段只作锚点）

| 块 | 内容 |
|---|---|
| 机制 | 冻结锚、精确账本、G1 零未登记税、intake 巡检、类型免税 T15 |
| 取长 | L-001~L-006（安全/approval/profile 门控/cron-unattended） |
| 自有 P0 | L-007/008 cron 标记 contextvar + 启动 sanitize；L-010 `VERMES_SESSION_ID` |
| 制度 | DIVERSION/TAKEALONG 两账、DIVERSION D-001~D-006、T 项台账 |
| 并行轨 | UX 打扰治理已合入并审计通过（T16 遗留挂账） |

---

## 2. 总览（四阶段，不并行铺开）

```text
Phase A  会话/进程状态收口     ← 下一刀（安全正确性，1~2 天）
Phase B  制度层长项           ← T1 清税 + T2 canary（2~3 天，可穿插）
Phase C  结构减债（骨架）     ← ContextEngine 纸面→试点（先评审后码）
Phase D  常态化取长           ← intake 月更 + 队列消化 + 复查点

> **范围边界（2026-09-24 拍板）**：产品插件化（S2–S4 / ContextEngine walking skeleton）
> **摘出发行版化验收项**，单独立项——它是产品路线（决定将来能否形态 B），
> 不是「海关/进货/血统」三层的收口条件。挂在发行版化里会永远收不了口。
> 发行版化收口 = 升级演练 + 门禁连续全绿 + 取长稳态 + 配额连续达标（见 §8）。
```

原则：**A 不挡 B 的纸面；C 不与 A/B 抢代码面；D 贯穿。**

---

## Phase A · 会话/进程状态收口（优先）

> 病根同一类：会话语义写进进程级 `os.environ`，gateway 多线程/多任务串味。  
> L-007/008/010 已治 `CRON_SESSION` / `SESSION_ID`；剩 presence 与 `TERMINAL_CWD`。

| ID | 项 | 修法 | 验收 | 估 |
|---|---|---|---|---|
| **L-011** | `TERMINAL_CWD` cron↔用户线程串味 | **最小修复**：① 用户路径 reader（`file_tools` / `message_handler_mixin` / `runtime_footer` / slash cwd）在 cron 上下文 **不读进程 env cwd**，或优先子进程/任务局部值；② cron job 优先 **子进程 env 注入 workdir**，减少改父 `os.environ`。**不做**整表 contextvar | 契约测：cron workdir job 执行中，用户线程读 cwd 不被污染；tick 内 workdir 串行回归不破 | 0.5~1d |
| **L-009** | `EXEC_ASK` / presence 源头 | **本阶段只出清单**（T14 已有骨架）：`VERMES_EXEC_ASK`、`VERMES_INTERACTIVE`、`VERMES_GATEWAY_SESSION` 的 writer/reader/是否可能在 cron 线程；标「进程策略 / 应 task-local / 子 env 剥离」 | 报告落 `reports/`，产出 L-012+ 实施表 | 0.25d 纸面 |
| **L-012**（清单后） | presence 消费方 cron-aware | 仅改清单内 reader：`if is_cron_session(): 按无人值守`；**禁止** gateway 父进程 `pop EXEC_ASK/INTERACTIVE` | 反例：cron 泄漏 presence 不进交互审批；用户路径仍弹卡 | 0.5~1d |

**明确不做**：删 `gateway/run.py:770` module-level EXEC_ASK（用户线程需要）；TUI 进程级 presence 整改（语义自洽，T14 判低风险）。

**账本**：L-011/L-012 类型「修复（自有缺陷）」→ **TAKEALONG 不免税**；follow 落点另记 DIVERSION（T15 纪律）。

---

## Phase B · 制度层长项（S1 收尾）

| ID | 项 | 内容 | 验收 |
|---|---|---|---|
| **T1** | 历史契约税清理 | `v2.4.9..freeze` 跟随区约 48 处：**外置** `docs/vermes/` / `scripts/vermes/`（同步 ZONES）或 **精确 DIVERSION**；禁止整目录放水 | `boundary` 全窗口干净或逐条有账 |
| **T2** | canary 周跑 | 一键：`watch --dry-run` + `intake` + `boundary` + 机制/approval/env 契约测；未登记税>0 或测试红 → 告警。不依赖 tests-os 解禁 | 文档化命令 + 首跑报告 |
| T8 | file_safety 高 churn 复查 | 上游后续 4 变更对照后再取长 | 下次碰该文件前 |
| T10 | profile 门控残留 | default 豁免 / fail-open / 形状偏宽 | 非阻塞 |

T1 与 Phase A 可穿插（不同文件面）；T2 半天可完。

---

## Phase C · 结构减债（walking skeleton，先纸面后码）

> 「治本」= 把红线从 core 挪进插件/ABC，使跟随上游冲突面下降。**不是** P0 消化队列。

| 步 | 内容 | 门槛 |
|---|---|---|
| C0 | **ContextEngine caller diff**（只读） | 5 个上游缺方法是「可选默认」还是要改 `compression_scheduler` / `conversation_compression` / 调用方？结论前 **零迁移代码** |
| C1 | 若 diff 判「加方法即可」→ walking skeleton 试点 | 字节级/夹具验收 vs v2.5.1；回退开关；ROI：该路径取长人时/契约税 |
| C2 | 自进化静态块 / 记忆织物 | C1 成功后再评；**不**并行铺开 |

**明确不做（首期）**：记忆织物全量、中文平台 17 个迁移、workflow DAG（需上游开口）、`_GateSpec` 整搬。

---

## Phase D · 常态化取长（贯穿）

| 节奏 | 动作 |
|---|---|
| **月** | `upstream_watch.py intake` → 人工清单 → 采纳/拒绝均入 §7c；真取长才落点免税 |
| 队列 | approval 剩余（presence 泄漏、无人值守系列）按 P0→P1；file_safety T8 复查 |
| 安全 | `fix(security)` / GHSA **优先**；等价面三判据（同类符号 / 后果链可复述 / 不引入上游新抽象） |
| 度量 | `boundary` + intake 校准摘要；人时入账（对照 L-001~L-010 量级） |

### 月度复查点 · 固定字段（2026-09-24 立，勿省）

| 字段 | 读法 | 达标线 |
|---|---|---|
| **自动门禁 streak** | `python3 scripts/canary_streak.py --need 3` 的 `trailing_streak`（**必须是跨自然日的 launchd 自动跑**，同窗口连打不算时间自持） | 停止条件②：连续 3 个自然日 |
| **用户可见变化条数** | 本 release notes 的「用户可见变化」小节条目数 | 停止条件④：连续 2–3 周每周 ≥1 |
| **churn≥5 复查点** | §7c 状态列含「复查点」的行是否已对照上游新变更 | 必处置 |
| boundary 未登记税 / core 登记率 | `upstream_watch.py boundary` | 0 / 100% |
| 高 churn 文件 | 同上复查点行 | 必处置 |

> 停止条件②④ 是**日历量**，机制冻结 ≠ 杮子清零。2026-09-24 时点：
> ② 自动运行天数 = 0（launchd 已装，首次真自动 ≈ 9/25 09:17 → 满 3 日 ≈ 9/27）；
> ④ 2.5.3 起算第 1 周 → 满 2–3 周 ≈ 10/8–10/15。

---

## 3. 时序（建议）

```text
本周
  ├─ A: L-011 实现 + 契约测 + DIVERSION 登记
  ├─ A: L-009 reader 清单纸面（可并行）
  └─ B: T2 canary 一键脚本（半天）

下周
  ├─ A: L-012 按清单改 presence reader（若清单成立）
  ├─ B: T1 历史税分批清理
  └─ C: C0 ContextEngine caller diff（纸面）

再往后
  └─ C1 试点（若 C0 通过）→ D 月更固化
```

---

## 4. 验收口径（可量化）

| 指标 | 目标 |
|---|---|
| G1 未登记税 | 持续 0 |
| L-011 | cron workdir 与用户线程 cwd 隔离反例测试 ≥2 |
| L-009 清单 | 覆盖 EXEC_ASK/INTERACTIVE/GATEWAY_SESSION 全部 writer+reader |
| T1 | 历史 follow 改动「有账或已外置」 |
| T2 | 可一键跑通；周更报告有日期 |
| C0 | 书面结论：加方法 / 改调用方 / 换试点 |
| 取长 | 每条 §7c 有人时与验收；拒绝也有行 |

---

## 5. 风险与红线（再钉一次）

| 不碰 | 原因 |
|---|---|
| 记忆织物 / 自进化 / 中文平台 / 打包链 / ScholarForge | 红线资产 |
| `git merge upstream` | 无共同祖先 |
| 整目录 DIVERSION 免税 | G1 放水（T15 已防 TAKEALONG，DIVERSION 也要克制） |
| 形态 B 现在做 | 打包链 + PyPI 滞后，ROI 未到 |
| 未审计就批改 `os.environ` 会话变量 | L-007 病根复发 |

---

## 6. 需要你拍板的点

1. **是否按 Phase A → B → C 顺序**（A=L-011 实现 + L-009 清单）？  
2. T1 外置策略：倾向 **`git mv` 到 `docs/vermes/` / `scripts/vermes/`**，还是 **精确 DIVERSION 登记**（少动文件、账面长）？  
3. ContextEngine 是否维持「**只纸面、你点头才写码**」？  
4. canary 落点：本地一键脚本即可，还是要进 GitHub Actions 周跑？

---

## 7. 证据索引

| 内容 | 位置 |
|---|---|
| 分区/账本/G1 | `docs/DISTRIBUTION_MANIFEST.md` |
| T14 会话状态审计 | `reports/vermes-t14-session-env-audit_20260921.md` |
| 雷达/intake | `scripts/upstream_watch.py` + `reports/upstream-intent-*.md` |
| 骨架纸面 | `reports/vermes-plugin-walking-skeleton-review_20260921.md` |
| UX 并行轨（已合入） | `docs/plans/2026-09-22-frontend-ux-interruption-report.md` |

---

## 8. 交叉审计补丁（2026-09-23，采纳 QClaw + MiMo 复核）

> 审计快照：`main` @ `b8e1950510`；本文入库时 HEAD 已前进（勿用相对位置引用，报进度带 HEAD hash）。
> 完整审计：`reports/qclaw/cross-audit-distribution-plan_20260923.md`。

### 8.1 数字口径（**窗口 + 当场命令**，禁止只报裸数字）

> 30 天是**滚动**窗口——同一时刻不同人测出的值可以都对。引用必须带 **快照日 + HEAD + 复现命令**。
> 本表快照：**快照日 2026-09-23**（窗口为快照日倒推 30 天的描述性标注，**非可复现口径**；复现一律用右列命令）；上游 HEAD `5a0c2fb89e`，Vermes HEAD `04f4604715`。

| 口径 | 快照值（2026-09-23 实测） | 复现命令 |
|---|---|---|
| 上游近 30 天提交 | **12,603** | `git -C ~/.hermes/hermes-agent log --since='30 days ago' --oneline \| wc -l` |
| 上游总提交 | **37,857** | `git -C ~/.hermes/hermes-agent rev-list --count HEAD` |
| Vermes 近 30 天 | **557** | `git log --since='30 days ago' --oneline \| wc -l` |
| Vermes 总提交 | **1,839** | `git rev-list --count HEAD` |
| 30 天倍率 | **≈22.6×** | 上两项相除（12603/557） |

量级结论不变：**整体追更新不成立**。历史台账 13,011 / 14,603 / 30× / 25× 均为各自时刻的滚动窗口快照，**不得再当「当前值」引用**。

### 8.2 指标 5（不退化）— Sprint 级 A/B，**制度绿 ≠ 可收口**

S2 注入载体 / S3 记忆后端 / S4 渠道迁移都是「接口不变、行为可变」区。

- **语料预固化 ≥30 条**（中文长会话、跨会话记忆、工具调用链、渠道收发），冻结进 `reports/ab-corpus/`（或等价路径），**改语料必须升版**。
- 每 Sprint 收尾：**v2.5.2 冻结包 vs 新包** 同语料并排输出；任一维度退化 → Sprint 不过（回退或 §7b 有意登记）。
- 禁止「感觉变好/变差」结论（E9/E10 教训）。

### 8.3 停止条件收紧（原 1/2 条作废）

| 原 | 新 |
|---|---|
| 连续两个「发版窗口」零未登记 | **连续两个 Sprint** 零未登记税，且 follow 契约税增量 ≤10（窗口= Sprint，不是 tag） |
| 一次真实取长 diff 落 plugins/ | **S2–S4 全部取长**中：落 plugins/ 且零核心 diff 的比例统计入账；follow 核心改动逐条 §7b 登记，**不允许「挑简单一次」充数** |

### 8.4 canary 必须 pinned

- 上游基线钉 **固定 tag**（默认 `v2026.9.14` 或下一季度钉点），**禁止** canary 拉 HEAD 当判据。
- 季度升钉点：单独 PR，写明对比区间。
- 内容：`intake` + `boundary` + 契约测（机制/approval/env）+ `check_coexistence.py --deep`。
- **只告警不阻塞** 发版（红因多在上游漂移）。

### 8.5 两本账真源（更正 QClaw §3.4）

- **单一真源 = `docs/DISTRIBUTION_MANIFEST.md` §7b DIVERSION + §7c TAKEALONG**（HTML 注释 `<!--DIVERSION_LEDGER:START-->` 等包裹，供 `upstream_watch.py` 解析）。
- `D-001` / `L-001` **在仓内可检索**（manifest §7b/§7c）；审计「三次检索零命中」应为并发改写竞态，**不是无真源**。
- 契约测 `tests/tools/test_distribution_mechanism.py` 已断言解析结果；另补 **ID 在块内**断言，做到「改 ID/删行不改测试会红」。
- **不另建** `DIVERSION_LEDGER.md`（避免两份真相）。

### 8.6 已拍板（2026-09-23）

| 项 | 决定 | 理由 |
|---|---|---|
| **T9** control-file / google_oauth / bws_cache | **不跟上游 #45947 放松**；维持 write-deny，登记 DIVERSION（产品取舍） | 用户含非技术场景；放松=扩大误操作面；成本仅一条登记 |
| **D5** P4 试点 | **飞书 + Telegram**（不用微信首发） | P4 硬验收=断线重连不丢消息；TG 可自动化、无审核门禁；微信/QQ 协议重+审核，不适合首发 |
| 开工序 | **T2 canary（1d）→ S2 工单（1d）∥ AGENTS.md 目录化（2–3d）** | S2 最早验证「注入逐字等价」是否真做得到；AGENTS.md 不阻塞 S2 |

### 8.7 下一步（锁定）

1. T2 pinned canary  
2. S2 walking skeleton **工单**（仍：caller/等价验收方案过关再迁码）+ 指标 5 语料 ≥30 冻结  
3. 12 目录 AGENTS.md 与 S2 并行  
4. 引用数字一律「当场命令 + HEAD hash」  

### 8.8 交叉审计正式回应（2026-09-23）

- 统一注入入口 `system_prompt._resolve_section(name) → (content, source, content_hash)`；
  `_proc_or_default` 改为薄包装（**13 个调用点零改动**，字节等价）。
- `identity` 第一个迁入该入口（source=processor|fallback|fallback-lazy；hash=canonical/`sha256`）。
- gold 门闩：`s2_snapshot.py --check` 17 场景 × 3 段**逐字相同**（S2.2 不改注入文本）。
- 双探针变异：改 identity 内容 → 仅相关 stable 红；fallback 优先 → source 字段可抓。
- 交付报告：`reports/qclaw/s22-identity-walking-skeleton_20260923.md`。

- S2.3：`editing_guardrails` 补 YAML（与常量字节等价）；15 注入点全部 `_resolve_section`；
  `_resolve_section` 单次查找；逐键契约测 15 条。gold 仍 17×3 逐字相同。
- **情报**：4 键 YAML 已领先常量（task_completion / scholarforge_workflow / tool_use_enforcement /
  openai_model），已钉 `KNOWN_YAML_CONSTANT_DRIFT`——S2.4 退役常量前必须对齐。
- 交付报告：`reports/qclaw/s23-migrate-sections_20260923.md`。

两份 QClaw 审计的逐条处置见 `reports/qclaw/response-to-cross-audits-20260923.md`：

- 方案审计三条（指标 5 / 停止条件 / canary pinned）**采纳**，已在 §8.2–8.4。  
- 两本账「无真源」**不成立**（假阴性已自证），真源仍为 manifest §7b/§7c。  
- T2/S2 审计 A1–A7：补登记 D-007/D-008、boundary 措辞、adapter 形态、plugin<builtin<user、  
  排期按真缺口、L-014 三护栏、回退开关先不做。  
- **制度补丁**：boundary / canary 非绿 = 月度复查点**必处置项**（登记流程此前无强制力）。  

## 8.9 Hermes 反馈落地（2026-09-23）

- **G1 绿色边界**：boundary PASS **只覆盖 follow 区**；core 区不计税。报告与 G1 注记已写明。
- **CORE_DIVERGE_LEDGER（manifest §7d）**：core 改动必须登记「为什么改 core + 上游对应面 + 为何不能走插件」。C-001~C-008 补齐自冻结锚起 **8** 个 core 文件（Hermes 点名 7 个 + 实测 `acp_adapter/server.py`）；S2 的 `system_prompt.py`/`prompt_processor_loader.py` 为 C-001/C-002。
- **core 登记率进月度复查点**：`upstream_watch.py boundary` 输出 `core登记率=N%`；月度复查点与 boundary/canary 非绿同级为必处置项。
- 卫生：删 `.ff.txt`/`.ff2.txt` 调试残留。
- 措辞：`VERMES_DISABLE_PROMPT_SECTIONS` **P3 已落地**（2026-09-23，load_all 出口过滤 + 显式禁用打印）。此前「计划项未落地」措辞作废。  
