import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// localStorage mock（store import 前注入）
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

// IndexedDB mock
Object.defineProperty(globalThis, 'indexedDB', {
  value: { open: vi.fn(() => ({ onupgradeneeded: null, onsuccess: null, onerror: null, result: {} })) },
  writable: true,
})
Object.defineProperty(globalThis, 'crypto', {
  value: { randomUUID: () => 'test-uuid-' + Math.random().toString(36).slice(2, 8) },
  writable: true,
})

// fetch mock：进化简报/渠道列表请求都走它，返回安全空响应
globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))

// 只覆写 loadMessagesFromIDB 为「永不 resolve」的 Promise，证明 hydrate 不阻塞；
// 其余 chat-storage 导出保持真实，避免破坏 switchSession 的其他依赖。
vi.mock('../src/stores/chat-storage.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    loadMessagesFromIDB: vi.fn(() => new Promise(() => {})), // 永不 resolve
    loadChannelMessagesFromAPI: vi.fn(() => Promise.resolve([])),
  }
})

const { useChatStore } = await import('../src/stores/chat.js')

describe('Chat Store — P0-2 非阻塞首屏 hydrate', () => {
  beforeEach(() => {
    localStorageMock.clear()
    setActivePinia(createPinia())
  })

  it('switchSession(hydrate:false) 不被永不 resolve 的 IDB 阻塞，立即返回', async () => {
    const chat = useChatStore()
    chat.sessions = [{ id: 's1', name: 'S1', model: 'm', provider: 'p' }]
    localStorageMock.setItem('vermes-last-session', 's1')

    const t0 = Date.now()
    await chat.switchSession('s1', { hydrate: false })
    const elapsed = Date.now() - t0

    expect(chat.currentSessionId).toBe('s1')
    // 关键断言：IDB 历史永不就绪，但 switchSession 应在毫秒级返回（不挡首屏）
    expect(elapsed).toBeLessThan(100)
  })

  it('init() 在 IDB 历史未就绪时仍快速 resolve（首屏可交互）', async () => {
    const chat = useChatStore()
    chat.sessions = [{ id: 's1', name: 'S1', model: 'm', provider: 'p' }]
    localStorageMock.setItem('vermes-last-session', 's1')

    const t0 = Date.now()
    await chat.init()
    const elapsed = Date.now() - t0

    expect(chat.currentSessionId).toBe('s1')
    // init 不应被 IDB 阻塞：即便历史永不加载，首屏也应快速可交互
    expect(elapsed).toBeLessThan(150)
  })

  it('正常 switchSession（hydrate 默认 true）仍等待历史（行为不变）', async () => {
    const chat = useChatStore()
    chat.sessions = [{ id: 's1', name: 'S1', model: 'm', provider: 'p' }]
    // 恢复正常 IDB
    const { loadMessagesFromIDB } = await import('../src/stores/chat-storage.js')
    loadMessagesFromIDB.mockImplementation(() => Promise.resolve([{ id: 'm1', role: 'user', content: 'hi' }]))

    await chat.switchSession('s1') // hydrate 默认 true
    expect(chat.messages.some(m => m.id === 'm1')).toBe(true)
  })
})
