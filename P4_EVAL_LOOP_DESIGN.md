# P4 设计：评测闭环（Eval Loop）

> 状态：设计草案 v1，待团队评审（未动码）。
> 来源：老周聊架构《最后一公里》四根支柱对照 + 家底审计 + 报告校准。
> 纪律：最大化复用现有资产、fail-open、凡加回归测试必做反向验证（R5 解药）。

---

## 0. 修正框架（必须先说清）

本设计**不是从零建指标**。家底审计 + 报告校准发现两处既有资产，P4 在其上补"行为质量"层：

| 既有资产 | 路径 | 说明 |
|---|---|---|
| 工具执行成功率 | `raw_events`(self-model.db) → `v_outcomes` → `get_evolution_status().success_rate` | 已实时累积（默认开启），前端 `EvolutionPanel.vue` 展示"成功率 %" |
| P2 验证信号 | `agent._recent_tool_verify`（Verifier 通过/失败） | 目前**仅当回合内存**，不持久 → P4 要把它"接出来" |
| 工具返回裁剪 | `tools/budget_config.py` + `tool_result_storage.py` + `tool_output_limits.py` | **已存在三层裁剪**（per-result 100K / per-turn 200K / preview 1.5K）。报告称"不裁剪"是错的，P5 应降级为调参 |

**被推翻的报告结论（备忘）**：
- ❌ "无成功率指标" → 已有 `success_rate`（工具执行可靠性）。缺的是**答案正确性/幻觉率**。
- ❌ "工具返回不裁剪" → 裁剪子系统已在跑。
- 报告说"差的只是聚合分析层"偏轻：聚合层已有 `success_rate`，真正缺的是 golden-set + 任务级成功率 + 持久化 verify 信号 + CI 门禁。

---

## 1. 目标 / 非目标

**目标（measure → compare → gate 三件套）**
1. **Golden-set**：版本化任务集 + 程序化/LLM 判定，让"agent 是否真把任务做对"可度量。
2. **Verify 持久化**：把 P2 `tool_verify` 落库 → 跨会话"未验证率/接地率"（幻觉率代理）。
3. **Eval 看板**：在现有成功率旁并列展示任务成功率 + 未验证率。
4. **CI 门禁**：golden-set 跑分跌破基线即告警/拦截。

**非目标（本期不做）**
- 不改模型训练/微调（CS329A 第 4 环，API 冻结刻意不做）。
- 不重建工具返回裁剪（P5 降级为调参现有 caps）。
- 不接全链路 trace_id（P7，排 P4 之后）。

---

## 2. 架构四组件

### 2.1 Golden-set（`tests/eval/golden/`，版本化）
每条用例：
```
{
  "id": "scholarforge.create_project_and_section",
  "task": "为论文《XXX》新建项目并写入摘要章节，引用至少 1 篇 arxiv 文献",
  "setup": "fresh_project",            # 前置：建空项目 / 用固定 fixture
  "expect": {                          # 判定（两类）
    "predicate": "project_has_section('abstract') and section_chars('abstract')>=200 and citations>=1",
    # 或 "llm_rubric": "摘要是否准确概括了文献[1]的 Method 贡献"
  },
  "category": "scholarforge" | "file" | "web" | "code" | ...
}
```
- **判定类型**：`predicate`（确定性，CI 可离线跑）优先；`llm_rubric`（需 LLM 判分，默认**不进 CI**，按需手动跑，避免飘+烧钱）。
- **范围决策（用户拍板：全 agent 任务）** → 分两期：
  - **一期 ScholarForge 垂直切片**：26 工具语义清晰、易写确定性 predicate，先验证整条 harness。
  - **二期 通用 agent 任务**：file / web / code / memory 等高频任务，predicate 更杂，逐步扩充。

### 2.2 Verify 信号持久化（**我的推荐：扩展 self-model.db**）
- 给 `raw_events` 加 `verified INTEGER`（0/1/NULL）列，或建 sibling 表 `eval_verifications(tool_use_id, session_id, verified, reason, ts)`。
- 在 `tool_executor.py` 的 verify 环节（已调 `record_tool_verify`）**额外落库** verified 结果，关联到已有 `raw_events` 行（同 session_id+turn_number+tool_name）。
- 聚合：`get_evolution_status()` 增 `unverified_rate = 1 - SUM(verified)/COUNT(verified)` → 前端展示。
- **理由**：复用现有 DB + 聚合层 + 前端通道，零新存储；与现有 success_rate 并列最自然。

### 2.3 Eval 看板（`EvolutionPanel.vue` 扩展）
- 现有卡片"成功率 %"（工具执行可靠性）**保留**。
- 新增两张卡片："任务成功率 %"（来自 golden-set 跑分） + "未验证率 %"（来自 2.2）。
- 后端新增 `/api/eval/status` 聚合接口（读 self-model.db + 最近一次 golden 跑分结果）。
- 不改既有流协议（保持前端"已对齐/不动"的既定结论）。

### 2.4 CI 评测门禁（`scripts/eval_gate.py`）
- 跑 `tests/eval/golden/` 的 predicate 类用例 → 输出任务成功率。
- **严格度（待团队拍板，默认提案：warn-only 非阻塞）**：
  - 先 warn-only：跌破基线仅告警，不挡 PR（符合 fail-open 纪律，建基线后再升 blocking）。
  - 基线：首次全量跑分定为 baseline，warn 阈值 = baseline − Δ%（Δ 待定，建议 5%）。
- 本地优先：`python scripts/eval_gate.py --suite scholarforge` 可离线跑；CI 接入另议。

---

## 3. 数据模型（草稿）

```sql
-- 扩展 raw_events（或新建 eval_verifications，二选一，推荐前者）
ALTER TABLE raw_events ADD COLUMN verified INTEGER;  -- NULL=未验证, 0=失败, 1=通过

-- golden 跑分结果表（新建，轻量）
CREATE TABLE eval_runs (
  id INTEGER PRIMARY KEY,
  suite TEXT,            -- scholarforge / general
  run_at TEXT,
  total INTEGER,
  passed INTEGER,
  task_success_rate REAL,
  git_sha TEXT
);
```

---

## 4. 反向验证（R5 解药，必做）
- **golden-set 必须含"当前代码应失败"用例**：例如某任务当前会产生未验证工具结果 → baseline 未验证率应 <100%，证指标真能抓问题。
- **eval_gate 必测"红"**：拷贝一条 golden 用例 + 故意引入回归（如改 predicate 期望），确认 gate 变红、且失败信息精确对应所修问题。不做这步 = 测试可能根本没测到。
- 所有新回归测试需能在"feature 前 commit"上跑失败（证验真功能）。

---

## 5. 分期与 Effort

| 期 | 内容 | Effort |
|---|---|---|
| 一期 | harness + golden-set 格式 + ScholarForge 垂直切片 + verify 落库 + 反向验证 | 3–4 天 |
| 二期 | 通用 agent 任务扩充 + Eval 看板 + eval_gate warn-only | 4–6 天 |
| **合计** | | **~1.5–2 周** |

> 注：报告原估 M(3–5 天) 是按"scholarforge 优先"估的；"全 agent 任务"范围下偏长，属预期。

---

## 6. 待团队拍板的问题
1. **CI 门禁严格度**：warn-only（默认提案）vs hard-fail vs 仅本地？（用户：要和团队聊）
2. **基线阈值 Δ%**：跌破多少告警？（建议 5%）
3. **LLM-rubric 用例是否进 CI**：默认不进（成本+飘）；如需进，预算谁出？
4. **golden-set 存放位置**：`tests/eval/golden/`（版本化，推荐）vs 独立数据目录。

---

## 7. 落地纪律（重申）
- 先设计讨论确认 → 再动码；每步小步提交 + 反向验证。
- 前端保持现状的既定结论不变（仅扩展看板卡片，不改流协议）。
- 全部 fail-open，零回归。
