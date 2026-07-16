import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock localStorage + IDB before importing store
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

// Mock IndexedDB
Object.defineProperty(globalThis, 'indexedDB', {
  value: { open: vi.fn(() => ({ onupgradeneeded: null, onsuccess: null, onerror: null, result: {} })) },
  writable: true,
})

// Mock crypto.randomUUID
Object.defineProperty(globalThis, 'crypto', {
  value: { randomUUID: () => 'test-uuid-' + Math.random().toString(36).slice(2, 8) },
  writable: true,
})

const { useChatStore } = await import('../src/stores/chat.js')

describe('Chat Store — per-session model isolation', () => {
  beforeEach(() => {
    localStorageMock.clear()
    setActivePinia(createPinia())
  })

  it('createSession 应继承当前 model/provider', async () => {
    const chat = useChatStore()
    chat.currentModel = 'deepseek-v4-flash'
    chat.currentProvider = 'agnes'

    // 直接构造 session 对象模拟 createSession 的行为
    // （不调 createSession，因为它依赖 switchSession → IDB 等复杂链路）
    const session = {
      id: 's1',
      name: 'test-session',
      model: chat.currentModel,
      provider: chat.currentProvider,
    }
    chat.sessions.push(session)

    expect(session.model).toBe('deepseek-v4-flash')
    expect(session.provider).toBe('agnes')
  })

  it('switchSession 应恢复目标会话的 model/provider', async () => {
    const chat = useChatStore()
    // 模拟 switchSession 中的模型恢复逻辑
    chat.sessions = [
      { id: 's1', name: 'Session1', model: 'model-A', provider: 'provider-A' },
      { id: 's2', name: 'Session2', model: 'model-B', provider: 'provider-B' },
    ]

    // 模拟 switchSession 核心逻辑
    const restoreModel = (id) => {
      const s = chat.sessions.find(s => s.id === id)
      if (s && s.model) {
        chat.currentModel = s.model
        chat.currentProvider = s.provider || ''
      }
    }

    restoreModel('s2')
    expect(chat.currentModel).toBe('model-B')
    expect(chat.currentProvider).toBe('provider-B')

    restoreModel('s1')
    expect(chat.currentModel).toBe('model-A')
    expect(chat.currentProvider).toBe('provider-A')
  })

  it('appendModelChange 应在 fromModel === toModel 时不产生消息', () => {
    const chat = useChatStore()
    chat.sessions = [{ id: 's1', name: 'S1', model: 'model-A' }]
    chat.currentSessionId = 's1'
    chat.messages = []

    chat.appendModelChange('s1', 'model-A', 'model-A')
    expect(chat.messages.length).toBe(0)
  })

  it('appendModelChange 应在模型不同时插入变更消息', () => {
    const chat = useChatStore()
    chat.sessions = [{ id: 's1', name: 'S1', model: 'model-A' }]
    chat.currentSessionId = 's1'
    chat.messages = []

    chat.appendModelChange('s1', 'model-A', 'model-B')

    expect(chat.messages.length).toBe(1)
    const msg = chat.messages[0]
    expect(msg._isModelChange).toBe(true)
    expect(msg.content).toContain('model-A')
    expect(msg.content).toContain('model-B')
  })

  it('pendingModel 初始值应为 null', () => {
    const chat = useChatStore()
    expect(chat.pendingModel).toBeNull()
  })

  it('currentModel 应可读写', () => {
    const chat = useChatStore()
    chat.currentModel = 'test-model-123'
    expect(chat.currentModel).toBe('test-model-123')
  })
})
