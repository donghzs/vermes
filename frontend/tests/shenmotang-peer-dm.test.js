/**
 * T2 1:1 默认面：选中联系人 → 右栏 peer_dm 会话（消息流 / 头像 / 状态）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import ShenmotangPeerDm from '../src/components/ShenmotangPeerDm.vue'

const contact = {
  id: 'p1',
  name: 'Codex',
  hue: 210,
  transport: 'acp',
  provider: 'openai',
  has_api_key: true,
}

const timeline = {
  ok: true,
  timeline: [
    { id: 'm1', author_type: 'user', author_ref: null, content: '帮我盯一下 CI 挂了' },
    { id: 'm2', author_type: 'system', author_ref: null, content: '[私聊 @董董 ↔ @Codex]' },
    { id: 'm3', author_type: 'agent', author_ref: 'p1', content: '已看，是 pytest 超时…' },
  ],
}

describe('T2 1:1 默认面', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(async (url, opts) => {
      const u = String(url)
      const method = (opts && opts.method) || 'GET'
      let body = {}
      try { body = opts && opts.body ? JSON.parse(opts.body) : {} } catch { body = {} }
      if (u.includes('/bot/rooms') && u.includes('/timeline')) {
        return { ok: true, json: async () => timeline }
      }
      if (u.includes('/bot/rooms') && method === 'POST' && !u.includes('/messages') && !u.includes('/peer')) {
        return { ok: true, json: async () => ({ ok: true, room_id: 'dm-p1' }) }
      }
      if (u.includes('/messages') && method === 'POST') {
        const sent = body.text || body.message || ''
        return {
          ok: true,
          json: async () => ({
            ok: true,
            timeline: [...timeline.timeline, { id: 'm4', author_type: 'user', author_ref: null, content: sent }],
          }),
        }
      }
      if (u.includes('/peer') && method === 'POST') {
        return { ok: true, json: async () => ({ ok: true, result: '[peer-reply] 收到', timeline: timeline.timeline }) }
      }
      if (u.includes('/bot/rooms')) {
        return { ok: true, json: async () => ({ ok: true, rooms: [] }) }
      }
      return { ok: true, json: async () => ({}) }
    })
  })

  it('渲染 1:1 头像 / 状态 / 消息流', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangPeerDm, {
      global: { plugins: [router, createPinia()] },
      props: { contact },
    })
    await new Promise(r => setTimeout(r, 30))
    const text = wrapper.text()
    expect(text).toContain('Codex')
    expect(text).toContain('已接入')
    expect(text).toContain('1:1 私聊')
    expect(text).toContain('帮我盯一下 CI 挂了')
    expect(text).toContain('已看，是 pytest 超时…')
    // 头像首字
    expect(text).toContain('C')
  })

  it('发送用户消息走房间消息路径（默认用户→agent）', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangPeerDm, {
      global: { plugins: [router, createPinia()] },
      props: { contact },
    })
    await new Promise(r => setTimeout(r, 30))
    await wrapper.find('textarea').setValue('再看一眼')
    expect(wrapper.find('textarea').element.value).toBe('再看一眼')
    const sendBtn = wrapper.findAll('button').find(b => b.text().includes('发送'))
    expect(sendBtn.attributes('disabled')).toBeUndefined()
    await sendBtn.trigger('click')
    await new Promise(r => setTimeout(r, 50))
    const calls = globalThis.fetch.mock.calls.map(c => String(c[0]))
    expect(calls.some(u => u.includes('/messages'))).toBe(true)
    expect(wrapper.text()).toContain('再看一眼')
  })

  it('peerFrom 时走 peer_dm 端点（A2A）', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/shenmotang', component: { template: '<div/>' } }],
    })
    const wrapper = mount(ShenmotangPeerDm, {
      global: { plugins: [router, createPinia()] },
      props: { contact, peerFrom: 'p2' },
    })
    await new Promise(r => setTimeout(r, 30))
    await wrapper.find('textarea').setValue('私聊一下')
    const sendBtn = wrapper.findAll('button').find(b => b.text().includes('发送'))
    await sendBtn.trigger('click')
    await new Promise(r => setTimeout(r, 30))
    const calls = globalThis.fetch.mock.calls.map(c => String(c[0]))
    expect(calls.some(u => u.includes('/peer'))).toBe(true)
  })

  it('Shenmotang 选中联系人后挂上 1:1 面（源码断言）', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(
      path.resolve('src/components/Shenmotang.vue'),
      'utf8',
    )
    expect(src).toContain('ShenmotangPeerDm')
    expect(src).toContain('activeContact')
    // T2：选联系人不再只踢去 hall 群聊主区
    expect(src).toMatch(/onSelectContact[\s\S]*activeContact\.value\s*=\s*c/)
  })

  it('P0：订阅 room_update 以接收秘书/直答推送', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(
      path.resolve('src/components/ShenmotangPeerDm.vue'),
      'utf8',
    )
    expect(src).toContain("addEventListener('vermes:room_update'")
    expect(src).toContain("removeEventListener('vermes:room_update'")
    expect(src).toContain('onRoomUpdate')
  })
})
