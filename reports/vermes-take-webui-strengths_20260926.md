# Vermes（神魔堂）能否取长 WebUI —— 深度调研报告

> 调研日期：2026-09-26 ｜ 对象：EKKOLearnAI 的 Hermes Web UI / Ekko Studio（前身为 Hermes Studio）
> 方法：三方必查（WebFetch 上游仓库 + 本仓源码回盘 + Grep 工具核实），否定性结论一律回盘复核。
> 关联：`vermes-upstream-catchup` 工作流；上一轮已确认 Vermes 的 `upstream = NousResearch/hermes-agent`（MIT）。

---

## 0. 结论先行（Verdict）

1. **能"取长"，但只能取"思路 / UX / 产品战略"，绝对不能搬代码。**
   Ekko Studio 是 **BSL-1.1**，商业使用（含嵌入商业产品、SaaS 托管）须向 EKKOLearnAI 申请商业许可，且 **Change Date = 2029-05-10** 才转 Apache-2.0。Vermes 经 vbit.top 商业分发，现在抄 Ekko 代码即违约。安全路径：只借鉴设计，自己用 MIT 重写。
2. **Vermes 前端并未"水平面落后"。** 实证核实后纠正：渠道配置 UI 不仅存在，且支持 **25+ 平台 > Ekko 的 10 平台**；另在 ScholarForge / 神魔堂 / Bricks / 3D / Benchmark / Market 上有 Ekko **没有**的差异点。
3. **真正值得取长的 Ekko 长板可枚举、且有限**：用量分析大盘、可视化工作流深度、模型治理深度、Web 终端+远程文件浏览器（服务器场景）、语音（可选）。
4. **"取长"本质是洁室（clean-room）重建**，不是 fork 合并——与引擎层（MIT，已 fork）的取用规则完全不同。

---

## 1. 许可证硬闸（决定"能否取长"的根）

Ekko Studio / hermes-web-ui 的 `LICENSE` 原文（raw 直引）：

| 参数 | 值 |
|---|---|
| Licensor | EKKOLearnAI |
| Licensed Work | Ekko Studio（曾用名 Hermes Studio / Hermes Web UI），含 `ekko-studio`、`hermes-web-ui` npm 包与 CLI、桌面/Web 应用、服务端、文档 |
| **Additional Use Grant** | 非商业（个人 / 教育 / 研究）可用；**商业使用（销售、许可、SaaS 托管、嵌入商业产品）须向 Licensor 申请独立商业许可** |
| **Change Date** | **2029-05-10** —— 之后自动转为 Apache License 2.0 |
| Change License | Apache License 2.0 |

**推论（对 Vermes 决策）：**
- ❌ 现在把 Ekko 前端代码并入 Vermes 商业分发 = 违约（商业禁令至 2029-05-10）。
- ✅ 学其 **UX 范式 / 功能设计 / 产品战略** 后，用 Vermes 自有 MIT 代码独立重写 = 合规。
- ✅ 引擎本体 `NousResearch/hermes-agent` = MIT（Vermes 已 fork），那一层可自由取用——但**引擎 ≠ WebUI**，二者许可证不同。

---

## 2. 对象澄清（避免错层对比）

- **Ekko Studio / Hermes Web UI** = Hermes Agent 引擎的**官方控制台**（Vue3 + Koa2 BFF + Socket.IO + node-pty + SQLite），职责是会话/渠道/用量/定时/看板/文件/终端的**可视编排**，本身不做模型聚合（模型聚合在引擎层）。
- **Vermes 对应层** = `vermes-frontend`（Vue3 + Pinia + Vite + Tailwind，MIT） + 引擎（MIT fork）。
- 所以"取长 WebUI"的精确含义：**Vermes 前端（MIT）借鉴 Ekko 前端（BSL-1.1）的 UX / 功能设计**。二者是**同源（Nous Research）的平行产品**：Ekko 走"通用多运行时工作台"，Vermes 走"中文本地化 + 垂直学术 + 神魔堂多 Agent 联邦"。

---

## 3. 差距映射表（已逐一回盘，非臆测）

判定口径：✅ 持平/具备 ｜ ⚠️ Ekko 领先（深度或治理）｜ ❌ 缺失（Vermes 前端层）｜ 🟢 Vermes 领先

| 能力维度 | Ekko WebUI | Vermes 前端现状（证据锚点） | 判定 | 取长可行性 |
|---|---|---|---|---|
| 聊天 / 会话 / Markdown / 工具展开 | ✅ | ✅ `ChatView` `StudioChat` `MessageList` `ArtifactPanel` | 持平 | — |
| 渠道配置（平台接入） | 10 平台 | 🟢 `Settings.vue:1805-2634`（`loadChannels`/`saveChannel`/`api.listGatewayChannels`），**支持 25+ 平台**，含 home-channel 热重载、凭据不回显 | **Vermes 领先** | 不取（已超越） |
| 看板 / 任务 / Todo | ✅ | ✅ `KanbanBoard` `TodoPanel` `TaskFlowCard`（已收编神魔堂） | 持平 | — |
| 可视化工作流 | ✅ + 审批门/证据回放/冻结快照 | ⚠️ `WorkflowsPage.vue`（router 注释：基础 DAG + 触发器） | **Ekko 领先（深度）** | 可取（思路） |
| 模型 / Provider 治理 | ✅ + OAuth 设备流 / 可见性 / 别名 / STT-TTS 目录 | ⚠️ `ProviderCard.vue` + `Settings.vue` 密钥录入（无 OAuth 设备流 UI / 可见性 / 别名） | **Ekko 领先（治理深度）** | 可取（思路） |
| 用量分析大盘 | ✅ token/成本/30 天趋势 | ❌ 前端无专用页（router 无相关路由；源码无 analytics 组件） | **Ekko 领先** | 可取（新建） |
| 群聊 / @mention / 上下文压缩 | ✅ | ✅ `BotRooms` `Shenmotang`（多 Agent 房间） | 持平 / 方向不同 | — |
| 多运行时适配器管理（Claude/Codex/Pi…） | ✅ 安装/启停/监控 + 内置终端/diff | ⚠️ `GenericAcpClient`（Zed npx 适配器，比 per-runtime 更标准化） | 机制不同；Ekko 的"内置终端+diff"体验可取 | 可取（思路） |
| 技能 / 记忆浏览 | ✅ | ✅ `SkillManager` `MemoryBrowser` `MemoryProfile` | 持平 | — |
| MCP 管理 | ✅ | ✅ `MCPManager` `MCPCommandCenter` | 持平 | — |
| 命令面板 Ctrl+K | ✅ 会话搜索 | ✅ `CommandPalette.vue`（范围更广） | 持平 | — |
| Web 终端 + 远程文件浏览器 | ✅ node-pty + Docker/SSH/Singularity | ❌ 无（桌面端有原生 FS，需求弱） | Ekko 领先（服务器场景） | 可选取 |
| 语音 TTS / STT | ✅ 10+8 适配器 | ❌ 无 | Ekko 领先（可选） | 可选取 |
| 桌面 Agent 浏览器（MCP 驱动） | ✅ | ❌ 无（战略不重合） | 不追 | 🚫 红线 |
| 多租户 Multi-Profile / 超级管理员 | ✅ | ❌ 单用户桌面定位 | 不追（定位差异） | 🚫 红线 |
| 主题实时预览 | ✅ | ⚠️ 设置项有，实时预览待确认 | 锦上添花 | 低优先 |
| 日志查看器 | ✅ | ❌ 无 | 低优先 | 可选取 |
| 独立桌面聊天浮窗 | ✅ | ⚠️ 待确认 | 低优先 | 可选取 |

**Vermes 独有（Ekko 无，即差异点 / 卖点）：**
`scholar/*`（ScholarForge 学术垂直）、`Shenmotang` + `BotRooms`（神魔堂多 Agent 联邦）、`BricksPage`（四态合一积木市场）、`ThreeDStudio`、`BenchmarkDashboard`、`MarketCard`/`SoftwareDiscover`/`ExpertCatalog`、`WechatLogin.vue`（微信登录）。

> 否定性结论注明：上表"❌ 用量分析大盘"为**前端层**判定（已查 router 无路由 + 前端无 analytics 组件）；后端是否暴露 usage 端点**待二次确认**（建议 `grep -n "usage\|token" vermes_cli/.../api.py` 复核，不据此下"全无"结论）。
>
> **2026-09-26 二次确认（当场纠错）**：后端**早已就绪** —— `vermes_cli/blueprints/analytics.py`
> 提供 `GET /api/analytics/usage`（daily / by_model / totals / skills）与
> `GET /api/analytics/models`，且已 `register_to(app)`。**T0 缺口只剩前端页**。
> 当场洁室落地 `frontend/src/components/UsageDashboard.vue` + `/usage` 路由 + 命令面板入口。

---

## 4. "取长"路线图（洁室 · 分级 · 纯增量）

| 档位 | 项 | 做法（MIT 自写，非抄） | 优先级 |
|---|---|---|---|
| **T0 必做** | 用量分析大盘 | 新建 `UsageDashboard.vue` + 后端 usage 端点（token 拆分/成本/30 天趋势），数据来自引擎既有用量记录 | 高（商业分发刚需） |
| **T1** | 工作流深度 | 在 `WorkflowsPage` 加审批门（approval gate）+ 节点执行证据回放 + 冻结快照 | 中 |
| **T1** | 模型治理深度 | OAuth 设备流 UI（Codex/Nous/Claude/Copilot）、Provider 可见性开关、别名、STT/TTS 独立目录 | 中 |
| **T2 可选** | Web 终端 + 远程文件浏览器 | 仅当你推 headless / 服务器部署时；node-pty + 后端代理 Docker/SSH | 低（桌面端弱需） |
| **T2 可选** | 语音 TTS/STT | 若做"对话式"入口 | 低 |
| **T3 锦上添花** | 日志查看器 / 独立聊天浮窗 / 主题实时预览 | — | 低 |
| 🚫 红线 | 桌面 Agent 浏览器（MCP 驱动）、多租户 Multi-Profile | 与 Vermes 桌面单用户 + 垂直定位不符，不追 | — |

---

## 5. 神魔堂专项（用户原问落点）

- 神魔堂已采用 **`GenericAcpClient`**（Codex/Claude 经 Zed npx 适配器接入），比 Ekko 的 per-runtime 适配器**更标准化**，不应回退去照搬 Ekko 的适配器清单。
- 可洁室借鉴的 **UX 细节**（仅设计，自写实现）：
  1. 群聊 `@mention` 路由触发 + **上下文压缩阈值可视化调节**；
  2. 每个 Agent **独立浮窗**（脱离主窗）；
  3. 编码 Agent 的 **"内置终端 + 文件 diff"** 视图。

---

## 6. 行动建议

1. **README 落点**（策略文档待办 line 68-70）：在 `README.zh-CN.md` 写明"引擎 MIT fork 自 Nous Research 的 Hermes Agent；GUI 自研 MIT；与 Ekko Studio（BSL-1.1）同源异 GUI，取长仅限设计层面，不搬代码"。
2. **法律防火墙**：任何前端 PR 若疑似借鉴 Ekko，先过 BSL 闸；建议在团队 wiki 记一条"Ekko = BSL-1.1，禁抄代码，仅学设计"。
3. **优先级**：先把 T0 用量大盘做了（商业分发最该有的透明化），再按需推进 T1。

---

## 7. 实证锚点（可复现）

- 许可证原文：`https://raw.githubusercontent.com/EKKOLearnAI/hermes-studio/main/LICENSE`（BSL-1.1，Change Date 2029-05-10）
- Ekko 功能清单：WebFetch `github.com/EKKOLearnAI/hermes-studio` README（多运行时 / 可视化工作流 / 10 平台 / 用量分析 / Web 终端）
- Vermes upstream：`git -C vermes-electron remote -v` → `upstream=NousResearch/hermes-agent.git`（MIT）
- Vermes 前端路由：`frontend/src/router/index.js`（11 条路由，无 /channels、无 analytics 路由）
- Vermes 渠道 UI：`frontend/src/components/Settings.vue:1805-2634`（25+ 平台，`listGatewayChannels`/`saveChannel`）
- Vermes 前端组件清单：`frontend/src/components/`（scholar/、Shenmotang、BricksPage、BenchmarkDashboard、MCPManager…）

## 8. 自检（按 `vermes-upstream-catchup` §7）

- [x] 每条"缺失/领先"结论已用 Grep 工具 + 源码回盘（含一处自我纠错：渠道 UI 原疑缺，核实后确认存在且 25+ 平台）。
- [x] 否定性结论（无用量大盘）明确标注为"前端层"，后端待确认。
- [x] 许可证条款直引 raw LICENSE，非二手。
- [x] 纯调研，未改动任何文件，工作树未动。
