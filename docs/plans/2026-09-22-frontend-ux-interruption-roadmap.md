# 前端交互层 · 打扰治理与任务流优化路线（2026-09-22）

> 与 `reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`（后端/能力主线）**并行的一条独立轨**，不动后端主线范围。
> 取证基线：本机源码仓 `frontend/src/`（Vue 3 + Pinia + Tailwind，v2.5.1），全部结论带文件行号。

---

## 0. 结论先行

| # | 结论 | 证据 |
|---|---|---|
| 1 | **自动弹出挂在了错误的事件上**：现状是「过程产物弹、最终交付反而不弹」，与诉求完全相反 | `chat.js:1276` 弹 / `chat.js:1302` 不弹 |
| 2 | 产物判定是「可渲染」白名单，不是「最终交付物」白名单，`py/js/txt/log` 全在内 | `chat.js:47-61` |
| 3 | 该函数注释声称「避免 .txt 日志」，实现却列出 `txt`/`log`——**注释与实现自相矛盾** | `chat.js:46` vs `chat.js:57-58` |
| 4 | 后端放大问题：`execute_code` 把沙箱内每次 `write_file`/`patch` 都上报为 artifact，不区分中间/最终 | `code_execution_tool.py:1402-1418` |
| 5 | 顶部任务高亮 = `TodoPanel` 常驻聊天区上方 + `in_progress` 蓝底 | `TodoPanel.vue:39,61` |
| 6 | 任务流「不准」有硬伤：进度**父子双计**导致百分比失真，任务名 `truncate` 截断 | `TaskFlowCard.vue:33,36,97` |
| 7 | 存在**第二套**右栏自动开开关，两套状态机易漂移 | `useRightPanel.js:8` vs `useArtifactPanel.js:7` |

**一句话**：修法不是「加个开关少弹点」，而是**把自动弹出从 tool_step 搬到 onDelivery**——代码里已经有正确的原语，只是接反了。

---

## 1. 逐条核实（用户点名的 3 项）

### 1.1 过程产物弹窗 —— ✅ 成立，且根因比表象更深

**链路（端到端闭环）：**

```
execute_code 沙箱内 write_file/patch
   → code_execution_tool.py:1415 全部作为 artifacts 上报（source="execute_code"）
   → chat.js:1276  autoOpen && isRenderableDeliverable(path) 为真
   → openArtifactById() / openPanel('artifacts')   ← 右栏自动弹出
```

- `useArtifactPanel.js:7` — `autoOpen = ref(true)`（默认开）。
- `chat.js:47-61` — `isRenderableDeliverable` 白名单含
  `py/js/ts/sh/json/yaml/yml/toml/ini/cfg/txt/log/xml/sql/java/go/rs/c/cpp/h/rb/php/vue/css/scss/less`。
  这是「能不能渲染」，不是「是不是交付物」。
- `chat.js:46` 注释原文：「避免 `.txt` 日志、临时文件、中间产物一产生就弹面板打扰用户」——
  但 L57-58 恰恰把 `txt`、`log` 列进了白名单。

**架构倒置（最关键的一条）：**

| 事件 | 语义 | 现状行为 | 期望 |
|---|---|---|---|
| `tool_step`（过程产物） | 中间态，后端未过滤 | **自动弹右栏** ❌ | 静默入列表 |
| `onDelivery`（最终交付物） | 后端已过滤，只留交付物 | 只 push 一条 delivery 消息，**不弹面板** | **自动弹** ✅ |

`onDelivery` 见 `chat.js:1302-1357`，L1303 注释明确写着「只保留最终交付物（后端已过滤）」，
但 L1335 仅 `messages.push({type:'delivery'})`，**没有任何 openPanel 调用**。

### 1.2 任务高亮顶部 —— ✅ 成立

- `TodoPanel.vue:39` — `v-if="showTodoPanel && todoItems.length>0"`，挂 `mb-3`，位于消息区**上方**。
- L61 — `in_progress` 项加 `bg-blue-50`（蓝底高亮）；L71 再加「进行中」徽标。
- L57 — `max-h-48` 滚动区，任务多时持续占据顶部。
- 可见性 `chat.js:292` per-session computed，✕ 可关（`TodoPanel.vue:53`），但**有 todos 即默认占位**。
- 附加噪音：`TaskProgress.vue:6` 的 `animate-pulse` 脉冲圆点，长任务期间持续闪烁。

### 1.3 任务流不够优雅准确 —— ✅ 成立，且是硬伤不是观感

`TaskFlowCard.vue`：

| 行 | 问题 | 影响 |
|---|---|---|
| L33 + L36 | `percent = completed/total`，`countNodes` **父子节点双计** | 进度百分比系统性失真（偏高/跳变） |
| L97 | 任务名 `truncate` 截断 | 长任务名不可读 —— 「不准确」主因 |
| L74 | `max-h-64` 滚动区，**无自动滚动/聚焦当前节点** | 看不出「现在在哪一步」 |
| — | 无每步耗时、无时间轴（`utils/harnessTimeline.js` 未接入） | 无法判断卡在哪一步 |
| L98 | 多 agent 仅文字 `agent_role`，无角色色/头像 | 多 agent 协作区分度低 |
| L43 | `pending:'○'` 与 `in_progress:'▶'` 视觉差异小 | 状态扫读成本高 |

---

## 2. 我补充核实的其他打扰点（用户未点名）

| 位置 | 问题 | 档位 |
|---|---|---|
| `App.vue:70-84` | **两条**常驻顶部 `PrereqBanner`（profile 错配 / 崩溃回滚）叠加占两行，每次启动重现 | P1 |
| `App.vue:97-99` | `ApprovalDialog`/`ConfirmDialog`/`UpdateDialog` 全局阻塞弹窗；审批队列连续弹出属强打断 | P2 |
| `useRightPanel.js:8` | 第二套右栏自动开开关 `autoOpenOnArtifact=true`，与 `useArtifactPanel` 并存 → 双状态机易重复弹 | P0 |
| `TaskProgress.vue:95` | 秒针 `setInterval` 每秒触发重渲染 | P2 |
| `chat.js:1276` | 无「每轮弹窗次数上限」，多产物工具连续触发多次弹出 | P2 |

### 2.1 核实后**排除**（防止过度修复）

- **toast 不是主要打扰源**：全仓 `toast(` 仅 29 处（`BotRooms.vue` 28 + `AgentsPage.vue` 1）。
- **`SceneStatusBar` 已是「简洁优先」**：正常态为极细灰 chips（L39），仅告警才出 `PrereqBanner`。**明确不动**。

---

## 3. 优化路线（P0 / P1 / P2）

### P0 · 过程静默、成果才弹（1~2 天，前端热更即可）

| 项 | 动作 | 锚点 |
|---|---|---|
| **P0-1** | **把自动弹出从 `tool_step` 迁移到 `onDelivery`**：移除/旁路 L1276 的自动展开（改为静默入列表 + 未读计数），在 `onDelivery` 末尾补一次自动展开 | `chat.js:1276` → `chat.js:1302` |
| **P0-2** | **收窄 `isRenderableDeliverable`** 为真交付物：`md/htm/html/docx/xlsx/xls/csv/pdf` + 图片；剔除全部过程类扩展；**并修正注释使其与实现一致** | `chat.js:47-61` |
| **P0-3** | 收编 `useRightPanel.autoOpenOnArtifact`，删除或代理到 `useArtifactPanel`，消除双状态机 | `useRightPanel.js:8` |
| **P0-4** | 保留 `autoOpen` 但语义改为「仅最终交付自动展开」，并暴露设置项允许用户彻底关闭 | `useArtifactPanel.js:7` |
| **P0-5（后端）** | artifact 增加 `intermediate` 标记；`execute_code` 产物默认 `intermediate=true` | `code_execution_tool.py:1415` |

> **Trade-off / 落地顺序**：P0-5 属后端改动，**须重打 DMG 才生效**；P0-1/P0-2/P0-3/P0-4 是纯前端，**热更即可**。
> 建议 **P0-1~P0-4 先上（当天可验），P0-5 随下次打包跟进**。
> 扩展名白名单（P0-2）是过渡手段，`intermediate` 标记（P0-5）才是终态；二者不冲突，前者先止血。

> **兜底（2026-09-22 实施时修正，推翻初版方案）**：P0-1 后若某链路不发 `onDelivery`，
> 产物确实不会自动弹面板 —— 这是**可接受**的：该场景仍会在聊天流里出一张 delivery 卡片
> （`onDone` 聚合，`chat.js:1546-1572`），用户点一下即可查看，产物不会丢。
>
> ⚠️ **不要**用 `sessionPendingDeliveryArtifacts` 做「pending 非空就补弹」的兜底：
> `chat.js:1268` 是**无条件**把每个 tool_step artifact 加入 pending（含 execute_code 的中间脚本），
> 一旦补弹就会把过程产物重新弹出来，等于回滚本次修复。
> 初版方案此处写错，实现阶段核实 L1268/L1546 后已推翻。

### P1 · 任务区降噪（2~4 天）

| 项 | 动作 | 锚点 |
|---|---|---|
| P1-1 | `TodoPanel` 默认折叠为**单行摘要**（`3/7 · 正在：XXX`），点击才展开；去掉常驻整块列表 | `TodoPanel.vue:39-77` |
| P1-2 | 修 `percent` 父子双计——只按**叶子节点**计数（或按权重）；任务名去 `truncate`，改 `title` 属性 + 浮层 | `TaskFlowCard.vue:33,36,97` |
| P1-3 | 自动滚动 + 高亮当前 `in_progress` 节点；补每步耗时，接入已有 `harnessTimeline.js` | `TaskFlowCard.vue:74` |
| P1-4 | `animate-pulse` 改为状态变更瞬间闪烁（~3s 后转静态点），去除持续脉冲 | `TaskProgress.vue:6` |
| P1-5 | 两条 `PrereqBanner` 合并为单条可折叠通知入口，不再顶部堆叠 | `App.vue:70-84` |

### P2 · 全局浮层治理（1~2 周）

| 项 | 动作 |
|---|---|
| P2-1 | `ApprovalDialog` **批量聚合**：一次弹窗列多条待审批，避免逐条强打断 |
| P2-2 | `UpdateDialog` 降级为非阻塞角标，用户主动点才展开 |
| P2-3 | 建立**打扰预算**：单轮会话自动弹窗次数上限，超阈值自动降级为静默通知 |
| P2-4 | 顶部浮层统一由「通知中心」托管，杜绝多行叠加 |

---

## 4. 明确不做（防范围爆炸）

- 不动 `SceneStatusBar` —— 已是简洁优先。
- 不动审批的**安全语义**（P2-1 只改呈现频次，不改审批逻辑/放行条件）。
- 不引入新 UI 框架或组件库。
- 不改后端主线的 `onDelivery` 过滤规则本身（它是正确的，只是前端没接上）。

---

## 5. 红线

1. **成果必须可达**：静默化只针对过程，**最终交付物必须仍然自动可见**（不得因降噪而丢失）。
2. **过程产物不得丢失**：不再弹窗，但必须仍能在 ArtifactPanel「产物」tab 查到。
3. **安全件不得静默**：审批类弹窗（ApprovalDialog）**永远保持打断**，P2-1 只做聚合不做隐藏。

---

## 6. 验收口径（可量化）

| 指标 | 现状 | 目标 |
|---|---|---|
| 含 `execute_code` 的一轮会话中，tool_step 阶段自动弹面板次数 | ≥ 1 | **0** |
| `onDelivery` 到达时自动展开次数 | 0 | **1** |
| 顶部任务区常驻高度 | ≤ 192px（`max-h-48`） | **≤ 28px**（折叠单行） |
| `TaskFlowCard` percent 与「叶子完成数/叶子总数」偏差 | 系统性偏高（父子双计） | **一致** |
| 前端右栏自动开状态机数量 | 2（`useArtifactPanel` + `useRightPanel`） | **1** |

---

## 7. 待复核（下一步先做）

1. 后端 `onDelivery` 的覆盖率：是否存在「有交付产物但不发 delivery 事件」的链路？若有，P0-1 会造成产物永不自动弹 → 必须靠 `sessionPendingDeliveryArtifacts` 兜底。
2. `window.__vermesArtifacts` 的 `openArtifactById` 在产物已被用户手动关闭后的行为（避免重复弹回）。
3. `useRightPanel.js` 是否仍有活跃调用方——若有，P0-3 需先迁移调用点再删。
