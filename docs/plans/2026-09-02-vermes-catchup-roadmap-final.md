# Vermes 向上游 Hermes 按需取长 · 最终版路线方案

> **性质**：取长决策基准（**单一权威文档**），同时也是**可回证的工程记忆资料**——不只是路线图，任何结论都应能追到锚点与修订记录（见 §13 变更记录 / §14 锚点总索引）。
> **配套文档**：`Vermes-asset-audit.md`（逐项家底核验 + 纠偏证据）、`Vermes-botmode-impl-spec.md`（③ 可 apply 规格）、`Vermes-cron-monitor-mode-spec.md`（④ 可 apply 规格）。**配套文档与本文冲突时，一律以本文为准**（本文是唯一权威口径；配套若用子集口径须显式标注换算关系，见「文档契约」第 2 条）。
> **综合来源**：两轮上游报告（v0.20.0 Herald + v0.21.0 Pantheon 两代源码核验）+ 用户 5 轮审阅意见 + 家底审计实跑 + **2026-09-02 异构融合平台深化** + **Bot 实验室 / Agent 一等公民愿景** + **2026-09-02 通篇审计（哲学统一）**。
> **真源**：`/Users/dongzusheng/Projects/vermes-electron`（HEAD `d634478778` / v2.4.7）。**只读不写**。
> **取证纪律**：能力存在性三方必搜（源码仓 / 运行时 venv / 用户技能目录）+ 实跑；锚点只定位，改动由用户 commit/push；`dist/` 镜像噪声与 Glob 误报已排除，凡关键结论以 **Grep 为权威**（Glob 对嵌套包误报空，禁止用 Glob 下否定结论）。
> **品牌定位（2026-09-02 定名）**：Hermes = **万神殿**（Pantheon，v0.21.0 代号）；Vermes = **神魔堂**——**任意 AI agent 皆可登堂，只要用户设备上有**（详见 §0 第 5 条）。
> **发布策略（2026-09-02 决策）**：本轮重大升级**只本地 commit、暂不 push 远端**；开源时机待升级完成、发布市场获取真实用户后再定。
> **版本状态**：**vFINAL.6（2026-09-05「⑭ B 部分 · ACP Registry dump + 文档权威裁决」版）**。⚠️ 历史漏升已纠正：vFINAL.4 曾漏升（仍写 vFINAL.3）；本副本 vFINAL.6 内容一度误标 vFINAL.5，2026-09-05 审计后已对齐。相对 vFINAL 的全部变更见 §13。已落定：3 轮审阅补丁（A2A 行数上调、Phase2 拆 2a/2b、Plugin SDK↔MCP 对调、Cron 200–250 行、`_session_key_for_room` 澄清、锚点 2 处纠偏、⑪⑫⑬ 纳入）+ 异构融合平台升级（① transport adapter registry + 外部 agent 当 bot；③ agent_profiles 异构三列；#1 再纠偏）+ **⑭ Bot 实验室与 Agent 一等公民** + **⑮ 文档记忆层** + **通篇审计修复 14 项一致性问题（见 §9 #11 与 §13）** + **vFINAL.3 实证深化**：§11 重写为「错题本 / 笔记本 / 插件层」三发现（`anti_patterns` 僵尸表坐实、P3 学习管线出口坐实推翻"无出口"旧结论 §9 #12、`project_handoff` 通用笔记本仅单域发射、`continuity_facade` 7 源门面），⑮ 落地形态改**三条腿**（~170–260 行），§11.4 确立「错题本 + 笔记本 + 索引本」三件套方法论。

---

## 文档契约（先读这一节：如何读 / 如何回证 / 如何避免误读）

> 本节是本文档的**元规则**。设立原因：2026-09-02 通篇审计发现配套文档之间存在**数字分裂与事实矛盾**（共 14 项，详见 §9 #11）——若不统一，后续推进会按错误口径返工，回头溯源时更会产生误读。**任何人在任何阶段引用本文档（人也好、agent 也好），请先遵守本节。**

### 1. 编号体系：①–⑮ 全局唯一，**终身不变**
- 编号是**身份**，不是优先级顺序。某项被降级 / 延后，**编号绝不重排**（如 ⑧ Plugin SDK 从 Tier2 下调 Tier3，编号仍是 ⑧；④ Cron 从传闻的 P0 调降 Tier2，编号仍是 ④）。
- 这样"⑧"在任何历史文档、聊天记录、commit message、issue 里都指向同一个东西，避免"我记得 ⑧ 是 XXX"式的溯源错位。
- 完整对照见 §1 总览表。约定：**⑥a/⑥b** 是 ⑥ 的渠道拆分；**⑫** 是 ③ 的显式子项；**⑬** 是 ④ 的已知限制；**⑭⑮** 是 **Vermes 原生愿景**（非取长自 Hermes，Hermes 无此概念）。

### 2. 数字口径：以 §1 总览表为唯一权威
- 凡"估算行数 / 工期"以 **§1 总览表**为准。配套文档若采用不同口径（例如只算核心逻辑、未含 API/UI 参数链路），必须**显式标注换算关系**，不得直接写出另一个总数而不解释。
- **实例（本轮已修）**：**④ Cron** —— 本文件口径 `~200–250 行`（含 `create_job` 参数链路 + API/UI 暴露开关），`Vermes-cron-monitor-mode-spec.md` 口径 `~95 行`（仅 `scheduler.py` 核心逻辑）。二者不矛盾，后者是前者的**子集**，已在 §6 ④ 与规格 §5 双向标注。
- **实例（本轮已修）**：**① A2A** —— 本文件 `~250–350 行`（含 transport adapter registry），`Vermes-asset-audit.md` 旧值 `~200–300 行`（未含 adapter registry），已统一为前者。

### 3. 锚点纪律：`file:line` 必须实跑，且**每次引用都重新实跑**
- **Glob 对嵌套包目录会误报空**（已多次踩坑：`**/scholarforge/citation*.py`、`**/agent/transports/*`、`**/capabilities/registry.py` 均误报"无文件"）。凡"存在 / 不存在"结论一律以 **Grep 为权威**，**禁止**用 Glob 下否定性结论。
- 否定性结论（"Vermes 没有 X"）须**三方必搜**（① 源码仓 ② 运行时引擎 venv `~/.vermes/engines/...` ③ 用户技能目录 `~/.vermes/skills/`）+ 实跑，且**不加 head 截断**（截断会漏掉真正的调用方，历史上已因此误判过"没有出口"）。
- **不信任历史批注**：连自家上一轮的"纠正"都可能被新 Grep 推翻——§9 #1 即为实例（曾判 `hermes_tools_mcp_server.py` 不存在，后被证实存在）。锚点结论随**每次引用**重新实跑。
- 所有引用过的锚点汇总于 **§14 锚点总索引**，真源演进后可批量回扫复验。

### 4. 交叉引用：禁止悬空
- 引用必须指向**真实存在的节 / 条目**。本轮已修复一处悬空：§10.6 曾引"§5 ① 第 7 点"，而当时 ① 的取长动作只列到 6 步——现已在 §5 ① 补齐第 7 点。
- 新增 / 删除 / 重排任何一节时，必须同步更新所有指向它的引用。**checklist**：§1 总览表 → §3 落地顺序 → §5/§6/§7 明细 → §9 纠偏表 → §10–§12 正文 → **配套三份文档** → §13 变更记录 → §14 锚点索引。

### 5. 变更可追溯
- 每次修订必须进 **§13 变更记录**（时间 + 改了什么 + 为什么改）。
- **不删除历史错误结论**，而是标注为"**已纠偏**"并保留原说法（见 §9）——后期溯源时能看清"当时为什么这么判断、后来为什么改"，避免重蹈覆辙。这是本文档作为"记忆资料"的核心价值。

### 6. 本文档也是「可回证的记忆资料」（元方法论）
- 它不只是计划，更是**决策的记忆**：每条结论都带来源（实跑锚点 / 用户审阅 / 上游报告 / 第三方审阅），每种分歧都记进 §9。
- 这一点本身正是 **⑮ 文档记忆层**要解决的问题：Vermes 有多层记忆（会话 / 长期 / 技能 / 反模式 / 疲劳桥），但缺"**外部知识库 · 文档层**"——长程任务与长会话中，阶段结论若只留在上下文里，会在压缩 / 换会话后丢失；落成文档才能跨会话回证与续接。**我们此刻做的文档工程，就是该能力的人类侧范本**（见 §11）。

---

## 0. 核心判断

1. **Vermes 并不落后。** 对比 Hermes v0.20/v0.21 的 22 项能力，Vermes **已有 13 项**持平或更宽（含 2 项 Hermes 完全没有的独有差异化：操作链验证器、疲劳桥方案，见 §8）。真正要"取的长"集中在 **异构多智能体协同（A2A / Bot Mode / peer）+ 学术可信（Grounded Citations）+ 外部可扩展（Plugin SDK）+ 低成本 Cron monitor-mode** 四块。
2. **取长第一原则：A2A 协议先于一切多智能体特性。** Bot Mode、hermes peer、Raft 都是"A2A 之上的应用层"。没有统一 agent 寻址/消息协议，每个特性都得各写一套传输，返工巨大。故 A2A v1.0 是 Tier1 的 **P0 地基**。
3. **守长不重复造轮子。** 13 项长板只守/强，仅 Steer 可增量补"委托编排"（JSON-schema 校验 / 成本回显），不另起新能力。
4. **🔴 生产级终态是"异构 Agent 联邦（Heterogeneous Agent Federation）"，不是"单底层多 bot 尝鲜"（用户 2026-09-02 深化；DeepSeek 独立审阅点破隐含前提：现 Bot Mode=同源 agent 多实例，你真要的是 N 个异构系统经统一协议互联）。** Hermes/Vermes 现 Bot Mode 的 bot 本质共用同一 `AIAgent`、同一 model 后端、同一工具集，只差 persona——这不是分工，是"同一脑子换帽子"。真正的生产分工需满足 **模型独立性**（`model_overrides` 已按 `session_key` 覆盖 provider/model，`run.py:3272`）+ **能力栈独立性**（Skills Hub / modules market 各异）。更进一层：群里 bot 不必是 Vermes-native，可经 A2A 的 **transport adapter** 接入外部 agent（Codex / Claude Code / OpenClaw / 扣子 / 豆包 / 百度搭子 / 腾讯 WorkBuddy·QClaw …）——Vermes 作调度中枢（orchestrator），不重写它们。此范式**已被真源验证**：`agent/transports/codex_app_server.py` 驱动 `codex app-server` 子进程 + `agent/transports/hermes_tools_mcp_server.py` 把 Vermes 工具集作为 stdio MCP 暴露给 Codex。故 ① A2A 须显式含 **transport adapter registry**；③ Bot Mode P1 的 `agent_profiles` 表须自带 `provider`/`model`/`skill_set` 三列。（此愿景的产品级综合见 §10 Bot 实验室与 Agent 一等公民——含 agent 一等公民缺口、两层底座、三股流动维度跃迁。）
5. **🔴 品牌定位：Hermes = 万神殿，Vermes = 神魔堂（2026-09-02 定名，本轮升级的对外主轴）。** 上游 Hermes v0.21.0 代号 **Pantheon（万神殿）**——供奉的是**同源之神**：bot 共用同一 `AIAgent`、同一 model 后端、同一工具集，只差 persona。Vermes 对应的意象是 **神魔堂**——**任意 AI agent 皆可登堂，只要用户设备上有**：Codex / Claude Code / OpenClaw / 腾讯 WorkBuddy·QClaw / 豆包 / 扣子 / 百度搭子…… 无论 Vermes-native 还是经 ① 的 A2A transport adapter 接入的外部 agent，一律作为**一等公民 bot** 对待，Vermes 只作调度中枢（orchestrator），不重写它们。
   - 这**不是修辞差异，而是架构差异的产品化表达**：万神殿 = 同质化多实例（尝鲜），神魔堂 = 异构 Agent 联邦（生产）。
   - 落点即 **⑭ Bot 实验室（§10）**：它既是 agent "登堂"的入口（发现 → 接入 → 编排 → 监控），也是"神魔共堂"的运行容器。

---

## 1. 取长总览（Tier + 依赖 + 本地底座 + 估算改动量）

| # | Tier | 取长项 | 目标版本 | 依赖 | 本地可复用底座 | 估算改动量 |
|---|---|---|---|---|---|---|
| ① | **Tier1** | **A2A v1.0 协议**（agent 寻址 + 消息信封 + 传输抽象 + **transport adapter registry（外部 agent 接入）** + 生命周期） | v2.5.0 | —（地基） | `tools/delegate_tool.py` + `tools/mcp_tool.py` + `mcp_serve.py` + `agent/transports/{codex_app_server,hermes_tools_mcp_server}.py`（外部 agent 现成范式） | **~250–350 行**（含 AgentRegistry 持久化 + 注册/下线/心跳 + transport adapter registry） |
| ② | **Tier1** | **Grounded Citations / 事实核查** | v2.5.0 | — | `scholarforge/` 三件套（matcher 289 + provider + graph 102，**~580 行现成底座**） | ~100–150 行封装 + 测试 |
| ③ | **Tier1** | **Bot Mode Phase 1**（桌面单房间多 agent + @mention，含**异构 LLM/技能**） | v2.5.0 | ① | 见 `Vermes-botmode-impl-spec.md`（`_agent_cache` 1:1 复用 + `model_overrides` 按 session 覆盖） | 前端 ~3d + 后端 ~3–4d（含 RoomIdNormalizer + agent_profiles 异构三列） |
| ④ | **Tier2** | **Cron monitor-mode**（记忆 + hash 短路 + notepad） | v2.5.0 并行先落 | — | 见 `Vermes-cron-monitor-mode-spec.md`（`cron/scheduler.py:1622`，**硬编码** `skip_memory=True`） | **~200–250 行** |
| ⑤ | **Tier2** | **MCP 指挥中心统一页**（↑ 与 Plugin SDK 对调） | v2.6.0 | — | `vermes_cli/blueprints/mcp_catalog.py:137-161`（`/api/mcp/catalog` + `/install` 后端已就位） | ~300 行前端可视化 |
| ⑥a | **Tier2** | **【已移除·2026-09-04 渠道战略终局】原 Bot Mode Phase 2a**（国产渠道群聊：飞书/钉钉/企微/微信）——**团队不做**，理由见 §13 vFINAL.4 | v3.0.0 | ①③ | `gateway/platforms/weixin.py:362`（已提 `room_id`/`is_group`） | ~3–5d |
| ⑥b | **Tier2** | **【已移除·2026-09-04 渠道战略终局】原 Bot Mode Phase 2b**（海外渠道：Discord/Telegram/Slack）——**团队不做**，理由见 §13 vFINAL.4 | v3.0.0+ | ①③⑥a | 各渠道 `@mention` 解析自写（SDK 原生优先） | ~3–5d |
| ⑦ | **Tier2** | **hermes peer**（bot 间 DM） | v3.0.0 | ① | messaging 群聊能力（`delegate_tool` 可抽象） | ~1–2d |
| ⑪ | **Tier 1.5 / P0 先决** | **桌面-渠道实时同步（channel_push）** | v2.5.0 | — | `tui_gateway/event_publisher.py`（事件总线思路可复用） | ~150 行 |
| ⑭ | **原生愿景** | **Bot 实验室（发现→接入→编排→监控）+ Agent 一等公民** 🆕 | v2.5.0–v3.0.0 | ①③ | `vermes_cli/blueprints/capabilities.py` + `agent/capability_registry.py`（468 行）+ `vermes_cli/capabilities/registry.py` BrickRegistry + `vermes_cli/adapters/*` | **~430–570 行**（agent 类型 + `_discover_agents()` 80–120 / `LocalAgentScanner` ~150 / 前端 200–300）<br>🆕 **vFINAL.5 已落地「请神收尾」T0–T5**：ACP 客户端泛型化（`AcpAgentTransportBase` + `vermes_cli/a2a/recipes/` 食谱目录 + `POST /api/agents/register-profile` 登堂端点 + 前端「登堂」按钮），**任意 ACP 兼容 agent 现可登堂**；实测 65 绿。详见 §13 vFINAL.5 与 `outputs/vermes-14-acp-generic-client-sprint_20260905.md` |
| ⑮ | **原生愿景** | **文档记忆层（Doc Memory / 自建文档记忆）**——长程任务与长会话的外部知识库 🆕 | v2.5.0+ | ①（可选） | 🔴 **比预期厚得多（vFINAL.3 实跑）**：`agent/project_handoff.py` **通用笔记本表**（读写双向已接 `continuity_facade.py:215-219`，但仅 scholarforge 单域发射）+ `continuity_facade.py` **7 源注入门面** + `plugins/memory/` **8 插件可插拔插槽** + 疲劳桥 + `~/.vermes/` 用户目录 | **~170–260 行**（三条腿 A/B/C，见 §11.3） |
| ⑧ | **Tier3** | **Plugin SDK + quick-entry**（↓ 与 MCP 指挥中心对调，全仓 0 底座） | 后续 | — | Skills Hub + modules market（差"外部开发者 SDK + 随处唤起"） | 300–500 行从零建 |
| ⑨ | **Tier3** | **安全硬化补全**（AGENTS.md / 记忆 / 技能写入审批） | 观望 | — | `slash_confirm` 审批机制（`gateway/run.py:2564` 起）可扩 | — |
| ⑩ | **Tier3** | **Raft / memory_graph / Journey / Learn** | 观望 | — | 上游较新，跟踪即可（`\bRaft\b` 词边界确认无） | — |

> ⑫（跨渠道 room_id 归一化）、⑬（Cron job 间数据传递已知限制）不单列 Tier，分别并入 ③ 显式子项与 ④ 已知限制，见 §5 / §6。
> **⑭⑮ 是 Vermes 原生愿景**（Hermes 无此概念，非"取长"），但依赖 ① A2A 的寻址与传输抽象，故排在 Tier1 之后、Tier3 之前的"原生愿景"区块，与 v2.5.0–v3.0.0 同期推进。二者是「神魔堂」（§0 第 5 条）品牌的**两个落点**：**⑭ 让 agent 能登堂，⑮ 让堂内经验可沉淀、可回证**（详见 §10 / §11）。

---

## 2. 依赖图（为什么 A2A 先）

```
① A2A 协议（寻址 + 信封 + 传输抽象 + transport adapter registry（CLI/subprocess · MCP · HTTP-API） + 生命周期）
   - 外部 agent 当 bot：Codex/Claude Code（subprocess/stdio-MCP）、OpenClaw/扣子/WorkBuddy（MCP）、豆包/百度搭子（HTTP-API）经 adapter 接入，Vermes 作 orchestrator
   ├── ③ Bot Mode Phase1（房间内 @mention → 按 A2A 寻址到 agent profile 的 session，profile 自带 provider/model/skill_set 实现异构）
   │      └── ⑥a/⑥b Bot Mode Phase2（跨渠道群聊，复用 ① 寻址 + 渠道 room_id）
   └── ⑦ hermes peer（bot 间 DM = A2A 的点对点特例）
⑩ Raft 子代理网（若取，也建在 ① 之上）
② Grounded Citations 与 ④ Cron monitor-mode 互相独立，可并行插队。
```

---

## 3. 落地顺序（开工节奏，先易后难 / 依赖合规 / 纯增量）

> 这是**开工节奏**，不等于依赖图。先把便宜、零回归、独立的项做完热身，再铺 A2A 地基，再建依赖它的应用层。

1. **④ Cron monitor-mode** —— 最小、零回归、独立项，先热身（~200–250 行）。
2. **② Grounded Citations** —— 热身（底座 3 文件 ~580 行，封装仅 ~100–150 行，学术增益最高）。
3. **⑮ 文档记忆层**（🆕 原生愿景·**并行热身**）—— **三条腿 ~170–260 行**（A 清僵尸 / B 通用发射桥 / C 索引插件，见 §11.3），不依赖 ①。**刻意提前**：它是"边推进边沉淀"的元能力，越早落地，后面 ① ③ ⑭ 的推进过程越可回证、越不易丢失阶段结论（详见 §11）。可与 ②④ 并行插队。**ROI 提示（vFINAL.3）**：腿 B 单独先做也成立——`project_handoff` 读侧零改动，纯加发射端即通。
4. **① A2A v1.0 协议（含 transport adapter registry）** —— 地基（~250–350 行 + agent 生命周期），必须在 ③ ⑥ ⑦ ⑭ 之前。
5. **⑪ 桌面-渠道实时同步（channel_push）** —— 须在 ③ Bot Mode 群聊前解决的 P0 先决条件（~150 行 SSE）。
6. **③ Bot Mode Phase 1（含异构 LLM/技能 + RoomIdNormalizer）** —— 依赖 ①。
7. **⑭ Bot 实验室 + Agent 一等公民**（🆕 原生愿景）—— 依赖 ①③（`BrickRegistry` 扩 agent 类型 + `LocalAgentScanner` + 前端动态渲染，~430–570 行）。**「神魔堂」的主落点**。
8. **Steer 委托编排补全**（增量非新能力）—— JSON-schema 校验 / 成本回显。
9. **⑤ MCP 指挥中心统一页**（与 Plugin SDK 对调后上移，~300 行前端，有 `mcp_catalog` 底座）。
10. **⑥a Bot Mode Phase 2a**（国产渠道）→ **⑥b 2b**（海外渠道）。
11. **⑦ hermes peer**。
12. **⑧ Plugin SDK + quick-entry**（下移，全仓 0 底座，300–500 行从零）。
13. **⑨ 安全硬化补全**（观望，AGENTS.md / 记忆 / 技能写入审批，扩 `slash_confirm`）。
14. **⑩ Raft / memory_graph / Journey / Learn**（观望）。

---

## 4. 三项基准原则（用户审阅已认同）

1. **纯增量、可复用本地底座** —— 不碰既有链路；新增功能以不影响现有链路的方式集成。
2. **否定性结论三方必搜 + 实跑** —— 不建立在截断 / 子串污染 / dist 镜像 / Glob 误报上（本轮再纠 2 处，见 §9）。
3. **真源只读** —— 锚点只定位，改动由用户 commit/push。

---

## 5. Tier1 明细（v2.5.0）

### ① A2A v1.0 协议（P0 地基）
- **是什么**：异构多智能体互操作的"电线协议"——统一 agent 标识（URI/handle）、消息信封（sender/recipient/type/payload）、传输抽象（本地进程内 / MCP / HTTP）、agent 生命周期（注册/下线/心跳）。
- **取长动作**（7 步，估算 **~250–350 行 / 6–10 天**，用户审计纠偏：原报告漏估 ④⑤ 导致偏乐观，本轮再补 transport adapter registry；**第 7 步是三股流动的协议依托，被 §10.6 引用**）：
  1. `AgentHandle`（`vermes://<profile_id>` 或 `@<name>`）+ 解析器（复用 @mention 正则）。
  2. 消息信封 dataclass（`from_handle`, `to_handle`, `room_id`, `kind`, `payload`）。
  3. 传输层：本地用 `tools/delegate_tool.py` 的 subagent 委派原语；跨进程用 `tools/mcp_tool.py` + `mcp_serve.py` 的 MCP 通道。（纠偏：`hermes_tools_mcp_server.py` 存在，见 §9 #1，但它是"外部 agent 适配器"实例，非通用 A2A 桥；A2A 协议本体底座仍是 `mcp_serve.py` + `delegate_tool.py`。）
  4. **`AgentRegistry`（注册 / 发现 / 路由表）**——持久化到 `SessionDB` 或新表。
  5. **agent 生命周期（注册 / 下线 / 心跳）**——否则 @mention 路由到已销毁的 agent 会**静默丢消息**。
  6. **🔴 transport adapter registry（用户 2026-09-02 深化；DeepSeek 审阅强化为「可插拔传输适配器架构」）**——把"外部 agent 接入"从一次性代码抽象成**插槽化 registry**，且**直接镜像 Vermes 已有的 `agent/transports` 可插拔范式**（见下「本地底座升级」）：
     - **CLI/subprocess**（Codex / Claude Code）：复用 `codex_app_server.py` 的 spawn 子进程 + JSON-RPC 范式；
     - **MCP**（OpenClaw / 扣子 / 腾讯 WorkBuddy·QClaw）：复用 `mcp_tool.py` + `hermes_tools_mcp_server.py` 的 stdio-MCP 范式；
     - **HTTP-API**（豆包 / 百度搭子 / 大厂 SaaS）：复用 `outbound_webhook`（`gateway/platforms/webhook.py:14`）+ 204 providers 注册表做鉴权与路由；
     - **WS/SSE**（预留，实时双向）：先留接口，外部 agent 接满后再补具体实现。
     - 新增外部 agent 只需挂一个 adapter 工厂注册进 registry，**不再各写一套传输**（这正是你最担心的返工点）。
  7. **🔴 A2A 消息类型原生承载「三股流动」语义**（§10.6 引用的即本条；也是 ⑮ 文档记忆的交换依托）——信封 `kind` 字段除常规 `message` / `task` / `result` 外，**原生支持 `skill` / `tool` / `knowledge` 三类交换**：
     - `skill`：agent A 把学到的技能（或技能包引用）共享 / 传播给 B；
     - `tool`：工具 / MCP 能力的借用与归属传播（谁贡献、谁在用）；
     - `knowledge`：经验、反模式、`@decision` / `@preference` 约束的跨 agent 流动（含 ⑮ 文档记忆层沉淀的条目）。
     - 这样"相互协作、相互教育、相互进步"**不是产品口号，而是协议的一等语义**——多 agent 网络飞轮（§10.6）才有协议层依托。初版只落 `kind` 枚举与 payload 约定，不做复杂路由。
  - **🔴 本地底座升级（DeepSeek 审阅实跑印证，本轮关键发现）**：A2A 传输层**不必从零设计**——Vermes 在 LLM 提供方层已有一套完整的「可插拔传输适配器」范本，A2A 直接镜像其 ABC + registry 模式即可：
    - `agent/transports/base.py`：`ProviderTransport(ABC)`——抽象 `api_mode` / `convert_messages` / `convert_tools` / `build_kwargs` / `normalize_response` + 可选 `validate_response` / `extract_cache_stats` / `map_finish_reason`；
    - `agent/transports/__init__.py`：`_REGISTRY` 字典 + `register_transport()` / `get_transport()` / `_discover_transports()`（**导入即自动注册**，discovery 列表含 `anthropic` / `codex` / `chat_completions` / `bedrock` 4 类 concrete transport）；
    - A2A 应新建 `agent/a2a/transports.py`：`AgentTransport(ABC)`（`send(envelope)` / `recv()`）+ 平行 `_A2A_REGISTRY` + `register_a2a_transport()`，复用同一「ABC + registry + 自动发现」idiom。A2A 传输层与既有 provider 传输层共享同一工程语言，降低评审与维护成本。
  - **🔴 同源多实例 vs 异构 Agent 联邦（DeepSeek 审阅点破的隐含前提）**：当前 ③ Bot Mode 讨论的是「同源 agent 多实例」，你真正要的是「N 个完全不同 AI 系统经统一协议互联」。差异下表，A2A v1.0 的设计须以右列（异构联邦）为靶心：

    | 维度 | Bot Mode 同源多实例（尝鲜） | 异构 Agent 联邦（生产，你描述的场景） |
    |---|---|---|
    | agent 身份 | 同源 `AIAgent` profile 多实例 | 异构系统各自独立运行（Codex / Claude Code / OpenClaw / 扣子 / 豆包 / 百度搭子 / 腾讯 WorkBuddy…） |
    | LLM/能力 | 共用 One-API 模型池 | 各用各的（Claude / GPT / 豆包 / 混元），经 `model_overrides` 按 session 覆盖 |
    | 传输 | 进程内 / 本地 MCP | 跨网络 / 跨厂商：subprocess · MCP · HTTP-API · WS/SSE |
    | 任务语义 | @mention 路由消息 | 任务委派 + 结果回收 + **能力发现** |
    | 状态管理 | Vermes 内部 session | 各系统自管，Vermes **聚合**（不依赖外部记忆） |

  - **🔴 两个 A2A 设计决策（DeepSeek 审阅提出，采纳）**：
    - **透传 vs 翻译**：Vermes 的 `ProviderTransport` 已同时 embody 两者——`map_finish_reason` 默认**透传**（raw 原样返回），`normalize_response` 做**翻译**（provider 原生 → 统一 `NormalizedResponse`）。A2A 信封层同理：轻量模式仅转发不改语义（无调度），生产模式由 Vermes 理解各 bot 能力做智能路由（才是「超级生产场景」）。初版做透传，联邦成熟后进阶翻译。
    - **能力发现（capability discovery）**：每个 transport adapter 声明能力标签（`code` / `search` / `writing` / `vision` / …），群聊调度时按标签匹配派活（"写代码"→@codex，"搜资料"→@扣子）。此标签表应作为 `AgentHandle` 的元数据字段，随注册写入 `AgentRegistry`。
- **🔴 外部 agent 当 bot（异构融合终态，用户 2026-09-02 深化）**：
  - 现 Bot Mode 的 bot 共用同一 `AIAgent`/同一 model 后端/同一工具集，只差 persona——是"尝鲜"，非生产分工。生产需**异构**：每个 bot 接不同 LLM（`model_overrides` 已按 `session_key` 覆盖 provider/model，`run.py:3272`）+ 挂不同 skill 包（Skills Hub）。
  - 更进一层：bot 不必是 Vermes-native，可经 adapter 接入**外部 agent**——Codex / Claude Code（CLI→subprocess/stdio-MCP）、OpenClaw / 扣子 / 腾讯 WorkBuddy·QClaw（暴露 MCP）、豆包 / 百度搭子（HTTP-API）。Vermes 作调度中枢，不重写它们：群主 `@bot` 派活 → bot 用自身底层干活 → 回群。这正是"乐高底座 / 大融合平台"的落地形态。
  - **范式已现成（真源实跑确认）**：`agent/transports/codex_app_server.py`（docstring：`codex app-server` JSON-RPC 客户端，spawn `codex app-server` 子进程、initialize 握手、驱动 thread/turn）+ `agent/transports/hermes_tools_mcp_server.py`（docstring：把 Vermes 工具集 web_search/browser_*/vision/image_generate/skill_view/... 作为 stdio MCP 暴露给 codex_app_server runtime 的子进程）。**Codex 即是"外部 agent bot"的端到端验证**——证明这条路不仅可行，且 wire-level 实现已存在。
- **不取**：Hermes 的 Raft 共识层（⑩）先观望；A2A 只做"能寻址 + 能发消息 + 能探活 + 能挂 adapter"，共识后置。

### ② Grounded Citations / 事实核查
- **是什么**：每条 claim 可溯源、引用命中真实页面文本（而非模型编造）。
- **底座**（比原报告更厚，用户审计确认 3 文件 ~580 行现成）：`vermes_cli/scholarforge/`
  - `citation_matcher.py`（**289 行**，6 步管线：粗排→精排→阈值→去重→连续编号）；
  - `citation_provider.py`（引用来源提供，含 Semantic Scholar 免费源）；
  - `citation_graph.py`（**102 行**，基于 Semantic Scholar 一跳引文网络，落 `citation_graph_cache` 表 **30 天 TTL**）。
- **取长动作**：抽一层通用 `grounded_citation` 工具集（claim↔来源匹配），让任意 agent 在产出事实性陈述时自动挂溯源锚点，直接加固 ScholarForge 学术可信度，几乎零从零成本。

### ③ Bot Mode Phase 1（详规格见 `Vermes-botmode-impl-spec.md`）
- **是什么**：桌面内单房间多 agent 群聊，`@mention` 路由到对应 agent profile 的独立 session。
- **关键架构**：房间是"会话派生层"，**不动 `_agent_cache` 1:1**（`gateway/run.py:1441` + `vermes_cli/blueprints/agent_cache.py:107`）。`(room, @mention→agent)` → 派生 `session_id` → 复用现有缓存/LRU/TTL（用户审计确认派生 key 兼容 `pop_for_session` 的 `endswith` 匹配）。
- **🔴 异构 LLM/技能是一等公民（用户 2026-09-02 深化，将"尝鲜"变"生产"的分水岭）**：`agent_profiles` 表须自带 **`provider` / `model` / `skill_set`** 三列（非仅 persona/name/avatar）。每个 bot 经 `@mention` 路由到自己的派生 `session_id` 后，复用既有 `model_overrides`（`run.py:3272` 读 `self._session_model_overrides.get(session_key)`）自动挂不同 provider/model；挂不同 skill 包复用 Skills Hub。这样"一个用 Claude 做长文推理、一个用 GPT 做代码、一个用 Qwen/DeepSeek 做中文执行"成为配置项，而非重写。规格 `Vermes-botmode-impl-spec.md` 的 `agent_profiles` schema 需补这三列（并新增外部 agent 类型：若 profile 标记 `transport=adapter`，则走 ① 的 transport adapter registry 而非本地 AIAgent）。
- **⚠️ 显式子项（用户审计要求，原隐含）**：**房间 ID 归一化层 `RoomIdNormalizer` ~100 行**（即 ⑫）
  - `_session_key_for_room(room_id, agent_profile_id)` 是**本轮要新建的 helper**，**非已有函数**（实跑确认：真源仅 `_session_key_for_source(event.source)`，`gateway/session_mixin.py:92`、`run.py:2590/2663`）。它以 `_session_key_for_source` 为基座加 room 维度派生。
  - 跨渠道 `room_id` **非同构**：飞书 `chat_id` ≠ 企微 `chatid` ≠ 微信 `room_id` ≠ 钉钉 `conversationId`；`RoomIdNormalizer` 负责 `channel::room_id` → 统一内部 `room_uid`，管理持久化与 TTL，避免同一物理群被识别为多个房间。
  - 复用已有提取底座：`weixin.py:362-366`（room_id/is_group）、`wecom.py:504`（`chattype` 判群）、`feishu.py`（群消息带 `mentions[]`）。

---

## 6. Tier2 明细（v2.6.0 / v3.0.0）

### ④ Cron monitor-mode（详规格见 `Vermes-cron-monitor-mode-spec.md`）
- **🔴 用户审计修正**：原估 ~110 行**偏乐观**，实测上调 **~200–250 行**。
- **根因**：`cron/scheduler.py:1622` 的 `skip_memory=True` 是**硬编码**（同款散见 `gateway/message_handler_mixin.py:1926`、`session_handlers.py:537`），**非配置项**。须先把 `skip_memory` 改为 config-driven（读 `job["monitor_mode"]`），再加状态表/短路/变化检测。
- **取长动作**：
  1. `cron/jobs.py:509` `create_job` 加 `monitor_mode` 参数 + 构造 AIAgent 时 `skip_memory=not bool(job.get("monitor_mode"))`（替换硬编码）；
  2. 新增 `cron_monitor_state` 表（目标哈希 + 上次输出）+ hash 短路分支（无变化跳过 LLM，省 token）；
  3. 新增 `cron_notepad` 表（持久 notepad）；
  4. 变化检测复用 `context_from` 基线（`scheduler.py:996-1035`，`jobs.py:522/632` 已规范化）——但 `context_from` 是"取最近一次输出"，**无版本锁**（见 ⑬）。
- **净逻辑 ~200–250 行 / 1–2 天**；`monitor_mode` 缺省 False 时与现状**零回归**。仍是 Tier2 中最小、最独立、可立即开做的项。

### ⑤ MCP 指挥中心统一页（↑ 与 Plugin SDK 对调）
- **为何上移**：MCP 指挥中心有现成后端底座，比 Plugin SDK 从零建更划算。
- **已有底座**：`vermes_cli/blueprints/mcp_catalog.py:137-161`——`GET /api/mcp/catalog`（列表 + `installed` 标记）、`GET /api/mcp/catalog/{name}`（详情）、`POST /api/mcp/catalog/install`（含安全校验）；已在 `web_server.py:3024` 注册、`blueprints/__init__.py:23,35` 接入。
- **取长动作**：补 ~300 行前端可视化（目录浏览 / 安装状态 / 调用监控统一页）。

### ⑥ Bot Mode Phase 2（跨渠道群聊）—— 拆 2a / 2b

> **🔴 本项已于 2026-09-04 正式从路线图移除（不是降级，是"不做"）。**
> **决策**：国际国内渠道群聊**都不做**。理由：飞书 aily（2026-03-19 发布、持续升级）已是**云端的神魔堂**且比桌面版更成熟——多 agent 入群/预置上百专业 Agent/智能体队长自动拆任务分发/长期记忆+主动工作/权限与用户本人一致。我们再写飞书 adapter 接群聊，等于与**平台原生 + 权限原生 + 数据原生**的对手正面撞车且毫无优势。
> **Vermes 的护城河不在"能拉几个 agent 入群"**，而在**端侧自控 + 开源可改 + 本地 agent 联邦**——这块云端渠道产品恰恰给不了（数据在云、锁生态、闭源）。
> **用户诉求处置**：若有用户确实要渠道群聊，直接推荐其使用飞书 aily 等平台原生方案，Vermes 不重复造。
> **⚠️ 警惕交叉点**：aily 兼容 OpenClaw 的 skill 生态（skill 经官方安全审核）——渠道方正在吸收开源生态反哺云端，这**强化**而非削弱上述判断。
> 完整调研：见 `outputs/Vermes-channel-strategy-final_20260904.md`。以下原内容**保留存档**（文档契约第 5 条：保留历史说法，不静默覆盖）。

- **用户审计关键纠偏**：原报告"挂 26 渠道基座"过于乐观。审计 37 个 adapter 文件中**仅 15 个原生有 `is_group`/`room_id`**；且各海外渠道 `@mention` 解析逻辑不同，不是"挂上去"就完事。
- **⑥a 国产渠道（有 room_id 底座，优先）**：飞书 / 钉钉 / 企微 / 微信——`weixin.py:362` 已提取 `room_id`/`is_group`，群消息 → room 映射直接复用 `_session_key_for_room`。
- **⑥b 海外渠道（各写 @mention 解析）**：
  - **原则（用户审计风险点#4）**：**优先用 SDK 原生 `mentions` 结构化字段解析，正则仅作兜底**，否则微信/企微等渠道 @mention 会漏解析。
  - **飞书（已有原生解析）**：`feishu.py:357` `FeishuMentionRef` + `feishu.py:1177` `_extract_mention_ids` + `feishu.py:1197` `_build_mentions_map` 已从 `mentions[].id.open_id` 抽出被 @ 的 bot/user（`feishu.py:37-38`），无需正则。
  - **企微（半结构化）**：`wecom.py:504` 用 `chattype` 结构化判群；但 @mention 当前用正则 `re.sub(r"^@\S+\s*")` 仅做路由剥离（`wecom.py:519-523`），未派生 session——2a 需补"@谁 → 哪个 agent profile"映射。
  - **Discord**：`<@user_id>` 格式，从 message 结构化字段取；**Telegram**：`message.entities` 数组（`mention` / `text_mention`）；**Slack**：`@Uxxxx` 或 `<@Uxxxx>`。各渠道在 envelope 解析层写适配。

### ⑦ hermes peer（bot 间 DM）
- = A2A 的点对点特例（bot 间私信），复用 ① 的 `AgentHandle` 寻址 + 生命周期探活。

### ⑪ 桌面-渠道实时同步（**P0 先决条件 / Tier 1.5**）
- **🔴 用户审计风险点#3**：Memory 记录 `channel_push未实现(服务端WS无推送)`——桌面面板无法实时看到渠道消息。Bot Mode 上线后此问题更突出（用户期望桌面看群聊）。
- **现状（实跑确认）**：无 `/api/v1/events` SSE 端点、无 `EventSource` 前端消费者；`tui_gateway/ws.py` 只是桌面控制信道，不推送渠道消息。
- **取长动作**：后端在 `gateway/run.py` 消息处理链路 emit 事件 → 前端 SSE 订阅 `/api/v1/events`（可复用 `tui_gateway/event_publisher.py` 事件总线思路）。
- **估算 ~150 行**。建议**置于 ③ Bot Mode Phase 1 之前**作为先决条件（见 §3 落地顺序第 4 步）。

### ⑫ 跨渠道 room_id 归一化（P1，Bot Mode Phase 1 显式子项）
- 见 §5 ③ 的 `RoomIdNormalizer`：`channel::room_id` → 统一内部 `room_uid`，~100 行。跨渠道 room_id 非同构，须归一化避免同一物理群被识别为多个房间。

### ⑬ Cron job 间数据传递（已知限制，暂不取）
- 现有 `context_from`（`scheduler.py:996-1035` + `cron/jobs.py:509/522/632`）支持"取前序 job 最近一次输出"作上下文，**但无版本锁 / DAG 调度**（A 跑完才跑 B）。若做依赖链需引入简单 DAG 调度，暂不在路线图内，标注为**已知限制**。

---

## 7. Tier3（观望，不急取）—— ⑧ / ⑨ / ⑩

- **⑧ Plugin SDK + quick-entry**（↓ 与 MCP 指挥中心对调）：全仓 `plugin_sdk`/`PluginSDK`/`external_developer`/`developer_sdk`/`sdk_server`/`quick_entry` 均 0 命中，须 300–500 行从零建；ROI 低于有底座的 MCP 指挥中心，排后。
- **⑨ 安全硬化补全**：AGENTS.md / 记忆 / 技能写入审批（已有 `slash_confirm` 机制可扩，`gateway/run.py:2564` 起）。⚠️ **与 ⑮ 相关**：文档记忆层的落盘属"写入"行为，其审批应归入本项范畴（见 §11.5）。
- **⑩ Raft / memory_graph / Journey / Learn**：上游也较新，跟踪即可（`\bRaft\b` 词边界确认无；Journey/Learn 类/函数搜索空）。

> 编号说明：⑨ 与 ⑩ 在早期版本中同列一行（写作"⑨⑩"），2026-09-02 通篇审计时拆分为独立编号，与 §1 总览表、§3 落地顺序第 13/14 步一致。

---

## 8. 已有长板（守 / 强，不重复造轮子）— 现 **14 项**

语音多平台（飞书/钉钉/微信语音笔记）、签名外发 Webhook、上下文压缩/compaction、工具自恢复、MoA、Skills Hub、Artifacts、Steer（`run_agent.py:2070` `def steer` + `session_mixin.py:319`）、**model_overrides（per-session provider/model 覆盖——定义 `run.py:1446` `_session_model_overrides`、读取 `run.py:3272` `self._session_model_overrides.get(session_key)`；异构 bot 的底层机制，今天就有）**、PII 脱敏（`redact.py`）、浏览器自动化（`tools/registry.py:134-137` 10 个 browser_*）、**操作链验证器**、**疲劳桥方案**、**🆕 能力注册表三层底座（Capability Registry）**。

- **操作链验证器**（Vermes 独有·Hermes 无）：`agent/claim_verifier.py:40`「Layer2: 生成工具操作的完整性签名」——三层防护：① 回复内容验证（抗 Agent 编造）；② **工具结果 MD5 签名**（`run_agent.py:2361-2369` `_build_tool_signature`：`hashlib.md5(result_str...).hexdigest()[:8]`）；③ **压缩保护签名**（`run_agent.py:2405`「签名在上下文压缩时被保护，不会被摘要替代」）。
- **疲劳桥方案**（Vermes 独有·Hermes 无）：`agent/conversation_compression.py:1145` `_build_fatigue_bridge_note`——把 `@decision`/`@preference` 等生命周期标记硬约束注入衔接，使关键约束在 `prune_context` 确定性轮删中存活（裁剪取代压缩）。
- **🆕 能力注册表三层底座（第 14 项，本轮补入——用户自纠"能力注册表比想象厚"）**：`agent/capability_registry.py`（**468 行**三层：声明 / 涌现决策 / 自安装）+ `vermes_cli/capabilities/registry.py` BrickRegistry（`BRICK_TYPES` 五态合一，`registry.py:36`）+ `vermes_cli/blueprints/capabilities.py`（`GET /api/v1/capabilities` + `/self-check`，注册于 `web_server.py:3008/428`）+ `vermes_cli/adapters/{bootstrap,discovery,software_adapter}.py`（`discover_l2_adapters()` / `BackendLocator` @ `discovery.py:112` / `SoftwareAdapter` @ `software_adapter.py:129`）。**这是 ⑭ Bot 实验室"扩展而非从零建"的根本原因**（见 §10.3 / §10.4），也是「神魔堂」能广纳异构 agent 的注册表依托。
- 仅 Steer 可增量补"委托编排（JSON-schema 校验 / 成本回显）"——列入 §3 落地顺序**第 8 步**（原第 6 步，因插入 ⑮⑭ 两步而顺延）。

---

## 9. 锚点纠偏记录（实跑更正，已并入正文）

| # | 旧说法（误） | 实跑结论（正） |
|---|---|---|
| 1 | A2A 桥接底座 = `hermes_tools_mcp_server.py` | **再纠偏**：该文件**真实存在**（`agent/transports/hermes_tools_mcp_server.py`），docstring 明示「Vermes-tools-as-MCP server for the codex_app_server runtime」——把 Vermes 工具集作为 stdio MCP 暴露给 Codex 子进程；这正是「外部 agent 当 bot」的现成范式（见 ① 节）。但 A2A **协议本体**底座仍是 `mcp_serve.py` + `tools/delegate_tool.py` / `tools/mcp_tool.py`；`hermes_tools_mcp_server.py` 是"外部 agent 适配器"实例，非通用 A2A 桥。 |
| 2 | Grounded Citations 底座路径 `scholarforge/citation_matcher.py` | 存在，真实路径 `vermes_cli/scholarforge/citation_matcher.py` |
| 3 | `quick_entry` 在 tui hotkeys | 全仓 0 命中，**确属缺失** |
| 4 | `plugin_sdk` 存疑 | `plugin_sdk`/`PluginSDK`/`external_developer`/`developer_sdk`/`sdk_server` 全 0 命中，**确认缺失** |
| 5 | Glob 报 `citation_graph.py` / `mcp_catalog.py` "无文件" | **Glob 误报**；Grep 确认两文件均在（`vermes_cli/scholarforge/citation_graph.py:1`、`vermes_cli/blueprints/mcp_catalog.py`） |
| 6 | `vermes_cli/scheduler.py:1622` | 实为 **`cron/scheduler.py:1622`**（路径错；内容 `skip_memory=True` 确实硬编码存在） |
| 7 | `_session_key_for_room` 为已有 helper | 实为**待建 helper**（真源仅 `_session_key_for_source(event.source)` @ `session_mixin.py:92`、`run.py:2590/2663`） |
| 8 | （新增·外部 agent 接入范式）Codex 经 `codex_app_server.py` + `hermes_tools_mcp_server.py` 已可当 bot 接入 | **实跑确认**：`agent/transports/codex_app_server.py`（spawn `codex app-server` 子进程 JSON-RPC 客户端）+ `agent/transports/hermes_tools_mcp_server.py`（Vermes 工具集 stdio MCP 暴露）均存在。印证 ① 节的「A2A transport adapter registry + 外部 agent 当 bot」不是空想，已有端到端验证路径。 |
| 9 | （上一轮自述）先建 Capability Registry（待建） | **用户自纠：它已存在且比想象厚**。实跑确认：`vermes_cli/blueprints/capabilities.py`（GET /api/v1/capabilities + /self-check，注册于 `web_server.py:3008/428`）、`agent/capability_registry.py`（468 行三层架构：声明/涌现决策/自安装）、`vermes_cli/capabilities/registry.py` BrickRegistry（四态合一）、`vermes_cli/adapters/{bootstrap,discovery,software_adapter}.py`（`discover_l2_adapters` / `BackendLocator` / `SoftwareAdapter`）均已就位。真正缺口是 **agent 非 BrickRegistry 一等公民**（见 §10.3），净增量是"扩展"而非"从零建"。 |
| 10 | DeepSeek「`run_agent.py` 有 `asyncio.gather` 并发心跳 30s 超时」 | **未实跑验证、不实**：`run_agent.py` 仅 kanban heartbeat（2424-2435 行），无此硬编码超时。真实瓶颈是**并发**（内存 + API 配额），非某超时值（已在 §12 记）。 |
| 11 | （本轮通篇审计前）四份文档口径一致、可直接互引 | **❌ 不成立——文档自身也会错**。2026-09-02 通篇审计发现 **14 项一致性问题（P0 级 4 项）**，已全部修复。**P0**：① `Vermes-asset-audit.md` 纠偏#1 仍判 `hermes_tools_mcp_server.py`「不存在」，与本文件 §9 #1「存在」**直接矛盾**；② 本文件 §10.6 曾引「§5 ① 第 7 点」，而 ① 当时只列到 6 步（**悬空引用**）；③ `Vermes-botmode-impl-spec.md` 的 `agent_profiles` 建表 SQL **缺本文件 §5 ③ 要求的 `provider`/`model`/`skill_set` 异构三列**（照此开发会退化成"同源多实例"，而非"异构 Agent 联邦"）；④ ④ Cron 改动量三份文档三套数字（`50–80` / `~95` / `~200–250`）。**其余 10 项见 §13 变更记录。** 教训已固化为「文档契约」第 2 / 4 条（数字口径统一 + 交叉引用禁止悬空）。 |
| 12 | （历史结论）「Vermes 自学习管道没有出口」+ 「反模式记忆 ✅ 可用」 | **❌ 两条都不成立，本轮实跑推翻**。① **管道有出口且已接进 system prompt**：`skill_extractor.py:621-623` 触发 P3 → `emergent_insight.py:648` `build_insight_prompt_block` → `evolution_injector.py:340-341` → `:324` `load_and_format_evolution` → `continuity_facade.py:129-130` → `conversation_loop.py:1193/1212`。② **"反模式"一半是死的**：`anti_patterns` 表是 **zombie**（`evolution_manager.py:360-362` 注释明写 "no writes"），`evolution_injector.py:96` `_load_anti_patterns()` 读它 → 永远返回空（死代码）；活体是 P3 涌现洞察管线。**教训（第三、四次同类错误的延续）**：否定性结论的搜索范围必须覆盖整条链路（不能只看数据源，要一路追到注入点），且"表存在"≠"表在写"。详见 §11.2 发现一、§11.4。 |

> **纪律回响**：否定/锚点结论必须实跑，不能沿用上一轮的"我记得有"；且 **Glob 对嵌套包目录会误报空，凡关键否定/存在结论一律以 Grep 为权威**。本轮再证：连自家上一轮的"纠偏#1 说不存在"都能被新 Grep 推翻——锚点结论应随每次引用重新实跑，而非信任历史批注。

---

## 10. ⑭ Bot 实验室与 Agent 一等公民（Vermes 原生产品愿景 · ① 的落地形态 · 「神魔堂」主落点）

> 本节是 ①③ + 能力注册表底座的**产品级综合**，不是"取长自 Hermes"（Hermes 无此概念），而是 Vermes 借 ① A2A + 现成 capability 底座自然长出的差异化合体。用户 2026-09-02 深度分析，全部锚点经 Grep 实跑确认。
> **品牌落位**：若「神魔堂」是 Vermes 的对外意象（§0 第 5 条），**Bot 实验室就是那座"堂"本身**——"任意 agent 皆可登堂"的入口在此（发现 → 接入 → 编排 → 监控，§10.2/§10.4），堂内三股流动（§10.6）亦在此发生。

### 10.1 先点破本质：发现的是"有自主性的 Agent"，不是"无状态的技能"
| 概念 | 本质 | 有无身份/状态 | 能否接任务 |
|---|---|---|---|
| 技能/积木 | 能力块 | ❌ 无状态 | ❌ 被调用 |
| Agent（Claude Code/豆包/Codex） | 执行实体 | ✅ 有身份+记忆+自己的 LLM | ✅ 接任务+回传 |

"发现 agent"与"发现技能"是两类完全不同的事：积木市场的 GitHub 热门解决"社区有什么能力块"，但发现不了你电脑上装着的 Claude Code——因为它是有自主性的协作伙伴，不是积木。

### 10.2 三层递进（非三选一）：Bot 实验室是顶层聚合
```
┌─ Bot 实验室（顶层聚合）─────────────────────┐
│  发现→接入→编排→监控（全链路）               │
│  ├─ 引用 Agent 管理（已接入实例的状态维护）   │
│  └─ 引用 积木市场（能力块：agent 会用的技能） │
└────────────────────────────────────────────┘
```
- 积木市场 = 能力块目录（技能/工具/模块），agent 接了任务后从这里调能力；
- Agent 管理 = 已接入 agent 的配置/生命周期维护（被动维护，不承担主动发现）；
- **Bot 实验室 = 主动发现 + 接入 + 编排 + 监控的全链路**（发现功能归属此处，因为它之后必然跟着接入→编排→监控）。Bot 实验室**不重造**二者，而是引用——agent 的能力标签、可调用的技能，都从积木市场/Agent 管理里取。

### 10.3 🔴 关键缺口：agent 不是 BrickRegistry 一等公民（已实跑确认）
- `vermes_cli/capabilities/registry.py:36`：`BRICK_TYPES = ("skill", "tool", "module", "software", "provider")`——**无 "agent"**。现有 `software` 类型走 CLI-Anything 路径（FreeCAD/Blender 类工具软件），发现机制是 `shutil.which()` 扫 PATH + macOS app bundle；它发现的是"工具软件"，不是"有自主性的 AI Agent"。
- 两者本质差异在代码层完全没体现：工具软件→被发现→注册为一堆 CLI 工具→被动调用；AI Agent→被发现→有身份/记忆/自己的 LLM→接任务+回传。
- **但 `BrickEntry` 字段已含 `capabilities` / `entry_point` / `source` / `extra`（`registry.py:43-64`）**——加 "agent" 类型只需扩 `BRICK_TYPES` + 增量 `agent_kind`（`cli`/`desktop`/`plugin`/`mcp`/`cloud_api`）/`auth_scheme`（`api_key`/`oauth`/`local_session`）两字段，**复用既有 capabilities/entry_point**。故净增量小（验证下方 10.7 估算）。

### 10.4 🔴 修正落地路径（用户自纠：上一轮"先建 Capability Registry"是错的——它已存在）
- **底座比想象厚（实跑确认）**：能力清单 API `vermes_cli/blueprints/capabilities.py`（GET /api/v1/capabilities + /self-check，注册于 `web_server.py:3008/428`）✅；能力注册表 `agent/capability_registry.py`（468 行，三层架构：声明/涌现决策/自安装）✅；四态合一 `vermes_cli/capabilities/registry.py` BrickRegistry ✅；本地软件发现 `vermes_cli/adapters/bootstrap.py discover_l2_adapters()` ✅；两层发现 `vermes_cli/adapters/discovery.py BackendLocator`（`discovery.py:112`，CLI 二进制 + macOS app bundle 双候选 `locate()` @139）✅；软件薄插槽 `vermes_cli/adapters/software_adapter.py`（`SoftwareAdapter:129`）✅。
- **修正后净增量（非从零建 registry，~400 行 → 仅扩展）**：
  1. `BrickRegistry` 扩展 `"agent"` 类型（`BRICK_TYPES` 加一项）+ `agent` 专属字段（`agent_kind`/`auth_scheme`，复用 `capabilities`/`entry_point`）；
  2. **新增 `LocalAgentScanner`（agent 专项扫描，区别于 CLI-Anything）**：CLI 扫描 `command -v claude/codex/openclaw/hermes/cursor/aider`；配置扫描 `~/.claude.json`/`~/.codex/`/`~/.config/`/`~/.openclaw/`；桌面 App 扫描 `/Applications` 的 AI app（豆包/扣子/元宝/WorkBuddy/QClaw）；IDE 插件扫描 VS Code/Cursor/JetBrains；MCP 扫描 `~/.claude.json` 的 `mcpServers`、Cursor `mcp.json`；**产出 `AgentInventory` 清单**（名称/类型/是否已接入/能力标签/版本路径），喂给 `BrickRegistry._discover_agents()`（置于 `_discover_software()` 旁）；
  3. **Bot 实验室前端读 `/api/v1/capabilities` 动态渲染**（Capability-Driven UI 已有 API 底座）——新增 agent 即 registry 加一条 + 前端自动出 UI，不用改硬编码菜单。

### 10.5 两层底座：Layer A 出厂底座（手脚）vs Layer B 协作伙伴层（外脑）
```
┌─ Layer B · 协作伙伴层（外脑）— AI Agent ───────────┐
│  Claude Code/豆包/Codex/OpenClaw/扣子… 有身份/记忆/自己 LLM │
│  行为：接任务→执行→回传→相互教育                    │
├─ Layer A · 出厂底座（手脚）— 工具软件类积木 ─────────┤
│  FreeCAD/Blender/pandoc/任意 CLI-Anything 无状态/无身份 │
│  行为：被发现→注册为 CLI 工具→被动调用              │
├─ 共享底座（Vermes 护城河）──────────────────────────┤
│  Hermes 引擎 + 记忆/自进化/自学习一体化框架 + 生命周期 + 任务执行飞轮 │
└───────────────────────────────────────────────────┘
```
`BrickRegistry` 的 `software` 类型管 **Layer A**；缺口的 `agent` 类型就是 **Layer B**。真正的跃迁不在这一行类型，而在下方 10.6 的"共享维度"。

### 10.6 🔴 三股流动 = 维度跃迁：从单 agent 飞轮到多 agent 网络飞轮
"相互协作、相互教育、相互进步"拆成三股跨 agent 流动，每股市现有底座 + 缺口：

| 流动 | 已有底座 | 缺口 |
|---|---|---|
| ① 技能互通（skill 流动） | Skills Hub、`skill_utils.py` 发现/解析 | agent 间自动传播（A 学到的→网络里可被 B 订阅） |
| ② 工具互通（tool/MCP 流动） | BrickRegistry 的 tool/module/software + MCP catalog | agent 间共享机制（缺"谁贡献/谁在用"的归属与传播） |
| ③ 经验互通（knowledge/反模式流动） | 反模式库、操作链验证器、疲劳桥（`@decision`/`@preference`） | 缺跨 agent 的**共享记忆层** |

- **单 agent 飞轮（已有）**：用户用→agent 记忆/自进化→agent 更强→用户更愿用。
- **多 agent 网络飞轮（你要的）**：agent A 学到的→沉淀进共享底座→agent B 借用→B 更强→B 的贡献又回流底座（网络里 agent 越多→底座越厚→每个 agent 越强→更多 agent 愿接入）。**这正是"长期粘性"的真正来源**：不是用户离不开 Vermes，而是 agent 离不开这个网络——单独用 Claude Code 是"孤儿"，接进 Vermes 能借力整张技能/工具/经验底座。
- **落到代码：A2A 协议应原生承载共享语义（呼应 §5 ① 第 7 点）**——A2A 消息类型里原生包含 `skill` / `tool` / `knowledge` 的交换。这样"相互教育"不是产品口号，而是协议的一等语义。

### 10.7 工程量估算、依赖与"没有的热门"
- `agent` 类型 + `_discover_agents()` ~80–120 行；`LocalAgentScanner` ~150 行；前端 Capability-Driven UI 消费（agent 卡片/一键接入/编排画布）~200–300 行。**合计 ~430–570 行**，依赖 ① A2A transport adapter（外部 agent 经 adapter 接入后，其 capability 标签直接落入 `BrickRegistry.agent` 条目）。
- **"没有的热门"= Agent 协作伙伴热度榜（新远端 catalog，与积木市场 GitHub 热门并存但数据源不同）**：① 社区接入热度（哪些 agent 被其他 Vermes 用户接入最多，类比 App Store 下载榜）；② 任务评测榜（按场景口碑：代码→Claude Code、搜索→Perplexity、写作→豆包）；③ 官方推荐矩阵（场景×预算）。数据源=社区匿名上报 + 评测 + 官方 curated，非 GitHub stars。
- **前端四层感知（用户拆的四个递进层次）**：① 版本感知（已有：version.json+changelog）；② 能力清单感知（**后端底座已就绪**，缺口在 agent 类型+前端动态渲染+LocalAgentScanner，即本节）；③ 生态感知（积木/agent 市场远端 catalog 定期拉取）；④ 自进化感知（`agent/evolution_manager.py` 已有，按实际使用行为动态调整推荐）。

---

## 11. ⑮ 文档记忆层（Doc Memory / 自建文档记忆）——长程任务与长会话的外部知识库

> **来源**：用户 2026-09-02 元层洞见——「Vermes 有多层记忆，但记忆有**外部知识库 / 文档那一层**；自建文档记忆，也是应对长程任务、持续长会话的技能。**记忆管理，懂得文档落实的本领，也是一个强大的记忆管理艺术。**」
> **定位**：Vermes **原生愿景**（Hermes 无此概念），非"取长"。本节全部锚点于 **2026-09-02 实跑确认**。

### 11.1 问题：记忆层很厚，但缺"文档那一层"

Vermes 现有记忆层次（本轮实跑确认）：

| 层次 | 作用 | 真源锚点 | 抗压缩性 |
|---|---|---|---|
| 会话记忆 | 当前会话上下文 | `vermes_state.py` 的 sessions / messages 表 | ❌ 受 `prune_context` 轮删 |
| 长期记忆（**可插拔**） | 跨会话语义记忆 / profile 召回 | `agent/memory_manager.py` + **`plugins/memory/` 8 个插件**（`supermemory` / `mem0` / `honcho` / `hindsight` / `holographic` / `byterover` / `openviking` / `retaindb`），统一实现 `agent/memory_provider.py` 的 `MemoryProvider(ABC)` | ✅ |
| 记忆召回 | 语义检索 | `agent/memory_recall.py` | ✅ |
| 技能记忆 | 学到的技能 | `vermes_cli/skills_hub.py:22`、`tools/skills_hub.py` | ✅ |
| 决策记忆 | `@decision` / `@preference` 追踪 | `agent/decision_tracker.py` | ✅ |
| 反模式记忆（**错题本**） | 错误模式库 | ⚠️ **半死**（本轮实跑）：**`anti_patterns` 表 = 僵尸表**——`evolution_manager.py:360-362` 注释原文明写 *"zombie table, superseded by P3 EmergentInsightExtractor"*，`:1033-1046` 仅留兼容读、`:1126-1129` count 恒 0，`evolution_injector.py:96` `_load_anti_patterns()` 读的正是它 → 永远返回空（死代码）。**活体是 P3 涌现洞察管线**，出口已通（详见 §11.2 发现一） | ✅（仅活体） |
| 约束记忆 | 硬约束跨压缩存活 | `agent/conversation_compression.py:1145` 疲劳桥 | ✅（受保护） |
| **文档层（❌ 缺口）** | **长程任务的阶段结论 / 决策依据 / 可回证索引** | **无——本轮待建** | **天然免疫（不在上下文里，不参与压缩）** |

**缺口的本质**：上面七层都在"模型够得着的存储"里——或在 prompt 片段、或在 DB 行、或需注入才生效。而**长程任务真正的风险不是"记不住结论"，而是"当时为什么这么决策"丢失**：三个月后回看只剩结论没有依据，于是重新争论一遍，甚至推翻正确决策。

**这正是我们此刻在人类侧做的事**：本文档 + §13 变更记录 + §14 锚点索引 = 一套完整的文档记忆实践。Vermes 缺的是**同一件事的 agent 侧能力**。

### 11.2 🔴 三个关键发现：「错题本」已活、「笔记本」已半通、插件层已备

> 用户原话（2026-09-02）：「**Vermes 有反模式，这其实就是人类学习过程中的错题本**，是存在的。落实『懂得文档落实的本领也是记忆管理艺术』，其实就是**好记性不如烂笔头**！」
>
> 本轮把这句话核到了代码层。结论出人意料：**Vermes 已经有两本了，只是都不叫这个名字，且各有缺陷。**

#### 发现一：「错题本」已活——但旧的那一本已经死了

| 实现 | 状态 | 硬证据 |
|---|---|---|
| `anti_patterns` 表（显式错题本） | 🔴 **僵尸表（zombie）** | `evolution_manager.py:360-362` 注释原文：*"anti_patterns table intentionally NOT seeded — zombie table, superseded by P3 EmergentInsightExtractor … but no writes"*；`:1033-1046` 只保留向后兼容读（try/except 兜底）；`:1126-1129` count 恒 0；`evolution_injector.py:96` `_load_anti_patterns()` 读的正是它 → **永远返回空，实为死代码** |
| **P3 涌现洞察管线**（隐式错题本） | ✅ **活的，且出口已通** | 见下方闭环 |

**错题本的完整闭环（本轮逐段实跑，出口坐实）**：

```
工具成败 / 用户反馈 → raw_events
  （agent/raw_event.py:5 声明分类留给 P2/P3；agent/feedback_learning.py:25 record_user_feedback 写点）
  → P2 聚类      agent/emergent_clusterer.py
  → P3 涌现洞察   agent/emergent_insight.py:108  class EmergentInsightExtractor
                 触发点 agent/skill_extractor.py:621-623（H4.3 技能自进化评测闭环）
  → 提示块       agent/emergent_insight.py:648  build_insight_prompt_block(db_path, max_lines=12)
  → 注入器       agent/evolution_injector.py:340-341（P3 优先，失败才回退 legacy）
                 agent/evolution_injector.py:324  load_and_format_evolution()
  → 门面第 2 源   agent/continuity_facade.py:129-130
  → 最终注入      agent/conversation_loop.py:1193 / :1212
```

> ⚠️ **这条链推翻了一条旧结论**：历史上曾判「自学习管道没有出口」。实跑证明**出口存在且已接进 system prompt**。教训仍是同一条——否定性结论不得建立在截断或收窄的搜索上（见 §9 #12）。

**设计含义**：错题本**不该重建**，而应 ① 复用 P3 管线；② 清理 `anti_patterns` 僵尸表及其读取死代码（`evolution_injector.py:96` `_load_anti_patterns`），否则后人会误以为"错题本在往这张表写"，从而对着空表排查半天。

#### 发现二：「笔记本」已半通——`project_handoff` 就是它，但全仓只有一个域在写字

`agent/project_handoff.py`（259 行）是一套**通用**的阶段结论落盘机制，表与 API 齐备：

- **表**：`project_handoffs`（`:54`），字段 `domain` / `project_id` / `title` / `status` / `progress` / `last_section` / `extra` / `updated_at` / `created_at`，约束 `UNIQUE(domain, project_id)`
- **写入端**：`record_project_handoff()`（`:86`）、`remove_project_handoff()`（`:150`）
- **读取端**：`get_active_handoffs(limit)`（`:171`）、`format_handoffs_prompt()`（`:215`）
- **双向都已接线**：读侧 `continuity_facade.py:215-219`（**第 7 源**）；写侧 `vermes_cli/scholarforge/project_context.py:301/307/377/384`

🔴 **缺口**：该表设计上就是通用的——`continuity_facade.py:212-213` 注释原文：*"any domain (paper/screenplay/novel/shortdrama) can emit via record_project_handoff()"*——但**全仓只有 scholarforge（论文域）一个发射端**。

> **一句话**：Vermes 已经造好了笔记本，却只有一个人往上面写字。⑮ 的主体不是"造本子"，而是**让任意长程任务都能往本子上写**。

#### 发现三：插件层已备——第 9 个 memory plugin 是现成插槽

本轮实跑的另一收获：**Vermes 的记忆层是可插拔架构，且已有 8 个 provider**。

- **接口**：`agent/memory_provider.py` 的 `MemoryProvider(ABC)`，方法齐备：
  - 身份 / 可用性：`name()` / `is_available()` / `initialize(session_id)`
  - 召回：`system_prompt_block()` / `prefetch(query)` / `queue_prefetch()` / `search(query, limit)`
  - **工具暴露**：`get_tool_schemas()` + `handle_tool_call(tool_name, args)`
  - **生命周期钩子**：`on_turn_start()` / `on_session_end(messages)` / `on_session_switch()` / **`on_pre_compress(messages)`** / `on_delegation(task, result)` / `on_memory_write()`
  - 配置 / 备份：`get_config_schema()` / `save_config()` / `backup_paths()`
- **现成参照实现**：`plugins/memory/supermemory/__init__.py`（语义长期记忆 + profile 召回 + 显式 memory 工具 + turn capture + **session-end conversation ingest**）。

**结论**：⑮ 应做成 **`plugins/memory/docmemory/`（第 9 个插件）**，实现 `MemoryProvider` 接口即可——**注册、发现、工具暴露、生命周期挂载、配置、备份全部复用现成机制**，而非另起一套。

**其中两个钩子是该能力的"天作之合"**：
- **`on_pre_compress(messages)`**——压缩前钩子。用户担心的"阶段结论在压缩 / 轮删中丢失"，在此可直接拦截：**每次压缩前把当前阶段结论写入文档**，此后天然免疫裁剪。
- **`on_session_end(messages)`**——会话结束钩子。supermemory 已用它做 conversation ingest；文档记忆用它做"会话结论落盘"。

#### 附：注入门面的 7 个源（`agent/continuity_facade.py`，本轮实跑）

| # | 源 | 行 | 作用 | 对应人类文具 |
|---|---|---|---|---|
| 1 | `handoff` | 122 | 会话交接 | 笔记本（会话级） |
| 2 | `evolution` | 133 | 学习经验 / 涌现洞察 | **错题本** |
| 3 | `recall` | 144 | 记忆召回 | — |
| 4 | `continuity` | 162 | 连续性 | — |
| 5 | `compression_handoff` | 188 | 压缩交割快照（`memory_fabric.recall(tag_filter=["volatile"])`，筛 `source=="compression"` 且 `type=="compression_handoff"`） | 笔记本（压缩级）· **长程任务关键** |
| 6 | `reflection_flags` | 205 | 开放反思标记（矛盾 / 过期信息），来自 `agent/memory_reflection.py` 的 `memory_flags` | 待办本 |
| 7 | `project_handoff` | 219 | 项目阶段结论 | **笔记本（项目级）· ⑮ 主落点** |

> **一张图看懂现状**：Vermes 不缺"笔记本零件"（第 1 / 5 / 7 源都是），缺的是 ① **让任意长程任务都能往本子上写字的通用发射端**；② 把这些本子串起来的**索引层**。这正是 ⑮ 要补的两件事。

### 11.3 落地形态（**三条腿**：清理僵尸 / 通用发射桥 / 第 9 插件 —— 合计 **~170–260 行**）

**先做哪条腿**：A（清死代码，10 分钟、零风险）→ B（接通已有笔记本，ROI 最高）→ C（补索引层，可后置）。

| 腿 | 做什么 | 复用什么（**关键：几乎都有现成的**） | 行数 |
|---|---|---|---|
| **A. 清理僵尸** | 删除 `anti_patterns` 僵尸表的读取死代码：`evolution_injector.py:96` `_load_anti_patterns()` 及其调用点、`:1126-1129` count 分支、`:1189` / `:1272-1273` 状态拼装；或保留但加醒目注释指向 P3 | 无（纯删改） | **~10**（可独立提交） |
| **B. 通用发射桥** | 把 `record_project_handoff()` 从"scholarforge 专用"推广为**任意长程任务可调用**：补 `domain="generic"` 通用发射入口 + 阶段切换 / 会话结束时的自动发射钩子 | `agent/project_handoff.py`（表 + 4 个 API **全现成**）、`continuity_facade.py:215-219`（**读侧已通，零改动**） | **~60–100** |
| **C. 索引层 + 落盘** | `plugins/memory/docmemory/`：跨本索引 + Markdown 落盘 + 四工具 | `MemoryProvider` 全套机制 + 8 个现成插件作参照 | **~100–150** |

**腿 C 的接口映射**（实现 `MemoryProvider`）：

| 接口方法 | 文档记忆的实现 | 行数 |
|---|---|---|
| `name()` / `is_available()` | 返回 `docmemory`；检查 `~/.vermes/docs/` 可写 | ~10 |
| `get_tool_schemas()` + `handle_tool_call()` | 暴露 `doc_write` / `doc_read` / `doc_list` / `doc_search` 四工具（**复用既有工具暴露机制**，无需另接 registry） | ~60–100 |
| `on_pre_compress(messages)` | 压缩前把「阶段结论 + 决策依据 + 未完成项」落成 Markdown | ~30–40 |
| `on_session_end(messages)` | 会话结束落一份会话结论文档（**与腿 B 的自动发射钩子同源**） | ~20–30 |
| `prefetch()` / `search()` | 按 scope / 标签 / 时间检索；召回时**只注入摘要**（省 token） | ~40–60 |
| `backup_paths()` | 把 `~/.vermes/docs/` 纳入既有备份机制 | ~5 |

- **落盘约定**：`~/.vermes/docs/<scope>/<slug>.md`，scope ∈ `{project, agent, task}`；每篇带 front-matter（创建时间 / 来源会话 / 关联锚点 `file:line` / 标签）。
- **与腿 B 的分工**：**B 负责"阶段进度"进 `project_handoffs` 表**（结构化、可被门面第 7 源直接注入）；**C 负责"结论 + 依据 + 索引"进文档**（非结构化、可被人直接读、天然免疫压缩）。两者互补，**不要重复造**。
- **与 ① 的衔接**：文档条目可直接作为 A2A `knowledge` 类型 payload（见 §5 ① 第 7 点）→ 实现**跨 agent 经验流动**，即三股流动的"经验互通"（§10.6）。
- **🔴 工程量口径（诚实版）**：总量 **~170–260 行**，与 vFINAL.2 估的 ~150–250 基本持平，**但构成完全不同**——vFINAL.2 假设"从零造笔记本"，本轮实跑后改为"**接通已有笔记本（B）+ 补索引层（C）+ 清死代码（A）**"。总量未下降是因为：B 的通用发射端是**新增**成本，C 因复用插件机制而**下降**，两者相抵。**这次修正的意义不在行数，而在方向**——不造新本子，先让所有人都能写字。

### 11.4 「好记性不如烂笔头」：三件套闭环（本节是全章的方法论内核）

> 用户原话（2026-09-02）：「懂得文档落实的本领，也是一个强大的记忆管理艺术……其实就是**好记性不如烂笔头**！Vermes 有反模式，这其实就是人类学习过程中的**错题本**，是存在的。」

这句俗语之所以成立，是因为人类的学习从来不是"记住更多"，而是**把记忆外化成可回查的结构**。三件套缺一不可：

| 人类文具 | 解决什么 | 人类侧实践（**本文档正在做**） | Vermes 侧对应（本轮实跑定位） | 现状 |
|---|---|---|---|---|
| **错题本** | 不再犯同一个错 | §9 锚点纠偏表（保留原说法 + 标注"已纠偏"） | `evolution` 源 → P3 涌现洞察管线（`emergent_insight.py:108`→`evolution_injector.py:340`→`conversation_loop.py:1193`） | ✅ **已活**（旧 `anti_patterns` 表已死，见 §11.2 发现一） |
| **笔记本** | 阶段结论不丢、可续接 | §13 Changelog（何时改、为什么改）+ 各节正文 | `project_handoff` 源（`agent/project_handoff.py`）+ `compression_handoff` 源 | ⚠️ **已半通**（仅 scholarforge 单域发射，见 §11.2 发现二） |
| **索引本** | 不靠回忆也能找到 | §14 锚点总索引 + §15 术语表 | **无直接对应** —— 靠各源各自召回，无跨源目录 | ❌ **缺口**（腿 C 补它） |

**三条可操作原则**（agent 与人类通用，且本文档正在践行）：

1. **结论与依据同落**——只记结论 = 下次重新争论；连依据（锚点 / 来源 / 当时的取舍）一起落 = 可回证、可续接。→ 对应 §9 纠偏表"**保留原说法 + 标注已纠偏**"（不删历史错误，才看得清当时为什么这么判）。
2. **变更留痕**——每次修订进 Changelog，能看清"何时改、为什么改"。→ 对应 §13。
3. **索引优于记忆**——不要求记住所有细节，只需维护一份可检索的索引。→ 对应 §14 锚点总索引 + §15 术语表。

**为什么"烂笔头"对 agent 比对人类更要紧**：人类的记忆衰减是渐进的，agent 的记忆是**断崖式**的——一次 `prune_context` 轮删或一次压缩，上下文里的阶段结论就**整段消失**，且模型**不会察觉自己忘了**（没有"我好像忘了什么"的信号）。所以人类可以偷懒不记，agent 不能：**凡是没落到上下文之外的东西，等于从没发生过。**

> 这正是 §11.1 那句"文档层天然免疫压缩"的分量——它不是"多一个存储位置"，而是**唯一一种不会因为上下文操作而消失的记忆形态**。

### 11.5 依赖与排期

- **依赖**：无强依赖（不依赖 ① 即可独立落地）；若要跨 agent 经验流动，则需 ① 的 `knowledge` 消息类型。
- **排期**：§3 落地顺序**第 3 步**（并行热身项）。刻意提前——它是"边推进边沉淀"的元能力，越早落地，① ③ ⑭ 的推进过程越可回证。
- **风险**：低（新插件 + 新目录，不破既有链路）。注意：文档落盘属"写入"行为，应纳入 **⑨ 安全硬化**的审批范畴（对齐记忆 / 技能写入审批思路）。

---

## 12. 已知限制与待决

- **⑬ Cron 依赖链**：`context_from` 无版本锁/DAG，A→B 串行依赖暂不支持（不阻塞本期）。
- **Phase 2b 海外渠道适配**：Discord/Telegram/Slack 各写 @mention 解析，工作量独立于 2a（见 ⑥b）。
- **Raft / memory_graph / Journey / Learn**：上游较新，作为 Tier3 跟踪，不在 v2.5–v3.0 范围。
- **外部 agent 规模上限（用户 2026-09-02 提问）**：bot **档案数**无硬上限（落 DB，可成千上万）；但**同时驻内存的 Vermes-native 活跃实例**受 `_agent_cache(maxsize=20)`（`agent_cache.py:107`，源码本就 20，非本次改动引入）LRU 限制。**外部 agent bot 经 adapter 接入时几乎不占此缓存**（Vermes 只持轻量 `AgentHandle`+transport 连接，LLM 在外部跑），故"拉 50 个外部 bot 进群"资源可行，"拉 50 个 Vermes-native bot 进群"才撞 20 的墙。生产级融合群应设计为 **registry 无限 + native 活跃实例可调 maxsize + 外部 bot 走 connector 不进 cache**——此约束应写入 ① A2A 规格。
- **DeepSeek 审阅的「30s asyncio.gather 心跳超时」断言未实跑验证（见 §9 #10）**：`run_agent.py` 无此硬编码超时，仅 kanban heartbeat（2424-2435 行）。真实瓶颈是**并发**（内存 + API 配额），非某超时值——融合群设计以「外部 bot 走 connector」规避，不依赖超时机制。
- **版本节奏**：v2.5.0 打 Tier1（A2A + Citations + Bot Mode P1）+ 先落 Cron + ⑪ channel_push + ⑮ 文档记忆层；v2.6.0 打 MCP 指挥中心；v3.0.0 打跨渠道群聊 + peer + ⑭ Bot 实验室完整形态；Plugin SDK / Raft 等观望。
- **🔴 发布 / 开源策略（2026-09-02 决策，约束本节全部工作）**：本轮重大升级（尤其 ⑭ Bot 实验室与异构 Agent 联邦）**只本地 `git commit`、暂不 `git push` 远端**。理由：涉及太多重大升级，开源时机待升级完成、发布市场（AppStore）获取真实用户后再定。**执行含义**：开发照常推进，但**不得 push**；真源仍只读，编辑 / 暂存后由用户本地提交。
- **⑮ 文档记忆层的开放问题（待决）**：① **落盘范围与隐私**——文档含决策依据，是否随 ⑨ 安全硬化走写入审批、是否纳入 PII 脱敏（`redact.py`），需定；② **检索注入策略**——召回只注入摘要，但"摘要由谁生成"（规则截断 vs 一次小模型调用）直接影响 token 成本；③ **与 8 个既有 memory plugin 共存**——多 provider 同时启用时的优先级与冲突消解规则，本文档尚未定义，实现前须回真源核 `agent/memory_manager.py` 的多 provider 调度逻辑（**锚点待实跑**）。
- **锚点漂移（文档契约的固有风险）**：本文件所有 `file:line` 以真源 HEAD `d634478778` (v2.4.7) 为准。真源任何提交都可能使行号漂移——**引用前须按「文档契约」第 3 条重新实跑**，批量复核入口为 §14 锚点总索引。

---

## 13. 变更记录（Changelog · 可回证用）

> **用途**：任何时候对本文档某条结论产生疑问，先来这里看"它是什么时候、因为什么被改成这样的"。**保留历史错误，不静默覆盖**（文档契约第 5 条）——这是本文档作为"记忆资料"的核心价值。

| 版本 | 时间 | 变更摘要 | 触发来源 |
|---|---|---|---|
| vDRAFT | 2026-09-02 上午 | 初版路线方案（据两轮上游报告 v0.20.0 Herald / v0.21.0 Pantheon 源码核验） | 上游取长分析 |
| vREVIEW1 | 2026-09-02 | Bot Mode 拆两期；Cron monitor-mode 定为最划算；Phase2 拆 2a/2b；Plugin SDK ↔ MCP 指挥中心对调；长板补操作链验证器 + 疲劳桥（13 项） | 用户第 1 轮审阅 |
| vREVIEW2 | 2026-09-02 | A2A 行数上调 ~250–350（含生命周期）；③ 补 RoomIdNormalizer（⑫）；新增 ⑪ channel_push（P0 先决）；⑥b @mention 改 SDK 原生优先；Cron 上调 ~200–250（硬编码 `skip_memory` 改 config-driven）；新增 ⑬ 已知限制；锚点纠偏 #6 #7 | 用户第 2 轮审阅（Vermes.app 反馈） |
| vFINAL | 2026-09-02 13:35 | 重写为**单一权威决策基准**；编号统一（修 §2/§2.5 瑕疵） | 用户指令"出最终版路线图方案" |
| vFINAL+异构 | 2026-09-02 14:03 | ① 加 transport adapter registry + 外部 agent 当 bot；③ `agent_profiles` 加异构三列；§0 加第 4 条；#1 再纠偏（`hermes_tools_mcp_server.py` 实为**存在**）；新增 #8 | 用户"异构融合平台深化" |
| vFINAL+联邦 | 2026-09-02 14:20 | ① 本地底座升级为镜像 `agent/transports` 的 `ProviderTransport` ABC+registry 范式；采纳 DeepSeek 两设计决策（透传 vs 翻译 / 能力发现）；新增 #9 #10（含 30s 断言不实） | DeepSeek 独立审阅 |
| vFINAL+Bot实验室 | 2026-09-02 14:50 | 新增 §10 Bot 实验室与 Agent 一等公民（10.1–10.7）；§9 加 #9 #10 | 用户深度产品愿景 + 自核真源 6 锚点 |
| **vFINAL.2** | **2026-09-02 15:30** | **通篇审计 + 哲学统一版（本版）**。新增「文档契约」节（6 条元规则）；§0 加第 5 条 **品牌定位：神魔堂**；§1 总览表补 **⑭ / ⑮**、⑨⑩ 拆行；§3 落地顺序扩为 14 步（插入 ⑮ 第 3 步、⑭ 第 7 步）；§5 ① 补齐**第 7 点**（三股流动协议语义，修复 §10.6 悬空引用）；§8 长板 **13 → 14 项**（补能力注册表三层底座）+ model_overrides 双锚点；§9 加 **#11**；**新增 §11 ⑮ 文档记忆层**（含"第 9 个 memory plugin"关键发现）；原 §11 顺延 §12；**新增 §13 / §14 / §15** | 用户指令"全面通篇审计文档，做到统一哲学" |
| **vFINAL.3** | **2026-09-02 16:20** | **「好记性不如烂笔头」实证版**。用户洞察「反模式 = 人类错题本」触发真源深挖，改写 §11：① **§11.1 表**"反模式记忆 ✅"纠正为 **⚠️ 半死**（`anti_patterns` 僵尸表 + 死代码 `_load_anti_patterns`）；② **§11.2 重写为三个关键发现**——**错题本已活**（P3 涌现洞察管线出口坐实，逐段实跑到 `conversation_loop.py:1193`）、**笔记本已半通**（`agent/project_handoff.py` 通用表读写双向已接，但全仓仅 scholarforge 单域发射）、**插件层已备**；新增 **continuity_facade 7 源清单表**；③ **§11.3 落地形态改为「三条腿」**（A 清僵尸 ~10 / B 通用发射桥 ~60–100 / C 索引插件 ~100–150），工程量口径诚实修订为 **~170–260 行**（总量持平但**方向从"造新本子"改为"接通已有本子"**）；④ **§11.4 升级为「错题本 + 笔记本 + 索引本」三件套闭环** + 新增"为什么烂笔头对 agent 更要紧"（agent 记忆是断崖式丢失且**不会察觉自己忘了**）；⑤ §9 加 **#12**（推翻"自学习管道没有出口"+"反模式 ✅ 可用"两条旧结论）；⑥ §1 ⑮ 行、§14、§15 同步 | 用户洞察「Vermes 有反模式 = 错题本；文档落实 = 好记性不如烂笔头」 |
| **vFINAL.4** | **2026-09-04** | **渠道战略终局：⑥a / ⑥b 正式移除（不是降级，是"不做"）**。据飞书 aily 调研：aily 已是**云端神魔堂**且更成熟（多 agent 入群 / 上百预置 Agent / 智能体队长分发 / 权限与本人一致 / 长期记忆 + 主动工作），我们再接渠道群聊属正面撞车且无优势。护城河重述为**端侧自控 + 开源可改 + 本地 agent 联邦**。⚠️ 警惕点：aily 兼容 OpenClaw skill 生态，渠道方正在吸收开源生态。§1 总览表 ⑥a/⑥b 两行标"已移除"、**保留原文存档**；§⑥ 详述段加决策块（原文保留）；新产出 `outputs/Vermes-channel-strategy-final_20260904.md`。**后续焦点收缩为：端侧双平台 / 移动端 / ⑭ 请神收尾 / ⑮ P2 依据** | 用户「QClaw 渠道调研」+ 董董拍板「国际国内渠道都不做」 |
| **vFINAL.5** | **2026-09-05** | **⑭ 请神收尾 B 试水：ACP 客户端泛型化（T0→T5 全落地）**。**起点**：QClaw ACP 底子盘点经独立核验属实——Vermes ACP **Server** 完整（`acp_adapter/`），但 ACP **Client** 只绑 Copilot（`agent/copilot_acp_client.py` 695 行），恰好缺「请神」所需的那半边。本轮把 `CopilotACPClient` 抽为 `AcpAgentTransportBase` 泛型基类 + Copilot 子类（保留 gh-copilot 弃用守卫）；`auxiliary_client.py` 5 处引用按语义分流（**2 处** isinstance 放宽为「任意 ACP transport」= L1206 聊天 / L3224 同步，**1 处**保留 Copilot 专用 = L3854 `resolve_provider_client`，**2 处** provider 别名表与类无关、未动）；`agent_runtime_helpers.py` 新增 `acp-*` 通用路由。新增 `vermes_cli/a2a/recipes/`（**目录即 agent 食谱**：schema + safe YAML loader + 3 条食谱）与 `vermes_cli/a2a/transport.py` 工厂；新增 `POST /api/agents/register-profile`（recipe 匹配→鉴权读取→upsert `agent_profiles`→register `a2a_agents`→健康检查→状态回写）与 `GET /api/agents/recipes`（食谱名单后端下发，前端不硬编码）；前端 `AgentsPage.vue` 「登堂」按钮 + 状态机（idle→loading→success/fail/need_auth）+ 鉴权弹窗 + ACP 兼容 badge。**两条硬约束（董董拍板）**：① isinstance 语义钉死（区分「任意 ACP transport」与「Copilot 专用」）；② Codex / Claude Code 的 entry_point 必须是 **Zed npx 适配器包**（`@agentclientprotocol/codex-acp@1.8.0` / `@agentclientprotocol/claude-agent-acp@0.73.0`），**非裸 CLI**（Codex 本体无 `--acp`；Claude Code 本体是 ACP **client** 而非 server）。**实测 65 绿**（a2a 15 / botmode 19 / copilot-acp 11 / auth-public-guard 8 / benchmark 4），前端 `vite build` 通过（831 modules）。**已知边界（本 sprint 未做，待办）**：① 鉴权弹窗填的 Key 仅注入进程 `os.environ`，Vermes 重启后失效，持久化待接 `recipe.auth.fallback_settings`（`~/.vermes/settings.json`）；② 健康检查为命令可达性探针（`shutil.which`），未做真实 stdio 握手——刻意如此，避免注册时触发 npx 下载适配器包，深度探活留待运行时首调。新产出 `outputs/vermes-14-acp-generic-client-sprint_20260905.md`。**同时修正一处文档不一致**：本文档头部「版本状态」此前停留在 vFINAL.3（vFINAL.4 时漏升），本版一并更正为 vFINAL.5 | 用户「QClaw 报告核验 + 2 个实现细节钉死」+ 董董拍板「SDK 保持 0.9.0 裸 JSON-RPC / recipe A+B 混合 / 粒度 B 试水」+ 总指令「动，按 T0→T5 施工」 |

| **vFINAL.6** | **2026-09-05** | **⑭ B 部分：ACP Registry → recipe 批量 dump（P2 起步器）**。在 T0–T5 泛型化基础上，落实 recipe「A+B 混合」的 B 侧：新增 `generate_from_registry.py` 把 ACP Registry（39 agents）dump 成 38 条 recipe（跳过手写核心 `codex-acp` 避免覆盖 `@1.8.0`）；分发映射 npx(20)/uvx(2)/binary(16)；`loader.py` 加 `recursive` 参数（默认 False 向后兼容 T1 测试），端点 `api_list_agent_recipes` 改 `recursive=True` 把生成食谱与手写 3 条一起端上前端。registry 真源事实核读：无 auth 字段（`authors` 子串误报）、无 capabilities、binary 无 spawn 命令需下载解包。新增 `tests/a2a/test_registry_recipes.py`（5 用例）。回归 a2a 20 / botmode 21 / T0+鉴权守护 23 全绿。提交 `ea43af61cf`（ahead 29，§12 冻结）。**附（2026-09-05 独立审计裁决）：登堂端点 `/api/agents/register-profile` 公开写操作经审计维持现状**——清单内早有 `/api/config`、`/api/provider/add`、`/api/update/apply`、`/api/claim` 等公开写端点，设计哲学为「loopback 绑定信任本机操作者 + session token 防跨站」，非「写操作必须鉴权」；单独收紧此端点 = 补一洞留一堆同类洞，不一致。统一收紧所有写端点留待 ⑨ 隐私硬化一并处理 | 用户「继续未完成任务」（B 部分）+ 审计裁决 |

### 13.1 本轮（vFINAL.2）修复的 14 项一致性问题

| # | 位置 | 问题 | 级别 | 处置 |
|---|---|---|---|---|
| 1 | `asset-audit.md` §0 #1 / ① 缺口 | 仍判 `hermes_tools_mcp_server.py`「不存在」，与主文档 §9 #1 矛盾 | **P0** | 已同步为"存在（适配器实例）" |
| 2 | 本文件 §10.6 | 引「§5 ① 第 7 点」，而 ① 只列到 6 步 | **P0** | 已在 §5 ① 补齐第 7 点 |
| 3 | `botmode-impl-spec.md` §1 | `agent_profiles` 缺 `provider`/`model`/`skill_set` 异构三列 | **P0** | 已补三列 + `transport` 列 |
| 4 | 三份文档 | ④ Cron 改动量三套数字（50–80 / ~95 / ~200–250） | **P0** | 统一 ~200–250，口径换算双向标注 |
| 5 | 本文件 §1 / §3 | ⑭ Bot 实验室未进总览表与落地顺序 | P1 | 已补（§1 行 + §3 第 7 步） |
| 6 | 本文件 §8 / `asset-audit.md` §4 | 长板仍 13 项，能力注册表（第 14 项）未补 | P1 | 两边均补至 14 项 |
| 7 | `asset-audit.md` 结论 #5 | "Steer 落地顺序第 5 步"（主文档为第 6 步） | P1 | 已统一（现第 8 步） |
| 8 | `cron-monitor-mode-spec.md` §4 | "新表靠 `_reconcile_columns()` 加列"——**概念错误** | P1 | 已改：建表走 `SCHEMA_SQL`，**加列**才靠 reconcile |
| 9 | 本文件 §1 表格 | ⑨⑩ 挤一行，⑨ 安全硬化未显式编号 | P2 | 已拆为 ⑨ / ⑩ 两行 |
| 10 | `botmode-impl-spec.md` §5 | 落地顺序与主文档 §3 不一致（Citations 位置） | P2 | 已标注"依赖视角 vs 开工节奏" |
| 11 | `botmode-impl-spec.md` §5 | "A2A 底座（hermes_tools_mcp_server.py 桥接）"与纠偏冲突 | P2 | 已改为正确表述 |
| 12 | `cron-monitor-mode-spec.md` §5 | 汇总表漏 `create_job` 加参与 `_resolve_monitor_target` | P2 | 已补入汇总表 |
| 13 | `asset-audit.md` ④ | "见 §5 ⑬"交叉引用错误（该文件 §5 是结论，⑬ 在 §2） | P2 | 已改指本文件 §6 ⑬ / 该文件 §2 ⑬ |
| 14 | 全部四份 | 无 Changelog / 锚点索引 / 术语表，不可溯源 | P1 | 已新增 §13 / §14 / §15 + 配套文档头部指向 |

---

## 14. 锚点总索引（批量复核入口）

> 真源演进后可沿本表**逐条回扫**。复核方法：对每行的 `file:line` 重新 Grep，确认仍成立；行号漂移则更新本表与正文。

| 锚点 | 用途 | 首次出现 | 复核状态 |
|---|---|---|---|
| `agent/transports/base.py` `ProviderTransport(ABC)` | ① A2A 传输范式参照 | §5 ① | 实跑 ✅ |
| `agent/transports/__init__.py` `_REGISTRY` / `register_transport` / `_discover_transports` | ① registry 自动发现范式 | §5 ① | 实跑 ✅ |
| `agent/transports/codex_app_server.py` + `hermes_tools_mcp_server.py` | ① 外部 agent 端到端验证 | §5 ① / §9 #1 #8 | 实跑 ✅（曾误判不存在，#1 已纠） |
| `gateway/run.py:3272` / `:1446` | ③ model_overrides 读取 / 定义 | §5 ③ / §8 | 实跑 ✅ |
| `cron/scheduler.py:1622` | ④ `skip_memory=True` 硬编码 | §6 ④ | 实跑 ✅（路径曾写错，#6 已纠） |
| `cron/jobs.py:509` | ④ `create_job` | §6 ④ | 实跑 ✅ |
| `vermes_cli/blueprints/mcp_catalog.py:137-161` | ⑤ MCP 指挥中心后端底座 | §6 ⑤ | 实跑 ✅ |
| `agent/claim_verifier.py:40` + `run_agent.py:2361-2369` / `:2405` | ⑧ 操作链验证器（三层防护） | §8 | 实跑 ✅ |
| `agent/conversation_compression.py:1145` | ⑧ 疲劳桥 | §8 | 实跑 ✅ |
| `vermes_cli/capabilities/registry.py:36` `BRICK_TYPES` | ⑭ agent 非一等公民（**缺口**） | §10.3 | 实跑 ✅ |
| `agent/capability_registry.py`（468 行三层） | ⑭ / §8 能力注册表 | §10.4 | 实跑 ✅ |
| `vermes_cli/adapters/discovery.py:112` `BackendLocator` | ⑭ 两层发现（CLI + app bundle） | §10.4 | 实跑 ✅ |
| `vermes_cli/blueprints/capabilities.py`（`web_server.py:3008/428` 注册） | ⑭ 能力清单 API | §10.4 | 实跑 ✅ |
| **`agent/memory_provider.py` `MemoryProvider(ABC)`** | **⑮ 文档记忆插件接口** | §11.2 | **2026-09-02 本轮实跑 ✅** |
| **`plugins/memory/*` 8 个插件** | **⑮ 现成参照实现** | §11.1 | **本轮实跑 ✅** |
| **`agent/memory_manager.py` / `memory_recall.py` / `decision_tracker.py`** | ⑮ 记忆层盘点 | §11.1 | **本轮实跑 ✅** |
| `gateway/platforms/weixin.py:362` / `wecom.py:504` / `feishu.py:357/1177/1197` | ⑥ 渠道 room_id / @mention 原生解析 | §6 ⑥ | 实跑 ✅ |
| `_session_key_for_source` `session_mixin.py:92` / `run.py:2590` / `:2663` | ③ 待建 helper 的基座（#7） | §5 ③ | 实跑 ✅ |
| `vermes_cli/scholarforge/{citation_matcher,citation_provider,citation_graph}.py` | ② Grounded Citations 三件套 ~580 行 | §5 ② | 实跑 ✅ |
| `tui_gateway/event_publisher.py` / `tui_gateway/ws.py` | ⑪ channel_push 缺口 | §6 ⑪ | 实跑 ✅ |
| `gateway/run.py:2564` `slash_confirm` | ⑨ 安全硬化可扩基座 | §7 / §1 ⑨ | 实跑 ✅ |
| `agent/evolution_manager.py:360-362` | ⑮ **`anti_patterns` = 僵尸表**（注释原文 "no writes"） | §11.2 / §9 #12 | **vFINAL.3 实跑 ✅** |
| `agent/evolution_manager.py:1033-1046` / `:1126-1129` / `:1189` / `:1272-1273` | ⑮ 僵尸表兼容读 / count 恒 0 / 状态拼装（**腿 A 待清理**） | §11.2 / §11.3 | **vFINAL.3 实跑 ✅** |
| `agent/evolution_injector.py:96` `_load_anti_patterns()` | ⑮ 错题本**死代码**（读僵尸表，恒返回空） | §11.2 | **vFINAL.3 实跑 ✅** |
| `agent/emergent_insight.py:108` / `:648` | ⑮ 错题本**活体**：`EmergentInsightExtractor` / `build_insight_prompt_block` | §11.2 | **vFINAL.3 实跑 ✅** |
| `agent/skill_extractor.py:621-623` | ⑮ P3 提取触发点（H4.3 评测闭环） | §11.2 | **vFINAL.3 实跑 ✅** |
| `agent/evolution_injector.py:324` / `:340-341` | ⑮ 注入器（P3 优先，失败回退 legacy） | §11.2 | **vFINAL.3 实跑 ✅** |
| `agent/continuity_facade.py:93` + 7 源 `122/133/144/162/188/205/219` | ⑮ 多源注入门面（handoff / evolution / recall / continuity / compression_handoff / reflection_flags / project_handoff） | §11.2 附表 | **vFINAL.3 实跑 ✅** |
| `agent/conversation_loop.py:1193` / `:1212` | ⑮ 门面最终注入点（**学习闭环终点**） | §11.2 | **vFINAL.3 实跑 ✅** |
| `agent/project_handoff.py:54` / `:86` / `:150` / `:171` / `:215` | ⑮ **通用笔记本**：表 + `record_` / `remove_` / `get_active_` / `format_` 四 API | §11.2 发现二 | **vFINAL.3 实跑 ✅** |
| `vermes_cli/scholarforge/project_context.py:301/307/377/384` | ⑮ **唯一发射端（缺口）**——通用表却只有论文域在写 | §11.2 发现二 | **vFINAL.3 实跑 ✅** |
| `agent/feedback_learning.py:25` `record_user_feedback` | ⑮ 错题本写点（点赞/点踩/纠错 → `raw_events`） | §11.2 | **vFINAL.3 实跑 ✅** |

---

## 15. 术语表（统一哲学词汇）

> 同一概念在四份文档中**只用一个词**。新增术语请先来此登记，避免"同义异名"造成后期溯源歧义。

| 术语 | 含义 | 首次出现 |
|---|---|---|
| **神魔堂** | Vermes 品牌意象：**任意 AI agent 皆可登堂，只要用户设备上有**（对 Hermes「万神殿」的差异化定位） | §0 第 5 条 |
| **万神殿（Pantheon）** | 上游 Hermes v0.21.0 代号；本文特指其"同源多实例"bot 形态（共用 `AIAgent`/model/工具集，只差 persona） | §0 第 5 条 |
| **异构 Agent 联邦** | 生产级终态：N 个不同 AI 系统经统一协议互联（**vs** 同源多实例） | §0 第 4 条 |
| **Bot 实验室（⑭）** | 顶层聚合：发现 → 接入 → 编排 → 监控；「神魔堂」的实体承载 | §10 |
| **两层底座** | Layer A 出厂手脚（工具软件积木）/ Layer B 协作伙伴（AI Agent 外脑） | §10.5 |
| **三股流动** | 技能 / 工具 / 经验互通 = 从单 agent 飞轮到**多 agent 网络飞轮**的维度跃迁 | §10.6 |
| **文档记忆层（⑮）** | 长程任务与长会话的外部知识库。落地形态为**三条腿**：A 清僵尸 / B 通用发射桥（`project_handoff`）/ C 索引插件 `plugins/memory/docmemory/` | §11 |
| **错题本** | 反模式记忆的俗称。Vermes 的**活体是 P3 涌现洞察管线**（`emergent_insight.py:108`→`evolution_injector.py:340`→`conversation_loop.py:1193`）；**`anti_patterns` 表是僵尸表**，其读取函数 `_load_anti_patterns` 为死代码 | §11.2 / §11.4 |
| **笔记本** | 阶段结论落盘。Vermes 对应 `continuity_facade` 第 1 / 5 / 7 源（`handoff` / `compression_handoff` / `project_handoff`）。**现状：本子已造好，但仅有 scholarforge 一个域在写** | §11.2 / §11.4 |
| **索引本** | 跨本目录层——不靠回忆也能检索到。**Vermes 目前无对应能力**，由 ⑮ 腿 C 补 | §11.4 |
| **好记性不如烂笔头** | ⑮ 的方法论内核：学习不是"记住更多"，而是**把记忆外化成可回查的结构**。对 agent 比人类更要紧——agent 记忆是**断崖式**丢失，且**不会察觉自己忘了** | §11.4 |
| **僵尸表（zombie table）** | 表结构存在、代码仍在读，但**已无人写入**（恒空）。典型：`anti_patterns`。**危害**：后人误以为数据在写，对着空表排查 | §11.2 / §9 #12 |
| **积木（Brick）** | BrickRegistry 的能力单元，现有 5 类（skill / tool / module / software / provider），**无 agent**——即 ⑭ 的缺口 | §10.3 |
| **transport adapter** | 把外部 agent（Codex / Claude Code / 扣子 / 豆包…）接入 Vermes 的插槽化适配器 | §5 ① |
| **monitor-mode（④）** | Cron 的监控模式：加载记忆 + hash 短路 + notepad | §6 ④ |
| **开工节奏 vs 依赖图** | §3 是"先易后难的施工顺序"，§2 是"技术依赖"——二者**不等价**，引用时勿混 | §2 / §3 |
| **数字口径** | 行数/工期以 §1 总览表为唯一权威；配套子集口径须显式标注换算 | 文档契约 第 2 条 |
