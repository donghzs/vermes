/**
 * T0 用量大盘：路由 + 统计卡 + 日趋势条 + 按模型表（后端 /api/analytics/usage）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import UsageDashboard from '../src/components/UsageDashboard.vue'
import appRouter from '../src/router/index.js'
import { buildPalettePageCommands } from '../src/utils/palette-commands.js'

const sample = {
  daily: [
    { day: '2026-09-24', input_tokens: 1000, output_tokens: 400 },
    { day: '2026-09-25', input_tokens: 2000, output_tokens: 800 },
    { day: '2026-09-26', input_tokens: 500, output_tokens: 100 },
  ],
  by_model: [
    { model: 'deepseek-chat', input_tokens: 3000, output_tokens: 1100, sessions: 5, api_calls: 40, estimated_cost: 0.12 },
    { model: 'qwen-max', input_tokens: 500, output_tokens: 200, sessions: 1, api_calls: 3, estimated_cost: 0.02 },
  ],
  totals: {
    total_input: 3500, total_output: 1300,
    total_estimated_cost: 0.14, total_sessions: 6, total_api_calls: 43,
  },
  period_days: 30,
  skills: { top_skills: [{ id: 'weather', name: 'weather', title: '天气', uses: 3 }] },
}

describe('T0 用量大盘', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => sample }))
  })

  it('/usage 路由已注册', () => {
    expect(appRouter.getRoutes().some(r => r.path === '/usage')).toBe(true)
  })

  it('命令面含 用量与成本', () => {
    const pages = buildPalettePageCommands({ push: () => {} })
    const item = pages.find(c => c.key === 'page:usage')
    expect(item?.label).toBe('用量与成本')
  })

  it('挂载后渲染汇总卡 / 趋势 / 模型表', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/usage', component: UsageDashboard }],
    })
    router.push('/usage')
    await router.isReady()
    const wrapper = mount(UsageDashboard, { global: { plugins: [router, createPinia()] } })
    await new Promise(r => setTimeout(r, 20))
    const text = wrapper.text()
    expect(text).toContain('用量与成本')
    expect(text).toContain('输入 token')
    expect(text).toContain('预估成本')
    expect(text).toContain('deepseek-chat')
    expect(text).toContain('天气')
    // 趋势 3 天
    expect(wrapper.findAll('[title*="2026-09"]').length).toBe(3)
    expect(globalThis.fetch).toHaveBeenCalledWith('/api/analytics/usage?days=30')
  })
})
