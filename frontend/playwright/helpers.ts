/**
 * helpers.ts — 共享 e2e 工具
 *
 * 抽取两个 spec 共用的：plan 数据、SSE 拼装、聊天发送、抽屉步骤选择器、以及
 * 「纯前端 mock」路由安装（不依赖任何后端，全部由 Playwright route 拦截）。
 */
import { expect, type Page, type Route } from '@playwright/test'

// ── plan 数据（与后端 agent/task_planning.py 模型对齐）─────────────────────
// store.onPlanCreated 取 step.title 作为展示文本，step.id 作为 key。
export const PLAN = {
  id: 'plan-e2e',
  title: '端到端验证计划',
  steps: [
    { id: 's1', title: '检索相关资料', status: 'pending' },
    { id: 's2', title: '撰写对比报告', status: 'pending' },
  ],
}

// 纯前端 mock 用的快照（与后端 plan_snapshot 端点返回同构）
export const SNAPSHOT = {
  plan: PLAN,
  todo_states: { s1: 'in_progress', s2: 'pending' },
}

// 把事件对象拼成一行 SSE `data: ...\n\n`
export function sseEvent(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`
}

// 完整 SSE 流：plan_created → step_update(s1 进行中) → [DONE]
export function happyStreamBody(): string {
  return (
    sseEvent({ type: 'plan_created', plan: PLAN }) +
    sseEvent({ type: 'plan_step_update', subtype: 'step_update', step: { id: 's1', status: 'in_progress' } }) +
    sseEvent({ type: 'delta', content: '正在处理…' }) +
    'data: [DONE]\n\n'
  )
}

// 在聊天框输入并发送
export async function sendMessage(page: Page, text: string): Promise<void> {
  const textarea = page.locator('textarea')
  await expect(textarea).toBeVisible()
  await textarea.fill(text)
  await page.getByRole('button', { name: '发送' }).click()
}

// TaskDrawer 步骤行容器（标题「任务清单」+ 步骤列表）
export function stepRows(page: Page) {
  return page.locator('aside:has-text("任务清单") div.space-y-2 > div')
}

/**
 * 纯前端 mock 路由：所有 /api/* 由 Playwright 拦截，无需后端。
 * - happy：chat/completions 返回脚本化 SSE
 * - drop ：chat/completions 主动 abort（模拟断线）→ 触发重连分支
 * - plan_snapshot：返回内存快照
 */
export async function installMockRoutes(page: Page, mode: 'happy' | 'drop'): Promise<void> {
  await page.route('**/api/chat/completions', async (route: Route) => {
    if (mode === 'drop') {
      await route.abort('failed')
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream; charset=utf-8',
      body: happyStreamBody(),
    })
  })

  await page.route('**/api/session/**/plan_snapshot', async (route: Route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json; charset=utf-8',
      body: JSON.stringify(SNAPSHOT),
    })
  })

  // 其余后端请求兜底，避免会话创建等旁路请求 404 阻断发送
  await page.route('**/api/**', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
}
