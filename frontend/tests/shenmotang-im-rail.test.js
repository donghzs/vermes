/**
 * T1 左栏 IM 化：常驻联系人/群混合列表 + ⊕ 入口 + 不再劫持全局侧栏。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ShenmotangImRail from '../src/components/ShenmotangImRail.vue'

const contacts = {
  ok: true,
  contacts: [
    { id: 'p1', name: 'Codex', hue: 210, transport: 'acp', provider: 'openai' },
    { id: 'p2', name: 'Kimi', hue: 20, transport: 'acp', provider: 'moonshot' },
  ],
}
const rooms = {
  ok: true,
  rooms: [{ id: 'r1', name: '论文攻坚', members: [{}, {}], announcement: '写论文' }],
}

describe('T1 神魔堂 IM 左栏', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url)
      if (u.includes('/agents/contacts')) return { ok: true, json: async () => contacts }
      if (u.includes('/bot/rooms')) return { ok: true, json: async () => rooms }
      return { ok: true, json: async () => ({}) }
    })
  })

  it('渲染联系人 + 群混列 + ⊕ 入口', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangImRail, {
      global: { plugins: [router, createPinia()] },
      props: { selectedKey: '' },
    })
    await new Promise(r => setTimeout(r, 20))
    const text = wrapper.text()
    expect(text).toContain('联系人')
    expect(text).toContain('群聊')
    expect(text).toContain('Codex')
    expect(text).toContain('Kimi')
    expect(text).toContain('论文攻坚')
    expect(text).toContain('添加联系人')
    expect(text).toContain('用量与成本')
  })

  it('点击联系人/群发出 select 事件', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangImRail, {
      global: { plugins: [router, createPinia()] },
      props: { selectedKey: '' },
    })
    await new Promise(r => setTimeout(r, 20))
    const btns = wrapper.findAll('button')
    const codexBtn = btns.find(b => b.text().includes('Codex'))
    await codexBtn.trigger('click')
    expect(wrapper.emitted('select-contact')?.[0]?.[0]?.id).toBe('p1')
    const roomBtn = btns.find(b => b.text().includes('论文攻坚'))
    await roomBtn.trigger('click')
    expect(wrapper.emitted('select-room')?.[0]?.[0]?.id).toBe('r1')
    const plus = btns.find(b => (b.attributes('title') || '').includes('拉群'))
    expect(plus).toBeTruthy()
    await plus.trigger('click')
    expect(wrapper.emitted('create-group')).toBeTruthy()
  })

  it('dm-* 的 1:1 房不进群聊列表（防与联系人区重复）', async () => {
    globalThis.fetch = vi.fn(async (url) => {
      const u = String(url)
      if (u.includes('/agents/contacts')) return { ok: true, json: async () => contacts }
      if (u.includes('/bot/rooms')) {
        return {
          ok: true,
          json: async () => ({
            ok: true,
            rooms: [
              { id: 'r1', name: '论文攻坚', members: [{}, {}] },
              { id: 'dm-p1', name: '1:1 · Codex', members: [{}] },
              { id: 'dm-p2', name: '1:1 · Kimi', members: [{}] },
            ],
          }),
        }
      }
      return { ok: true, json: async () => ({}) }
    })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangImRail, {
      global: { plugins: [router, createPinia()] },
      props: { selectedKey: '' },
    })
    await new Promise(r => setTimeout(r, 20))
    const text = wrapper.text()
    expect(text).toContain('论文攻坚')
    expect(text).not.toContain('1:1 · Codex')
    expect(text).not.toContain('1:1 · Kimi')
  })

  it('Shenmotang 不再劫持 sidebarOpen（源码断言）', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(
      path.resolve('src/components/Shenmotang.vue'),
      'utf8',
    )
    expect(src).toContain('ShenmotangImRail')
    expect(src).not.toContain('chat.sidebarOpen = false')
    expect(src).not.toContain('wasSidebarOpen')
  })

  it('点左栏群 / 建群成功都必须 selectRoom 打开会话', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(
      path.resolve('src/components/Shenmotang.vue'),
      'utf8',
    )
    // 退化修复：只改 selectedKey 高亮、不打开会话 → 消息流仍停在旧房
    expect(src).toMatch(/onSelectRoom[\s\S]*bot\.selectRoom\(rid\)/)
    expect(src).toMatch(/onGroupCreated[\s\S]*bot\.selectRoom\(rid\)/)
  })
})
