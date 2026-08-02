# Vermes P2 — 闭环提案引擎设计稿（AEGIS 四阶段 + Critic 闸门 + 确定性闸门）

> 状态：**设计稿 v2（已吸收评审）**。评审结论：挂接点全部核实属实 ✅；首版范围收紧为 **B1 配置级 only**；确定性闸门补数量指标；三个拍板点已锁定。
> 上游：P0（前端只读展示，commit `44c738e60`）+ P1（策略外置，`4180e909a` / `44c738e60` + 0 注入护栏 `2ebe62e8f`）已收尾。
> 愿景：运行时利用涌现式记忆自学习飞轮自我改造，减少源码修补（HarnessX/AEGIS「搭乐高」）。

---

## 1. 目标与边界（v2 收紧）

- **目标**：Vermes 在运行态自动发现「退化点」并生成**可审查**的自改提案；任何写操作必须过人类审批（沿用 `self_modify` 闸门）。
- **首版范围 = B1 配置级 only**：只改 P1 外置的 `config.yaml memory.autoResolve` dial（+ 可选 per-task_type 覆盖段）。**不做 B2 源码级**（见 §6 理由，B2 留作 P4 变体隔离的前置）。
- **非目标**：不重新硬编码阈值；P2 是「发现该调什么 + 生成配置级提案 + 过双闸门 + 进待审队列」。

---

## 2. 真实代码挂接点（评审已核实属实 ✅）

| 关注点 | 位置 | 角色 |
|---|---|---|
| 反思 daemon | `agent/memory_reflection.py:157 run_reflection_review()`，R3-R4 标「待实现」 | P2 AEGIS 扫描 `_scan_evolution_proposals()` 插此（**独立 3600s 间隔，不跟反思同频**）|
| 成功率真源 | `agent/evolution_manager.py` 的 `v_outcomes`（视图，底层 `raw_events`）+ `strategies(task_type, strategy, success_rate_when_used, times_used)` | Phase A 输入 |
| 指标时序 | `self_model(metric, value, details)` | Phase A 输入 |
| **zombie 表** | `anti_patterns`（`:195/:824/:861-863` 注释 "superseded"） | **AEGIS 输入一律绕过** |
| 应用/闸门 | `tools/self_modify_tool.py` → `agent/emergent_change.EmergentChangePipeline.propose_change/apply_change`（`:148/:272`）+ `tools/approval.approve_privileged_action`（`:612`） | 实际写 config + 人类审批 |
| 数据结构 | `agent/emergent_change.py:48 ChangeProposal`：`source/target_path/content/description/metadata/initiator` | B1 提案承载 |
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
- 通过 → `status=proposed`，写入提案队列；失败 → `deterministic_rejected`。

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
  status TEXT,                -- proposed | applied | rejected | expired
  reject_reason TEXT,
  created TEXT,
  applied_at TEXT,
  applied_by TEXT
);
```
- 与 `raw_events` 分离（提案 ≠ 已发生事件）；**applied 后由 `self_modify` 写 `raw_events`**（自动进 `self_modify_history` 面板，无需重复实现）。
- **过期（评审补充 a）**：`proposed` 状态超过 7 天（外置）未被 apply/reject → 扫描时自动标 `expired`，防止 config 已变后仍被误 apply。

---

## 5. API 扩展（`chat.py`）

| 路由 | 作用 |
|---|---|
| `GET /api/evolution/proposals?status=proposed` | 列表，供 P3 前端渲染 + 用户审阅 |
| `POST /api/evolution/proposals/{id}/apply` | 调 `EmergentChangePipeline` 走审批（config 级：写 `config.yaml`，**不需源码重打包**即运行态生效）|
| `POST /api/evolution/proposals/{id}/reject` | 记 `rejected` + reason |

- 复用 `emergence_status` 已注入的 `autoResolve` 字段；P3 前端 Phase 0' 只读行扩展为「提案数 / 待审数」。
- **触发频率（评审补充 c）**：`_scan_evolution_proposals` 用**独立 state 文件**记录 `last_aegis_run_at`，默认 3600s 间隔，不跟反思 daemon（600s）同频。

---

## 6. 与 P1 的关系 + B2 为何留 P4（评审决策）

- P1 把阈值外置成 config dial；**P2 的 B1 候选就是去改这些 dial**，过 C/D 双闸门 + 人类审批。
- 即：P1 抽出的 config 层 = P2 自改写引擎的**写入目标**。P2 不是「又写一套硬编码」，而是让引擎在 config 层自助调参，每次改动有提案可回滚（`self_modify_rollback`）。
- **B2 源码级不纳入 P2 首版**：其确定性闸门会退化成「结构校验」（无法廉价重放工具执行），本质是对 `self_modify` 已有能力的重复封装。真正的价值要等 **P4 变体隔离**（A/B 跑真实任务对比、有行为验证）上线后才有意义。B2 是 P4 的前置，不在 P2 范围内。

---

## 7. fail-open 与护栏

- `_scan_evolution_proposals` 整体 `try/except`，失败只 `warning`（沿用 reflection daemon 的 fail-open 纪律）。
- Critic / 确定性闸门全离线可降级：LLM 不可用时**提案不生成**（不静默应用）。
- 默认**全量人工审批**：无 `VERMES_YOLO_MODE` 时，config 级提案也走 `approve_privileged_action`（与源码级同闸门）；仅显式 `/yolo` 跳过。
- 所有外置参数（Critic 阈值、count_delta 上限、过期天数、扫描间隔）缺失/非法 → 回落硬编码默认（纵深防御）。

---

## 8. 实现顺序（对齐后，按评审收紧）

1. `agent/evolution_manager.py`：加 `evolution_proposals` 表 + `record_proposal` / `get_proposals` / `expire_stale_proposals`。
2. `agent/emergence_critic.py`（新）：Critic（batch+cache）+ 确定性闸门（precision + count_delta）纯函数（可单测）。
3. `agent/memory_reflection.py`：加 `_scan_evolution_proposals()`（A→B→C→D，B1 only），独立 3600s state；挂 `run_reflection_review` R3。
4. `vermes_cli/blueprints/chat.py`：加 3 个 proposals 路由（含 apply 走审批写 config）。
5. P3 前端：`EvolutionPanel` 渲染提案列表 + 审阅/应用按钮（复用现有 approval UI）。
6. 测试：`tests/agent/test_aegis_*.py` 聚焦四阶段 + 护栏（config=0 必拒、删表必拒、退化点检测、Critic 拒、确定性拒/数量爆拒、过期机制）。

---

## 9. 评审遗留决策（已锁定，无需再议）

| 议题 | 决策 |
|---|---|
| 退化点灵敏度（斜率/样本）是否外置 | **否**。固定默认，审计观察后改源码 |
| B2 源码级是否进 P2 首版 | **否**。留作 P4 变体隔离前置 |
| 确定性闸门用 open flag 离线回放 | **认可**，但补 `auto_resolved_count_delta`（≤1.5x）|

**非阻塞实现期处理（评审补充）**：(a) 提案 7d 过期；(b) Critic batch + 24h cache（key=task_type+config_hash）；(c) AEGIS 独立 3600s 间隔。
