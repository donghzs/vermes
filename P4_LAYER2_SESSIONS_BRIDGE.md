# P4 Layer 2 — ScholarForge 成本并入 sessions 表（体验整合方案）

> 目的：把 G4a Layer 1 已采集的 ScholarForge token/cost，**并入主链路 `sessions` 表**，让 `/insights` 总成本、`/usage` 趋势、ModelsPage 模型分布、以及未来的 G4b 告警**统一看到** ScholarForge。属"体验整合"，非功能缺口——Layer 1 已让成本"不丢、可查"。

## 0. 审计结论（已实测，file:line 证据）

| 事实 | 证据 |
|---|---|
| 主链路写成本的 API | `vermes_state.py:1405 update_token_counts(session_id, input_tokens, output_tokens, estimated_cost_usd, model, billing_provider, billing_base_url, api_call_count)`，默认**增量**累加 |
| 主链路调用点（范式） | `conversation_loop.py:2700 agent._session_db.update_token_counts(agent.session_id, ...)` |
| ScholarForge 工具调度链路 | `_run_tool`(tool_executor.py:256, 含 `agent` 闭包) → `agent._invoke_tool`(run_agent.py:4751) → `invoke_tool`(agent_runtime_helpers.py:1598) → `handle_function_call`(model_tools.py:813) → `registry.dispatch`(registry.py:402) → `entry.handler(args, **kwargs)` |
| **handler 拿不到 session** | `registry.py:416 entry.handler(args, **kwargs)` —— ScholarForge handler 仅收 `function_args`，无 `session_id`/`agent` ⇒ 需 contextvar 桥 |
| 两路径都过 `handle_function_call` | 并发 `invoke_tool`(agent_runtime_helpers.py:1681) + 串行(tool_executor.py:1044 / :1105)，且都已传 `session_id=agent.session_id or ""` |
| `agent` 在两 worker 中可见 | 并发 `_run_tool` 闭包持 `agent`；串行 `execute_tool_calls_sequential`(tool_executor.py:734) 持 `agent` ⇒ 可在两处取 `agent._session_db` + `agent.session_id` |
| 无全局 session 句柄 | `vermes_state.py` 无 `current_session` / `_current_session` / 全局 session_db（grep 空） |
| 并发模型 | `tool_executor.py:488 ThreadPoolExecutor` + `executor.submit(ctx.run, _run_tool, ...)` ⇒ contextvar 经 `ctx.run` 复制到 worker，桥可见 |

## 1. 设计（单一真相源 + 最小侵入）

**约定（防 double-counting）**：`sessions` 表 = 成本统一真相源；`scholarforge.db.tool_usage` = ScholarForge 内部明细台账（仅排障用，任何 dashboard/聚合不读它）。两者分属不同库，无叠加风险。

### 1.1 新增共享 contextvar（中立模块，无分层污染）
`agent/session_bridge.py`：
```python
import contextvars
# (session_db, session_id) — 由 tool_executor 在调用 ScholarForge 前注入
SF_SESSION_CTX: contextvars.ContextVar = contextvars.ContextVar("sf_session_ctx", default=None)
```

### 1.2 注入点（仅 `tool_executor.py`，2 处，零改 `handle_function_call` 签名）
- 并发：`_run_tool` 入口（tool_executor.py:256 之后）——
  `if agent._session_db and agent.session_id: SF_SESSION_CTX.set((agent._session_db, agent.session_id))`
- 串行：每工具循环顶部（tool_executor.py:1044 / :1105 所在循环前）——
  同上 `set`。
- **fail-open**：若 `agent._session_db`/`session_id` 缺失（web/网关等无 agent 上下文），ctx 保持 `None`，下游跳过桥接、不报错。

### 1.3 落库点（ScholarForge `tools.py` `_with_usage.finally`，复用 G4a 已算出的 summary）
在 G4a 现有 `record_tool_usage(...)`（Layer 1 台账）**之后**，读取 `SF_SESSION_CTX`：
```python
_ctx = SF_SESSION_CTX.get()
if _ctx is not None and summary["input_tokens"]:
    sdb, sid = _ctx
    sdb.update_token_counts(
        sid,
        input_tokens=summary["input_tokens"],
        output_tokens=summary["output_tokens"],
        estimated_cost_usd=summary["estimated_cost_usd"],
        model=summary["model"],            # ScholarForge 自有 provider/model（如 gpt-4o），非 agent.model
        billing_provider=summary["provider"],
        billing_base_url=summary["base_url"],
        api_call_count=1,
    )
```
- `summary` 即 G4a `_summarize_llm_usage` 产出（`input/output_tokens`, `estimated_cost_usd`, `model`, `provider`, `base_url`）——**不重写计价**，复用 `normalize_usage`+`estimate_usage_cost`。
- 一次 handler 调用 = 一次增量 delta（G4a 已把重试累加成一个 summary，不会重复计）。
- Layer 1 `record_tool_usage` 保留不动（明细台账）。

## 2. 改动清单（约 25 行，3 文件）

| 文件 | 改动 |
|---|---|
| `agent/session_bridge.py` | **新增**：`SF_SESSION_CTX` contextvar（~5 行） |
| `agent/tool_executor.py` | 2 处 `SF_SESSION_CTX.set(...)`（并发 `_run_tool` + 串行循环） |
| `vermes_cli/scholarforge/tools.py` | `_with_usage.finally` 读完 G4a summary 后，按 §1.3 桥接 `sessions` |

**不动**：`handle_function_call` 签名、`registry.dispatch`、26 个 handler、`sessions` 表结构（列已齐备）、G4a Layer 1 台账逻辑。

## 3. 反向验证（R5 解药，必做）

- `tests/scholarforge/test_layer2_sessions_bridge.py`：
  1. `test_bridge_writes_sessions`：mock `_call_llm_request` 返回 usage，mock `agent._session_db`（或真 `vermes_state` 临时库 + `update_token_counts` 探针），`SF_SESSION_CTX.set((db,"sess1"))`，调 `scholarforge_write` handler，断言 `sessions` 行 `input_tokens/output_tokens/estimated_cost_usd` 被累加、`tool_usage` 台账仍写入。
  2. `test_bridge_fail_open`：`SF_SESSION_CTX` 默认 `None` 时调 handler，断言不抛错、`sessions` 零写入、`tool_usage` 仍写。
- **红测**：在 Layer 2 落地前的 commit 上用 `git worktree` 跑 `test_bridge_writes_sessions`，**必须失败**（旧代码不碰 `sessions`）——证测试真抓到桥接。

## 4. 体验整合差异（做完后）

| 看板 | 现在（仅 Layer 1） | Layer 2 之后 |
|---|---|---|
| `/insights` 总成本 | 仅主 Agent | **主 Agent + ScholarForge 真总成本** |
| 写论文会话的成本 | 偏低（缺 write/polish/review/查重/deaigc） | 完整归因到该 `agent.session_id` |
| ModelsPage 模型分布 | 无 ScholarForge 模型 | 出现 ScholarForge 用过的模型（gpt-4o 等） |
| `/usage` 趋势线 | 不含论文写作段 | 补齐 |
| G4b 成本告警 | 看不到 ScholarForge（盲区） | **覆盖 ScholarForge**（Layer 2 是 G4b 前置） |

## 5. 风险与纪律

- **零耦合**：桥是 contextvar + 2 行 `set` + 1 段 `finally`，不侵入 `conversation_loop.py`/`tool_executor` 核心逻辑；ScholarForge 仍不依赖 `agent` 对象。
- **fail-open**：ctx 缺失→跳过，绝不阻断主流程（同 G4a 纪律）。
- **并发安全**：`ctx.run` 复制 context 到 worker，per-call 隔离；`_run_tool` 改 ctx 不泄漏回父线程（ctx.run 跑在副本上）。
- **不做的事**：不重写计价（复用 `normalize_usage`）；不动 `sessions` schema；不把 `tool_usage` 计入任何 dashboard 聚合（双计防护）。

## 6. 后续（backlog，不在本方案）
- G4b 成本告警：建立在 `sessions.estimated_cost_usd` 上，Layer 2 落地后即可覆盖 ScholarForge。
- G1 扩 golden-set、G3 shadow/金丝雀：照原计划，与本方案无关。
