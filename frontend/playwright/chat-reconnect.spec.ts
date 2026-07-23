/**
 * chat-reconnect.spec.ts
 * ───────────────────────────────────────────────────────────────────────────
 * 边界优化 #3 — 浏览器级 e2e 冒烟测试（纯前端 mock 版）
 *
 * 覆盖 P1-3 SSE 重连 + plan_snapshot 合并的端到端行为：
 *   1) 正常路径：SSE 流推 plan_created + plan_step_update → TaskDrawer 渲染步骤/状态。
 *   2) 断线恢复：chat/completions 被 abort → 前端自动重连 → 拉 plan_snapshot → 渲染。
 *
 * 纯前端 mock：所有 /api/** 由 Playwright route 拦截（见 helpers.ts installMockRoutes），
 * 不依赖 Python 后端。沙箱无浏览器，作为可维护资产交付（CI 跑）。
 * 真实后端跨重启恢复版本见 chat-cross-restart.spec.ts。
 * ───────────────────────────────────────────────────────────────────────────
 */
import { test, expect } from '@playwright/test'
import { installMockRoutes, sendMessage, stepRows } from './helpers'

// ── 测试 1：正常路径 ─────────────────────────────────────
test('renders plan steps from SSE plan_created', async ({ page }) => {
  await installMockRoutes(page, 'happy')
  await page.goto('/')

  await sendMessage(page, '帮我调研并写一份对比报告')

  const rows = stepRows(page)
  await expect(rows).toHaveCount(2, { timeout: 10000 })

  await expect(page.getByText('检索相关资料')).toBeVisible()
  await expect(page.getByText('撰写对比报告')).toBeVisible()

  // 第一个步骤被自动标记为「进行中」
  await expect(rows.first()).toContainText('进行中')
  await expect(rows.nth(1)).toContainText('待办')
})

// ── 测试 2：断线恢复路径（核心 P1-3 行为）─────────────────
test('recovers plan steps after SSE disconnect via plan_snapshot', async ({ page }) => {
  await installMockRoutes(page, 'drop')
  await page.goto('/')

  await sendMessage(page, '帮我调研并写一份对比报告')

  // 首次 chat/completions 被 abort → transport 进入重连分支
  // → fetchSnapshot 返回快照 → onPlanCreated + onPlanUpdate 重建步骤
  const rows = stepRows(page)
  await expect(rows).toHaveCount(2, { timeout: 10000 })

  await expect(rows.first()).toContainText('进行中')
  await expect(rows.nth(1)).toContainText('待办')
  await expect(page.getByText('检索相关资料')).toBeVisible()
})
