# Vermes 双模式定位 & 神魔堂 UX 升级 — 产品规格（审计定稿 v2 · mimo 手持版）

> 状态：v2（2026-09-26 源码审计定稿；董董已拍板双模式定位与 3 项决议；本报告为交给 **mimo 执行**的手持版，含交叉核验清单 + 执行切片）
> 关联：前序调研 `vermes-take-webui-strengths_20260926.md`、IM 入口 reframing、双模式定位讨论
> 许可证红线：Ekko/Hermes Studio = BSL-1.1（商用禁令至 2029-05-10）；本规格所有"壳"组件必须洁室自研（MIT），只借 Hermes **引擎层**（MIT）接口思路，不搬 GUI 代码。
> **工作重心**：单聊 Vermes 已达主流大厂体验层次 → **优化重心 100% 落在神魔堂**（多 Agent 桌面 IM 入口）；单聊不在本期优化范围。

---

## 0. 产品定位定稿（董董已拍板 · 双模式）

**Vermes = 桌面 Agent OS · 双模式**：一层是「单聊 Vermes」（保持 Hermes 发行版单聊体验），一层是「神魔堂」（多 Agent 桌面 IM 入口）。两者是代码里已存在的两道独立一级入口，本规格把它说清楚并定为正式定位。

### 0.1 双模式映射（代码实证，非新建）
| 模式 | 路由 | 入口 | 心智 |
|---|---|---|---|
| 单聊 Vermes | `/`（`ChatView`） | 落地页（静态 import，`router/index.js:13,16`） | WorkBuddy 式纯净单聊，**已达主流大厂体验** |
| 神魔堂（多 Agent IM） | `/shenmotang` | 侧栏 ⛩️ 图标（`Sidebar.vue:719-721`）+ 命令面板 `page:shenmotang`（`palette-commands.js:7`）+ 欢迎页（`WelcomeGuide.vue:197`） | 微信式多 agent：联系人 → 单聊 → 拉群 → 派活 → 协作 |

- 收编已发生：`/kanban`、`/bot-rooms` 均 `redirect: '/shenmotang'`（`router/index.js:35,45`，注释明写"蜂群协作看板已收编进神魔堂"）。
- "另一番风景"是实证：进神魔堂自动收起全局左栏、离开恢复（`Shenmotang.vue:26-31`）——本规格要把它**反转**为 IM 常驻左栏（见 §5 / §10）。

### 0.2 董董拍板的三项决议（已认同）
1. **对外主定位语**定为"桌面 Agent OS · 双模式"；"发行版"降级为**技术背书词**（README 血缘小节提一句即可），不再当产品主标。
2. **单聊纯净到底**：单聊不加 `@某 agent` 轻联邦入口，重联邦全留给神魔堂——两层心智彻底不混。
3. **不加顶层模式切换器**：现状两个独立入口心智已清晰，加切换器反而模糊。

### 0.3 叙事冲突消解（避免自相矛盾）
不能对外既说"Hermes 发行版"又说"多 agent 桌面入口"。分层口径：
- 技术层（诚实）：引擎 fork 自 Nous Research 的 Hermes Agent（MIT）。
- 产品层（主叙事）：Vermes = 桌面 Agent OS，双模式（轻=单聊 Vermes / 重=神魔堂）。
- "发行版"去向：降级为技术背书词。

### 0.4 诚实 subtlety（别踩）
"单聊保持 Hermes 发行版" ≠ "单聊只能用 Hermes 模型"。单聊 `ChatView` 也接 204/7424 模型注册表（DeepSeek/Kimi/通义都能切）。准确表述：**保持"单聊形态"的纯净体验**，模型源与神魔堂共享。锁模型既不现实也没必要。

---

## 1. 审计后的家底（已验证，直接可复用）

| 能力 | 现状 | 实证锚点 |
|---|---|---|
| 联系人/agent 全量列表 | ✅ | `frontend/src/components/BotRooms.vue`（contacts） |
| 建群 = 建组织 | ✅ | `api.js:695` `applyBotOrg`；`org_engine.py:71` `ORG_TEMPLATES`；`chat.py:4370` `bot_org_templates_get` 端点 |
| 拉群（多选未入群联系人） | ✅ | `BotRooms.vue:131` `inviteModal` + `handleInvite` |
| 1:1 私聊（A2A） | ✅ **且更强** | `chat.py:4282` `bot_room_peer_dm`；**line 4286** 群聊 A@B orchestrator 走同一 `peer_exchange`；**line 4317-4318** 支持跨群联邦（联系人池即可私聊，不限同群） |
| 任务分配 | ✅ | `kanban_db.py` 状态机 + dispatcher（`dispatch_once`）；`assignee` 字段 |
| 多 Agent 接入 | ✅ **44 个 ACP recipe，全 transport:acp** | `vermes_cli/a2a/recipes/`（见 §3 全清单） |
| 协作执行面 | ✅ | `Shenmotang.vue` 蜂群看板（🐝 swarm tab）；`KanbanBoard.vue` |
| 模型注册表（可作 1:1 聊天联系人源） | ✅ | 204 provider / 7424 model（既有），`ChatView` 直连模型 API |
| 用量/成本大盘（IM 壳的透明化底座） | ✅ 已补 | `UsageDashboard.vue` + `/usage` 路由（`router/index.js:27-28`）+ 后端 `analytics.py:182,185` |

**结论**：微信式「聊 → 拉群 → 派活 → 协作」链路的**后端已通**，不是从零建。缺口在「壳」（信息架构），不在后端。

---

## 2. 对本轮及前序判断的纠正清单（重要，避免后人踩坑）

| # | 先前说法（含 mimo / 前轮） | 审计后真相 | 证据 |
|---|---|---|---|
| 2.1 | "多 Agent 接入 ✅ 6+ ACP recipes" | **实际 44 个**，全部 `transport: acp`；其中 38 个由 ACP Registry 自动生成（`generate_from_registry.py` + `registry/`，来源 `cdn.agentclientprotocol.com`），随上游注册表增长而自动扩容 | `vermes_cli/a2a/recipes/` 全量枚举 + 各文件头 `# Auto-generated from ACP Registry` |
| 2.2 | "DeepSeek/Kimi/通义是裸模型 API，进不了 peer_exchange，需另写 API 适配器" | **Kimi（kimi-cli）、Qwen-Code（qwen-code CLI）、Gemini（Google CLI）、GLM（glm-acp-agent）均为 ACP-native agent**，已能走 `peer_exchange` 联邦。需另写适配器的只剩「无 ACP recipe 的裸聊天模型」（如 DeepSeek-V3/R1、豆包）和「网页 agent」（ChatGPT/Grok web），且前者走既有模型注册表即可 | `registry/kimi.yaml:12 transport:acp`、`registry/qwen-code.yaml:12`、`registry/gemini.yaml:12`、`registry/glm-acp-agent.yaml` |
| 2.3 | kanban 状态机 "`dispatched→planning→…`" | 真实枚举无 `dispatched`/`planning`。**真枚举见 §4**；`dispatch` 是动词（dispatcher 把 `ready→running` 提升），`planning` 只是 `kanban_swarm` 的"规划根卡类型"，非状态 | `kanban_db.py:97-98` `VALID_STATUSES` / `VALID_INITIAL_STATUSES` |
| 2.4 | "3 tab（BotRooms/Shenmotang/Kanban）要收编/降级" | **路由层已收编**：`/kanban → /shenmotang`、`/bot-rooms → /shenmotang` 重定向已存在（`router/index.js:35,45`）。神魔堂已是融合容器（hall/roster/swarm 三 tab，静态 import BotRooms/AgentsPage/KanbanBoard）。**真正未做的是把"联系人/群"提升为常驻左栏**——现状是进神魔堂**默认收起左栏**（`Shenmotang.vue:29-31`） | `router/index.js:35,41,45`；`Shenmotang.vue:15,29-31,95-97` |

> 纠错纪律：2.1/2.2 直接推翻了前轮"国产模型需非 ACP 适配器"的 prereq 判断——家底比预判更厚，IM 壳 MVP 的可达性因此显著提高。

---

## 3. 44 个 ACP Recipe 复用清单（全量，按地域/厂商归类）

> 全部 `transport: acp`，零例外。38 个 `registry/*` 由 ACP Registry v1.0.0 自动生成，随上游扩容；6 个顶层为手写核心 recipe（含 Vermes 自维护的 `codex-acp`/`claude-agent-acp`，依赖 Zed npx 适配器）。

### 3.1 顶层手写（6）
| recipe | 厂商/归属 | 备注 |
|---|---|---|
| `hermes` | Nous Research（美，引擎本体） | 同源 |
| `openclaw` | OpenClaw 生态 | 记忆标注为国内友好系 |
| `codebuddy` | 腾讯 CodeBuddy（国内） | 国内 |
| `codex-acp` | OpenAI（美） | 手写核心，经 `@agentclientprotocol/codex-acp@1.8.0` |
| `claude-agent-acp` | Anthropic（美） | 手写核心，经 `@agentclientprotocol/claude-agent-acp` |
| `copilot` | GitHub/Microsoft（美） | |

### 3.2 Registry 自动生成（38）
**国内厂商（明确，4）**：`kimi`（Moonshot AI）、`qwen-code`（阿里 QwenLM）、`glm-acp-agent`（智谱）、`codebuddy-code`（腾讯 CodeBuddy）
**国际厂商（明确，≥8）**：`claude-acp`（Anthropic）、`gemini`（Google）、`github-copilot-cli`（Microsoft）、`pi-acp`（svkozak）、`grok-build`（xAI/Grok）、`cursor`（Anysphere）、`devin`（Cognition）、`goose`（Block）…
**其余（26，按 ACP Registry 上游标注，厂商=上游 agent 自身，未逐一核验地域）**：
`agoragentic-acp` `amp-acp` `antigravity-acp` `auggie` `autohand` `cline` `cortex-code` `corust-agent` `crow-cli` `deepagents` `dimcode` `dirac` `factory-droid` `fast-agent` `harn` `junie` `kilo` `minion-code` `mistral-vibe` `nova` `opencode` `poolside` `qoder` `sigit` `stakpak` `vtcode`

> **缺口提示**：`deepseek`、`doubao/豆包`、`chatgpt-web`、`grok-web` **均无 ACP recipe**。前两者作为「裸聊天模型」走 ChatView 模型注册表（已含），不作为联邦 agent；网页类需 scraper/bridge，超出本期范围（见 §6）。

---

## 4. Kanban 真状态枚举与生命周期（写进方案必须用真名）

**真枚举**（`kanban_db.py:97` `VALID_STATUSES`）：
```
triage · todo · scheduled · ready · running · blocked · review · done · archived
```
**仅可作初始态**（`VALID_INITIAL_STATUSES:98`）：`running` · `blocked`

**生命周期（状态机，非 mimo 口语版）**：
```
triage ──specify──▶ todo ──recompute(父done)──▶ ready ──claim──▶ running ──complete──▶ done ──▶ archived
                  │                                         │  ▲                   │
                  │                                         │  │                   └─block─▶ blocked ──unblock──▶ ready/todo
                  └──unschedule/schedule──▶ scheduled       └──reclaim(stale/crash)─┘
                                              review ◀──worker移入── running ──claim_review──▶ running(reviewer) ──merge──▶ done
```
- `dispatch` 是**动词**（`dispatch_once` 把 `ready` 原子提升为 `running` 并 spawn worker），不是状态。
- `planning` **不是状态**，是 `kanban_swarm` 的"规划根卡类型"。
- 依赖门控：`recompute_ready` 保证父未 `done`/`archived` 时子停在 `todo`，不提前 `ready`。

**对 IM 壳的含义**：L3 任务卡要绑定的是 `task_id`，进度回写读 `status` 字段——必须用上表真名，否则后人按 `dispatched/planning` grep 会扑空。

---

## 5. 3-tab 降级 / 重构映射（现状 → 目标 IA）

现状（`Shenmotang.vue`）：神魔堂 = 融合容器，三 tab：
- `hall` 💬 诸神会晤 = BotRooms（群聊/多 agent）
- `roster` 🔥 神魔架 = AgentsPage（请神/造神/agent 市场）
- `swarm` 🐝 蜂群看板 = KanbanBoard（任务图/心跳回收）
- **默认收起全局左栏**（`Shenmotang.vue:29-31`），单聊需点 ☰ 展开 → 心智仍是「编排台为主、单聊为辅」。

目标 IM 壳（反转心智：IM 为主，编排为次）：

| 现状组件 | 目标角色 | 降级/重构方式 |
|---|---|---|
| 全局左栏（单聊列表） | **提升为 IM 常驻左栏**：联系人 + 群 混合列表，1:1 为默认面 | 进神魔堂**不再收起**，作为一等公民；群与联系人同列，置顶「⊕ 拉群/加联系人」 |
| `hall`（BotRooms） | **右栏会话流本体**（1:1 或群） | 从一个"tab"变为左栏选中项的右栏渲染目标 |
| `roster`（神魔架 / AgentsPage） | **降级为「⊕ 添加」动作**：邀请 agent 进联系人/群 | 不再是与 hall 平级的 tab，而是左栏「添加联系人」入口拉起的面板（复用 `applyBotOrg` + `inviteModal`） |
| `swarm`（蜂群看板 / KanbanBoard） | **降级为房间内二级视图**：点开某群/任务 → 「任务看板」子视图 | 不再顶层 tab；从会话流的「派活」任务卡点开，或房间头「看板」按钮进入 |
| 用量/成本（UsageDashboard） | 左栏底部「用量」入口 or 设置项 | 复用既有 `/usage` |

**关键**：不是删掉编排能力，是把「群聊实验场 + 编排台 + 看板」三种心智**收进 IM 的会话/房间语境里**，让 90% 用户只看到微信式界面，重度用户点进去才有编排深度。

---

## 6. 国产模型适配器 prereq（纠正后的真实缺口）

前轮判断"需另写非 ACP 适配器接 DeepSeek/Kimi/通义"**已被 §2.2 推翻**——国内 agent（Kimi/Qwen-Code/GLM）已 ACP-native 可联邦。纠正后的真实 prereq：

| 缺口 | 性质 | 解法（复用优先） | 优先级 |
|---|---|---|---|
| 6.1 裸聊天模型（DeepSeek-V3/R1、豆包、以及注册表内的 chat-model）作为 1:1 联系人 | **IA 统一层，非新后端** | 把既有模型注册表（204/7424）暴露为「模型联系人」类型，与 ACP agent 联系人共用同一 Contact 抽象；会话走 `ChatView` 既有模型路径 | **P0（MVP 必需，否则联系人列表国内模型是灰的）** |
| 6.2 网页 agent（ChatGPT web / Grok web / Claude web） | 真缺口（scraper/bridge） | 超出本期；标记为 roadmap，不 blocking | P3 |
| 6.3 DeepSeek 作为联邦 agent（而非聊天模型） | 可选 | DeepSeek 无官方 coding agent，建议只作 6.1 模型联系人，不强行造 ACP recipe | P2 / 否决建议 |

> **结论**：prereq 从「写一套 API 适配器」降格为「统一 Contact 抽象（模型 + ACP agent 两类）」——纯前端 IA 工作，后端 0 新增。这与 mimo「纯前端 IA 优先」的判断一致，且比我前轮预估轻得多。

---

## 7. IM 壳分层方案（L1/L2/L3，复用点锚定）

### L1 — 联系人壳（左栏 + 1:1 默认面）
- 左栏：联系人（ACP agent + 模型联系人）/ 群 混合列表，置顶「⊕ 拉群 / 加联系人」。
- 右栏：选中项会话流（1:1 或群），复用 `ChatView` 渲染 + `peer_dm`（`chat.py:4282`）走 A2A。
- 复用：`BotRooms.vue` contacts、`ChatView`、模型注册表、`peer_exchange`。
- 新增：Contact 抽象（区分 `kind: agent|model`）+ 左栏组件。

### L2 — 拉群（1:1 右上「⊕ 拉群」）
- 选人建群 = 现有建群弹窗；复用 `applyBotOrg`（`api.js:695`）+ `ORG_TEMPLATES`（`org_engine.py:71`）+ `inviteModal`（`BotRooms.vue:131`）。
- 纯接线，无新建后端。

### L3 — 任务卡（@某人 + 「派活」→ 消息流内任务卡）
- 消息流里 `@某人 + 派活` → 创建 kanban task（`kanban_db.create_task`），消息携带 `task_id`。
- 点开任务卡看进度：读 `status`（§4 真枚举）+ 实时回写（事件流 `task_events`）。
- 复用：`kanban_db` 状态机 + dispatcher + `assignee`；`Shenmotang` swarm 视图作二级。
- **新增胶水**：消息↔task 绑定层 + 行内任务卡组件 + 进度订阅。

---

## 8. 风险与 tradeoff（不粉饰）

1. **心智反转风险**：把编排台翻成 IM，重度用户（原神魔堂三 tab 重度使用者）会失去一眼看全的编排视图。缓解：编排能力降级为房间内二级视图（§5），不删。
2. **Contact 抽象污染既有数据模型**：`peer_dm` 假设对方是 ACP agent profile；模型联系人走不同 runner。必须明确两类联系人的会话路由分支，避免把模型当 agent 喂给 `peer_exchange`。
3. **ACP Registry 自动生成的版本漂移**：38 个 recipe 随上游变。需在 CI 锁版本 + 人工 review diff，防止上游 recipe 引入不兼容参数。
4. **BSL 红线**：壳必须洁室自研（MIT）。Ekko 的「统一仪表盘/单聊壳」是 BSL-1.1，**只学 UX 范式，不搬代码**。这反与 Vermes 全栈 MIT 卖点一致，是加分项。
5. **联邦深度边界**：`peer_exchange` 已覆盖「同生态 agent 互聊」（§1 已验证跨群联邦）；真正仍 open 的是「跨异构运行时（ACP agent + 模型 API + 网页 agent）共享一份上下文」——不本期承诺。

---

## 9. 落地顺序建议（Sprint 切片）

- **Sprint 1（L1+MVP）**：Contact 抽象 + 左栏常驻 + 1:1 会话（agent 与模型两类联系人）→ 达到「微信式单聊」最小可用。**prereq 6.1 同期完成**。
- **Sprint 2（L2）**：⊕ 拉群接线（复用 `applyBotOrg`/`inviteModal`）。
- **Sprint 3（L3）**：@派活 + 行内任务卡 + 进度回写（绑定 `kanban_db`）。
- **Sprint 4（降级收尾）**：roster/swarm 从顶层 tab 降为二级视图；左栏整合用量入口。
- **Roadmap（非 blocking）**：网页 agent bridge（6.2）、跨异构上下文联邦（8.5）。

> 各 Sprint 遵循项目分支规范：`feat/im-shell-l1` 等，一个改动一个分支，本地提交由董董完成、审计+push 由董董负责。

---

## 10. 神魔堂 UX 优化升级专项（工作重心）

> 前提：单聊 Vermes 已达主流大厂产品体验层次 → **本期优化 100% 落在神魔堂**。神魔堂不是从零建，后端联邦链路已通（§1），本期是「把已有能力用微信式心智重新打包 + 打磨交互」，属**升级而非重建**。

### 10.1 优化目标（用户视角）
点进神魔堂 = 另一番风景：左栏是 Agent 联系人 / 群（常驻），右栏是会话流（1:1 或群），@某人 + 派活即在消息里出任务卡，拉人/拉群一键完成。重度用户点进房间/任务才有编排深度（蜂群看板/心跳回收），轻度用户只看到微信式界面。

### 10.2 具体优化项（复用优先，标注已通后端）
| 项 | 做什么 | 已通后端 / 复用点 | 本期新增（前端为主） |
|---|---|---|---|
| 10.2.1 左栏 IM 化 | 进神魔堂**不再收起左栏**，改为常驻「联系人/群」混合列表，置顶 ⊕ 拉群/加联系人 | `BotRooms.vue` contacts；`Shenmotang.vue:26-31`（反转收起逻辑） | 左栏组件 + Contact 抽象（`kind:agent\|model\|group`） |
| 10.2.2 1:1 默认面打磨 | 选中联系人 → 右栏 1:1 会话；头像/在线状态/消息流打磨 | `chat.py:4282` `bot_room_peer_dm`、`ChatView` | 会话渲染打磨，Agent 头像/状态 |
| 10.2.3 拉群/A2A 流畅度 | 1:1 右上「⊕ 拉群」→ 选人建群；跨群联邦已通 | `applyBotOrg`(`api.js:695`)+`ORG_TEMPLATES`(`org_engine.py:71`)+`inviteModal`(`BotRooms.vue:131`)；`chat.py:4317` 跨群联邦 | 接线 + 建群反馈动效 |
| 10.2.4 任务卡体验 | `@某人 + 派活` → 消息流行内任务卡（带进度），点开看进度 | `kanban_db` 真枚举(§4)+dispatcher+`assignee` | 消息↔task 绑定层 + 行内任务卡组件 + 进度订阅(`task_events`) |
| 10.2.5 联邦可见性 | `@B` 接力、跨群联邦在 UI 上可见（谁在编排、上下文如何携带） | `chat.py:4286`(A@B orchestrator 同 peer_exchange)、`4317`(跨群) | 联邦状态条/上下文指示 |
| 10.2.6 三 tab 归处 | hall→右栏会话流、roster→⊕添加动作、swarm→房间内二级看板（§5 映射） | 已收编路由(`router/index.js:35,45`) | 重组 IA，不删能力 |
| 10.2.7 用量入口 | 神魔堂左栏底部「用量」复用 `/usage` | `UsageDashboard.vue`+`analytics.py:182,185` | 入口接线 |

### 10.3 不做（明确排除）
- **不优化单聊**：已达主流体验，本期不动 `ChatView` 单聊形态（决议 0.2 纯净到底）。
- **不写模型 API 适配器**：神魔堂联系人 = ACP agent roster（44 recipe 全 ACP，§3）+ 群；裸聊天模型（DeepSeek/豆包）留在单聊 `ChatView`，不进神魔堂联邦。后端 0 新增。
- **不加顶层模式切换器**（决议 0.3）。
- **不搬 Ekko 代码**（BSL-1.1 红线，§8.4）。

---

## 11. mimo 交叉核验清单（执行前必过）

> 目的：本报告多处推翻过早期判断（含 mimo 自己的"6+ recipes""dispatched/planning 状态"）。**mimo 执行前必须逐条核验下表，避免基于口播或旧结论动手**。每条给出"正确结论 + 去哪验"，与 §2 呼应。

| # | 核验项 | 正确结论（已审计定稿） | 去哪验 / 命令 |
|---|---|---|---|
| C1 | ACP recipe 数量与传输 | **44 个，全部 `transport: acp`**（顶层 6 手写 + registry 38 自动生成） | `vermes_cli/a2a/recipes/*.yaml` + `registry/*.yaml`；`grep -h '^transport:' ... \| sort \| uniq -c` |
| C2 | 国产 agent 是否可联邦 | Kimi/Qwen-Code/GLM **已是 ACP-native**，可直接 `peer_exchange`；无需另写适配器 | `registry/kimi.yaml:12`、`registry/qwen-code.yaml:12`、`registry/glm-acp-agent.yaml` |
| C3 | kanban 状态枚举 | 真枚举 `triage/todo/scheduled/ready/running/blocked/review/done/archived`；**无 `dispatched`/`planning`**（`dispatch`是动词、`planning`是swarm卡片类型） | `kanban_db.py:97-98` |
| C4 | 3-tab 是否已收编 | **已收编**：`/kanban`、`/bot-rooms` → `/shenmotang` 重定向存在 | `router/index.js:35,45` |
| C5 | 单聊 / 神魔堂是否两道独立入口 | 是：`/`(ChatView) 与 `/shenmotang` 独立一级入口；神魔堂进堂收起左栏(`Shenmotang.vue:26`) | `router/index.js:13,16`；`Sidebar.vue:719-721` |
| C6 | 1:1 私聊后端 | `bot_room_peer_dm`(`chat.py:4282`) + @B 接力(4286) + 跨群联邦(4317) 已通 | `vermes_cli/blueprints/chat.py` |
| C7 | 双模式定位决议 | 董董已拍板：主标=桌面 Agent OS·双模式；单聊纯净不加联邦；不加顶层切换器 | 本报告 §0.1–0.3 |
| C8 | BSL 红线 | Ekko/Hermes Studio=BSL-1.1，壳须洁室自研(MIT)，禁搬代码 | `reports/vermes-take-webui-strengths_20260926.md` + AGENTS.md:60 |

> mimo 若对上表任何一条有异见，先回到锚点复核，再决定是否挑战；挑战成立则同步回本报告，不要静默沿用旧结论。

---

## 12. mimo 执行任务切片（owner: mimo）

> 分支规范：`feat/shenmotang-ux-<项>`；一个改动一个分支；**本地提交由董董完成、审计+push 由董董负责**（mimo 不自行 merge/push）。
> 依赖：T1 是其余各项的 IA 基础；T4 依赖 §4 真枚举；T3 依赖 C4/C6 已通。

| 任务 | 内容 | 复用/锚点 | 验收标准 | 分支 |
|---|---|---|---|---|
| **T1** 左栏 IM 化 | 神魔堂进堂改为常驻「联系人/群」左栏，反转 `Shenmotang.vue:26-31` 收起逻辑 | `BotRooms.vue` contacts；Contact 抽象 | 进神魔堂左栏常驻；联系人/群混列；⊕ 入口可见 | `feat/shenmotang-ux-leftbar` |
| **T2** 1:1 默认面 | 选中联系人→右栏 1:1 会话渲染打磨 | `chat.py:4282` `peer_dm` + `ChatView` | 1:1 消息流、头像、状态正常 | `feat/shenmotang-ux-dm` |
| **T3** ⊕ 拉群 | 1:1 右上拉群接线，选人建群 | `applyBotOrg`+`ORG_TEMPLATES`+`inviteModal` | 拉群成功、成员入群、反馈动效 | `feat/shenmotang-ux-group` |
| **T4** 任务卡 | `@+派活`→行内任务卡+进度回写 | `kanban_db`(§4 真枚举)+`assignee`+`task_events` | 消息带 task_id；进度实时回写 | `feat/shenmotang-ux-taskcard` |
| **T5** 联邦可见性 | @B 接力/跨群联邦 UI 呈现 | `chat.py:4286,4317` | 联邦状态/上下文可视 | `feat/shenmotang-ux-federation` |
| **T6** 定位文案 | 双模式口径 + README 血缘同步 | §0.2/0.3 | README.zh-CN.md 血缘小节更新 | `feat/shenmotang-ux-readme` |
| **T7** 用量入口 | 神魔堂左栏底部接 `/usage` | `UsageDashboard.vue`+`analytics.py` | 入口可达 | `feat/shenmotang-ux-usage` |

### 12.1 明确不做的（mimo 勿碰）
- **单聊 `ChatView` 优化**：已达主流，本期冻结（决议 0.2）。
- **模型 API 适配器**：神魔堂联系人只接 ACP agent，裸模型留单聊。
- **顶层模式切换器**（决议 0.3）。
- **Ekko 代码搬运**（BSL-1.1 红线）。

### 12.2 交付节奏
mimo 按 T1→T7 顺序推进；每完成一项就地提交（董董审），全部完成后统一由董董批审（"全部搞定我再一并审"节奏），不增量 checkpoint。
