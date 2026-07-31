import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'

// 捕获 preload 推送回调（A.4.1 看门狗 → preload onBackendStatus → 此回调）
let capturedCb = null
const unsub = vi.fn()
Object.defineProperty(globalThis, 'window', {
  value: {
    vermes: {
      onBackendStatus: (cb) => { capturedCb = cb; return unsub },
    },
  },
  writable: true,
  configurable: true,
})

const { useBackendConnectionStore } = await import('../src/stores/backendConnection.js')

describe('backendConnection store (A.4.3)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    capturedCb = null
    unsub.mockClear()
  })

  it('默认在线（Web 模式无推送时）', () => {
    const s = useBackendConnectionStore()
    expect(s.online).toBe(true)
    expect(s.isOffline).toBe(false)
    expect(s.statusText).toBe('')
  })

  it('init 订阅 onBackendStatus', () => {
    const s = useBackendConnectionStore()
    s.init()
    expect(typeof capturedCb).toBe('function')
    expect(unsub).not.toHaveBeenCalled() // 未 dispose
  })

  it('收到 offline 推送 → isOffline + 状态文案', () => {
    const s = useBackendConnectionStore()
    s.init()
    capturedCb({ online: false, restarting: false, detail: 'backend unreachable' })
    expect(s.online).toBe(false)
    expect(s.isOffline).toBe(true)
    expect(s.detail).toBe('backend unreachable')
    expect(s.statusText).toBe('backend unreachable')
  })

  it('offline 且无 detail → 兜底文案"后端连接失败"', () => {
    const s = useBackendConnectionStore()
    s.init()
    capturedCb({ online: false, restarting: false, detail: null })
    expect(s.statusText).toBe('后端连接失败')
  })

  it('收到 restarting 推送 → "后端重连中…"', () => {
    const s = useBackendConnectionStore()
    s.init()
    capturedCb({ online: false, restarting: true, detail: 'restarting' })
    expect(s.restarting).toBe(true)
    expect(s.statusText).toBe('后端重连中…')
  })

  it('收到 recovered 推送 → 恢复在线', () => {
    const s = useBackendConnectionStore()
    s.init()
    capturedCb({ online: false, restarting: false, detail: 'backend unreachable' })
    capturedCb({ online: true, restarting: false, detail: 'recovered' })
    expect(s.online).toBe(true)
    expect(s.isOffline).toBe(false)
    expect(s.restarting).toBe(false)
    expect(s.statusText).toBe('')
  })

  it('setStatus 容忍部分字段', () => {
    const s = useBackendConnectionStore()
    s.init()
    capturedCb({ online: false }) // 无 restarting/detail
    expect(s.online).toBe(false)
    expect(s.detail).toBe(null) // 未提供则不覆盖
    expect(s.restarting).toBe(false)
  })

  it('dispose 调用 unsub', () => {
    const s = useBackendConnectionStore()
    s.init()
    s.dispose()
    expect(unsub).toHaveBeenCalled()
  })
})
