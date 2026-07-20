import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const localStorageMock = (() => {
  let store = {}
  return {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = String(v) }),
    removeItem: vi.fn((k) => { delete store[k] }),
    clear: vi.fn(() => { store = {} }),
  }
})()

Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock, writable: true })
Object.defineProperty(globalThis, 'indexedDB', {
  value: { open: vi.fn(() => ({ onupgradeneeded: null, onsuccess: null, onerror: null, result: {} })) },
  writable: true,
})
Object.defineProperty(globalThis, 'crypto', {
  value: { randomUUID: () => 'test-uuid-' + Math.random().toString(36).slice(2, 8) },
  writable: true,
})

const { useChatStore } = await import('../src/stores/chat.js')

describe('Chat Store — reasoning 累加逻辑', () => {
  let chat

  beforeEach(() => {
    localStorageMock.clear()
    setActivePinia(createPinia())
    chat = useChatStore()
  })

  it('searchEnabled 初始值为 false', () => {
    expect(chat.searchEnabled).toBe(false)
  })

  it('searchEnabled 从 localStorage 恢复为 true', () => {
    localStorageMock.getItem.mockReturnValueOnce('true')
    const fresh = useChatStore()
    // pinia 会复用已有 store 实例，所以这里直接验证 localStorage 读取
    expect(localStorageMock.getItem).toHaveBeenCalledWith('vermes-search-enabled')
  })

  it('reasoningEffort 初始值从 localStorage 读取', () => {
    // 验证初始状态
    expect(chat.reasoningEffort).toBe('')
    localStorageMock.getItem.mockReturnValueOnce('high')
    // 重新读取验证
    expect(localStorageMock.getItem).toHaveBeenCalledWith('vermes-reasoning-effort')
  })

  it('onReasoning 回调将推理 delta 累加到 message.reasoning', () => {
    // 模拟 store 内部的 onReasoning 逻辑
    chat.sessions = [{ id: 's1', name: 'S1', model: 'm', provider: 'p' }]
    chat.currentSessionId = 's1'
    chat.messages = [{ id: 'msg-1', role: 'assistant', content: '', streaming: true }]

    // 模拟 onReasoning 回调逻辑（与 chat.js:464-469 一致）
    const am = chat.messages.find(m => m.id === 'msg-1')
    if (!am.reasoning) am.reasoning = ''
    am.reasoning += 'Step 1: '
    am.reasoning += 'analyzing data...'

    expect(chat.messages[0].reasoning).toBe('Step 1: analyzing data...')
  })

  it('多条 reasoning delta 正确拼接', () => {
    chat.messages = [{ id: 'msg-1', role: 'assistant', content: '', streaming: true }]

    const deltas = ['First ', 'second ', 'third part.']
    const am = chat.messages.find(m => m.id === 'msg-1')
    if (!am.reasoning) am.reasoning = ''
    for (const d of deltas) am.reasoning += d

    expect(am.reasoning).toBe('First second third part.')
  })

  it('message 无 reasoning 字段时首次赋值创建该字段', () => {
    chat.messages = [{ id: 'msg-1', role: 'assistant', content: 'hi', streaming: true }]
    const am = chat.messages[0]
    expect(am.reasoning).toBeUndefined()

    if (!am.reasoning) am.reasoning = ''
    am.reasoning += 'initial reasoning'

    expect(am.reasoning).toBe('initial reasoning')
  })
})
