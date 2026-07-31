import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { mount } from '@vue/test-utils'

// mock 组件依赖（toast / useConfirm），聚焦 A.4.5 的"后端离线不刷红字"行为
vi.mock('../src/utils/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}))
vi.mock('../src/composables/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}))

if (typeof window !== 'undefined') {
  window.vermes = { onBackendStatus: () => () => {} }
}

const { useBackendConnectionStore } = await import('../src/stores/backendConnection.js')
const { toast } = await import('../src/utils/toast')
const EvolutionPanel = (await import('../src/components/EvolutionPanel.vue')).default

let wrapper = null
afterEach(() => { if (wrapper) { try { wrapper.unmount() } catch (_) {} wrapper = null } })

describe('EvolutionPanel (A.4.5)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    // 所有 fetch 失败（网络错误）→ 触发 _fail 路径
    globalThis.fetch = vi.fn(async () => { throw new Error('Failed to fetch') })
  })

  it('后端离线 → 不弹红色 toast（消除红字刷屏根因）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: false }) // 必须在 mount 前设置
    wrapper = mount(EvolutionPanel)
    await new Promise((r) => setTimeout(r, 60)) // 等 onMounted _refreshAll 的 6 个 fetch 失败
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('后端在线但端点返回 HTTP 500 → 仍弹 toast（真实错误不丢）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    // HTTP 500 不是网络错误，应该弹 toast
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }))
    wrapper = mount(EvolutionPanel)
    await new Promise((r) => setTimeout(r, 60))
    expect(toast.error).toHaveBeenCalled()
  })

  it('后端在线 + 网络错误（Failed to fetch）→ 不弹 toast（看门狗窗口期静默）', async () => {
    const conn = useBackendConnectionStore()
    conn.setStatus({ online: true })
    // 确保用网络错误 mock（beforeEach 已设，但前一个测试可能覆盖了）
    globalThis.fetch = vi.fn(async () => { throw new Error('Failed to fetch') })
    wrapper = mount(EvolutionPanel)
    await new Promise((r) => setTimeout(r, 60))
    expect(toast.error).not.toHaveBeenCalled()
  })
})
