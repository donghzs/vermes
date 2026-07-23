/**
 * chat-reconnect.spec.ts
 * ───────────────────────────────────────────────────────────────────────────
 * 边界优化 #3 — 浏览器级 e2e 冒烟测试
 *
 * 目标：在真实浏览器中验证「P1-3 SSE 重连 + plan_snapshot 合并」端到端可用：
 *   1) 正常路径：Agent 流式输出 plan_created + plan_step_update → 前端 TaskDrawer
 *      实时渲染步骤与状态。
 *   2) 断线恢复：SSE 连接中途断开 → 前端自动重连 → 拉取 /api/session/{id}/plan_snapshot
 *      → 抽屉从上次快照恢复渲染，步骤状态不丢。
 *
 * 设计：纯前端 e2e，不依赖 Python 后端。所有 /api/** 由 Playwright route 拦截并 mock：
 *   - POST /api/chat/completions  → 返回脚本化 SSE 流（或主动 abort 模拟断线）
 *   - GET  /api/session/**/plan_snapshot → 返回持久化快照
 *   - 其余 /api/**   → 200 {} 兜底，避免会话创建等旁路请求阻断发送
 *
 * 注意：本测试需要在 CI / 本地装有浏览器运行时的环境执行（见 README.md）。
 * 沙箱无浏览器，故交付为可维护资产，不在此处实跑。
 * ───────────────────────────────────────────────────────────────────────────
 */
import { test, expect, type Page, type Route } from '@playwright/test'

// ── 步骤数据（与后端 task_planning.py 的 JSON 模型对齐）─────────────────────
// 关键：store.onPlanCreated 取 step.title 作为展示文本，step.id 作为 key。
const PLAN = {
  id: 'plan-e2e',
  title: '端到端验证计划',
  steps: [
    { id: 's1', title: '检索相关资料', status: 'pending' },
    { id: 's2', title: '撰写对比报告', status: 'pending' },
  ],
}

const SNAPSHOT = {
  plan: PLAN,
  todo_states: { s1: 'in_progress', s2: 'pending' },
}

// 把事件对象拼成一行 SSE `data: ...\n\n`
function sseEvent(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`
}

// 完整 SSE 流：plan_created → step_update(s1 进行中) → [DONE]
function happyStreamBody(): string {
  return (
    sseEvent({ type: 'plan_created', plan: PLAN }) +
    sseEvent({ type: 'plan_step_update', subtype: 'step_update', step: { id: 's1', status: 'in_progress' } }) +
    sseEvent({ type: 'delta', content: '正在处理…' }) +
    'data: [DONE]\n\n'
  )
}

// ── 路由安装 ──────────────────────────────────────────────
async function installRoutes(page: Page, mode: 'happy' | 'drop'): Promise<void> {
  // 1) 聊天补全端点
  await page.route('**/api/chat/completions', async (route: Route) => {
    if (mode === 'drop') {
      // 模拟连接中途失败：让 fetch 抛网络错误 → 触发 transport 重连分支
      await route.abort('failed')
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream; charset=utf-8',
      body: happyStreamBody(),
    })
  })

  // 2) 重连快照端点
  await page.route('**/api/session/**/plan_snapshot', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(SNAPSHOT),
    })
  })

  // 3) 其余后端请求兜底，避免会话创建等旁路请求 404 阻断发送
  await page.route('**/api/**', async (route: Route) => {
    // 已经被上面更具体的路由命中的不会到这里；这里只接住其它 /api/*
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}

// 在聊天框输入并发送
async function sendMessage(page: Page, text: string): Promise<void> {
  const textarea = page.locator('textarea')
  await expect(textarea).toBeVisible()
  await textarea.fill(text)
  await page.getByRole('button', { name: '发送' }).click()
}

// 断言抽屉渲染出 N 个步骤，并返回步骤容器
function stepRows(page: Page) {
  // TaskDrawer 步骤行：v-for 渲染，含 STATUS_TEXT 徽标（待办/进行中/已完成…）
  return page.locator('aside:has-text("任务清单") div.space-y-2 > div')
}

// ── 测试 1：正常路径 ─────────────────────────────────────
test('renders plan steps from SSE plan_created', async ({ page }) => {
  await installRoutes(page, 'happy')
  await page.goto('/')

  await sendMessage(page, '帮我调研并写一份对比报告')

  // 抽屉应自动打开并渲染 2 个步骤
  const rows = stepRows(page)
  await expect(rows).toHaveCount(2, { timeout: 10000 })

  // 步骤文本来自 step.title
  await expect(page.getByText('检索相关资料')).toBeVisible()
  await expect(page.getByText('撰写对比报告')).toBeVisible()

  // 第一个步骤被自动标记为「进行中」
  await expect(rows.first()).toContainText('进行中')
  await expect(rows.nth(1)).toContainText('待办')
})

// ── 测试 2：断线恢复路径（核心 P1-3 行为）─────────────────
test('recovers plan steps after SSE disconnect via plan_snapshot', async ({ page }) => {
  await installRoutes(page, 'drop')
  await page.goto('/')

  await sendMessage(page, '帮我调研并写一份对比报告')

  // 首次 /api/chat/completions 被 abort → transport 进入重连分支
  // → fetchSnapshot 返回 SNAPSHOT → onPlanCreated + onPlanUpdate 重建步骤
  const rows = stepRows(page)
  await expect(rows).toHaveCount(2, { timeout: 10000 })

  // 快照中 s1=in_progress, s2=pending，且 onPlanCreated 再次把首步置 in_progress
  await expect(rows.first()).toContainText('进行中')
  await expect(rows.nth(1)).toContainText('待办')
  await expect(page.getByText('检索相关资料')).toBeVisible()
})
