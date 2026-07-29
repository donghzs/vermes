# 前端 e2e 冒烟测试（边界优化 #3）

本目录为 Vermes 前端补充的**浏览器级 e2e** 测试，覆盖审计报告中第 6 节遗留的边界
「缺浏览器级 e2e」。属于 [优化优劣报告](../../vermes_boundary_optimization_proscons_20260723.html)
中第 4 项（优先级最低、性价比最低）的收尾交付。

## 覆盖场景

| 用例 | 验证路径 | 触发的前端代码 |
|------|----------|----------------|
| `renders plan steps from SSE plan_created` | 正常路径：SSE 流推送 `plan_created` + `plan_step_update` → TaskDrawer 渲染步骤与状态 | `chat-transport.js` 解析 `plan_created` / `plan_step_update`；`stores/chat.js` `onPlanCreated` / `onPlanUpdate`；`components/TaskDrawer.vue` |
| `recovers plan steps after SSE disconnect via plan_snapshot` | 断线恢复：首次 `/api/chat/completions` 连接失败 → 自动重连 → 拉取 `/api/session/{id}/plan_snapshot` → 从上次快照重建步骤 | `chat-transport.js` 的 catch 重连分支（`_maxReconnects=2`、指数退避）+ `fetchSnapshot`；`session_plan_store.py` 持久化的快照经 `chat.py` 的 `plan_snapshot` 端点返回 |

两个用例共同构成 P1-3（SSE 重连 + 快照 merge）的端到端闭环验证：
**步骤实时渲染**（正常路径）与 **断线后状态不丢**（恢复路径）。

## 设计要点

- **纯前端 e2e，不依赖 Python 后端。** 全部 `/api/**` 由 Playwright `page.route` 拦截 mock：
  - `POST /api/chat/completions` → 脚本化 SSE 流（恢复路径用例主动 `abort` 模拟断线）
  - `GET /api/session/**/plan_snapshot` → 返回持久化快照 JSON
  - 其余 `/api/**` → `200 {}` 兜底，避免会话创建等旁路请求阻断发送
- **断言锚点**基于真实 DOM：聊天框 `textarea` + `发送` 按钮；TaskDrawer 标题「任务清单」、
  步骤行徽标文本「待办 / 进行中 / 已完成」。步骤展示文本取 `step.title`（`stores/chat.js`
  `onPlanCreated` 映射），首步被自动置为「进行中」。
- mock 的 plan JSON 字段（`id` / `title` / `status`）与后端 `agent/task_planning.py` 模型对齐。

## 运行前置

```bash
cd frontend
npm install                # 安装 @playwright/test 等 devDependencies
npx playwright install chromium   # 首次需下载 chromium 浏览器二进制
```

## 运行

```bash
cd frontend
npx playwright test                       # 跑全部 e2e（会自启 vite dev server）
npx playwright test --project=chromium    # 仅 chromium
npx playwright show-report                # 查看 HTML 报告
```

也可并入 npm script：

```bash
npm run test:e2e        # 等价于 npx playwright test
```

## ⚠️ 沙箱限制

本测试**需要浏览器运行时**（chromium）且需启动 vite dev server，当前开发沙箱环境无浏览器，
故作为**可维护资产交付，不在此处实跑**。CI 环境（已装 chromium + `@playwright/test`）可直接 `npx playwright test`。
建议接入 CI 的 `e2e` 阶段，作为 P1-3 的回归门禁。

## 跨重启恢复（真实后端用例）

`chat-cross-restart.spec.ts` 覆盖审计第 6 节遗留的「跨重启恢复」边界（边界 #4），**真正触达
生产模块 `agent/session_plan_store.py`**，而非纯前端 mock：

- `seed_session_plan.py`（进程 A）用真实 `save_plan_state` 把 plan 写入 `VERMES_HOME/session_plans.db`；
- `mock_backend.py`（进程 B，独立进程）的 `plan_snapshot` 走真实 `load_plan_state` 从 SQLite 读回；
- 前端 `chat/completions` 被主动 `abort`（模拟断线）→ 触发重连 → 拉 `plan_snapshot` → 从 SQLite 恢复渲染。

即「一个进程写入 SQLite → 另一进程（模拟重启后）读回」的跨进程持久化闭环，验证
`session_plan_store` 的落库/恢复路径。

**前置**：需 `python3` 且仓库 `agent` 包可导入（CI 的 ubuntu/python3.11 满足）。默认**不运行**，
置环境变量启用：

```bash
cd frontend
RUN_BACKEND_E2E=1 VERMES_HOME=/tmp/vermes-e2e npx playwright test chat-cross-restart.spec.ts
```

`VERMES_HOME` 指向临时目录，避免触碰用户真实的 `session_plans.db`。未置 `RUN_BACKEND_E2E=1`
时该用例 `test.skip()`，纯前端 mock 用例仍正常跑。

## CI 接入

`.github/workflows/ci-quality-gate.yml` 新增两个 job：

- `frontend-e2e`（**阻断**）：装 Node 20 + `@playwright/test` + chromium，跑 `npm run test:e2e`
  （纯前端 mock 两个用例，默认即可全绿，守护 P1-3 重连/快照合并）。
- `frontend-e2e-backend`（**阻断**）：`RUN_BACKEND_E2E=1` 同时跑真实后端跨重启恢复用例，
  验证 `session_plan_store` 落库/恢复路径（复用真实 `save_plan_state` / `load_plan_state`）。
  已去 `continue-on-error`，失败即阻断 PR 合并；`--retries=1` 吸收偶发抖动。

## 后续可扩展

- 增加超时/预算退出（边界 #3）的前端提示断言。
- 若日后有「mock LLM provider」，可让真实后端在 e2e 中真实产出 plan（write 路径），
  而不仅验证 read/恢复路径。
- 仓库管理员可在 Branch protection 中将两项 `Frontend E2E (...)` 标记为 Required status checks，
  使其成为硬性合并门槛（PR 模板已列出）。
