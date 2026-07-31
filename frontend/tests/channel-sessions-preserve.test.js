import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

const localStorageMock = (() => {
  let s = {}
  return {
    getItem: vi.fn((k) => s[k] ?? null),
    setItem: vi.fn((k, v) => { s[k] = String(v) }),
    removeItem: vi.fn((k) => { delete s[k] }),
    clear: vi.fn(() => { s = {} }),
  }
})()
Object.defineProperty(globalThis, 'localStorage', { value: localStorageMock, writable: true })
Object.defineProperty(globalThis, 'indexedDB', {
  value: { open: vi.fn(() => ({ onupgradeneeded: null, onsuccess: null, onerror: null, result: {} })) },
  writable: true,
})
Object.defineProperty(globalThis, 'crypto', {
  value: { randomUUID: () => 'u' + Math.random().toString(36).slice(2, 8) },
  writable: true,
})
globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))

// mock chat-storage：listChannelSessionsFromAPI 抛错（模拟后端不可达），其余保持真实
vi.mock('../src/stores/chat-storage.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    listChannelSessionsFromAPI: vi.fn(async () => { throw new Error('HTTP 500') }),
  }
})

const { useChatStore } = await import('../src/stores/chat.js')

describe('loadChannelSessions (A.4.6 Bug D)', () => {
  beforeEach(() => {
    localStorageMock.clear()
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
  })

  it('后端不可达时不清空旧渠道会话列表（避免"会话凭空消失"）', async () => {
    const chat = useChatStore()
    chat.channelSessions = [
      { id: 'c1', name: '旧会话1', channel: true, source: 'telegram' },
      { id: 'c2', name: '旧会话2', channel: true, source: 'qq' },
    ]
    await chat.loadChannelSessions() // 内部 listChannelSessionsFromAPI 抛错
    expect(chat.channelSessions.length).toBe(2)
    expect(chat.channelSessions.map((s) => s.id)).toEqual(['c1', 'c2'])
  })
})
