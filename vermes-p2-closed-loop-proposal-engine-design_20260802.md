# Vermes P2 — 闭环提案引擎设计稿（AEGIS 四阶段 + Critic 闸门 + 确定性闸门）

> 状态：**设计稿 v3（吸收审批分层评审）**。v2 结论保留（挂接点属实 ✅ / B1 only / 确定性补 count_delta / 三拍板点锁定）；**v3 核心变更**：P2 提案不再「全量进待审队列」，改为**双闸门过 → L1 自动 apply + 24h 撤回，闸门没过 → L2 进队列**（呼应 broader 审批分层计划 `vermes-approval-tiering_20260802.md`）。
> 上游：P0（前端只读展示，commit `44c738e60`）+ P1（策略外置，`4180e909a` / `44c738e60` + 0 注入护栏 `2ebe62e8f`）已收尾。
> 愿景：运行时利用涌现式记忆自学习飞轮自我改造，减少源码修补（HarnessX/AEGIS「搭乐高」）。

---

## 1. 目标与边界（v2 收紧 + v3 分层）

- **目标**：Vermes 在运行态自动发现「退化点」并生成**可审查**的自改提案；任何写操作必须过审批（**分层**：高风险 L2 人工弹窗，低风险 L1 自动执行 + 事后可撤回，见 §3.1）。
- **首版范围 = B1 配置级 only**：只改 P1 外置的 `config.yaml memory.autoResolve` dial（+ 可选 per-task_type 覆盖段）。**不做 B2 源码级**（见 §6 理由，B2 留作 P4 变体隔离的前置）。
- **非目标**：不重新硬编码阈值；P2 是「发现该调什么 + 生成配置级提案 + 过双闸门 + **分层分流**（L1 自动 / L2 队列）」。

---

## 2. 真实代码挂接点（评审已核实属实 ✅）

| 关注点 | 位置 | 角色 |
|---|---|---|
| 反思 daemon | `agent/memory_reflection.py:157 run_reflection_review()`，R3-R4 标「待实现」 | P2 AEGIS 扫描 `_scan_evolution_proposals()` 插此（**独立 3600s 间隔，不跟反思同频**）|
| 成功率真源 | `agent/evolution_manager.py` 的 `v_outcomes`（视图，底层 `raw_events`）+ `strategies(task_type, strategy, success_rate_when_used, times_used)` | Phase A 输入 |
| 指标时序 | `self_model(metric, value, details)` | Phase A 输入 |
| **zombie 表** | `anti_patterns`（`:195/:824/:861-863` 注释 "superseded"） | **AEGIS 输入一律绕过** |
| 应用/闸门 | `tools/self_modify_tool.py` → `agent/emergent_change.EmergentChangePipeline.propose_change/apply_change`（`:148/:272`）+ `tools/approval.approve_privileged_action`（`:612`） | 实际写 config + **L2 人类审批** |
| 能力激活闸门 | `agent/raw_event.py:390 _bg_activate` → `tools/approval.request_gateway_approval`（**当前全量 L2，无风险分级**）| 见 §3.1 / tiering 计划 |
| 数据结构 | `agent/emergent_change.py:48 ChangeProposal`：`source/target_path/content/description/metadata/initiator` | B1 提案承载 |
| 回滚/撤回 | `EmergentChangePipeline.rollback_change(target_path, backup_path, initiator='user')` **跳过审批闸门**；`apply_change` 写文件前生成 `<target>.bak.<timestamp>` | **L1 撤回机械底座（已存在）** |
| API 路由 | `vermes_cli/blueprints/chat.py:2595-2645`（`/api/evolution/*`、`/api/emergence/*`） | **缺 `/api/evolution/proposals`**，P2 新增 |
| 历史展示 | `chat.py:1939 self_modify_history` 读 `raw_events` | 提案经 self_modify 落地后自动进该面板 |

---

## 3. AEGIS 四阶段映射（v2）

### Phase A — 压缩轨迹 → 退化点
- **输入**：`v_outcomes` 近 30 天，按 `task_type`/`domain` 聚合 success_rate 时序；`strategies` 表；`self_model` 指标。
- **方法**：每个 task_type 算 success_rate 斜率（近 7d vs 近 30d 基线）。
  斜率 < 阈值 **（固定默认 `-10pp/周`，不外置）** 且 `times_used ≥ 最小样本（固定默认 20，不外置）` → 标记「退化点」。
- ⚠️ **灵敏度不外置（评审决策）**：这是 AEGIS 的「发现灵敏度」，不是 P1 的「处置阈值」。外置 → 用户调「多久算退化」→ 激进刷提案 / 保守不出提案 → 噪声大。固定默认，用 `evolution_proposals` 审计记录观察，需调时改源码。
- **输出**：`RegressionPoint(task_type, baseline_sr, recent_sr, delta, n)`。

### Phase B — 生成候选修改（v2：仅 B1 配置级）
- 对每个退化点，确定性 prompt（system=进化架构师，低温）产出**配置级**候选：
  - 建议调 P1 外置的 `config.yaml memory.autoResolve` 相关 dial（如某 task_type 的 `duplicate` 覆盖），或新增 per-task_type 覆盖段。
  - **只落 config，不碰源码**（B2 源码级留 P4，见 §6）。
- **输出**：`CandidateProposal(id, regression_ref, target_kind='config', config_patch, rationale, expected_effect)`。

### Phase C — Critic 闸门
- 独立 LLM pass（不同 prompt / 温度）：输入 `(当前行为, 候选改动, 近况 outcomes)`，输出 JSON `{safe: bool, concerns: str, confidence: float}`。
- `safe=false` 或 `confidence < 外置阈值(默认 0.7)` → 标记 `critic_rejected`，不进队列。
- **硬编码护栏（非 LLM，必过）**：候选若会
  (a) 删除/清空 `strategies`/`raw_events` 任何行；
  (b) 把任何 autoResolve dial 设为 `0`（复用 P1 `>0` 护栏，`2ebe62e8f`）；
  (c) 触及 `agent/*.py` 或 config 以外文件 → 直接 `rejected`。
- **成本控制（评审补充，非阻塞）**：
  - **Batch**：一次扫描的多个退化点 → 合并成一个 Critic prompt（非逐个调）。
  - **Cache**：同 `task_type` 的退化点 24h 内不重复 Critic，缓存 key = `task_type + config_hash`。

### Phase D — 确定性闸门（AEGIS 核心约束：新策略不得劣化已解决任务）
- **可计算实现（B1 配置级）**：用现有 `memory_flags`（open 状态）+ `auto_resolve` 逻辑，在最后 N（外置，默认 200）条 open flag 上**离线回放**新旧 config：
  1. **precision 不降**：新旧 config 下自动处置 flag 的精度（真阳率）比较，新 ≥ 旧。
  2. **数量不爆（评审补充 count_delta）**：`auto_resolved_count_delta = 新config自动处置数 / 旧config自动处置数`，比值 ≤ 外置上限（默认 1.5x）。
     - 防「duplicate 0.9→0.7」式陷阱：precision 不变但 recall 暴涨 → 误杀边缘案例。precision 不降 + 数量不爆 = 才算通过。
- **闸门结果分流（审批分层，见 §3.1）**：
  - **双闸门通过**（Critic `safe` 且 `confidence≥0.7` + 确定性 `passed`）→ **L1 自动 apply**：直接写 `config.yaml`（无需人工），记录「可撤回变更」（关联 `.bak` 快照 + 时间戳），推送一条「已调整 XX 阈值」通知，`status=applied`（`applied_by='agent'`）。
  - **任一闸门未过**（`critic_rejected` / `deterministic_rejected`）→ **L2 进 `proposed` 队列**，等人工审阅 apply/reject（走 `approve_privileged_action`）。
  - 设计理由：双闸门（Critic LLM 语义安全 + 离线回放确定性「不劣化已解决任务」）本身就是「安全代理」。过了即代表「低风险且可证伪不劣化」——事后 24h 撤回比事前弹窗审批成本低、不打断工作流，小白不被打扰、极客只审真正需要审的。

---

## 3.1 审批分层原则（L0 / L1 / L2）— 元决策

> 背景：进化系统当前「全有或全无」——要么全要人审（self_modify / capability_activate / 技能确认 / P2 队列），要么 `/yolo` 全过。触点太多且不分层，小白看不懂 diff 只能盲批（等于没审），极客被频繁打断。
> 原则：**按风险分层，事后撤回优于事前审批**（用户不忙时看到通知、觉得不对再撤，不打断工作流）。

| 层 | 语义 | 适用（含待落地） |
|---|---|---|
| **L0 自动处置** | 不通知，确定性安全 | 孤儿 flag 清理（已落地 `6c6b2668e`）；config 阈值微调（±10% 内且双闸门过）；**技能高置信+高频采纳**（待落地，见 tiering 计划）|
| **L1 静默执行** | 通知但不可撤 → **24h 内可一键撤回** | **P2 B1 提案双闸门过 → 自动 apply + 通知**（本稿）；低风险能力激活（无 pip install，待落地）|
| **L2 必须人工** | 弹窗审批 | 源码改写（self_modify）；文件删除 / rollback；涉及 pip install 的能力激活；config 大幅调整（>20%）；**P2 闸门没过的提案** |

- P2 只负责把「B1 配置级提案」正确分流到 L1（自动）或 L2（队列）；其余触点的分层（技能采纳、能力激活分级）属 broader tiering 计划，**不在 P2 范围内**，但 P2 的 L1 机制（通知 + 24h 撤回 + rollback 复用）是它们的通用底座。
- **撤回机制可复用（已实证）**：`EmergentChangePipeline.apply_change` 写 config 时已生成 `config.yaml.bak.<timestamp>`，`rollback_change(target_path, backup_path, initiator='user')` **跳过审批闸门**——故 L1 config 撤回机械可行；缺的是「按变更记录回溯到具体 bak + 24h 窗口 + 通知入口」三件套（实现见 §8）。

---

## 4. 提案队列存储（新建 `evolution_proposals` 表）

```sql
CREATE TABLE IF NOT EXISTS evolution_proposals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phase TEXT,                 -- A/B/C/D 进度追溯
  task_type TEXT,
  title TEXT,
  rationale TEXT,
  target_kind TEXT,           -- config（首版仅此值）
  target_path TEXT,           -- config 级 = ~/.vermes/config.yaml
  config_patch TEXT,          -- JSON，config 级提案的拨盘增量
  candidate_diff TEXT,        -- 预留（B2 源码级用，P2 首版 NULL）
  critic_verdict TEXT,        -- JSON {safe, concerns, confidence}
  deterministic_result TEXT,  -- JSON {passed, precision_old, precision_new, count_old, count_new, count_delta}
  status TEXT,                -- proposed | applied | rejected | expired | reverted
  reject_reason TEXT,
  created TEXT,
  applied_at TEXT,
  applied_by TEXT,            -- 'agent' = L1 自动；'user' = L2 手动
  bak_path TEXT,              -- L1 自动 apply 时的 .bak 快照路径（撤回用）
  retract_deadline TEXT       -- L1 自动 apply 后 24h 撤回截止（ISO-8601）
);
```
- 与 `raw_events` 分离（提案 ≠ 已发生事件）；**applied 后由 `self_modify` 写 `raw_events`**（自动进 `self_modify_history` 面板，无需重复实现）。
- **过期（评审补充 a）**：`proposed` 状态超过 7 天（外置）未被 apply/reject → 扫描时自动标 `expired`，防止 config 已变后仍被误 apply。
- **L1 撤回窗口**：`applied`（且 `applied_by='agent'`）状态下，`retract_deadline` 之前可 `retract` 还原 `.bak` → `status=reverted`；超时不可撤（bak 由 `_cleanup_old_backups` 自然回收）。

---

## 5. API 扩展（`chat.py`）

| 路由 | 作用 |
|---|---|
| `GET /api/evolution/proposals?status=proposed` | 列表，供 P3 前端渲染 + 用户审阅（**仅 L2 闸门没过的**）|
| `POST /api/evolution/proposals/{id}/apply` | **L2 手动 apply**（仅闸门没过的提案）：调 `EmergentChangePipeline` 走 `approve_privileged_action` 审批写 `config.yaml`（**不需源码重打包**即运行态生效）|
| `POST /api/evolution/proposals/{id}/reject` | 记 `rejected` + reason |
| `POST /api/evolution/proposals/{id}/retract` | **L1 撤回**（仅 `applied` 且 ≤24h）：`rollback_change(initiator='user')` 还原 `.bak` 快照，标 `status=reverted`；超时不可撤 |

- 复用 `emergence_status` 已注入的 `autoResolve` 字段；P3 前端 Phase 0' 只读行扩展为「已自动调整 N 项（可撤回）/ 待审 N 条」。
- **触发频率（评审补充 c）**：`_scan_evolution_proposals` 用**独立 state 文件**记录 `last_aegis_run_at`，默认 3600s 间隔，不跟反思 daemon（600s）同频。
- **通知**：L1 自动 apply 后写一条「已自动调整 XX 阈值（24h 内可撤回）」通知（复用现有通知通道，待 P3 前端展示）；不弹窗、不打断。

---

## 6. 与 P1 的关系 + B2 为何留 P4（评审决策）

- P1 把阈值外置成 config dial；**P2 的 B1 候选就是去改这些 dial**，过 C/D 双闸门 → 分层分流（L1 自动 / L2 队列）。
- 即：P1 抽出的 config 层 = P2 自改写引擎的**写入目标**。P2 不是「又写一套硬编码」，而是让引擎在 config 层自助调参，每次改动有提案可回滚（`self_modify_rollback` / L1 撤回）。
- **B2 源码级不纳入 P2 首版**：其确定性闸门会退化成「结构校验」（无法廉价重放工具执行），本质是对 `self_modify` 已有能力的重复封装。真正的价值要等 **P4 变体隔离**（A/B 跑真实任务对比、有行为验证）上线后才有意义。B2 是 P4 的前置，不在 P2 范围内。

---

## 7. fail-open 与护栏

- `_scan_evolution_proposals` 整体 `try/except`，失败只 `warning`（沿用 reflection daemon 的 fail-open 纪律）。
- Critic / 确定性闸门全离线可降级：LLM 不可用时**提案不生成**（不静默应用）。
- **分层审批（取代「全量人工」）**：双闸门过的 B1 提案 **L1 自动 apply**（不弹窗，事后 24h 可撤回）；仅闸门没过的进 `proposed` 队列走 `approve_privileged_action`（L2）。`/yolo` 仍可作全局跳过（但 L1 默认即自动，无需 yolo）。
- 所有外置参数（Critic 阈值、count_delta 上限、过期天数、扫描间隔、撤回窗口）缺失/非法 → 回落硬编码默认（纵深防御）。

---

## 8. 实现顺序（对齐后，按评审收紧 + v3 分层）

1. `agent/evolution_manager.py`：加 `evolution_proposals` 表 + `record_proposal` / `get_proposals` / `expire_stale_proposals`（**v3 补 `bak_path` / `retract_deadline` / `applied_by` 字段**）。
2. `agent/emergence_critic.py`（新）：Critic（batch+cache）+ 确定性闸门（precision + count_delta）纯函数（可单测）。
3. `agent/memory_reflection.py`：加 `_scan_evolution_proposals()`（A→B→C→D，B1 only），独立 3600s state；挂 `run_reflection_review` R3。
   - **分流逻辑（v3）**：双闸门过 → 调 apply（L1 自动）→ 记录可撤回变更（bak 路径+时间戳+`retract_deadline`）+ 推通知；闸门没过 → `record_proposal(status='proposed')`（L2 队列）。
4. `vermes_cli/blueprints/chat.py`：加 proposals 路由：
   - `GET /proposals`（列表，P3 渲染用）；
   - `POST /proposals/{id}/apply`（L2 手动，走 `approve_privileged_action` 写 config）；
   - `POST /proposals/{id}/reject`；
   - `POST /proposals/{id}/retract`（**L1 撤回**，≤24h，复用 `rollback_change(initiator='user')`）；
   - **通知**：L1 自动 apply 后写「已自动调整 XX 阈值（24h 内可撤回）」通知（复用现有通知通道，待 P3 前端展示）。
5. P3 前端：`EvolutionPanel` 改展示为「已自动调整 N 项（可展开看详情+24h 撤回）」+「待审 N 条」（仅闸门没过的）；审阅/应用/撤回按钮。
6. 测试：`tests/agent/test_aegis_*.py` 聚焦四阶段 + 护栏（config=0 必拒、删表必拒、退化点检测、Critic 拒、确定性拒/数量爆拒、过期机制、**双闸门过→自动 apply 不进队列 + 撤回还原**）。

---

## 9. 评审遗留决策（已锁定，无需再议）

| 议题 | 决策 |
|---|---|
| 退化点灵敏度（斜率/样本）是否外置 | **否**。固定默认，审计观察后改源码 |
| B2 源码级是否进 P2 首版 | **否**。留作 P4 变体隔离前置 |
| 确定性闸门用 open flag 离线回放 | **认可**，但补 `auto_resolved_count_delta`（≤1.5x）|
| **v3 新增**：P2 提案是否全量进待审队列 | **否**。双闸门过→L1 自动 apply+24h 撤回；闸门没过→L2 队列（呼应 tiering 计划）|

**非阻塞实现期处理（评审补充）**：(a) 提案 7d 过期；(b) Critic batch + 24h cache（key=task_type+config_hash）；(c) AEGIS 独立 3600s 间隔；(d) L1 通知 + 24h 撤回 UI（P3 前端）。
