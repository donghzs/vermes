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

// 捕获每次 fetch 的请求体，便于断言 delivery_id 语义
function captureFetch(handler) {
  const bodies = []
  globalThis.fetch = vi.fn(async (url, opts) => {
    if (opts && opts.body) bodies.push(JSON.parse(opts.body))
    const resp = handler ? await handler(url, opts, bodies) : { ok: true, json: async () => ({ ok: true }) }
    return resp
  })
  return bodies
}

describe('sendFromDesktopAPI (A.4.4 + P0.5/D1)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn()
  })

  it('正常发送：body 携带 delivery_id 与 text', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    const bodies = captureFetch(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    expect(bodies).toHaveLength(1)
    expect(bodies[0].text).toBe('hi')
    // P0.5: 幂等键随请求发出
    expect(typeof bodies[0].delivery_id).toBe('string')
    expect(bodies[0].delivery_id.length).toBeGreaterThan(0)
  })

  it('网络错误后重试成功（吸收自愈窗口）', async () => {
    let calls = 0
    const bodies = captureFetch(async () => {
      calls++
      if (calls === 1) throw new Error('Failed to fetch')
      return { ok: true, json: async () => ({ ok: true, session_id: 's1', state: 'pending' }) }
    })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    expect(calls).toBe(2) // 1 失败 + 1 重试
    // P0.5/D1: 重试全程复用同一 delivery_id（后端据此幂等去重）
    expect(bodies).toHaveLength(2)
    expect(bodies[0].delivery_id).toBe(bodies[1].delivery_id)
  })

  it('持续网络错误 → 重试耗尽返回 ok:false（detail 含 Failed to fetch）', async () => {
    let calls = 0
    const bodies = captureFetch(async () => { calls++; throw new Error('Failed to fetch') })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(false)
    expect(res.pending).toBeFalsy()
    expect(res.detail).toContain('Failed to fetch')
    expect(calls).toBe(3) // 初始 + 2 重试
    // 3 次重试仍复用同一 delivery_id
    expect(bodies.map((b) => b.delivery_id).every((v, i, a) => v === a[0])).toBe(true)
  })

  it('后端已知离线 → 返回 pending 且不发请求（不刷红字）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: false })
    const bodies = captureFetch(async () => { throw new Error('Failed to fetch') })
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(false)
    expect(res.pending).toBe(true)
    expect(bodies).toHaveLength(0) // 不发徒劳请求（也未生成 delivery_id 上送）
  })

  it('后端在线 → 正常发一次请求', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    const bodies = captureFetch(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    expect(bodies).toHaveLength(1)
  })

  it('后端幂等返回 terminal/failed 时原样透传（供 sendToChannelSession 跳过 2s 轮询）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    const bodies = captureFetch(async () => ({
      ok: true,
      json: async () => ({
        ok: true, session_id: 's1', state: 'failed',
        delivery_id: 'dlv-x', idempotent: true, terminal: true, error: 'gateway down',
      }),
    }))
    const res = await sendFromDesktopAPI('s1', 'hi')
    expect(res.ok).toBe(true)
    // P2 terminal 标记合同：sendFromDesktopAPI 必须把后端字段透传给调用方
    expect(res.idempotent).toBe(true)
    expect(res.terminal).toBe(true)
    expect(res.state).toBe('failed')
    expect(res.error).toBe('gateway down')
  })
})
