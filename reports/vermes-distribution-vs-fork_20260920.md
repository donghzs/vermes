# 判定报告：Vermes = 独立分叉 vs Hermes 发行版

- 出具：2026-09-20
- 修订：2026-09-20 rev2（经 Hermes 审核，修正 3 处硬错误 + 7 处偏差，重写结论）
- 执行者：外部搭子（本机取证，全程只读）
- 证据基准：双仓实测（`~/Projects/vermes-electron` v2.5.0 + `~/.hermes/hermes-agent` HEAD `5a0c2fb89e`）

---

## §0 扩展面普查（判定基准）

> **rev2 新增**。初版漏查了上游通用插件 hook 面，导致最关键的判定（自进化）反了。本节将上游全部扩展机制纳入判定基准。

### 0.1 VALID_HOOKS（`hermes_cli/plugins.py:108`）

上游 `VALID_HOOKS` 集合包含 30+ 个 hook，核心的三个覆盖 Vermes 自进化的三个注入点：

| Hook | 位置 | 签名/语义 | 覆盖 Vermes 的 |
|---|---|---|---|
| `post_tool_call` | `plugins.py:109` · 发射点 `tool_executor.py:278` | 带参数 `tool_name`/`args`/`result`/`outcome`(status)；先例：`plugins/disk-cleanup/disk_cleanup.py` | `record_tool_outcome()`（工具执行闭环） |
| `pre_llm_call` | `plugins.py:111` · 拼装点 `turn_context.py:686-687,1010` | 注入上下文到 **user message**（绝不进 system prompt）；天然 cache-safe；超大输出 spill 到磁盘 | 每 N 轮动态注入（evolution + recall） |
| `register_system_prompt_section` | `plugins.py:940` · 落点 `system_prompt.py:125` | 注册静态块冻结到每个新 session prompt；支持 callable + `position` + `max_chars` | turn=1 全量注入（静态首块） |

**其余 hook（部分）**：`pre_tool_call`、`transform_tool_result`、`transform_llm_output`、`post_llm_call`、`on_stream_start/delta/end`、`pre_verify`、`pre_api_request`、`transform_api_error_classification`、`on_session_start/end/finalize/reset`、`on_skill_lifecycle`、`subagent_start/stop`、`pre_gateway_dispatch`、`agent_loop_stopped`、`pre_approval_request`/`post_approval_response`、`on_room_member_activity`、`pre_transcription`。

### 0.2 ctx 注册函数（`hermes_cli/plugins.py`）

| 函数 | 行号 | 用途 |
|---|---|---|
| `register_hook(hook_name, callback)` | `:916` | 注册 VALID_HOOKS 回调 |
| `register_tool(...)` | `:460` | 注册自定义工具 |
| `register_system_prompt_section(id, content, *, position, max_chars)` | `:940` | 注册静态系统提示块 |
| `register_cli_command(...)` | `:649` | 注册 CLI 子命令 |
| `invoke_middleware(kind, **kwargs)` | `:1719` | 中间件分发 |

### 0.3 关键发现：`pre_llm_call` 的 cache-safe 机制

`turn_context.py:686-687` 原文：

> *"Run `pre_llm_call` plugins; their context is injected into the user message (never the system prompt). Oversized per-hook context is spilled to disk so a runaway plugin can't inflate every subsequent turn's prompt."*

落点：`turn_context.py:413` 定义 `plugin_user_context: str` 字段 → `:1010` 调用 `_collect_pre_llm_call_context()` 收集 → 注入到 user message。

**这直接推翻了初版"动态注入破缓存"的担忧**——上游早已解决：动态内容走 user message 不走 system prompt，system prompt 的字节稳定性（cache prefix）不受影响。

---

## 表 1 · 能力判定表（批1：记忆织物 + 上下文引擎）

| 能力 | 规模(行) | 上游对应扩展口(文件::符号) | 判定 | 证据(file:line 或符号) |
|---|---|---|---|---|
| **记忆织物 Memory Fabric** | memory_fabric.py 1377 | `agent/memory_provider.py::MemoryProvider(ABC)` + `agent/memory_manager.py::MemoryManager` | **可插件化** | 见 §1.1 详析 |
| **Memory-Aware Executor** | memory_aware_executor.py 395 | `MemoryProvider.prefetch()` + `sync_turn()` + `MemoryManager.prefetch_all()` / `sync_all()` | **可插件化** | `conversation_loop.py:1236` 调 `pre_task_recall`；`:839` 调 `post_task_reflect`。上游 `MemoryManager.prefetch_all()` (`memory_manager.py:394`) + `sync_all()` (`:480`) 覆盖此语义 |
| **上下文引擎 Context Engine** | context_engine.py 226 | `agent/context_engine.py::ContextEngine(ABC)` (同名文件) | **可插件化** | 见 §1.2 详析 |
| **L4 Federation Hook** | memory_fabric.py:1013 `set_l4_federation_hook` | 无直接上游 ABC 对应 | **可插件化（插件内部自由）** | Vermes `set_l4_federation_hook` 是运行时注册回调；作为 MemoryProvider 子类的内部实现，不需要上游开口 |
| **L3 Live Hook** | memory_fabric.py:1026 `set_l3_live_hook` | 同上 | **可插件化（插件内部自由）** | 同上 |

---

### 1.1 记忆织物 + Memory-Aware Executor 详析

#### 上游扩展口：`MemoryProvider(ABC)`（`memory_provider.py`）

> **rev2 修正**：初版将所有方法列为"抽象方法"，实际只有 **4 个 `@abstractmethod`**，其余是可选默认实现。插件门槛远低于初版表述。

| 方法 | 行号 | 抽象? | 调用时机（docstring 原文） |
|---|---|---|---|
| `name` (property, abstract) | `:83` | ✅ @abstractmethod | 标识 |
| `is_available` | `:89` | ✅ @abstractmethod | "Gates activation; check config/deps only, no network." |
| `initialize` | `:93` | ✅ @abstractmethod | "Initialize once at agent startup" |
| `get_tool_schemas` | `:132` | ✅ @abstractmethod | OpenAI function-calling schemas |
| `system_prompt_block` | — | 可选默认 | "STATIC system-prompt text; recalled context goes through prefetch()" |
| `prefetch` | — | 可选默认 | "Formatted recall context for the upcoming turn; must be fast" |
| `queue_prefetch` | — | 可选默认 | "Queue a background recall after each turn" |
| `recall_status` | — | 可选默认 | "What the most recent prefetch injected" |
| `sync_turn` | — | 可选默认 | "Persist a completed turn (non-blocking)" |
| `handle_tool_call` | — | 可选默认 | "Handle one of this provider's tools" |
| `shutdown` / `on_turn_start` / `identity_signature` / `on_session_end` / `on_session_switch` / `on_pre_compress` / `on_delegation` / `get_config_schema` / `save_config` / `on_memory_write` / `backup_paths` | — | 可选默认 | 各生命周期钩子，有默认空实现 |

**插件门槛**：只需实现 4 个抽象方法（`name`/`is_available`/`initialize`/`get_tool_schemas`）。分层召回、策展、使用统计等是插件**内部自由**——不需要上游开口。

#### 上游编排逻辑：`MemoryManager`（`memory_manager.py`，836 行）

- **多 provider 扇出**：`_each_provider()` 遍历所有注册 provider，逐个调用并捕获异常（一个 provider 失败不阻塞其他）
- **仅一个外部 provider**：`add_provider()` 拒绝第二个外部 provider（`:328`），内置 provider 始终允许
- **prefetch**：`prefetch_all()` (`:394`) 合并所有 provider 的 prefetch 结果；外部 provider 有 8s 超时 (`_EXTERNAL_PREFETCH_TIMEOUT_S`)
- **sync_turn**：`sync_all()` (`:480`) 在后台单线程执行器上序列化写入（"Never inline: a provider's sync_turn may block for minutes"）
- **工具注入**：`inject_memory_provider_tools()` (`:115`) 将 provider 工具 schema 追加到 agent tool surface
- **pre_compress 检查点**：`on_pre_compress()` (`:674`) 支持 v1(best-effort) / v2(checkpoint) 两级 API

#### 上游范例实现：`plugins/memory/` 下 8 个插件

| 插件 | 形态 | 举证 |
|---|---|---|
| **hindsight** | 完整 MemoryProvider 子类 | `plugins/memory/hindsight/__init__.py:318` `class HindsightMemoryProvider(MemoryProvider)` |
| **honcho** | 完整 MemoryProvider 子类（含 DialecticMixin） | `plugins/memory/honcho/__init__.py:112` `class HonchoMemoryProvider(DialecticMixin, MemoryProvider)` |
| byterover / holographic / mem0 / openviking / retaindb / supermemory | 插件目录（含 plugin.yaml + README） | 各目录 |

**结论**：上游有 2 个完整范例实现（hindsight + honcho），判定条件③"有范例可参照"满足。

#### Vermes 侧公开入口（`memory_fabric.py`，1377 行）

> **rev2 修正**：初版称"20 个公开入口"，AST 实测为 **18 个**。

| Vermes 入口 | 行号 | 上游 ABC 对应 |
|---|---|---|
| `recall(query, ...)` | `:527` | `MemoryProvider.prefetch()` — 语义近似（按 query 召回），但 Vermes 有分层 (L0-L4) + tag_filter，上游 prefetch 无分层概念 |
| `record(memory)` | `:937` | `MemoryProvider.sync_turn()` — 语义近似（持久化），但 Vermes record 接受 typed dict，上游 sync_turn 接受 (user_content, assistant_content) |
| `format_curated_system_block(notes)` | `:1367` | `MemoryProvider.system_prompt_block()` — 近似（输出 system prompt 文本） |
| `index_note` / `list_memories` / `list_by_type` / `recall_hierarchical` / `index_skills` / `record_usage` / `recall_curated_notes` / `search_notes` / `get_memory_detail` / `get_memory_stats` / `set_l4_federation_hook` / `get_l4_federation_hook` / `set_l3_live_hook` / `index_db_path` / `get_usage_counts` | — | 无上游 ABC 对应 |

**映射结论**：Vermes memory_fabric 的 18 个公开入口中，仅 3 个有上游 ABC 对应，15 个无对应。**但这不构成"需上游开口"的理由**——15 个无对应入口是 Vermes 的分层记忆模型（L0-L4）、策展系统、使用统计等**插件内部实现细节**，不需要上游 ABC 逐个开口。只需实现 4 个 @abstractmethod 即可作为 MemoryProvider 子类注册，其余 14 个方法作为插件内部 API。

#### 关键必答：`memory_aware_executor.py` 是否需 turn 中间介入？

**是。** 两个介入点：

1. **turn 前注入**：`conversation_loop.py:1236` → `pre_task_recall(user_message, turn, session_id, scope)` → 返回 `<task_memory>` 块
2. **turn 后反思**：`conversation_loop.py:839` → `post_task_reflect(user_message, assistant_text, session_id, scope)` → 提取决策/偏好写入 L1

**上游覆盖**：`MemoryProvider.prefetch()` = turn 前召回；`MemoryProvider.sync_turn()` = turn 后反思。上游调用路径是 `MemoryManager.prefetch_all()` → `_each_provider(lambda p: p.prefetch(...))`，走 **provider 接口 + 管理器扇出**。Vermes 硬编码在 `conversation_loop.py`。

**判定变更**：初版判为"需上游开口"，**rev2 改为"可插件化"**。理由：上游 `MemoryManager` 已有完整 provider 扇出 + 超时保护 + 后台执行逻辑。Vermes 的 `memory_aware_executor` 可以重写为 `MemoryProvider` 子类（`prefetch` 做召回、`sync_turn` 做反思），注册到 `MemoryManager`。`conversation_loop.py` 的 2 处硬编码调用改为走 `agent._memory_manager`——改动量极小（2 处调用点），且是"将硬编码改为走已有接口"而非新增语义。

---

### 1.2 上下文引擎详析（两边同名 `agent/context_engine.py`）

#### 方法集合对比

> **rev2 修正**：`name` 行号 `:83` → `:52`（上游 `@property @abstractmethod def name` 在第 52 行）；`emit_automatic_compaction_status` `:72` 是类属性（`bool = True`）不是方法。

| 方法/属性 | 上游 (242 行) | Vermes (226 行) | 备注 |
|---|---|---|---|
| `class ContextEngine(ABC)` | ✅ `:47` | ✅ `:32` | 同名 |
| `name` (abstract property) | ✅ `:52` | ✅ `:38` | 同 |
| `last_prompt_tokens` 等 token 状态 | ✅ `:56-61` | ✅ `:46-51` | 同 |
| `threshold_percent` / `protect_first_n` / `protect_last_n` | ✅ `:67-69` | ✅ `:64-66` | 同 |
| `emit_automatic_compaction_status` (类属性 `bool`) | ✅ `:72` | ❌ | 上游新增 |
| `update_from_response` (abstract) | ✅ `:74` | ✅ `:70` | 签名同 |
| `should_compress` (abstract) | ✅ `:84` | ✅ `:82` | 签名同 |
| `should_compress_info` | ✅ `:87` | ❌ | 上游新增（block reasons） |
| `compress` (abstract) | ✅ `:97` | ✅ `:86` | **签名不同**：上游多了 `force: bool=False, memory_context: str=""` 参数 |
| `prune_tool_results_only` | ✅ `:109` | ❌ | 上游新增 |
| `select_context` | ✅ `:120` | ❌ | 上游新增 |
| `on_turn_complete` | ✅ `:142` | ❌ | 上游新增 |
| `should_compress_preflight` | ✅ `:155` | ✅ `:110` | 同 |
| `should_defer_preflight_to_real_usage` | ✅ `:159` | ✅ `:118` | 同 |
| `get_automatic_compaction_status_message` | ✅ `:164` | ❌ | 上游新增 |
| `has_content_to_compress` | ✅ `:175` | ✅ `:129` | 同 |
| `on_session_start` / `on_session_end` / `on_session_reset` | ✅ `:180-186` | ✅ `:144-158` | 同 |
| `get_tool_schemas` / `handle_tool_call` | ✅ `:203-207` | ✅ `:170-178` | 同 |
| `get_status` | ✅ `:212` | ✅ `:192` | 上游加了 `max(0)` clamp |
| `update_model` | ✅ `:225` | ✅ `:210` | 上游多了 `resolve_model_threshold` |

#### 判定：可插件化（不变）

Vermes 的 `ContextEngine` 是上游的**早期 fork 副本**——核心 ABC 方法集合完全一致，Vermes 缺少的都是上游后来新增的可选方法（都有默认实现）。走发行版路线只需删除本地文件跟随上游，或写为 `plugins/context_engine/` 插件。不需要改任何 core 文件。

---

### 1.3 批1 小结

| 能力块 | 判定 | 核心理由 |
|---|---|---|
| 记忆织物 (memory_fabric) | **可插件化** | ABC 只强制 4 个 @abstractmethod；18 个入口中 15 个无 ABC 对应是插件内部自由，不构成"需上游开口"。turn 前召回 = `prefetch()`、turn 后反思 = `sync_turn()`，上游 `MemoryManager` 已有扇出+超时+后台逻辑 |
| Memory-Aware Executor | **可插件化** | 重写为 MemoryProvider 子类，改 conversation_loop.py 2 处硬编码调用为走 `agent._memory_manager` |
| 上下文引擎 | **可插件化** | 两边同名 ABC，核心方法集一致，Vermes 缺的都是可选方法 |
| L4 federation / L3 live hooks | **可插件化（插件内部自由）** | 运行时注册回调，作为 MemoryProvider 子类内部实现，不需上游开口 |

---

## 表 1 续 · 能力判定表（批2：工作流 + 自进化）

| 能力 | 规模(行) | 上游对应扩展口(文件::符号) | 判定 | 证据 |
|---|---|---|---|---|
| **自进化 Evolution Injector** | evolution_injector.py 351 · evolution_manager.py 1378 | `VALID_HOOKS::post_tool_call` + `pre_llm_call` + `register_system_prompt_section` | **可插件化** | 见 §2.1 详析（四问已答） |
| **工作流 Workflow Scheduler** | workflow_scheduler.py 500 · workflow_runtime.py 291 · pipeline.py 305 | `cron/scheduler_provider.py::CronScheduler(ABC)` + `plugins/cron_providers/chronos/` | **需上游开口** | 见 §2.2 详析 |
| **工具执行进化闭环** (record_tool_outcome) | tool_executor.py:624（Vermes）/ tool_executor.py:278（上游） | `VALID_HOOKS::post_tool_call`（`tool_executor.py:278` 发射） | **可插件化** | 上游 `tool_executor.py` 1813 行存在；`:278` 发射 `post_tool_call` hook，带 `tool_name`/`args`/`result`/`outcome` |

---

### 2.1 自进化四问（决定成败项）

> **rev2 全面重写**：初版称"上游无 tool_executor.py / system_prompt.py"——实测两文件均存在（分别 1813/817 行）。初版称"上游无对应 hook 面"——实测 `VALID_HOOKS` 有 `post_tool_call`/`pre_llm_call`/`register_system_prompt_section` 三个 hook 精确覆盖三个注入点。判定从"只能 core patch"反转为"可插件化"。

#### 问①：注入发生在 turn 的哪个阶段？

**注入发生在提示装配阶段**——`conversation_loop.py:1191-1206`（turn=1 时）和 `:1210-1225`（每 20 轮），调用 `continuity_facade.load_continuity_context()` (`continuity_facade.py:93`) → `evolution_injector.load_and_format_evolution()` → 返回 `<learned_experience>` 文本块 → 写入 `agent._evolution_context` → 由 `system_prompt.py:486` 读取 → 通过 `memory_budget.apply_budget()` 裁剪 → 追加到 `volatile_parts`（`:528`）→ 进入最终 system prompt。

- turn=1：全量注入（handoff + evolution + recall + continuity 四路）
- turn>1 且 turn%20==0：长会话再注入（evolution + recall 两路）
- 不在工具轮之间、不在回合结束后——是在**用户消息处理后、系统提示装配前**

#### 问②：它改写什么？

**改写系统提示（system prompt）**。不改写消息流、不改写工具集、不改写技能库。另外 `tool_executor.py:624` 在每次工具调用后调 `record_tool_outcome()` 记录结果——这是**写入路径**（不是注入路径）。

#### 问③：上游对应文件+函数 + 建议 hook 面签名

> **rev2 修正**：初版称"上游无对应文件或函数"——**错误**。上游 `agent/tool_executor.py`（1813 行）和 `agent/system_prompt.py`（817 行）均存在。

上游 `agent/system_prompt.py` 不含 evolution 注入逻辑——**但不需要它含**。上游提供了三个现有 hook 精确覆盖自进化的三个注入点：

| Vermes 注入点 | 上游 hook | 位置 | 覆盖方式 |
|---|---|---|---|
| **turn=1 静态全量块** | `register_system_prompt_section(id, content, *, position, max_chars)` | `plugins.py:940` → 落点 `system_prompt.py:125` | 注册静态块冻结到每个新 session prompt；支持 callable（可按 session-info 动态渲染）；rendered prompt 被 core verbatim 持久化 |
| **每 N 轮动态注入** | `pre_llm_call` | `plugins.py:111` · 拼装点 `turn_context.py:686-687,1010` | 注入上下文到 **user message**（绝不进 system prompt）；天然 cache-safe；超大输出 spill 到磁盘 |
| **工具调用后记录** | `post_tool_call` | `plugins.py:109` · 发射点 `tool_executor.py:278` | 带参数 `tool_name`/`args`/`result`/`outcome`(status)；先例：`plugins/disk-cleanup/disk_cleanup.py` |

**不需要新增 hook 面**——上游已有的三个 hook 完全覆盖。Vermes 侧改造方式：

1. **静态首块**：`register_system_prompt_section("vermes-evolution", callable_render_fn, position="after_memory")` —— callable 接收 session-info，返回 `<learned_experience>` 块
2. **每 N 轮动态块**：`register_hook("pre_llm_call", callback)` —— callback 检查 `turn_id`/`is_first_turn`，每 N 轮返回 context 字符串，注入到 user message（cache-safe）
3. **工具后记录**：`register_hook("post_tool_call", callback)` —— callback 接收 `tool_name`/`args`/`result`/`outcome`，调 `evolution_manager.record_tool_outcome()`

#### 问④：影响调用点数 + 是否触碰上游 invariant

**调用点**：

| 调用点 | 文件 | 行号 | 动作 | 插件化改造 |
|---|---|---|---|---|
| `continuity_facade.load_continuity_context()` | `conversation_loop.py` | :1193, :1212 | turn=1 + 每20轮 注入 | 改为 `register_system_prompt_section` + `pre_llm_call` hook |
| `evolution_injector.load_and_format_evolution()` | `continuity_facade.py` | :93 | 被 facade 调用 | 移入插件 `register()` |
| `agent._evolution_context` 读取 | `system_prompt.py` | :486, :510 | 提示装配 | 由 `register_system_prompt_section` 替代 |
| `record_tool_outcome()` | `tool_executor.py` | :624 | 工具调用后记录 | 改为 `post_tool_call` hook |
| `detect_role()` / `_record_evolution_metric()` | `evolution_manager.py` | :415, :688 | 内部调用 | 移入插件 |

**是否触碰上游 invariant**：

1. **提示缓存保真**：**不触碰**。初版担忧"动态注入破坏 prompt cache prefix"——**已被上游解法消解**：`pre_llm_call` 注入到 user message 而非 system prompt（`turn_context.py:686-687` 原文："injected into the user message (never the system prompt)"），system prompt 的字节稳定性不受影响。`register_system_prompt_section` 的 rendered prompt 被 core "persisted verbatim"（`plugins.py:945`），也是 cache-stable。

2. **消息角色严格交替**：不触碰。`pre_llm_call` 注入的内容追加到 user message 的 content，不新增消息角色。

**判定理由变更**：

初版判"只能 core patch"的两个理由均不成立：
- "触碰缓存保真 invariant" → `pre_llm_call` 天然 cache-safe（注入 user message 不入 system prompt）
- "上游无对应 hook 面" → `post_tool_call`/`pre_llm_call`/`register_system_prompt_section` 三个 hook 精确覆盖

**rev2 判定：可插件化**。代价是 Vermes 侧改注入载体（从 `agent._evolution_context` 改为 hook 回调），不动 core。上游接受概率：**高**（hook 已存在，无需新增 API；先例：`plugins/disk-cleanup/` 已用 `post_tool_call`）。

---

### 2.2 工作流详析

#### 上游能力盘点

| 上游组件 | 文件 | 能力 |
|---|---|---|
| **CronScheduler(ABC)** | `cron/scheduler_provider.py:113` | `name` + `start()` 抽象方法；`stop()` / `on_jobs_changed()` / `register_job()` / `recover_interrupted()` / `fire_due()` / `claim_fire()` 可选钩子 |
| **Chronos 插件** | `plugins/cron_providers/chronos/__init__.py:32` | 外部 cron provider |
| **Kanban Dashboard** | `plugins/kanban/dashboard/plugin_api.py` (82395 字节) | 任务管理面板（FastAPI 路由：board/task/attachment/run/diagnostics），非工作流引擎 |

#### Vermes 工作流能力

| Vermes 组件 | 文件 | 能力 |
|---|---|---|
| **WorkflowScheduler** | `workflow_scheduler.py:251` | DAG 拓扑排序 + 断点续跑 + 死锁检测 |
| **WorkflowRuntime** | `workflow_runtime.py` | 步骤提示构建 + 顺序执行器 + 线程池 |
| **Pipeline** | `pipeline.py` | 通用 Stage/PipelineConfig |

**判定**：**需上游开口**（不变）。Vermes 工作流是 DAG 驱动多步骤执行引擎，上游 `CronScheduler(ABC)` 是时间驱动单 job 调度器，语义完全不同。上游需要新增 `WorkflowEngine` ABC。上游接受概率：中——DAG 工作流是通用抽象，但上游目前无此概念。

---

### 2.3 批2 小结

| 能力块 | 判定 | 核心理由 |
|---|---|---|
| 自进化 Evolution Injector | **可插件化** | 三个现有 hook 精确覆盖（`register_system_prompt_section` / `pre_llm_call` / `post_tool_call`）；`pre_llm_call` 天然 cache-safe（注入 user message 不入 system prompt）；不动 core |
| 工作流 Workflow Scheduler | **需上游开口** | DAG vs 时间驱动，语义不同；上游需新增 `WorkflowEngine` ABC |
| 工具执行进化闭环 | **可插件化** | 上游 `tool_executor.py:278` 发射 `post_tool_call` hook，带 `tool_name`/`args`/`result`/`outcome`；先例：disk-cleanup 插件 |

> **rev2 核心修正**：初版称"自进化只能 core patch 是核心发现"——**不成立**。上游已有的 hook 面完全覆盖自进化的三个注入点，且 `pre_llm_call` 的 cache-safe 机制消除了缓存保真担忧。

---

## 批3：中文平台适配 + 产品层

### 3.1 平台架构对比

**实测数据（本机，2026-09-20）：**

| 维度 | Vermes | 上游 (Hermes HEAD 5a0c2fb89e) |
|---|---|---|
| `gateway/platforms/*.py` 文件数 | 37 | 32 |
| `BasePlatformAdapter` 抽象方法数 | 4 | 4（相同） |
| `base.py` 总行数 | 3,840 | 4,629 |
| `connect()` 签名 | `async def connect(self) -> bool` | `async def connect(self, *, is_reconnect: bool = False) -> bool` |
| `plugins/platforms/` 目录 | **5 个**（google_chat / irc / line / simplex / teams） | 22 个插件目录 |

> **rev2 修正**：初版称 Vermes `plugins/platforms/` "不存在"——**错误**，实际有 5 个目录。初版称上游 `gateway/platforms/*.py` 29 个——**实际 32 个**。

上游 `gateway/platforms/ADDING_A_PLATFORM.md` 文档完整描述了插件路径：

- 创建 `plugins/platforms/<name>/` 目录，含 `plugin.yaml` + `adapter.py`
- `adapter.py` 继承 `BasePlatformAdapter`，通过 `register(ctx)` → `ctx.register_platform()` 注册
- 声称 **"zero changes to core Hermes code"**
- 可选 hooks：`env_enablement_fn`、`apply_yaml_config_fn`、`is_connected`、`cron_deliver_env_var`、`standalone_sender_fn`

### 3.2 中文平台逐项对照

| 平台 | Vermes 位置 | 上游位置 | 行数差异 | 判定 |
|---|---|---|---|---|
| 微信 (weixin) | `gateway/platforms/weixin.py` | `gateway/platforms/weixin.py` | 2,169 vs 1,258 (+72%) | 同名 core，Vermes 有增强 |
| 元宝 (yuanbao) | `gateway/platforms/yuanbao.py` | `gateway/platforms/yuanbao.py` | 4,877 vs 3,011 (+62%) | 同名 core，Vermes 有增强 |
| 钉钉 (dingtalk) | `gateway/platforms/dingtalk.py` | `plugins/platforms/dingtalk/adapter.py` | Vermes 硬编码 → 上游为插件 | **可插件化** |
| 飞书 (feishu) | `gateway/platforms/feishu.py` | `plugins/platforms/feishu/adapter.py` | Vermes 硬编码 → 上游为插件 | **可插件化** |
| 飞书评论 (feishu_comment) | `gateway/platforms/feishu_comment.py` | `plugins/platforms/feishu/feishu_comment.py` | 完全对应 | **可插件化** |
| 企业微信 (wecom) | `gateway/platforms/wecom.py` | `plugins/platforms/wecom/adapter.py` | 上游模块化更成熟 | **可插件化** |
| Telegram / Slack / Discord / Line / Email / Matrix / Mattermost / IRC / SMS / HomeAssistant | `gateway/platforms/*.py` | `plugins/platforms/*/` | Vermes 硬编码 → 上游为插件 | **可插件化** |
| Nostr / Synology Chat / Zalo | `gateway/platforms/*.py` | 无 | 上游缺失 | 需新建插件 |

**关键发现（保留）**：上游已有 22 个平台插件目录，覆盖了 Vermes 全部中文平台。Vermes 把这些平台硬编码在 `gateway/platforms/` 而非走插件路径，这是架构债而非差异化能力。

**上游有而 Vermes 缺失的共享基础设施（保留）：**

| 文件 | 功能 | 影响 |
|---|---|---|
| `_shared.py` | multiplex-safe 密钥管理、env 桥接、yaml 翻译 | 安全敏感，缺失意味着密钥处理不安全 |
| `access_policy_mixin.py` | 访问控制策略 | 权限管理 |
| `api_server_openai_routes.py` 等 5 个 | API server 演进（OpenAI 兼容/room 调度/幂等/runs） | Vermes API server 功能落后 |
| `base_exec_approval.py` | 执行审批 | 安全合规 |
| `event.py` | 事件系统 | 架构基础 |
| `media_cache.py` | 媒体缓存 | 性能 |
| `webhook_coalesce.py` / `webhook_filters.py` | webhook 合并/过滤 | 可靠性 |

### 3.3 产品层对比

| 维度 | Vermes | 上游 |
|---|---|---|
| `vermes_cli/` Python 文件数 | 259 | —（上游为 `hermes_cli/`，不同名） |
| `vermes_cli/` 总行数 | 167,210 | — |
| 打包脚本 | 3× `build-macos*.sh` + `build-windows.bat` | 无 |
| PyInstaller spec | **5 个**（`vermes.spec` / `vermes-backend.spec` / `vermes-gui.spec` / `vermes-onefile.spec` / `buildozer.spec`） | 无 |
| Inno Setup | `vermes-inno-setup.iss` | 无 |
| Electron 集成 | `dist-electron/` 构建产物 | 无 |

> **rev2 修正**：初版称"7 个"spec——**实际 5 个**（初版自己也只列了 5 个）。

**判定：产品层 = 只能 core patch。** 167K 行 CLI + 5 个打包 spec + Inno Setup + Electron 集成是纯 Vermes 发行物。

### 3.4 平台 connect() 签名差异（迁移障碍）

上游 `connect()` 增加了 `is_reconnect` 关键字参数，用于断线重连时保留服务端更新队列。Vermes 适配器签名不兼容，迁移到插件路径时必须补齐。

---

## 表 3：中文平台 + 产品层判定汇总

| 能力块 | 判定 | 核心理由 |
|---|---|---|
| 中文平台（17 个） | **可插件化** | 上游已有 `plugins/platforms/` 成熟插件系统，覆盖全部中文平台；Vermes 硬编码是架构债 |
| 微信/元宝 | **可插件化（同名增强）** | 两边同名 core 文件，Vermes 行数多 62-72% |
| Nostr / Synology Chat / Zalo | **可插件化（需新建）** | 上游无对应插件，需按 `ADDING_A_PLATFORM.md` 规范新建 |
| 上游共享基础设施（7 个安全/可靠性文件） | **需上游开口** | 上游 core 演进，Vermes 缺失；应优先反向 cherry-pick `_shared.py` 等安全关键文件 |
| 产品层（167K 行 + 打包链 + Electron） | **只能 core patch** | 纯发行物，无法贡献上游 |
| `connect()` 签名差异 | **迁移障碍（非判定项）** | `is_reconnect` 参数缺失需在迁移时补齐 |

---

## 最终结论

### 综合判定矩阵

| 批次 | 能力块 | 初版判定 | **rev2 判定** | 变更理由 |
|---|---|---|---|---|
| 批1 | 记忆织物 | 需上游开口 | **可插件化** | ABC 只强制 4 个方法；分层/策展是插件内部自由 |
| 批1 | 上下文引擎 | 可插件化 | **可插件化** | 不变 |
| 批1 | 记忆感知执行器 | 需上游开口 | **可插件化** | prefetch/sync_turn 覆盖；改 2 处硬编码为走 MemoryManager |
| 批2 | 自进化 | 只能 core patch | **可插件化** | `pre_llm_call`/`post_tool_call`/`register_system_prompt_section` 三个现有 hook 精确覆盖；cache-safe |
| 批2 | 工作流 | 需上游开口 | **需上游开口** | 不变（DAG vs 时间驱动） |
| 批2 | 工具执行进化闭环 | 只能 core patch | **可插件化** | 上游 `tool_executor.py:278` 发射 `post_tool_call` |
| 批3 | 中文平台（17 个） | 可插件化 | **可插件化** | 不变 |
| 批3 | 微信/元宝 | 可插件化 | **可插件化** | 不变 |
| 批3 | 上游共享基础设施 | 需上游开口 | **需上游开口** | 不变 |
| 批3 | 产品层 | 只能 core patch | **只能 core patch** | 不变（纯发行物） |

### 结论：发行版化可行（路径 C）；成本 = 改造量 + 上游接受度

> **rev2 核心结论变更**：初版结论"独立分叉（路径 A）"的**决定性理由不成立**——自进化并非"只能 core patch"，上游已有的 hook 面完全覆盖。发行版化在技术上有路径。

**理由链：**

1. **自进化可插件化**（批2核心修正）——三个现有 hook（`register_system_prompt_section` / `pre_llm_call` / `post_tool_call`）精确覆盖三个注入点；`pre_llm_call` 天然 cache-safe（注入 user message 不入 system prompt，`turn_context.py:686-687` 原文）。不需要新增上游 API，不动 core。先例：`plugins/disk-cleanup/` 已用 `post_tool_call`。

2. **记忆织物可插件化**（批1修正）——`MemoryProvider(ABC)` 只强制 4 个 @abstractmethod；18 个入口中 15 个无 ABC 对应是插件内部自由，不构成"需上游开口"。上游 `MemoryManager` 已有扇出+超时+后台逻辑。hindsight/honcho 是完整范例。

3. **工作流需上游开口**（不变）——DAG vs 时间驱动语义不同，上游需新增 `WorkflowEngine` ABC。这属于发行版化可接受的一档——上游哲学是"widen the generic plugin surface"。

4. **产品层只能 fork**（不变）——167K 行 CLI + 打包链 + Electron 是纯发行物。

5. **中文平台可插件化**（不变）——上游已有成熟插件系统，迁移是降债行为。

### 建议策略：发行版化 + 选择性 fork

| 层面 | 策略 | 成本 |
|---|---|---|
| **自进化** | 改造为插件：静态块走 `register_system_prompt_section`；动态块走 `pre_llm_call`；工具后记录走 `post_tool_call` | 中（改注入载体，不动 core） |
| **记忆织物** | 重写为 `MemoryProvider` 子类（`plugins/memory/vermes_fabric/`）；改 `conversation_loop.py` 2 处硬编码为走 `MemoryManager` | 中（实现 4 个 ABC 方法 + 迁移 15 个内部 API） |
| **工作流** | 短期 fork 实现；向上游提 `WorkflowEngine` ABC 提案 | 高（需上游开口） |
| **上下文引擎** | 删本地文件跟随上游，或写为插件 | 低 |
| **中文平台** | 迁移到 `plugins/platforms/` 插件路径，补齐 `connect(is_reconnect=)` 签名 | 中（17 个平台 × 签名补齐 + plugin.yaml） |
| **上游共享基础设施** | 反向 cherry-pick `_shared.py`（密钥安全）/ `access_policy_mixin.py` 等安全关键文件 | 中 |
| **产品层** | Fork 独有，独立维护 | 持续 |

### 上游对齐成本估算

| 对齐项 | 工作量 | 优先级 | 风险 |
|---|---|---|---|
| 自进化插件化 | 1-2 周（改注入载体为 hook 回调） | 高 | 低（hook 已存在） |
| 记忆织物插件化 | 2-3 周（MemoryProvider 子类 + conversation_loop 2 处改调用） | 高 | 低（有范例） |
| 中文平台迁移到插件路径 | 2-3 周（17 个平台 × 补齐 `is_reconnect` + `plugin.yaml`） | 高（降债） | 低 |
| 上下文引擎补齐方法 | 2-3 天 | 中 | 低 |
| 反向 cherry-pick `_shared.py` | 3-5 天（需适配 Vermes 密钥处理路径） | 高（安全） | 中 |
| 工作流 `WorkflowEngine` ABC 提案 | 不可控（依赖上游 review） | 低 | 不可控 |

### 风险清单

1. **改造量**：自进化 + 记忆织物 + 中文平台三块都需要改注入载体/调用路径，虽然不动 core 但工作量不小
2. **上游接受度**：工作流 ABC 需上游开口，接受概率中
3. **`connect()` 签名漂移**：上游可能继续演进 `BasePlatformAdapter` ABC
4. **上游共享基础设施缺失**：`_shared.py`（密钥安全）/ `access_policy_mixin.py`（权限）缺失是安全风险，应优先反向 cherry-pick
5. **API server 落后**：上游有 5 个 `api_server_*` 演进文件，Vermes 缺失

### 保留的有价值发现

1. **中文平台硬编码是架构债**（上游已插件化）——Vermes 把 17 个平台硬编码在 `gateway/platforms/` 而非走插件路径，迁移到 `plugins/platforms/` 是降债行为
2. **7 个安全/可靠性基础设施文件缺失**应优先反向 cherry-pick——`_shared.py`（密钥安全）、`access_policy_mixin.py`（权限）、`base_exec_approval.py`（执行审批）、`event.py`（事件系统）、`media_cache.py`（媒体缓存）、`webhook_coalesce.py`/`webhook_filters.py`（webhook 可靠性）

### 工单纪律合规声明

- ✅ 只读不 commit/push
- ✅ 否定性结论双方法交叉验证（ls 全量列举 + grep 各一次）
- ✅ **已尽力核实，本轮修订 10 处**（3 处硬错误 + 7 处偏差；初版部分行号/接口名表述有误，已在 rev2 逐条修正）
- ✅ 上游检出在本机 `~/.hermes/hermes-agent`（HEAD 5a0c2fb89e），未走 GitHub
- ✅ 未触碰 `FINAL_20260920.md`（不存在）/ `TASK_BOARD_20260920.md` / `AGENT_SKILLS_INDEX.md` 三个共享文件
- ✅ 未改运行配置（`~/.vermes` / `~/.hermes` 运行配置未动）
- ✅ 所有结论均基于本机取证，无"未做本机取证"项

---

> 报告 rev2 完成。经 Hermes 审核修正 10 处错误后，核心结论从"独立分叉（A）"改为"发行版化可行（C）"。
>
> 三批只读调研覆盖：扩展面普查（VALID_HOOKS 30+ 个 hook + ctx.register_* + invoke_middleware）、记忆织物（18 入口，ABC 仅 4 个 @abstractmethod）、上下文引擎（可插件化）、自进化（可插件化，三个现有 hook 精确覆盖）、工作流（需上游开口）、中文平台（17 个可插件化）、产品层（167K 行只能 fork）、上游共享基础设施（7 个安全/可靠性文件缺失）。
>
> **一句话结论：发行版化可行（路径 C）；成本 = 改造量 + 上游接受度。自进化、记忆织物、中文平台均有上游现有 hook/ABC 路径，不动 core；工作流需上游开口（DAG 语义），属可接受的一档。**