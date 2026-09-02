# Vermes 取长 · 家底审计（据路线方案逐项目核验 + 纠偏 + 细节补充）

> 目的：依据《Vermes-catchup-roadmap-final.md》路线方案，对每项取长/守长目标做"家底盘点"——
> 已有资产（真实 file:line，本轮实跑复验）、缺口、可复用底座、取长动作、风险/工时。
> 真源：`/Users/dongzusheng/Projects/vermes-electron`（HEAD `d634478778`）。**只读不写。**
> 审计方法：全仓 Grep + 词边界去噪 + 排除 `dist/`/`dist-electron/` 镜像噪声。
> **⚠️ 权威性（2026-09-02 通篇审计后加）**：本文件是**证据配套**，`Vermes-catchup-roadmap-final.md` 是**唯一权威**。若本文件与路线图冲突，**一律以路线图为准**；数字口径若与路线图不同，须显式标注换算关系（路线图「文档契约」第 2 条）。
> **同步状态**：已同步至路线图 **vFINAL.3（2026-09-02「好记性不如烂笔头」实证版）**。vFINAL.2 本轮修订：纠偏 #1 改为「存在」（旧结论错误）、① 工时上调 ~250–350 行、守长板补至 **14 项**、Steer 步数对齐**第 8 步**、补 **⑭⑮ 家底**、修正 ④ 交叉引用、model_overrides 补双锚点。**vFINAL.3 增量**：⑮ 家底再挖——`anti_patterns` 僵尸表（死代码 `_load_anti_patterns`）、P3 学习管线出口坐实（推翻"无出口"旧结论）、`project_handoff` 通用笔记本（仅单域发射）、`continuity_facade` 7 源门面；⑮ 取长动作改为**三条腿**（清僵尸 / 通用发射桥 / 索引插件），工时口径 **~170–260 行**。完整变更见路线图 §13。

---

## 0. 本轮纠偏（旧报告偏差 + 用户补充锚点，均实跑更正）

| # | 旧报告说法 | 实跑结论 | 影响 |
|---|---|---|---|
| 1 | A2A 桥接底座 = `hermes_tools_mcp_server.py` | **🔴 再纠偏（本文件旧结论错误，已与路线图 §9 #1 对齐）**：该文件**真实存在**，路径 `agent/transports/hermes_tools_mcp_server.py`，docstring 明示「Vermes-tools-as-MCP server for the codex_app_server runtime」——把 Vermes 工具集作为 stdio MCP 暴露给 Codex 子进程。**旧判"0 命中"的根因**：Glob 对嵌套包目录误报空（同 #5），且当时搜索范围过窄。**但定位须精确**：它是"**外部 agent 适配器**"的现成实例，**不是**通用 A2A 协议桥——A2A 协议本体底座仍是 `mcp_serve.py` + `tools/delegate_tool.py` / `tools/mcp_tool.py`。 | ① A2A 的"外部 agent 当 bot"由此获得**端到端现成范式**（`codex_app_server.py` + `hermes_tools_mcp_server.py`）；但 A2A 协议层仍需新建 Registry + 信封 + transport adapter registry |
| 2 | Grounded Citations 底座 = `scholarforge/citation_matcher.py` | 文件**存在**，真实路径 `vermes_cli/scholarforge/citation_matcher.py` | 路径修正，底座属实 |
| 3 | `quick_entry` 在 tui hotkeys（旧口头判读） | `quick_entry`/`QuickEntry`/`quick-entry` 全仓 0 命中 | Plugin SDK + quick-entry 确属**完全缺失** |
| 4 | `plugin_sdk` 存疑 | `plugin_sdk`/`PluginSDK`/`external_developer`/`developer_sdk`/`sdk_server` 全仓 0 命中 | 确认缺失，Tier3 从零建 |
| 5 | （Glob 误报）`citation_graph.py` / `mcp_catalog.py` "无文件" | **Glob 误报**：`**/scholarforge/citation*.py`、`**/*mcp_catalog*` 返回空，但 Grep 确认两文件均存在（`vermes_cli/scholarforge/citation_graph.py:1`、`vermes_cli/blueprints/mcp_catalog.py`）。**用户提供锚点成立** | 用户补充的"Grounded Citations 三件套 ~580 行"与"MCP 指挥中心有底座"均属实，已写入路线方案 |
| 6 | `vermes_cli/scheduler.py:1622` | 实为 **`cron/scheduler.py:1622`**（路径写错，内容 `skip_memory=True` 确实存在，且为**硬编码**非配置项） | 路线图 §4 ④ 已更正路径，并标注硬编码依赖 |
| 7 | `_session_key_for_room`（路线图引用为已有 helper） | 实为**待建 helper**（用户实跑确认：真源仅 `_session_key_for_source(event.source)` @ `gateway/session_mixin.py:92` + `run.py:2590/2663`） | 路线图 §3 ③ 已更正为"新建 helper"，并锚定 `_session_key_for_source` 基座 |

> 纪律回响：否定/锚点结论必须实跑，不能沿用上一轮的"我记得有"；且 **Glob 对嵌套包目录会误报空，凡关键否定/存在结论一律以 Grep 为权威**。

---

## 1. Tier1 家底

### ① A2A v1.0 协议（P0 地基）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | `tools/delegate_tool.py`（子代理委派，含 approval 回调，`delegate_tool.py:74-112`）；`tools/mcp_tool.py`（`discover_mcp_tools`，`scheduler.py:1582` 已用）；`mcp_serve.py`（FastMCP 服务，把会话暴露给 Claude Code/Cursor 等外部 MCP 客户端，`mcp_serve.py:51`） |
| 缺口 | 无统一 `AgentHandle` 标识、无消息信封（sender/recipient/room/kind/payload）、无"本地 + MCP + HTTP"传输抽象、无 `AgentRegistry` 持久化、无 agent 生命周期（注册/下线/心跳）、**无 transport adapter registry（外部 agent 接入）**、**信封 `kind` 未原生支持 skill / tool / knowledge 三股流动语义**（见路线图 §5 ① 第 7 点） |
| 取长动作 | ① `AgentHandle`+解析器（复用 @mention 正则）；② 信封 dataclass；③ 传输层（本地 delegate / 跨进程 mcp_serve）；④ **`AgentRegistry` 注册/发现/路由表，持久化到 SessionDB 或新表**；⑤ **agent 生命周期（注册/下线/心跳）**——否则 @mention 路由到已销毁 agent 静默丢消息 |
| 风险 | 低（纯新增）；⚠️ 勿把 Raft 共识塞进 v1 |
| 工时 | **~250–350 行 / 6–10 天**（🔴 口径已与路线图 §1 对齐：旧值 ~200–300 行**未含 transport adapter registry** 与外部 agent 接入；另 §0 #1 再纠偏后，Codex 端到端范式虽现成，仍须抽象为插槽化 registry 才通用） |

### ② Grounded Citations / 事实核查
| 维度 | 结论 |
|---|---|
| 真实已有资产（**较原报告更厚**） | `vermes_cli/scholarforge/` 三件套，均有对应 tests：<br>• `citation_matcher.py`（**289 行**，6 步管线：粗排→精排→阈值→去重→连续编号）<br>• `citation_provider.py`（引用来源提供，含 Semantic Scholar 免费源）<br>• `citation_graph.py`（**102 行**，基于 Semantic Scholar 一跳引文网络，落 `citation_graph_cache` 表，**30 天 TTL**，`database.py:268`） | 
| 缺口 | 这些是 ScholarForge 内部引用匹配，未抽象成对**任意 agent** 开放的 `grounded_citation` 工具集；无"claim→溯源锚点"通用接口 |
| 取长动作 | 抽一层通用 `grounded_citation` 工具集，复用 `citation_matcher` 做 claim↔来源匹配；可独立插队（依赖关系在 A2A 之外） |
| 风险 | 低；学术可信增益高 |
| 工时 | ~2–3 天（含 ~100–150 行封装 + 测试；底座已 ~580 行，几乎零从零成本） |

### ③ Bot Mode Phase 1（详见 `Vermes-botmode-impl-spec.md`）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | `_agent_cache` 两套均 1:1：`gateway/run.py:1441`（`session_key`→`(agent,sig)`）、`vermes_cli/blueprints/agent_cache.py:107`（`_AgentCache(maxsize=20)`，key=`f"{provider}:{model}:{_session_id}"` @ `chat.py:1210`）；现有会话派生基座 `_session_key_for_source(event.source)`（`gateway/session_mixin.py:92`、`run.py:2590/2663`）；渠道 room 提取 `weixin.py:362-366`、`wecom.py:504`、`feishu.py`；前端聊天/房间概念可复用 Web/CLI blueprint |
| 缺口 | 无 room 数据模型、无 **房间 ID 归一化层**（跨渠道 room_id 非同构）、无 `@mention` 解析路由（SDK 原生优先/正则兜底）、无 agent profile 名册、无房间 UI；`_session_key_for_room` **为待建 helper**（真源尚无） |
| 取长动作 | ① 房间作"会话派生层"：`(room, @mention→agent)`→派生 `session_id`→复用现有 1:1 缓存；② **`RoomIdNormalizer`（`channel::room_id`→统一 `room_uid`，~100 行，管理持久化/TTL）**——用户审计要求显式化（原隐含在 P1）；③ 新增 4 表（agent_profiles/bot_rooms/bot_room_members/bot_room_messages） |
| 风险 | 低（缓存结构不动，仅加派生层 + 归一化 + 路由）；用户审计确认派生 key 兼容 `pop_for_session` 的 `endswith` 匹配 |
| 工时 | 前端 ~3 天 + 后端 ~3–4 天（含 RoomIdNormalizer ~100 行） |

---

## 2. Tier2 家底

### ④ Cron monitor-mode（详见 `Vermes-cron-monitor-mode-spec.md`）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | `cron/scheduler.py:1622` `skip_memory=True`（**硬编码**，注释"corrupt user representations"；同款硬编码散见 `gateway/message_handler_mixin.py:1926`、`session_handlers.py:537`）；`cron/jobs.py:509` `create_job`；`context_from` 基线 `scheduler.py:996-1035`；`_session_db` 已在 agent 构造处可用；`vermes_state.py` `_reconcile_columns()` 自动迁移 |
| 缺口 | ① `skip_memory` 硬编码非配置项；② 无 monitor 模式开关；③ 无 hash 短路；④ 无 monitor/notepad 持久表 |
| 取长动作 | 1) `create_job` 加 `monitor_mode` 参数 + 构造 AIAgent 时 `skip_memory=not bool(job.get("monitor_mode"))`（替换硬编码）；2) `cron_monitor_state` 表（目标哈希 + 上次输出）+ hash 短路；3) `cron_notepad` 表；4) 变化检测复用 `context_from` 基线（但**无版本锁**，见**本文件 §2 ⑬ / 路线图 §6 ⑬**；旧稿误写"§5"，本文件 §5 是审计结论） |
| 风险 | 低；`monitor_mode` 缺省 False 时与现状零回归 |
| 工时 | **~200–250 行 / 1–2 天**（🔴 用户审计修正：原 ~110 行偏乐观——根因是 `skip_memory` 硬编码需先改为 config-driven，再加状态表/短路/变化检测）。**口径说明（2026-09-02 加）**：本数**含** `create_job` 参数链路 + API/UI 暴露 `monitor_mode` 开关；`Vermes-cron-monitor-mode-spec.md` §5 的 `~95 行` 仅计 `scheduler.py` 核心逻辑，是**本数的子集**，二者不矛盾（路线图「文档契约」第 2 条） |

### ⑤ MCP 指挥中心统一页（↑ 与 Plugin SDK 对调）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | `vermes_cli/blueprints/mcp_catalog.py:137-161` — `GET /api/mcp/catalog`（列表+`installed` 标记）、`GET /api/mcp/catalog/{name}`（详情）、`POST /api/mcp/catalog/install`（含安全校验）；已在 `web_server.py:3024` 注册、`blueprints/__init__.py:23,35` 接入 |
| 缺口 | 后端目录/安装 API 已有，缺**前端统一可视化页**（目录浏览 / 安装状态 / 调用监控） |
| 取长动作 | 补 ~300 行前端可视化，复用现有 `mcp_catalog` 蓝图端点 |
| 风险 | 低（纯前端增项，不动后端契约） |
| 工时 | ~3–4 天（前端） |

### ⑥ Bot Mode Phase 2（跨渠道群聊）—— 拆 2a / 2b
| 维度 | 结论 |
|---|---|
| 真实已有资产 | 26 渠道平台适配器；`gateway/platforms/weixin.py:362` 已提取 `room_id`/`is_group`/`is_group` 判定；DM vs 群逻辑已存在 |
| 缺口（**用户审计纠偏**） | 审计 37 个 adapter 文件中**仅 15 个原生有 `is_group`/`room_id`**；且各海外渠道 `@mention` 解析逻辑不同，非"挂上去"即可 |
| 取长动作 | **2a 国产渠道（有底座，优先）**：飞书/钉钉/企微/微信 → room 映射复用 `_session_key_for_room`（待建）；<br>**2b 海外渠道（各写解析）**：**原则 SDK 原生 `mentions` 字段优先、正则兜底**——飞书 `feishu.py:357/1177/1197` 已原生解析 `mentions[].id.open_id`；企微 `wecom.py:504` `chattype` 判群 + `wecom.py:519-523` 正则剥 @mention（仅路由）；Discord `<@user_id>` / Telegram `message.entities`（`mention`/`text_mention`）/ Slack `@Uxxxx` 各异，每渠道在 envelope 层写适配 |
| 风险 | 中（多渠道幂等/去重 + 2b 逐渠道适配） |
| 工时 | 2a ~3–5d（依赖 ①③）；2b ~3–5d（依赖 2a） |

### ⑦ hermes peer（bot 间 DM）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | messaging 群聊能力；`delegate_tool.py` 委派原语 |
| 缺口 | 无 bot↔bot 点对点寻址（即 A2A 特例） |
| 取长动作 | 复用 ① 的 `AgentHandle` 寻址 + 生命周期探活，做点对点信封 |
| 风险 | 低（建在 A2A 之上） |
| 工时 | ~1–2 天（依赖 ①） |

### ⑪ 桌面-渠道实时同步（**P0 先决条件 / Tier 1.5**）
| 维度 | 结论 |
|---|---|
| 真实已有资产 | `tui_gateway/ws.py`（桌面控制信道）、`tui_gateway/event_publisher.py`（内部事件总线，非渠道消息推送） |
| 缺口（**用户审计风险点#3**） | `channel_push` 未实现——Memory 记录"服务端WS无推送"；无 `/api/v1/events` SSE、无 `EventSource` 前端消费者；桌面面板无法实时看到 26 渠道消息。Bot Mode 上线后更突出 |
| 取长动作 | 后端在 `gateway/run.py` 消息处理链路 emit 事件 → 前端 SSE 订阅 `/api/v1/events`（复用 `event_publisher.py` 事件总线思路） |
| 风险 | 低（新增事件发射 + SSE 端点，不破既有链路） |
| 工时 | ~150 行。**建议置于 ③ Bot Mode Phase 1 之前作为先决条件** |

### ⑫ 跨渠道 room_id 归一化（P1，Bot Mode Phase 1 显式子项）
- 见 §1 ③ 的 `RoomIdNormalizer`：`channel::room_id` → 统一内部 `room_uid`，~100 行。飞书 `chat_id` ≠ 企微 `chatid` ≠ 微信 `room_id` ≠ 钉钉 `conversationId`，非同构，须归一化避免同一物理群被识别为多个房间。

### ⑬ Cron job 间数据传递（已知限制，暂不取）
- 现有 `context_from`（`scheduler.py:996-1035` + `cron/jobs.py:509/522/632`）支持"取前序 job 最近一次输出"作上下文，**但无版本锁 / DAG 调度**（A 跑完才跑 B）。
- 若做依赖链需引入简单 DAG 调度，暂不在路线图内，标注为**已知限制**。

---

## 3. Tier3（观望项家底）
| 项 | 家底结论 |
|---|---|
| **⑧ Plugin SDK + quick-entry**（↓ 与 MCP 指挥中心对调） | `plugin_sdk`/`PluginSDK`/`external_developer`/`developer_sdk`/`sdk_server` 0 命中；`quick_entry`/`QuickEntry`/`quick-entry` 0 命中。**全仓 0 底座**，须 300–500 行从零建；ROI 低于有底座的 MCP 指挥中心，排后 |
| 安全硬化补全 | 已有 `slash_confirm`（`gateway/run.py:2564` 起）审批机制，可扩 AGENTS.md/记忆/技能写入审批 |
| Raft / memory_graph / Journey / Learn | 全仓 0 实现（`\bRaft\b` 词边界确认无；Journey/Learn 类/函数搜索空）；上游也较新，跟踪即可 |

---

## 3.5 原生愿景家底（⑭ Bot 实验室 / ⑮ 文档记忆层）

> 这两项 **Hermes 无对应概念**，不是"取长"，故单列。全部锚点 **2026-09-02 实跑确认**。详细论述见路线图 §10 / §11。

### ⑭ Bot 实验室 + Agent 一等公民

| 维度 | 结论 |
|---|---|
| 真实已有资产 | `agent/capability_registry.py`（**468 行**三层：声明 / 涌现决策 / 自安装）✅；`vermes_cli/capabilities/registry.py` BrickRegistry（`BRICK_TYPES` @ `registry.py:36`）✅；`vermes_cli/blueprints/capabilities.py`（`GET /api/v1/capabilities` + `/self-check`，注册于 `web_server.py:3008/428`）✅；`vermes_cli/adapters/bootstrap.py` `discover_l2_adapters()` ✅；`vermes_cli/adapters/discovery.py:112` `BackendLocator`（CLI 二进制 + macOS app bundle 双候选，`locate()` @139）✅；`vermes_cli/adapters/software_adapter.py:129` `SoftwareAdapter` ✅；`agent/evolution_manager.py`（自进化）✅ |
| 缺口 | ① **`BRICK_TYPES = ("skill","tool","module","software","provider")` 不含 `"agent"`**——agent 非一等公民（**核心缺口**）；② 无 `LocalAgentScanner`（全仓 0 命中，确属待建）；③ 无 `BrickRegistry._discover_agents()`；④ 前端未消费 `/api/v1/capabilities` 做 agent 卡片动态渲染 |
| 取长动作（**扩展，非从零建**） | 1) `BRICK_TYPES` 增 `"agent"` + 增量 `agent_kind`（cli/desktop/plugin/mcp/cloud_api）/ `auth_scheme`（api_key/oauth/local_session）——`BrickEntry` 已含 `capabilities`/`entry_point`/`source`/`extra`，**净增量小**；2) 新增 `LocalAgentScanner` ~150 行（CLI / 配置 / 桌面 App / IDE 插件 / MCP 五路扫描 → 产出 `AgentInventory`）；3) `BrickRegistry._discover_agents()` ~80–120 行（置于 `_discover_software()` 旁）；4) 前端 Capability-Driven UI ~200–300 行 |
| 风险 | 低（纯扩展 + 新增扫描器，不动既有 BrickRegistry 语义） |
| 工时 | **~430–570 行**（依赖 ① A2A transport adapter：外部 agent 接入后其 capability 标签落入 `BrickRegistry.agent` 条目） |

### ⑮ 文档记忆层（Doc Memory）

| 维度 | 结论 |
|---|---|
| 真实已有资产（**vFINAL.3 再挖，比 vFINAL.2 估计的还厚**） | **三件套有两件半**：① **记忆插件层**——`agent/memory_provider.py` 的 `MemoryProvider(ABC)` 接口完整（含 **`on_pre_compress`** / `on_session_end` / `get_tool_schemas` / `handle_tool_call`），已有 8 个插件 `plugins/memory/{supermemory, mem0, honcho, hindsight, holographic, byterover, openviking, retaindb}`；② **错题本已活**（P3 涌现洞察管线，出口坐实）：`raw_events`（写点含 `agent/feedback_learning.py:25` `record_user_feedback`）→ `emergent_clusterer.py` 聚类 → `emergent_insight.py:108` `EmergentInsightExtractor`（触发点 `skill_extractor.py:621-623`）→ `:648` `build_insight_prompt_block` → `evolution_injector.py:340-341` → `:324` `load_and_format_evolution` → `continuity_facade.py:129-130` → `conversation_loop.py:1193/1212` **注入 system prompt**；③ **笔记本已半通**——`agent/project_handoff.py`（259 行）通用 `project_handoffs` 表（`:54`）+ 4 API（`record_` `:86` / `remove_` `:150` / `get_active_` `:171` / `format_` `:215`），读侧已接 `continuity_facade.py:215-219`（第 7 源）；另有 **7 源注入门面** `continuity_facade.py`（handoff/evolution/recall/continuity/compression_handoff/reflection_flags/project_handoff @ 122/133/144/162/188/205/219） |
| 僵尸资产（**vFINAL.3 新发现**） | **`anti_patterns` 表 = zombie**：`evolution_manager.py:360-362` 注释明写 *"superseded by P3 … but no writes"*，`:1033-1046` 只留兼容读、`:1126-1129` count 恒 0；`evolution_injector.py:96` `_load_anti_patterns()` 读它 → **恒空，死代码**。"反模式记忆 ✅"的旧判断**一半不成立** |
| 缺口 | ① **通用发射端缺位**：`project_handoffs` 表设计上通用（`continuity_facade.py:212-213` 注释 *"any domain can emit"*），但全仓仅 `vermes_cli/scholarforge/project_context.py:301/307/377/384` 一个发射端——"本子造好了，只有一个人在写字"；② **索引层缺失**：7 源各自召回，无跨源目录（人类侧对应 §14 锚点索引）；③ 文档层无独立载体（结论+依据+可回查索引），长程任务"只剩结论、丢了为什么" |
| 取长动作（**三条腿，非从零建**） | **A. 清僵尸** ~10 行：删 `_load_anti_patterns()` 死代码及 `:1126-1129`/`:1189`/`:1272-1273` count 分支（或加注释指向 P3）；**B. 通用发射桥** ~60–100 行：把 `record_project_handoff()` 从 scholarforge 专用推广为任意长程任务可调用（`domain="generic"` 入口 + 阶段切换/会话结束自动发射钩子；读侧**零改动**）；**C. 索引插件** ~100–150 行：`plugins/memory/docmemory/`（第 9 插件）——四工具 `doc_write/read/list/search` + `on_pre_compress` 压缩前落盘（天然免疫轮删）+ 只注入摘要 + front-matter（`~/.vermes/docs/<scope>/<slug>.md`）+ `backup_paths()` |
| 风险 | 低（A 纯删改 / B 纯新增调用 / C 新插件新目录，均不破既有链路）。**待决**：落盘隐私审批（对齐 ⑨ 安全硬化）、摘要策略（规则截断 vs 小模型）、多 provider 共存优先级（**锚点待实跑**） |
| 工时 | **~170–260 行**（三条腿合计；与 vFINAL.2 估的 ~150–250 持平，但**构成反转**——从"从零造笔记本"改为"接通已有笔记本 + 补索引"） |

---

## 4. 守长板家底（确认持平，**现 14 项**，不重复取）
| 能力 | 真实锚点 |
|---|---|
| 语音多平台 | `gateway/voice_mixin.py`、`tools/voice_mode.py`、`tools/transcription_tools.py`（飞书/钉钉/微信语音笔记 docs） |
| 签名外发 Webhook | `gateway/platforms/webhook.py:14`、`msgraph_webhook.py`、`vermes_cli/webhook.py` + `test_webhook_*.py` |
| 上下文压缩/compaction | 压缩模块（compaction）已存在 |
| 工具自恢复 | `agent/auxiliary_client.py`、`conversation_loop.py`、`fallback-providers.md` |
| MoA | `tools/mixture_of_agents_tool.py:44` |
| Skills Hub | `vermes_cli/skills_hub.py:22`、`tools/skills_hub.py` |
| Artifacts | 桌面 Artifacts 已存在 |
| Steer | `run_agent.py:2070` `def steer` + `gateway/session_mixin.py:319` `is_steer_mode` + `vermes_cli/blueprints/status.py:353` `steer_agent` |
| model_overrides | **定义** `gateway/run.py:1446` `_session_model_overrides` / **读取** `gateway/run.py:3272` `self._session_model_overrides.get(session_key)`（per-session provider/model 覆盖——**异构 bot 的底层机制，今天就有**） |
| PII 脱敏 | `redact.py` |
| 浏览器自动化 | `tools/registry.py:134-137`（`browser_navigate`/`browser_click`/`browser_type`/`browser_snapshot`/`browser_cdp` 等 10 个） |
| **操作链验证器**（Vermes 独有·Hermes 无） | `agent/claim_verifier.py:40`「Layer2: 生成工具操作的完整性签名」——三层防护：① 回复内容验证；② **工具结果 MD5 签名**（`run_agent.py:2361-2369` `_build_tool_signature`：`hashlib.md5(result_str...).hexdigest()[:8]`）；③ **压缩保护签名**（`run_agent.py:2405`「签名在上下文压缩时被保护，不会被摘要替代」） |
| **疲劳桥方案**（Vermes 独有·Hermes 无） | `agent/conversation_compression.py:1145` `_build_fatigue_bridge_note`——把 `@decision`/`@preference` 等生命周期标记硬约束注入衔接，使关键约束在 `prune_context` 确定性轮删中存活（裁剪取代压缩） |
| **🆕 能力注册表三层底座**（第 14 项，2026-09-02 补入） | `agent/capability_registry.py`（**468 行**三层：声明 / 涌现决策 / 自安装）+ `vermes_cli/capabilities/registry.py` BrickRegistry（`BRICK_TYPES` @ `registry.py:36`）+ `vermes_cli/blueprints/capabilities.py`（`GET /api/v1/capabilities` + `/self-check`，注册于 `web_server.py:3008/428`）+ `vermes_cli/adapters/{bootstrap,discovery,software_adapter}.py`（`discover_l2_adapters()` / `BackendLocator` @ `discovery.py:112` / `SoftwareAdapter` @ `software_adapter.py:129`）。**这是 ⑭ Bot 实验室"扩展而非从零建"的根本原因**，也是「神魔堂」广纳异构 agent 的注册表依托 |

> Steer 委托编排补全（JSON-schema 校验 / 成本回显）属增量，非新能力，列入路线图 §3 落地顺序**第 8 步**（原第 6 步，因插入 ⑮⑭ 两步而顺延；本文件旧稿写"第 5 步"，已与路线图对齐）。

---

## 5. 审计结论
1. **路线方案成立**：Tier1（A2A + Citations + Bot Mode P1）+ 先落 Cron → Tier2（MCP 指挥中心 + 跨渠道 2a/2b + peer）→ Tier3（Plugin SDK / Raft 观望）。
2. **A2A 必须先行且行数上调**：**~250–350 行**（含 transport adapter registry + Registry 持久化 + agent 生命周期；旧值 ~200–300 未含 adapter registry，已于 vFINAL.2 与路线图 §1 统一），是 Bot Mode/peer/Raft 的地基；本地有 `agent/transports/` ABC+registry 范式 + `hermes_tools_mcp_server.py` 外部 agent 适配器实例 + `delegate_tool.py` 真底座。
3. **最划算先落 Cron monitor-mode**：~200–250 行（🔴 上调：原 ~110 行偏乐观，`skip_memory` 硬编码需先改 config-driven）、零回归，可立即开做。
4. **Grounded Citations 底座最实**：`scholarforge` 三件套 ~580 行（matcher 289 + provider + graph 102），封装成本最低、学术增益最高。
5. **MCP 指挥中心有底座、上移**：`mcp_catalog.py:137-161` 后端已就位，仅缺 ~300 行前端；比 Plugin SDK 从零建更划算（已与 Plugin SDK 对调）。
6. **Bot Mode Phase2 复杂度被低估、已拆**：2a 国产渠道（有 room_id 底座）/ 2b 海外渠道（Discord/Telegram/Slack 各写 @mention 解析）；**@mention 解析原则：SDK 原生 `mentions` 字段优先、正则兜底**（飞书已原生解析、企微半结构化、海外各格式）。
7. **Plugin SDK / quick-entry 确属空白**：全仓 0 命中，下移 Tier3 从零建。
8. **守长板 14 项全部属实**（2026-09-02 补第 14 项「能力注册表三层底座」）：含 2 项 Vermes 独有差异化（操作链验证器三层防护、疲劳桥方案），Hermes 完全没有；仅 Steer 可增量补委托编排。
9. **🔴 新增先决条件 channel_push（⑪，P0）**：桌面-渠道实时同步缺失（Memory 记录"服务端WS无推送"），Bot Mode 前须解决——~150 行 SSE `/api/v1/events`。
10. **Bot Mode Phase 1 显式子项 room 归一化（⑫）**：`RoomIdNormalizer` ~100 行（跨渠道 room_id 非同构）；**`_session_key_for_room` 为待建 helper**（真源仅 `_session_key_for_source`）。
11. **⑭ Bot 实验室 = 扩展，而非从零建**（2026-09-02 补）：能力注册表三层底座已就位（`agent/capability_registry.py` 468 行 + BrickRegistry + adapters 三件套 + 能力清单 API）。真正缺口只有两个：**`BRICK_TYPES` 不含 `"agent"`**（agent 非一等公民）与 **`LocalAgentScanner` 待建**（全仓 0 命中）。~430–570 行，依赖 ① A2A transport adapter。
12. **⑮ 文档记忆层 = 第 9 个 memory plugin**（2026-09-02 补，**本轮最意外的发现**）：Vermes 记忆层本就是**可插拔架构**——`agent/memory_provider.py` 的 `MemoryProvider(ABC)` 接口完整，且已有 **8 个插件**（supermemory / mem0 / honcho / hindsight / holographic / byterover / openviking / retaindb）。尤其 **`on_pre_compress()` 压缩前钩子**正好是"长程任务阶段结论不丢失"的现成挂载点。故该项由"从零建 ~200–300 行"下调为"**复用接口 ~150–250 行**"，是所有项中**底座最出乎意料地厚**的一项。

> 所有 file:line 锚点均本轮实跑复验；dist 镜像噪声已排除；真源未做任何改动。
>
> **同步状态（2026-09-02）**：本文件已与路线图 `Vermes-catchup-roadmap-final.md` **vFINAL.3** 对齐——vFINAL.2：纠偏 #1 改为"存在"、① 工时 ~250–350 行、守长板 **14 项**、Steer **第 8 步**、补 **⑭⑮ 家底**（§3.5）、④ 交叉引用修正；vFINAL.3：⑮ 家底含**僵尸表 / P3 出口 / 通用笔记本 / 7 源门面**，取长动作改**三条腿**。**后续任何修订请同步更新路线图 §13 变更记录**（路线图「文档契约」第 5 条）。
