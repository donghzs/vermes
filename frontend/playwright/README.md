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

## 后续可扩展

- 增加「跨重启恢复」端到端用例：起真实后端（含 SQLite `session_plans.db`），发消息→落库→
  重启后端→前端重连拉快照，断言步骤从 SQLite 恢复（当前为纯前端 mock，未触达 `session_plan_store.py` 落库路径）。
- 增加超时/预算退出（边界 #3）的前端提示断言。
