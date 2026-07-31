import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// window mock（stateDBHeaders 读 __VERMES_SESSION_TOKEN__，backendConnection 读 vermes.onBackendStatus）
Object.defineProperty(globalThis, 'window', {
  value: { __VERMES_SESSION_TOKEN__: '', vermes: { onBackendStatus: () => () => {} } },
  writable: true,
  configurable: true,
})

const { useBackendConnectionStore } = await import('../src/stores/backendConnection.js')
const { sendFromDesktopAPI } = await import('../src/stores/chat-storage.js')

describe('sendFromDesktopAPI (A.4.4)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn()
  })

  it('网络错误后重试成功（吸收自愈窗口）', async () => {
    let calls = 0
    globalThis.fetch = vi.fn(async () => {
      calls++
      if (calls === 1) throw new Error('Failed to fetch')
      return { ok: true, json: async () => ({ ok: true, session_id: 's1', state: 'pending' }) }
    })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    expect(calls).toBe(2) // 1 失败 + 1 重试
  })

  it('持续网络错误 → 重试耗尽返回 ok:false（detail 含 Failed to fetch）', async () => {
    let calls = 0
    globalThis.fetch = vi.fn(async () => { calls++; throw new Error('Failed to fetch') })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(false)
    expect(res.pending).toBeFalsy()
    expect(res.detail).toContain('Failed to fetch')
    expect(calls).toBe(3) // 初始 + 2 重试
  })

  it('后端已知离线 → 返回 pending 且不发请求（不刷红字）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: false })
    let calls = 0
    globalThis.fetch = vi.fn(async () => { calls++; throw new Error('Failed to fetch') })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(false)
    expect(res.pending).toBe(true)
    expect(calls).toBe(0) // 不发徒劳请求
  })

  it('后端在线 → 正常发一次请求', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })
})
