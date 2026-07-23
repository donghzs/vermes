/**
 * chat-cross-restart.spec.ts
 * ───────────────────────────────────────────────────────────────────────────
 * 边界优化 #4 — 跨重启恢复「真实后端」端到端用例
 *
 * 与 chat-reconnect.spec.ts（纯前端 mock）不同，本用例让前端在断线重连时拉取的
 * plan_snapshot 由**真实后端**提供 —— 而该后端复用生产模块 agent/session_plan_store.py
 * 的 load_plan_state 从 SQLite 读取。流程：
 *   1) seed_session_plan.py（进程 A）把 plan 写入 HERMES_HOME/session_plans.db（真实模块）
 *   2) mock_backend.py（进程 B，独立进程）启动，plan_snapshot 走真实 load_plan_state
 *   3) 前端发送消息 → chat/completions 被 abort（模拟断线）→ 自动重连
 *      → fetchSnapshot → 真实后端从 SQLite 读回 plan（跨进程/重启恢复）
 *      → TaskDrawer 渲染恢复后的步骤
 *
 * 这真正触达 session_plan_store.py 的落库/恢复路径（chat-reconnect.spec.ts 的 mock 未触达）。
 *
 * 前置：需 python3 + 仓库 agent 包可导入。默认不运行；置 RUN_BACKEND_E2E=1 启用。
 * 沙箱无浏览器，作为可维护资产交付（CI 跑）。
 * ───────────────────────────────────────────────────────────────────────────
 */
import { test, expect, type Page } from '@playwright/test'
import { spawn, execFileSync, type ChildProcess } from 'child_process'
import { mkdtempSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { sendMessage, stepRows } from './helpers'

const MOCK_PORT = 8799
const SESSION_ID = 'sess-cross-restart'
const RUN = process.env.RUN_BACKEND_E2E === '1'

let hermesHome = ''
let backend: ChildProcess | undefined

async function waitForBackend(timeoutMs = 15000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const resp = await fetch(`http://127.0.0.1:${MOCK_PORT}/api/session/__probe__/plan_snapshot`)
      if (resp.ok) return
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250))
  }
  throw new Error('mock_backend did not become ready in time')
}

test.beforeAll(async () => {
  if (!RUN) return
  hermesHome = mkdtempSync(join(tmpdir(), 'vermes-e2e-'))
  // 1) 进程 A：写入 SQLite（真实 session_plan_store.save_plan_state）
  const seed = join(__dirname, 'seed_session_plan.py')
  execFileSync('python3', [seed, SESSION_ID], {
    env: { ...process.env, HERMES_HOME: hermesHome },
    stdio: 'inherit',
  })
  // 2) 进程 B：独立进程读取（真实 session_plan_store.load_plan_state）
  const mb = join(__dirname, 'mock_backend.py')
  backend = spawn('python3', [mb, String(MOCK_PORT)], {
    env: { ...process.env, HERMES_HOME: hermesHome },
  })
  backend.stdout?.on('data', () => {})
  backend.stderr?.on('data', (d) => process.stderr.write(d))
  await waitForBackend()
})

test.afterAll(async () => {
  if (backend) backend.kill()
})

test('recovers plan from SQLite across restart via real session_plan_store', async ({ page }) => {
  test.skip(!RUN, 'set RUN_BACKEND_E2E=1 (and have python3) to run the real-backend recovery e2e')

  // chat/completions 主动 abort（断线）→ 触发重连；plan_snapshot 代理到真实后端
  await page.route('**/api/chat/completions', (route) => route.abort('failed'))
  await page.route('**/api/session/**/plan_snapshot', async (route) => {
    const pathname = new URL(route.request().url()).pathname
    const resp = await fetch(`http://127.0.0.1:${MOCK_PORT}${pathname}`)
    await route.fulfill({
      status: resp.status,
      contentType: 'application/json; charset=utf-8',
      body: await resp.text(),
    })
  })
  await page.route('**/api/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  )

  await page.goto('/')
  await sendMessage(page, '帮我调研并写一份对比报告')

  // 重连后从 SQLite 恢复的 plan 应渲染出 2 个步骤，首步「进行中」
  const rows = stepRows(page)
  await expect(rows).toHaveCount(2, { timeout: 15000 })
  await expect(rows.first()).toContainText('进行中')
  await expect(rows.nth(1)).toContainText('待办')
  await expect(page.getByText('检索相关资料')).toBeVisible()
})
