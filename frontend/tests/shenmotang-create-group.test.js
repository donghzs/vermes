/**
 * T3 ⊕ 拉群：选人显式建群（可选 applyBotOrg），从 1:1 预选对方。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ShenmotangCreateGroup from '../src/components/ShenmotangCreateGroup.vue'

const contacts = {
  ok: true,
  contacts: [
    { id: 'p1', name: 'Codex', hue: 210, transport: 'acp', provider: 'openai' },
    { id: 'p2', name: 'Kimi', hue: 20, transport: 'acp', provider: 'moonshot' },
    { id: 'p3', name: 'Qwen', hue: 140, transport: 'acp', provider: 'alibaba' },
  ],
}
const templates = {
  ok: true,
  templates: [
    { key: 'company', title: '公司', roles: [{ key: 'pm', name: '产品经理' }, { key: 'eng', name: '工程师' }] },
  ],
}

describe('T3 ⊕ 拉群', () => {
  let createdBodies

  beforeEach(() => {
    setActivePinia(createPinia())
    createdBodies = []
    globalThis.fetch = vi.fn(async (url, opts) => {
      const u = String(url)
      const method = (opts && opts.method) || 'GET'
      let body = {}
      try { body = opts && opts.body ? JSON.parse(opts.body) : {} } catch { body = {} }
      if (u.includes('/agents/contacts')) return { ok: true, json: async () => contacts }
      if (u.includes('/org/templates')) return { ok: true, json: async () => templates }
      if (u.includes('/org/apply') && method === 'POST') {
        createdBodies.push({ kind: 'applyOrg', body })
        return { ok: true, json: async () => ({ ok: true, pulled: ['p1', 'p2'] }) }
      }
      if (u.includes('/bot/rooms') && method === 'POST') {
        createdBodies.push({ kind: 'createRoom', body })
        return { ok: true, json: async () => ({ ok: true, room_id: 'g1' }) }
      }
      return { ok: true, json: async () => ({}) }
    })
  })

  it('渲染联系人多选 + 群名 + 搭组织开关', async () => {
    const wrapper = mount(ShenmotangCreateGroup, {
      global: { plugins: [createPinia()] },
      props: {},
    })
    await new Promise(r => setTimeout(r, 30))
    const text = wrapper.text()
    expect(text).toContain('拉群')
    expect(text).toContain('Codex')
    expect(text).toContain('Kimi')
    expect(text).toContain('Qwen')
    expect(text).toContain('搭组织')
  })

  it('从 1:1 发起时预选对方并预填群名', async () => {
    const wrapper = mount(ShenmotangCreateGroup, {
      global: { plugins: [createPinia()] },
      props: { preselectId: 'p1', preselectName: 'Codex' },
    })
    await new Promise(r => setTimeout(r, 30))
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    // 第一个是联系人 Codex 的勾选框（搭组织开关在后面）
    expect(checkboxes[0].element.checked).toBe(true)
    expect(wrapper.text()).toContain('1:1')
    expect(wrapper.find('input[type="text"]').element.value).toBe('Codex 群')
  })

  it('显式 createBotRoom 拉人（members 即勾选），不隐式加人', async () => {
    const wrapper = mount(ShenmotangCreateGroup, {
      global: { plugins: [createPinia()] },
      props: { preselectId: 'p1' },
    })
    await new Promise(r => setTimeout(r, 30))
    await wrapper.find('input[type="text"]').setValue('论文攻坚')
    // 再勾 Kimi
    const labels = wrapper.findAll('label')
    const kimi = labels.find(l => l.text().includes('Kimi'))
    await kimi.find('input').setValue(true)
    const btn = wrapper.findAll('button').find(b => b.text().includes('建群'))
    await btn.trigger('click')
    await new Promise(r => setTimeout(r, 30))
    const create = createdBodies.find(b => b.kind === 'createRoom')
    expect(create).toBeTruthy()
    expect(create.body.name).toBe('论文攻坚')
    expect(create.body.members).toEqual(expect.arrayContaining(['p1', 'p2']))
    // 未勾搭组织 → 不应调 applyBotOrg
    expect(createdBodies.some(b => b.kind === 'applyOrg')).toBe(false)
    expect(wrapper.emitted('created')?.[0]?.[0]?.id).toBe('g1')
    expect(wrapper.emitted('close')).toBeTruthy()
  })

  it('勾搭组织时 applyBotOrg 显式坐岗', async () => {
    const wrapper = mount(ShenmotangCreateGroup, {
      global: { plugins: [createPinia()] },
      props: { preselectId: 'p1' },
    })
    await new Promise(r => setTimeout(r, 30))
    await wrapper.find('input[type="text"]').setValue('攻坚组')
    // 开启搭组织（最后一个 checkbox 是搭组织开关）
    const checks = wrapper.findAll('input[type="checkbox"]')
    const orgSwitch = checks[checks.length - 1]
    // 搭组织 label 文本
    const orgLabel = wrapper.findAll('label').find(l => l.text().includes('搭组织'))
    await orgLabel.find('input').setValue(true)
    await new Promise(r => setTimeout(r, 10))
    // 再勾一人坐满 2 岗
    const kimi = wrapper.findAll('label').find(l => l.text().includes('Kimi'))
    await kimi.find('input').setValue(true)
    const btn = wrapper.findAll('button').find(b => b.text().includes('建群'))
    await btn.trigger('click')
    await new Promise(r => setTimeout(r, 40))
    const apply = createdBodies.find(b => b.kind === 'applyOrg')
    expect(apply).toBeTruthy()
    expect(apply.body.roles.length).toBeGreaterThan(0)
    expect(apply.body.roles.every(r => r.profile_id)).toBe(true)
    // 绝不走秘书隐式：createRoom 的 members 仍是显式勾选
    const create = createdBodies.find(b => b.kind === 'createRoom')
    expect(create.body.members.length).toBeGreaterThan(0)
  })

  it('Shenmotang 挂上拉群弹窗（源码断言）', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(path.resolve('src/components/Shenmotang.vue'), 'utf8')
    expect(src).toContain('ShenmotangCreateGroup')
    expect(src).toContain('createGroupOpen')
    expect(src).toContain('onGroupCreated')
  })
})
