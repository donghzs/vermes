# 桌面端全渠道统一 — 统一审计报告

> 日期：2026-07-28
> 范围：步骤 1（统一视图）+ 步骤 2（web 落 state.db）+ 步骤 3（send-from-desktop 桥 + §3.5 渠道还原元数据）+ 步骤 4（记忆 scope 治理）+ 存量回填脚本
> 配套设计/规划文档：`docs/design-send-from-desktop-bridge.md`、`docs/plan-desktop-channel-unification.md`、`docs/report-desktop-channel-unification-discussion.md`
> 执行顺序（按 `plan` 修正版）：**1 → 3（TG 验证）→ 2 → 4**，全部完成。

---

## 0. 交付总览

| 步骤 | 内容 | 状态 | 关键文件 |
|---|---|---|---|
| 1 | 前端双源合并统一视图（读闭环） | ✅ | `frontend/src/stores/chat-storage.js`、`chat.js`、`Sidebar.vue` |
| 2 | web/桌面会话落 `state.db`（承重项） | ✅ | `hermes_cli/blueprints/chat.py` |
| 3 | `send-from-desktop` 桥（写闭环）+ §3.5 渠道还原元数据 | ✅ | `hermes_cli/blueprints/session.py`、`gateway/{message_handler_mixin,watcher_mixin,session}.py`、`hermes_state.py` |
| 4 | 记忆 scope 治理（按渠道记 + 跨渠道加权） | ✅（核心机制 + 契约，激活待续） | `agent/memory_fabric.py`、`run_agent.py`、`agent/{memory_manager,memory_provider,rag_provider}.py` |
| — | 存量回填脚本（§3.5） | ✅ | `scripts/backfill_session_origin.py`、`tests/test_session_origin_columns.py` |

---

## 1. 步骤 1 — 统一视图（双源合并，低风险读闭环）

桌面控制台的会话列表 / 消息读取源从 `/api/gui/sessions`（扫 `~/.vermes/messages/*.json`）切到 `/api/sessions` + `/api/sessions/{id}/messages`（读 `state.db`）。

- **前端改造**：
  - `chat-storage.js` 新增 `stateDBHeaders()`（注入 `X-Hermes-Session-Token`）、`listChannelSessionsFromAPI` / `loadChannelMessagesFromAPI` / `deleteChannelSessionFromAPI`（带 token，`fixed 裸 fetch 吃 401`）。
  - `chat.js` 新增 `channelSessions`（与本地 `sessions` 分离，**永不写 localStorage** 防双源污染）、`isChannelSession()`、`loadChannelSessions()`、`_mapChannelMessages()`、`sendToChannelSession()`；`currentSession` computed 改为 `sessions ∪ channelSessions` 查找；`switchSession`/`deleteSession`/`sendMessage` 按 `isChannelSession` 分流。
  - `Sidebar.vue` 新增 `mergedSessions`（去重合并）、渠道徽标 `sourceBadge`。
- **去重护栏**：`loadChannelSessions` 过滤掉本地已存在的 id（`!localIds.has(r.id)`），避免 web 会话在「本地 + 渠道」双源下重复出现。
- **覆盖**：TG / 飞书 / discord / slack 等已在 `state.db` 的渠道会话。web 会话经步骤 2 纳入。

---

## 2. 步骤 2 — web/桌面会话落 `state.db`（承重项）

`hermes_cli/blueprints/chat.py` 的 `chat_completions` → `agent.run_conversation` 路径，每轮追加写 `state.db`：

- 新增 `_persist_web_turn_to_state_db(session_id, user_message, final_response)`：`SessionDB().create_session(session_id, source="web")`（INSERT OR IGNORE 幂等）+ `append_message(user)` + `append_message(assistant)`。
- **双写写点**：流式（`run_sync` 内 `run_conversation` 返回后）与非流式（`final_response` 算出后）两处各调用一次。**失败 open**：整个写入包在 `try/except` 内，state.db 异常绝不阻塞用户看到回复。
- **为什么不再 append 到 `~/.vermes/messages/*.json`**：web 消息仍由 `session.py:save_gui_messages` 写该路径（浏览器 IndexedDB 镜像），与 state.db 是不同存储；state.db 写者唯一（仅 `chat.py`），**无 state.db 内双写**——修正了设计稿 §3.2「端点 append_message」的隐患（gateway `_handle_message` 管线自身会落 user/assistant，双写会触发 #860 类重复；本实现端点只写 relay 信号）。
- **顺序铁律遵守**：web 落库（步骤 2）与读源切换（步骤 1）已同时存在；老 web 会话若在本地 localStorage 则经 `mergedSessions` 本地路径呈现，若来自其他客户端则经渠道路径呈现——**无消失风险**。

---

## 3. 步骤 3 — `send-from-desktop` 桥 + §3.5 渠道还原元数据

### 3.1 写闭环（桌面代发 → 原渠道）

- **端点**（`hermes_cli/blueprints/session.py`）：`POST /api/sessions/{id}/send-from-desktop` 只做「校验 + 写 pending relay 信号 + 立即返回」。**不跑 agent、不 append_message**（gateway 管线统一落库）。拒绝 `source='web'`（防环路）。`GET /api/sessions/{id}/relay-state` 供前端轮询。
- **消费**（`gateway/watcher_mixin.py`）：`_handoff_watcher` 轮询 state.db，识别 `relay_source='desktop'` 记录 → 过期标 `failed` + `clear_desktop_relay` → `claim_handoff` 后走 `_process_desktop_relay` → `_find_source_by_session_id` 还原 `SessionSource` → 构造 `internal MessageEvent` → `_handle_message(event)`（agent 运行、回复经 `adapter.send` 回原渠道、管线落库、记忆逐轮摄入）。
- **护栏三件套**：`X-Hermes-Session-Token` 防伪造（web_server auth_middleware 强制校验，**缺失/伪造 → 401**）；300s 超时回退（前端 5min + 后端 `relay_expire_at` 过期标 failed）；`claim_handoff` 幂等防重放（重复 relay → **409**）；拒绝 `source='web'` 防环路（端点层 → **400**）。
- **状态码口径（统一）**：token 护栏 = **401**（中间件层，非 403——设计稿 §2 曾写「`X-Desktop-Token` → 403」，实现改为复用全站 `X-Hermes-Session-Token` auth_middleware，返回 401，以设计稿附录勘误为准）；`source='web'` 拒绝 = **400**（端点层）；relay 防重放 = **409**（端点层）。
- **⚠️ 审计发现：`desktop_token` 列是 provenance（来源留痕），不是校验门**。`request_desktop_relay` 只把 token 落库（`hermes_state.py`），gateway 消费侧（`watcher_mixin`）**从不读取比对该列**——防伪造的唯一承重护栏是 auth_middleware 的 401。这是跨进程架构的必然而非缺陷：`_SESSION_TOKEN` 是 web 进程内存临时值（每次启动重生成），gateway 进程无渠道获知；若为让 gateway 校验而把 secret 落盘共享，能直写 state.db 的本地攻击者同样能读该 secret，防护增量≈0。剩余风险面（本地直写 state.db）不在本护栏威胁模型内——该攻击者已拥有等同 gateway 的数据权限。HTTP 攻击面由 401 完整覆盖。

### 3.2 §3.5 渠道还原元数据（origin_json / chat_id 等列）

- `hermes_state.py` sessions 表扩列 `chat_id/chat_type/thread_id/display_name/session_key/origin_json` + relay 列；新增 `request_desktop_relay` / `get_desktop_relay_state` / `clear_desktop_relay` / `backfill_session_origin`；`_insert_session_row` 扩参；`gateway/session.py` get_or_create_session / reset_session 补写 `origin_json = json.dumps(source.to_dict())`（对齐上游 #58899 格式，最小移植）。
- **还原路径**：主路径 B = 遍历 `session_store._entries` 取 `entry.origin`（完整 SessionSource，90 天 prune 边界内）；兜底 A = state.db `origin_json`（`SessionSource.from_dict`）；再兜底 = `chat_id` 重建最小 `SessionSource`。

### 3.3 ⚠️ 关键修正：§3.5 列改为「惰性创建」（rollback-safety）

原实现把 §3.5 列放进 `SCHEMA_SQL` 由 `_reconcile_columns` **每次打开即 ALTER 补齐**，违反了既有回归测试 `test_topic_mode_schema_is_not_auto_migrated_on_open` 守护的**回滚安全不变量**（"升级 Hermes 不应在打开旧 bot 的 state.db 时急切改写其 schema"）。

修正方案：
- §3.5 列**从 reconcile 豁免**（`_RECONCILE_EXEMPT_COLUMNS`），但**保留在 `SCHEMA_SQL` DDL 中**（新库天然拥有）。
- 新增 `_ensure_session_origin_columns(conn)`：**仅在真正写入渠道/relay 会话时惰性 ALTER**（写路径：`_insert_session_row` / `backfill_session_origin` / `request_desktop_relay` / `clear_desktop_relay` 各自 `_do` 开头调用）。
- 效果：打开旧库**不改动** schema（通过回滚安全测试）；创建/回填渠道会话时**惰性补齐并持久化** `chat_id/origin_json` 等（§3.5 功能正常）。

> 该测试**未改动其断言**——是 §3.5 实现去适配既有不变量，而非放宽测试。

### 3.4 重要发现（审计结论）：存量渠道会话缺 chat_id/origin_json

对当前 `~/.hermes/state.db` 实测：233 个会话中，`telegram`(11)/`feishu`(23)/`cli`(197)/`web`(2)。**所有 telegram/feishu 存量会话的 `chat_id` 与 `origin_json` 均为 NULL**——它们创建于 §3.5 之前，没有任何可重建的线索。

- 因此 `scripts/backfill_session_origin.py` 对该库 **dry-run = 0 回填**（正确行为：无 chat_id 可重建，绝不臆造）。
- 含义：对**存量**会话，渠道还原只能走**路径 B（live gateway `session_store`）**——即该会话当前在 gateway 进程中活跃（用户刚在渠道发过消息）。**路径 A（origin_json）只对 §3.5 之后新建的会话生效**（新会话已带 `chat_id/origin_json`）。
- 回填脚本仍是有效安全网：若未来出现「有 chat_id 但缺 origin_json」的部分写入会话（如上游仅写 chat_id），它能幂等补齐。

---

## 4. 步骤 4 — 记忆 scope 治理

### 4.1 实际架构校正（与 plan 的关键差异）

`plan` §0 曾假定「per-turn sync 把记忆写入 `memory_fabric` 且 scope=''」。实测代码：

- `MemoryProvider.sync_turn` 是抽象/no-op；`RAGProvider.sync_turn` 显式 `pass`（注释："RAG doesn't store turns — that's the session DB's job"）。
- 真实写入 `memory_fabric.memories` 表的是 `tools/memory_tool._sync_rag_index → index_note`（记忆工具操作时）、`index_skills`、`record_usage`、`memory_migration`——**均非 per-turn 自动捕获**。

即：**当前架构下 per-turn 自动记忆捕获到 fabric 是关闭的**；fabric 记忆是「精选/技能/迁移」型，并非每轮对话自动沉淀。因此 plan 步骤 4 的「防 per-turn 污染」前提在当前代码下并不成立（因为没有 per-turn 自动写入）。

### 4.2 本次落地（正确、安全、可独立 PR）

- **(A) 跨渠道加权召回**（`memory_fabric.recall`）：当传入 `scope` 时，**不再硬过滤为该 scope**，而是 `(m.scope = ?) DESC` 把当前渠道记忆**加权前置**，同时仍聚合其他渠道 + 渠道无关（`scope=""`）记忆保涌现（路径 A 主、兜底 fallback 同步修正）。`scope=None/""` 行为完全不变。
- **(C) scope 契约透传**：`run_agent._sync_external_memory_for_turn` 推导 `scope = self.platform or "web"` → `MemoryManager.sync_all(scope=...)` → `provider.sync_turn(scope=...)`（base + RAG 签名均加 `scope` 参数）。为任何未来/替代型记忆后端提供渠道标签能力。

### 4.3 未激活（单 agent 架构下为过度设计，保留为休眠契约 + 多 agent 前置储备）

- **(B) 真实 L1 写路径 scope 注入**：`tools/memory_tool._sync_rag_index` 调用 `index_note` 时未传 `scope`，因为该工具类 `__init__` 仅含 char-limit 参数、无渠道/platform 上下文。要给精选记忆打渠道标签，需把 `platform` 透传到 memory tool——涉及记忆工具重构。
- **recall 调用方激活**：`memory_recall.recall_context` / `continuity_facade` 未传 `scope`，故 (A) 的加权目前是「休眠的正确机制」——一旦有调用方传 scope 即生效。`recall_context` 走 `recall_hierarchical` 而非直接 `memory_fabric.recall`，透传需改 `recall_context`/`recall_hierarchical`，属更深改动。

> **架构判定（产品稳定性视角，覆盖原 plan 的 follow-up 表述）**：当前 Vermes 是**单 agent 实例**——桌面多会话、TG/飞书/web 会话全部跑在同一个 agent 进程内，靠 `session_id` 隔离，**未上多 agent 实例**。因此「跨渠道记忆污染」在**会话级已天然隔离**（不同 `session_id` 上下文互不可见）+ **记忆层 FTS5 相关性召回已过滤**（不相关话题根本不召回）。scope 加权解决的「同 agent 内跨 session 长期记忆检索优先级」命题，在单 agent 下权重极低、且本仓 fabric 仅 2 条 `scope=''` 的 skill 记忆（per-turn 捕获关闭），加权对当前数据是**空操作**。
>
> 故步骤 4 在当前架构下**不应激活**——它不是「漏做的 follow-up」，而是**多 agent 实例架构的前置储备**。届时 scope 语义需扩展为「agent 身份 + 渠道 + 用户」三元组（而非当前 `platform`），现有 `scope=platform` 契约应随多 agent 架构一并重设计。现在不接线、不激活，避免给单 agent 架构引入「为多 agent 设计的隔离复杂度」。**发版仍走手动出包**（`build-vermes.yml` 历史上 v2.3.0/v2.3.1/v2.3.7 均 failure，CI 出包不可用）。

> 结论：步骤 4 在「机制 + 契约」层完整落地且无害休眠；激活前置 = 多 agent 实例架构上线，而非记忆数据量增长或 per-turn 捕获开启。

---

## 5. 验证结果

- `uv run pytest tests/test_hermes_state.py tests/test_session_handoff.py tests/test_memory_recall.py tests/test_memory_budget.py tests/gateway/test_session.py tests/gateway/test_session_list_allowed_sources.py tests/gateway/test_session_store_prune.py tests/gateway/test_active_session_text_merge.py` → **400 passed**。
- 新增 `tests/test_session_origin_columns.py`：断言「打开旧库不加 §3.5 列（回滚安全）+ 写渠道会话惰性补齐并持久化 chat_id/origin_json + relay 路径可用」→ **1 passed**。
- 全量改模块导入冒烟（`run_agent` / `agent.memory_*` / `hermes_cli.blueprints.*` / `gateway.*`）→ 通过。
- `scripts/backfill_session_origin.py --dry-run` 在当前 state.db 实测：scanned=233, backfilled=0, skipped=233（与 §3.4 发现一致）。
- 已知前置失败：`tests/agent/test_emergent_change.py` 有 15 个与本次无关的预存在失败（冷启动门），未触碰。

---

## 6. 端到端自测建议（供你审计时手动验证）

1. **统一视图**：启动 gateway + 桌面，确认 TG/飞书历史会话出现在左侧列表并带渠道徽标；点开可读消息。
2. **桌面代发（TG）**：在桌面选中某 TG 会话 → 发一条消息 → 原渠道用户收到回复 + 桌面轮询看到 assistant 回复 + `memory_index.db` 出现该轮。
3. **web 落库**：浏览器/web 端发起对话 → `~/.hermes/state.db` 的 `sessions`/`messages` 出现该 web 会话（source='web'）。
4. **护栏**：gateway 未运行时桌面代发 → 5 分钟内前端显示超时提示；带错/无 token 的 `/api/sessions/*` 请求被 **auth_middleware 拦截返回 401**（不是 403——中间件层统一 401）；对渠道会话端点提交 `source='web'` 会话 → 端点返回 **400**；对同一 pending relay 重复提交 → **409**。

---

## 7. 提交拆分（供审计）

|c1| `fix: splash path lookup (dev+packaged) + clear stale Electron partition storage` | `electron/main.js`, `package.json`（与本次规划无关，但为防丢失单独提交）|
|c2| `feat(step1): unified cross-channel session view (dual-source merge)` | 前端三文件 |
|c3| `feat(step2): mirror web/desktop turns into state.db` | `hermes_cli/blueprints/chat.py` |
|c4| `feat(step3): send-from-desktop bridge + §3.5 origin_json (lazy columns)` | `hermes_state.py`, `hermes_cli/blueprints/session.py`, `gateway/{message_handler_mixin,watcher_mixin,session}.py` + `tests/test_session_origin_columns.py` |
|c5| `feat(step4): channel-scoped weighted memory recall` | `agent/{memory_fabric,run_agent,memory_manager,memory_provider,rag_provider}.py` |
|c6| `chore: origin_json backfill script` | `scripts/backfill_session_origin.py` |
|c7| `docs: desktop channel unification design/plan/report` | `docs/{design,plan,report}*` |

注：`docs/artifact-*` 与 `operator_claim_verifier_fixes_*.md` 为中间分析草稿，未纳入提交。

---

## 8. 风险登记（更新自 plan §5）

| 风险 | 处置 |
|---|---|
| 步骤 2 顺序错（先切读源后落库） | 已规避：web 落库与读源切换 coexist，mergedSessions 去重防消失 |
| handoff 消费者被 relay 污染 | `relay_source='desktop'` 过滤 + 单测 `test_session_origin_columns`/`test_session_handoff` |
| relay 端点被伪造 | `X-Hermes-Session-Token`（auth_middleware → **401**）+ 拒绝 `source='web'`（端点 → **400**）+ 防重放（**409**）。注：落库的 `desktop_token` 为 provenance 留痕、gateway 不校验（跨进程无共享秘密可验，见 §3.1 审计发现）；HTTP 面由 401 承重 |
| 本地直写 state.db 伪造 relay 行 | **接受（威胁模型外）**：该攻击者已拥有等同 gateway 的数据权限，任何 DB 内校验均无增量；落盘共享 secret 亦可被同权限读取 |
| gateway 未运行 → 死信 | `relay_expire_at` 超时 + 前端失败态 |
| web 双写不一致 | 端点只写 relay 信号，绝不 append_message；state.db 写者唯一 |
| §3.5 急切改写旧库 schema | **已修正为惰性创建**，通过 `test_topic_mode_schema_is_not_auto_migrated_on_open` |
| 步骤 4 激活不足 | (A)+(C) 落地，激活面（memory tool 渠道透传 / recall 调用方传 scope）记为独立 PR |
