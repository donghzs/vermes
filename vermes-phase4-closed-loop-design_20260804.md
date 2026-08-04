# Phase 4 — 闭环串联 / 模型-Harness 联合进化（GRPO 式变体选择）

> 状态：设计稿（待拍板） · 日期 2026-08-04 · 前序：Phase 0/1/2/2.5/3 已进运行态

## 0. 目标与诚实定位

**north star**：让 harness（processor/变体）随真实对话 outcome 演化，演化结果回灌模型提示，模型行为随之偏移——形成"模型行为 ↔ harness 选择"的闭环。判据仍是"像优秀员工越用越顺手"，非 benchmark。

**GRPO 的诚实定位**：本项目的"GRPO"≠ DeepSeek 那种 policy gradient 训练（我们无法重训基座模型）。它是 **GRPO 启发的组内相对排序选择**：
- group = 同一 processor 的所有变体 + 当前 active
- reward = 该变体的真实 outcome 分（success/反馈/re-ask）
- advantage = 组内相对（归一化），不是绝对阈值
- "优化"= 用相对排序决定哪个变体晋升/退役（harness 侧选择），不是更新权重

"联合进化"= 模型产生行为 → outcome 排名变体 → harness 选 active → active 选择注入提示 → 模型行为偏移。环闭合在"提示注入"这一步，而非模型权重。

## 1. 现状地图（探针实证，非读代码想当然）

| 环节 | 文件 | 状态 |
|---|---|---|
| 工具 outcome 采集 | `tool_executor.py:349`→`evolution_manager.record_tool_outcome` | ✅ 通电（success/duration/session/turn） |
| 用户显式反馈 | `feedback_learning.record_user_feedback`←`feedback_tool` | ⚠️ 被动，仅显式 thumbs |
| 聚类+生命周期 | `emergent_clusterer`/`cluster_lifecycle` | ✅ 通电（dead-cluster 已修 1580faef6+G5/G6） |
| 洞察→提示 | `evolution_injector.load_and_format_evolution` | ⚠️ 只注聚合统计，无 variant/active 信息 |
| 变更管线 | `emergent_change.apply_change` | ✅ 通电，但提案非 outcome 驱动 |
| 变体存储 | `variant_store`（Phase 3） | ❌ 仅归档，无 score/rank/选择 |
| Critic | `emergence_critic` | ⚠️ 窄域（B1 config patch），无变体 reward |

## 2. 五个缺口（P4 闭合目标）

1. **变体 outcome 归因缺失** — `record_tool_outcome` 不记 active `variant_hash` → 算不出 per-variant reward
2. **无变体排名/打分** — 全仓无 GRPO/relative_rank，`variant_store` 只归档
3. **无自动晋升/退役** — 全靠 UI 手动 rollback/pin
4. **提示不携带进化后 harness 状态** — `evolution_injector` 不注 active variant 选择
5. **提案引擎不消费 outcome** — `capability_evolver` 提案是启发式，不读 variant score

## 3. 设计

### P4-A 变体 outcome 归因（闭缺口 1）
- `raw_events` schema 增 `variant_hash` 字段（可空——非 processor 工具为 null）
- `tool_executor.py:349` 调 `record_tool_outcome` 前：查 `variant_store._load_registry(processor_id).active_hash`，传入
- `evolution_manager.record_tool_outcome` 写 `variant_hash` 列
- **消费方**：P4-B 的 ranker（避免"字段写进 schema≠已接线"——必须有 reader）

### P4-B 变体打分（GRPO 式组内相对排序，闭缺口 2）
- 新模块 `agent/variant_ranker.py`：
  - `score_variant(variant_hash, processor_id)`：从 `raw_events` 聚合（where variant_hash=X）算 reward = w1·success_rate + w2·thumb_balance + w3·(1 - reask_rate) ；duration 仅记录不进 reward
  - `rank_variants(processor_id)`：组内归一化（z-score 或 min-max），输出 relative advantage；写回 `variants/_registry.json` 的 `score`/`scored_at`/`n_samples`
  - 触发：事件驱动（攒够 N 个新 variant_hash 事件）+ MIN_INTERVAL floor（对齐 clusterer 节奏，防抖）
- **冷启动**：新变体无历史 → score = active 当前 score × 先验折扣 + 衰减不确定性；前 K 次调用强制不退役（ε-exploration 探索预算）

### P4-C 自动晋升/退役（闭缺口 3，治理收口）
- `variant_store` 增 `promote_best_variant(processor_id)` / `retire_underperforming(processor_id)`，但**不直接写盘**——生成 `ChangeProposal(source="variant_selector", target_path=active_yaml, content=variant_content)` 走 `emergent_change.apply_change`（和手动 rollback 同路径）
- **治理分层**（呼应 Phase 2.5 自证式治理教训）：
  - L1 processor：自动晋升可直接 `apply_change(force=True)` 落地，但留 self_modify raw_event + change_ledger
  - L2/inline processor：只生成"建议晋升"提案（status=proposed），等人工确认——不让自动排序直接改高风险 processor
- **门槛**：非 active 变体 advantage 超 active 达 Δ（如 +0.15）且持续 M 次评估才晋升；active 持续低于最佳替代才退役

### P4-D 提示回灌（闭缺口 4）
- 扩 `evolution_injector.load_and_format_evolution`：在 `<learned_experience>` 块补一段"当前 active variant / 最近晋升决策 / 理由（score delta）"
- 模型可见："你正运行 web_get 的变体 X（因 +18% success rate 晋升）"——行为受选择影响，环闭合于此

### P4-E 提案引擎消费 outcome（闭缺口 5）
- `capability_evolver.run_emergence_cycle` 或新增 `variant_selector` 源：读 variant score，差距超阈值时生成晋升提案
- 与 P4-C 的 promote_best_variant 共用提案生成逻辑，避免两套

## 4. 反模式自检（本项目高频）

- **字段≠接线**：P4-A 的 `variant_hash` 必须有 P4-B reader；P4-B 的 `score` 必须有 P4-C/P4-D reader。逐字段列消费方。
- **自证式治理**：variant score 来自真实 outcome（tool_executor 信号），非变体自报；晋升门槛 outcome-based。✓
- **测试全绿≠功能可用**：必须有 no-mock 端到端环测试——真实 tool outcome → 真实打分 → 真实晋升 → 真实 active swap → 真实提示注入。不能只单测每个函数。
- **宽 except 吞异常**：ranker/selector 至少 `logger.debug(type(e))`，不裸 pass。
- **部署铁律**：落地须 build + 重装 + PID 路径核验（08-04 Trash 陷阱）。

## 5. 拍板点（6 项，各带推荐）

**P4-① GRPO 范围**
- (A) 先只 `kind: tool` processor（outcome 信号硬：success/error/duration，闭环最短）【推荐】
- (B) 全 processor kind（prompt processor outcome 模糊，靠 re-ask/反馈间接）
- 推荐理由：tool processor 信号最干净，先把环闭通；prompt processor 留 P5。

**P4-② reward 信号组合**
- (A) success_rate 主 + re-ask 惩罚 + thumbs 加权；duration 仅记录不进 reward【推荐】
- (B) 含 duration 进 reward（快=好）
- 推荐理由：避免奖励"快但错"；duration 作副作用观察，不驱动选择。

**P4-③ group 定义（真 GRPO 语义）**
- (A) 组内相对排序（归一化）+ 最小样本 N 才触发【推荐】
- (B) 绝对阈值（success_rate > X 才晋升）
- 推荐理由：相对排序对新变体公平（无历史也能和 active 比）；N 防止单次调用就晋升。

**P4-④ 自动晋升治理**
- (A) L1 自动落地（留账本）；L2/inline 只生成"建议晋升"提案等人工确认【推荐】
- (B) 全部自动 force=True
- 推荐理由：呼应 Phase 2.5 自证式治理——自动排序不该直接改高风险 processor；L1 与 L2 体验差小（都不弹窗），L2 收紧不增弹窗预算。

**P4-⑤ 触发节奏**
- (A) 事件驱动（N 个新 outcome）+ MIN_INTERVAL floor【推荐】
- (B) 纯定时
- 推荐理由：对齐现有 clusterer 节奏，防抖；攒够样本才算，避免噪声。

**P4-⑥ 冷启动**
- (A) ε-exploration：新变体前 K 次不退役，score=先验+衰减不确定性【推荐】
- (B) 新变体 score=0，和 active 同等竞争
- 推荐理由：给探索预算，否则新变体永无出头（冷启动死锁）。

## 6. 风险与未决

- **outcome 信号稀疏**：tool processor 调用频次可能不够攒样本 → 需 N 设小（如 5）+ 跨 session 聚合。
- **re-ask 检测**：如何判定"re-ask"（同 session 内连续相似请求？）需独立定义，可能延后到 P4.5。
- **提示膨胀**：注入 active variant 信息要克制（只注有变更的 processor，不注全量）。
- **并行 agent 提交冲突**：`_registry.json` 写入须原子（tempfile+rename），防并行 agent 竞态。

## 7. 验收

- no-mock 端到端环测试：造 N 个 tool outcome（分属两变体）→ ranker 算出相对分 → promote 生成提案 → apply_change 落地 active swap → evolution_injector 注入新 active 信息。全链 no-mock。
- 运行态三段式：①源码改 ②冻结含 ranker/selector ③重装 + PID 路径在 /Applications + 真实端点验晋升链路。
