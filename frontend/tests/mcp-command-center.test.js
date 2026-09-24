/**
 * ⑤ C4/C5 真行为测试：MCP 统一入口（产品已并入 Agent 管理，页面仍名 MCP）
 * + ⌘K quick-entry。统计聚合/路由/命令清单/热键绑定均为可观察行为，不 mount
 * 重依赖组件。T16④: 断言跟产品现名，不再锁死旧称呼「指挥中心」为唯一文案。
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { nextTick } from 'vue'
import { aggregateMcpStatsByServer, summarizeMcpStats } from '../src/utils/mcp-stats.js'
import { isPaletteToggleEvent, attachPaletteHotkey } from '../src/utils/palette-hotkey.js'
import {
  buildPalettePageCommands,
  buildPaletteActionCommands,
  filterPaletteCommands,
} from '../src/utils/palette-commands.js'
import appRouter from '../src/router/index.js'
import MCPCommandCenter from '../src/components/MCPCommandCenter.vue'
import CommandPalette from '../src/components/CommandPalette.vue'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

describe('MCP 调用统计聚合（真行为）', () => {
  it('per-tool 聚合成 per-server，并计算三态成功率', () => {
    const byServer = aggregateMcpStatsByServer([
      { server: 'fs', tool: 'read', calls: 10, errors: 1, interrupts: 1, total_ms: 1000, max_ms: 200 },
      { server: 'fs', tool: 'write', calls: 6, errors: 0, interrupts: 0, total_ms: 600, max_ms: 100 },
      { server: 'search', tool: 'web', calls: 4, errors: 0, interrupts: 0, total_ms: 400, max_ms: 50 },
    ])
    expect(byServer.fs.calls).toBe(16)
    expect(byServer.fs.errors).toBe(1)
    expect(byServer.fs.interrupts).toBe(1)
    expect(byServer.fs.tools).toBe(2)
    expect(byServer.fs.avg_ms).toBe(100)
    expect(byServer.fs.rate).toBe(88)
    expect(byServer.search.rate).toBe(100)
  })

  it('零调用 server 不显示成功率（null）', () => {
    const byServer = aggregateMcpStatsByServer([{ server: 'empty', tool: 'x', calls: 0 }])
    expect(byServer.empty.rate).toBeNull()
  })

  it('summary 有调用才展示总览；否则 null', () => {
    expect(summarizeMcpStats(null)).toBeNull()
    expect(summarizeMcpStats({ summary: { calls: 0 } })).toBeNull()
    const s = summarizeMcpStats({
      summary: { calls: 20, errors: 2, interrupts: 1, success_rate: 0.85 },
      count: 3,
    })
    expect(s).toMatchObject({ calls: 20, errors: 2, interrupts: 1, tool_count: 3 })
  })
})

describe('路由：MCP 统一入口（并入 Agent 管理）', () => {
  it('/mcp 已注册且可 resolve', () => {
    expect(appRouter.getRoutes().some(r => r.path === '/mcp')).toBe(true)
    expect(appRouter.resolve('/mcp').path).toBe('/mcp')
  })

  it('MCPCommandCenter 挂载后可见四个分区标题', async () => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async () => ({
      ok: true, status: 200,
      json: async () => ({ summary: { calls: 9, errors: 1, success_rate: 0.8 }, count: 2, servers: {}, catalog: [], checks: [] }),
    }))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/mcp', component: MCPCommandCenter }],
    })
    router.push('/mcp')
    await router.isReady()
    const wrapper = mount(MCPCommandCenter, { global: { plugins: [router, createPinia()] } })
    await nextTick()
    const text = wrapper.text()
    // 页头仍是「MCP 指挥中心」；入口已并入 Agent 管理（见 palette 断言）
    expect(text).toContain('MCP 指挥中心')
    expect(text).toContain('服务与调用')
    expect(text).toContain('服务与调用')
    expect(text).toContain('目录安装')
    expect(text).toContain('安全校验')
    expect(text).toContain('专家目录')
    wrapper.unmount()
  })
})

describe('C5 quick-entry：⌘K 热键与命令面（真行为）', () => {
  it('isPaletteToggleEvent 识别 Cmd/Ctrl+K', () => {
    expect(isPaletteToggleEvent({ metaKey: true, key: 'k' })).toBe(true)
    expect(isPaletteToggleEvent({ ctrlKey: true, key: 'k' })).toBe(true)
    expect(isPaletteToggleEvent({ metaKey: true, key: 'K' })).toBe(false)
    expect(isPaletteToggleEvent({ key: 'k' })).toBe(false)
  })

  it('attachPaletteHotkey：keydown 触发 toggle，解绑后失效', () => {
    const listeners = new Map()
    const fakeTarget = {
      addEventListener: (t, fn) => listeners.set(t, fn),
      removeEventListener: (t) => listeners.delete(t),
    }
    let n = 0
    const detach = attachPaletteHotkey(fakeTarget, () => { n += 1 })
    const ev = { metaKey: true, key: 'k', preventDefault: vi.fn() }
    listeners.get('keydown')(ev)
    expect(n).toBe(1)
    expect(ev.preventDefault).toHaveBeenCalled()
    // 非快捷键不触发
    listeners.get('keydown')({ metaKey: true, key: 'x', preventDefault() {} })
    expect(n).toBe(1)
    detach()
    expect(listeners.has('keydown')).toBe(false)
  })

  it('命令面含 Agent 管理 / 设置·MCP（指挥中心已并入），选中后正确路由', () => {
    const pushed = []
    const router = { push: (p) => pushed.push(p) }
    const chat = { createSession: vi.fn(), toggleTheme: vi.fn() }
    const pages = buildPalettePageCommands(router)
    const actions = buildPaletteActionCommands(router, chat)
    // T16④: 产品已并入 Agent 管理 —— 不再断言旧名「MCP 指挥中心」/ page:mcp
    const agentsPage = pages.find(c => c.key === 'page:agents')
    expect(agentsPage?.label).toBe('Agent 管理')
    agentsPage.action()
    expect(pushed).toContain('/agents')
    const settingsMcp = pages.find(c => c.key === 'page:settings-mcp')
    expect(settingsMcp?.label).toBe('设置 · MCP')
    settingsMcp.action()
    expect(pushed).toContain('/settings')
    const agentsAct = actions.find(c => c.key === 'act:agents')
    agentsAct.action()
    expect(pushed.filter(p => p === '/agents').length).toBe(2)
  })

  it('filterPaletteCommands 可按关键词命中 Agent 管理里的 MCP 入口', () => {
    const router = { push: () => {} }
    const chat = { createSession() {}, toggleTheme() {} }
    const all = [...buildPalettePageCommands(router), ...buildPaletteActionCommands(router, chat)]
    // 「mcp」命中 hint 含 MCP 的 Agent 管理 / 设置·MCP
    const hits = filterPaletteCommands(all, 'mcp')
    expect(hits.some(c => c.key === 'page:agents' || c.key === 'page:settings-mcp')).toBe(true)
  })

  it('CommandPalette 挂载后 toggle 打开，真渲染出 Agent 管理/MCP 入口（真行为）', async () => {
    setActivePinia(createPinia())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div>chat</div>' } },
        { path: '/agents', component: { template: '<div>agents</div>' } },
        { path: '/settings', component: { template: '<div>settings</div>' } },
      ],
    })
    router.push('/')
    await router.isReady()
    const wrapper = mount(CommandPalette, { global: { plugins: [router, createPinia()] } })
    // 初始关闭，不渲染结果
    expect(wrapper.text()).not.toContain('Agent 管理')
    // 通过 defineExpose 的 toggle 打开
    wrapper.vm.toggle()
    await nextTick()
    const text = wrapper.text()
    // T16④: 产品现名 —— 指挥中心已并入 Agent 管理
    expect(text).toContain('Agent 管理')
    expect(text).toContain('设置 · MCP')
    expect(text).toContain('对话')
    wrapper.unmount()
  })

  it('CommandPalette 响应 Cmd+K 唤起（真行为）', async () => {
    setActivePinia(createPinia())
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<div>chat</div>' } },
        { path: '/agents', component: { template: '<div>agents</div>' } },
      ],
    })
    router.push('/')
    await router.isReady()
    const wrapper = mount(CommandPalette, { global: { plugins: [router, createPinia()] } })
    expect(wrapper.text()).not.toContain('Agent 管理')
    window.dispatchEvent(new KeyboardEvent('keydown', { metaKey: true, key: 'k', bubbles: true }))
    await nextTick()
    expect(wrapper.text()).toContain('Agent 管理')
    wrapper.unmount()
  })
})

describe('C5 Plugin SDK：本季显式递延（真行为）', () => {
  it('命令面不含 plugin SDK 相关命令，不静默假装 Plugin SDK 已做', () => {
    const router = { push: () => {} }
    const chat = { createSession() {}, toggleTheme() {} }
    const pages = buildPalettePageCommands(router)
    const actions = buildPaletteActionCommands(router, chat)
    const all = [...pages, ...actions]
    expect(all.some(c => /plugin/i.test(c.key))).toBe(false)
    expect(all.some(c => /plugin|插件/i.test(c.label))).toBe(false)
  })
})
