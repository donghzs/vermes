/**
 * T16①: approval queue is session-aware — count and pending prefer current session.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('../src/services/api.js', () => ({
  fetchWithAuth: vi.fn(),
  withSessionToken: (h) => h,
}))

describe('approvalQueue 会话分片（T16①）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
  })

  it('pendingApproval 优先本会话；计数按本会话', async () => {
    const { useChatStore } = await import('../src/stores/chat.js')
    const chat = useChatStore()
    chat.currentSessionId = 's-own'
    chat.approvalQueue = [
      { session_key: 'gui-s-other', session_id: 's-other', timestamp: 1, command: 'other' },
      { session_key: 'gui-s-own', session_id: 's-own', timestamp: 2, command: 'own-a' },
      { session_key: 'gui-s-own', session_id: 's-own', timestamp: 3, command: 'own-b' },
    ]
    expect(chat.pendingApproval.command).toBe('own-a')
    expect(chat.currentSessionApprovals).toHaveLength(2)
  })

  it('resolve 只出队当前展示的那条，不误删别会话同 key 前缀', async () => {
    const { useChatStore } = await import('../src/stores/chat.js')
    const chat = useChatStore()
    chat.currentSessionId = 's-own'
    chat.approvalQueue = [
      { session_key: 'gui-s-other', session_id: 's-other', timestamp: 1, command: 'other' },
      { session_key: 'gui-s-own', session_id: 's-own', timestamp: 2, command: 'own-a' },
    ]
    await chat.resolveApproval('once')
    expect(chat.approvalQueue.map(a => a.command)).toEqual(['other'])
    const body = JSON.parse(globalThis.fetch.mock.calls[0][1].body)
    expect(body.session_key).toBe('gui-s-own')
  })

  it('本会话无审批时回落全局队首（不卡死弹窗）', async () => {
    const { useChatStore } = await import('../src/stores/chat.js')
    const chat = useChatStore()
    chat.currentSessionId = 's-own'
    chat.approvalQueue = [
      { session_key: 'gui-s-other', session_id: 's-other', timestamp: 1, command: 'other' },
    ]
    expect(chat.pendingApproval.command).toBe('other')
  })
})
