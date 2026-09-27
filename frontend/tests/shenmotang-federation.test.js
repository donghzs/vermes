/**
 * T5 联邦可见性：@B 接力 / A2A peer / 跨群作用域 — 只读当前会话 timeline。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import {
  parseFederationSignals,
  latestFederation,
  federationLabel,
  federationScopeLabel,
} from '../src/utils/federation'
import ShenmotangFederationBar from '../src/components/ShenmotangFederationBar.vue'

const timeline = [
  { id: 1, author_type: 'user', content: '帮我盯 CI' },
  {
    id: 2,
    author_type: 'system',
    content: '[私聊 @Codex ↔ @Kimi]（hermes peer / A2A）\n已看',
    created_at: '2026-09-27T10:00:00Z',
  },
  { id: 3, author_type: 'agent', author_ref: 'p2', content: '已看，pytest 超时' },
  {
    id: 4,
    author_type: 'system',
    content: '（接力）@Codex 在回复中点名了你，请接力处理相关部分。',
    created_at: '2026-09-27T10:02:00Z',
  },
]

describe('T5 联邦信号解析', () => {
  it('解析 peer 与接力，附 transport/scope', () => {
    const sigs = parseFederationSignals(timeline, { roomId: 'dm-p1' })
    expect(sigs.length).toBe(2)
    expect(sigs[0].kind).toBe('peer')
    expect(sigs[0].from).toBe('Codex')
    expect(sigs[0].to).toBe('Kimi')
    expect(sigs[0].transport).toBe('A2A')
    expect(sigs[0].scope).toBe('room')
    expect(sigs[0].roomId).toBe('dm-p1')
    expect(sigs[1].kind).toBe('relay')
    expect(sigs[1].from).toBe('Codex')
    expect(sigs[1].transport).toBe('relay')
  })

  it('跨群文案 → scope=cross-group', () => {
    const tl = [{
      id: 9,
      author_type: 'system',
      content: '[私聊 @甲 ↔ @乙]（hermes peer / A2A）跨群联邦：联系人池即可私聊，不限同群',
    }]
    const sigs = parseFederationSignals(tl, { roomId: 'r1' })
    expect(sigs[0].scope).toBe('cross-group')
    expect(federationScopeLabel(sigs[0].scope)).toBe('跨群联邦')
    expect(federationLabel(sigs[0])).toContain('跨群')
  })

  it('空/无关消息不产生信号（不放大可见面）', () => {
    expect(parseFederationSignals([], {})).toEqual([])
    expect(parseFederationSignals([{ content: '普通聊天 #1 【任务 #2】' }], {})).toEqual([])
    expect(latestFederation([{ content: 'hi' }])).toBeNull()
  })

  it('只认传入 timeline（scope 边界，不跨房拉取）', () => {
    // 模拟「别房信号」根本不该出现在结果里——因为根本不传进来
    const onlyMine = parseFederationSignals(timeline, { roomId: 'dm-p1' })
    expect(onlyMine.every(s => s.roomId === 'dm-p1')).toBe(true)
    expect(latestFederation(timeline, { roomId: 'dm-p1' }).kind).toBe('relay')
  })
})

describe('T5 联邦状态条', () => {
  it('渲染最近活动 + 展开事件列表', async () => {
    const wrapper = mount(ShenmotangFederationBar, {
      props: { timeline, roomId: 'dm-p1' },
    })
    await new Promise(r => setTimeout(r, 10))
    const text = wrapper.text()
    expect(text).toContain('联邦')
    expect(text).toContain('Codex')
    expect(text).toContain('公开接力')
    const btn = wrapper.find('button')
    await btn.trigger('click')
    await new Promise(r => setTimeout(r, 10))
    expect(wrapper.text()).toContain('A2A')
    expect(wrapper.text()).toContain('Kimi')
    expect(wrapper.text()).toContain('仅当前会话可见')
  })

  it('无信号时不占位', async () => {
    const wrapper = mount(ShenmotangFederationBar, {
      props: { timeline: [{ content: '你好' }], roomId: 'r' },
    })
    expect(wrapper.text()).not.toContain('联邦')
  })

  it('PeerDm 挂上联邦条（源码断言）', async () => {
    const fs = await import('node:fs')
    const path = await import('node:path')
    const src = fs.readFileSync(path.resolve('src/components/ShenmotangPeerDm.vue'), 'utf8')
    expect(src).toContain('ShenmotangFederationBar')
    expect(src).toContain(':timeline="messages"')
  })
})
