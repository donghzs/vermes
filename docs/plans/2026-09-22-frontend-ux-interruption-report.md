# 前端交互层打扰治理 · 落实报告（2026-09-22）

> 方案：`docs/plans/2026-09-22-frontend-ux-interruption-roadmap.md`
> 分支：`fix/frontend-ux-quiet-interruption`（未 merge、未 push，留待审计）
> 提交：`1877675bf5`（P0-1~P0-4）→ `9cc8fffab6`（P1/P2/P0-5）

---

## 0. 结论先行

| # | 结论 |
|---|---|
| 1 | 方案 **14 项全部落地**，其中 2 项为**降级/收敛实现**（P2-1、P2-4），已在下文逐条说明原因，未虚标 |
| 2 | 核心架构修复：**自动弹出从 `tool_step` 搬到 `onDelivery`——「过程弹、成果不弹」的倒置已纠正** |
| 3 | 顺带修掉一个**真 bug**：审批单 ref 无条件覆盖 → 两审批连推时前一条 `session_key` 永久丢失 → 后端等不到响应 → 会话卡死 |
| 4 | 后端 P0-5 落点比原方案更准：不止打标记，还接到 `_filter_delivery_artifacts` 消费点（否则标记是死数据） |
| 5 | 修正了我在方案里写错的一条（P1-3「接入 harnessTimeline.js」是错的），见 §5.1 |
| 6 | 全量回归：**前端 369 用例 + 后端 80 用例通过**，剩余 4 例为既有环境失败（缺后端 :3000），与本次无关 |

---

## 1. 交付清单

| 文件 | 改动 |
|---|---|
| `frontend/src/stores/chat.js` | 审批队列 FIFO；tool_step 静默；onDelivery 唯一自动弹入口；每轮重置打扰预算 |
| `frontend/src/composables/useArtifactPanel.js` | `consumeAutoOpen()` / `resetAutoOpenBudget()`；`autoOpen` 持久化 |
| `frontend/src/composables/useRightPanel.js` | 删除死开关 `autoOpenOnArtifact`（P0，上一提交） |
| `frontend/src/components/TodoPanel.vue` | 默认折叠单行摘要 |
| `frontend/src/components/TaskFlowCard.vue` | 叶子计数 / 去 truncate / 当前节点高亮 + 自动滚动 / 步骤耗时 |
| `frontend/src/components/TaskProgress.vue` | 脉冲 3s 后转静态 |
| `frontend/src/components/ApprovalDialog.vue` | 显示「共 N 条待审批」 |
| `frontend/src/components/UpdateDialog.vue` | 降级为右下角非阻塞角标 |
| `frontend/src/App.vue` | 两条 PrereqBanner 合并为单条 |
| `frontend/tests/ux-quiet-interruption.test.js` | **新增** 8 例守卫（预算 + 审批队列） |
| `frontend/tests/useRightPanel.test.js` | 改守「不再导出 autoOpenOnArtifact」 |
| `tools/code_execution_tool.py` | 沙箱产物打 `intermediate` 标记（P0-5） |
| `vermes_cli/blueprints/chat.py` | `_filter_delivery_artifacts` 消费 `intermediate`（P0-5） |
| `docs/plans/...roadmap.md` | 方案同步（落实情况 + 纠错） |

---

## 2. 逐项落实对照

### P0 · 过程静默、成果才弹

| 项 | 状态 | 说明 |
|---|:---:|---|
| P0-1 自动弹出迁到 onDelivery | ✅ | `chat.js` — 全局唯一入口 |
| P0-2 收窄交付物判定 | ✅ | 直接删除 `isRenderableDeliverable`，不再用扩展名猜 |
| P0-3 收编双状态机 | ✅ | 删除 `useRightPanel.autoOpenOnArtifact` |
| P0-4 autoOpen 可关 + 持久化 | ✅ | localStorage `vermes-artifact-auto-open` |
| P0-5 后端 intermediate 标记 | ✅ | **须重打 DMG 才生效** |

### P1 · 任务区降噪

| 项 | 状态 | 实测效果 |
|---|:---:|---|
| P1-1 TodoPanel 折叠 | ✅ | 顶部占位 192px → **≈28px** |
| P1-2 percent 去父子双计 + 去 truncate | ✅ | 只数叶子节点；长任务名换行 + `title` |
| P1-3 高亮 + 自动滚动 + 耗时 | ✅ | 已完成步骤显示耗时，进行中不显示（避免每秒重渲染） |
| P1-4 脉冲收敛 | ✅ | 切换瞬间闪 3s → 静态 |
| P1-5 两条 banner 合并 | ✅ | 顶部恒占 **≤1 行**，一次 dismiss 全收 |

### P2 · 全局浮层治理

| 项 | 状态 | 说明 |
|---|:---:|---|
| P2-1 审批聚合 | ⚠️ 降级 | 见 §4.1 |
| P2-2 UpdateDialog 降级 | ✅ | 角标非阻塞；下载中仍强制展开 |
| P2-3 打扰预算 | ✅ | 单轮最多自动弹 1 次 |
| P2-4 浮层统一托管 | ⚠️ 收敛 | 见 §4.2 |

---

## 3. 验收指标对照（方案 §6）

| 指标 | 现状 | 目标 | 达成 |
|---|---|---|:---:|
| 含 execute_code 一轮中 tool_step 自动弹面板次数 | ≥1 | 0 | ✅ |
| onDelivery 到达时自动展开次数 | 0 | 1 | ✅ |
| 顶部任务区常驻高度 | ≤192px | ≤28px | ✅ |
| TaskFlowCard percent 与「叶子完成/叶子总数」偏差 | 系统性偏高 | 一致 | ✅ |
| 右栏自动开状态机数量 | 2 | 1 | ✅ |
| 单轮自动弹窗次数上限 | 无上限 | ≤1 | ✅（新增 P2-3） |

---

## 4. 降级与收敛（诚实披露，未虚标）

### 4.1 P2-1 审批聚合 —— 协议限制，降级为「队列化 + 修 bug」

后端审批协议是**阻塞串行**的：发一条 → 等前端 `/api/approve` → 再发下一条。前端**无法预知队列长度**，因此「一次弹窗列多条待审批」在当前协议下做不到。改为两件能做的事：

1. **修真 bug**（价值高于原计划）：原 `pendingApproval.value = {...}` 无条件覆盖，两条审批连推时前一条 `session_key` 永久丢失 → 后端等不到响应 → **会话卡死**。改 FIFO 队列，逐条 POST，已加回归守卫测试断言两次请求的 `session_key` 分别为 A、B。
2. **显式化队列**：弹窗显示「共 N 条待审批，当前第 1 条」。

**安全语义未改**：仍逐条打断征询，不做批量放行（红线 3）。

### 4.2 P2-4 浮层统一托管 —— 收敛为「顶部恒占 ≤1 行」

未做「全部浮层由 NotificationCenter 托管」重构：`NotificationCenter.vue` 当前未挂载，接线等于新建一套机制，范围与风险超出本次。改由 P1-5（banner 合并）+ P2-2（更新改角标）**等效达成**：顶部无多行叠加。完整托管列为后续项。

---

## 5. 自我纠错（审计重点）

### 5.1 方案原文错误：P1-3「接入 harnessTimeline.js」

方案写「补每步耗时，接入已有 `harnessTimeline.js`」。实读后确认该文件是**工具调用核验分类**（verified/unverified/fail/blocked），与任务耗时**无关**，接进去是错的。
正确数据源是 todo 自带 `started_at`/`finished_at`（`chat.js:1181` 已保留）→ 实现据此改。方案文档已加注推翻说明。

### 5.2 上一轮已纠错：pending 兜底会回滚修复

`chat.js:1268` 无条件把每个 tool_step artifact 塞进 pending（含中间脚本），若按初版方案「pending 非空就补弹」会把脚本重新弹出来。**已改为 pending 兜底只出 delivery 卡片、不弹面板。**

### 5.3 本轮踩坑（已修）

| 坑 | 处置 |
|---|---|
| 上一轮跑 `npm run build` 污染 `vermes_cli/web_dist/`（已跟踪、未 gitignore） | 已完全还原；本轮**只跑 vitest，不跑 build** |
| 删 `autoOpenOnArtifact` 时只 grep `src/` 漏了 `tests/`，引入 11 例回归 | 已改测试守新契约；本轮改动前先 grep `tests/` |

---

## 6. 验证证据

| 项 | 结果 |
|---|---|
| `vitest` 全量 | 40 文件 / **369 用例**：365 通过，**4 失败** |
| 失败归因 | 全部在 `mcp-command-center.test.js`（ECONNREFUSED :3000，缺后端）—— **既有失败，与本次无关** |
| 新增 `ux-quiet-interruption.test.js` | **8/8 通过**（预算配额 4 例 + 审批队列 4 例） |
| 相关既有用例 | `artifact-panel`、`artifact-panel-mount`、`artifact-smoke`、`useRightPanel`、`chat-store` 全绿 |
| `py_compile` | `tools/code_execution_tool.py` + `vermes_cli/blueprints/chat.py` 通过 |
| `pytest` 后端 | `test_code_execution.py` + `test_delivery_gate.py` **80 passed** |

> 后端 pytest 首次跑报 80 errors，原因是沙箱 shim 拦截 `mkdir /private/var/...`（`PermissionError`），
> 与改动无关；加 `--basetemp=/tmp/vpytest-ux` 绕过后 80 passed。

---

## 7. 风险与 trade-off

| 项 | 风险 | 处置 / 建议 |
|---|---|---|
| P0-5 后端改动 | **须重打 DMG 才生效**；前端部分热更即可 | 前端先止血，后端随下次打包跟进 |
| P0-5 误杀交付物 | 沙箱内写到用户工作区的报告可能被标为中间产物 | 已限定**仅临时目录内**打标记，用户工作区不标记 |
| 打扰预算=1 | 多步交付时第 2 次起不自动弹 | 交付物仍以 delivery 卡片出现在聊天流，**不丢失**（红线 1） |
| P2-1 降级 | 未见「一次弹 N 条」 | 修掉的卡死 bug 价值更高；批量聚合需后端协议支持 |

---

## 8. 工作树 / 分支状态（需你知悉）

1. **未 merge、未 push**，按纪律留待你审计。
2. 分支历史中**混入了他人提交 `b4be6e6d5e`（T15 distribution）**——另一个 agent 曾在本分支上提交。它与 main 上的 `bea2c1acf9` **patch-id 完全相同**（同一改动两份）。
   → **影响**：历史重复。合并不会冲突（两边都含该改动，3-way merge 结果一致）。
   → **建议**：合并前用 `git rebase -i` 剔除重复项，或直接 merge（结果一致）。**我没擅自 rebase**，避免重写已披露的提交 hash、也避免打断他人。
3. 当前工作目录在 `fix/frontend-ux-quiet-interruption` 分支上。若要切回 main：`git checkout main`。
4. 我**未触碰** `reports/`（他人在改的发行版化文档）与 `vermes_cli/web_dist/`（打包产物）。

---

## 9. 遗留项（未做，非本次范围）

| 项 | 原因 |
|---|---|
| P2-4 完整通知中心托管 | 需新建机制，范围外（见 §4.2） |
| P2-1 真正批量审批 | 需后端协议支持并发审批队列 |
| 打扰预算阈值可配置 | 当前硬编码 1，可后续提到设置项 |
