import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// Mock localStorage + IDB before importing store（与 chat-store.test.js 同套前置）
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
const { useArtifactPanel } = await import('../src/composables/useArtifactPanel.js')

describe('P2-3 打扰预算：单轮自动弹出配额', () => {
  beforeEach(() => {
    localStorageMock.clear()
    const p = useArtifactPanel()
    p.setAutoOpen(true)
    p.resetAutoOpenBudget()
  })

  it('单轮只放行 1 次自动弹出，第 2 次起静默', () => {
    const p = useArtifactPanel()
    expect(p.consumeAutoOpen()).toBe(true)
    expect(p.consumeAutoOpen()).toBe(false)
    expect(p.consumeAutoOpen()).toBe(false)
  })

  it('新一轮（resetAutoOpenBudget）恢复配额', () => {
    const p = useArtifactPanel()
    expect(p.consumeAutoOpen()).toBe(true)
    expect(p.consumeAutoOpen()).toBe(false)
    p.resetAutoOpenBudget()
    expect(p.consumeAutoOpen()).toBe(true)
  })

  it('用户彻底关闭 autoOpen 后永不自动弹', () => {
    const p = useArtifactPanel()
    p.setAutoOpen(false)
    expect(p.consumeAutoOpen()).toBe(false)
    p.resetAutoOpenBudget()
    expect(p.consumeAutoOpen()).toBe(false)
    p.setAutoOpen(true) // 还原，避免污染后续用例
  })

  it('autoOpen 偏好写入 localStorage 持久化', () => {
    const p = useArtifactPanel()
    p.setAutoOpen(false)
    expect(localStorageMock.setItem).toHaveBeenCalledWith('vermes-artifact-auto-open', 'false')
    p.setAutoOpen(true)
  })
})

describe('P2-1 审批队列：不再覆盖未处理的审批', () => {
  beforeEach(() => {
    localStorageMock.clear()
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) }))
  })

  it('pendingApproval 恒为队首，resolve 后下一条自动顶上', async () => {
    const chat = useChatStore()
    chat.approvalQueue = [
      { session_key: 'approval-A', command: 'rm -rf x' },
      { session_key: 'approval-B', command: 'chmod 777 y' },
    ]
    expect(chat.pendingApproval.session_key).toBe('approval-A')

    await chat.resolveApproval('once')
    expect(chat.pendingApproval.session_key).toBe('approval-B')

    await chat.resolveApproval('deny')
    expect(chat.pendingApproval).toBe(null)
    expect(chat.approvalQueue.length).toBe(0)
  })

  it('每条审批都会各自 POST /api/approve（守卫「前一条被覆盖导致卡死」回归）', async () => {
    const chat = useChatStore()
    chat.approvalQueue = [
      { session_key: 'approval-A' },
      { session_key: 'approval-B' },
    ]
    await chat.resolveApproval('once')
    await chat.resolveApproval('session')

    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    const bodies = globalThis.fetch.mock.calls.map(c => JSON.parse(c[1].body))
    // 旧实现单 ref 覆盖：A 的 session_key 丢失，只会发出 B。此断言锁死正确行为。
    expect(bodies.map(b => b.session_key)).toEqual(['approval-A', 'approval-B'])
  })

  it('空队列时 resolveApproval 不发起请求', async () => {
    const chat = useChatStore()
    chat.approvalQueue = []
    await chat.resolveApproval('once')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  it('请求失败也出队，不会对同一条无限追问', async () => {
    const chat = useChatStore()
    globalThis.fetch = vi.fn(() => Promise.reject(new Error('network down')))
    chat.approvalQueue = [{ session_key: 'approval-A' }, { session_key: 'approval-B' }]
    await chat.resolveApproval('once')
    expect(chat.approvalQueue.length).toBe(1)
    expect(chat.pendingApproval.session_key).toBe('approval-B')
  })
})
