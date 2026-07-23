/**
 * test: 切换会话时后台流式保活判定（keepStreamAliveOnSwitch）
 *
 * 回归守护「原会话切走后文本不流式」修复：switchSession 不得清掉仍在后台流式输出
 * 的会话的刷新定时器 / streaming 标记，否则模型长推理、暂不发文本阶段文本会冻住，
 * 表现为「文本不流式但思考还在进行」。判定已抽成纯函数 keepStreamAliveOnSwitch，
 * 这里直接对它做单元验证。
 */
import { describe, it, expect, vi } from 'vitest'

// 与 per-session-isolation.test.js 一致的轻量 mock 装配，使 ./chat 可导入
vi.mock('pinia', () => ({
  defineStore: (name, factory) => {
    let store
    return () => {
      if (!store) store = factory()
      return store
    }
  },
}))
vi.mock('vue', () => {
  const ref = (initial) => ({ value: initial })
  const computed = (fn) => ({ get value() { return fn() } })
  return { ref, computed, watch: vi.fn() }
})
vi.mock('../utils/toast', () => ({ showToast: vi.fn() }))
vi.mock('./chat-session', () => ({
  SESSION_TEMPLATES: [], QUICK_START_SUGGESTIONS: [],
  createSession: vi.fn(), deleteSession: vi.fn(), renameSession: vi.fn(),
  pinSession: vi.fn(), searchAllMessages: vi.fn(), getSessionStats: vi.fn(),
  exportSession: vi.fn(), importSession: vi.fn(), getMessageCount: vi.fn(),
  getFirstMessage: vi.fn(), evictOldSessions: vi.fn(), uid: () => 'test-uid',
  persistMessages: vi.fn(),
}))
vi.mock('./chat-storage', () => ({
  loadFromStorage: () => [], saveToStorage: vi.fn(),
  loadMessagesFromIDB: () => [], fileToBase64: vi.fn(),
}))
vi.mock('./chat-scroll', () => ({
  scheduleScroll: vi.fn(), flushScroll: vi.fn(), setScrollTarget: vi.fn(),
}))
vi.mock('../services/chat-transport', () => ({
  getChatTransport: () => ({ on: vi.fn(), off: vi.fn(), send: vi.fn(), stop: vi.fn(), fetchSnapshot: vi.fn() }),
}))

import { keepStreamAliveOnSwitch } from './chat'

describe('keepStreamAliveOnSwitch', () => {
  const streamingMsg = (sessionId = 's1', extra = {}) => ({
    streaming: true, sessionId, _streamBufTimer: 123, _streamBuffer: 'x', ...extra,
  })

  it('非 streaming 消息 → 不保活（由调用方 filter 已排除，纯防御）', () => {
    expect(keepStreamAliveOnSwitch({ streaming: false, sessionId: 's1' }, { isStreaming: () => true }))
      .toBe(false)
  })

  it('streaming 且 transport 报告该会话仍在活动流 → 保活', () => {
    const transport = { isStreaming: (sid) => sid === 's1' }
    expect(keepStreamAliveOnSwitch(streamingMsg('s1'), transport)).toBe(true)
  })

  it('streaming 但 transport 报告该会话已无活动流（真孤儿）→ 不保活', () => {
    const transport = { isStreaming: () => false }
    expect(keepStreamAliveOnSwitch(streamingMsg('s1'), transport)).toBe(false)
  })

  it('transport 无 isStreaming 方法 → 视为孤儿，不保活（防御性，避免误清活流）', () => {
    const transport = { send: vi.fn() }
    expect(keepStreamAliveOnSwitch(streamingMsg('s1'), transport)).toBe(false)
  })

  it('transport 为 null → 不保活', () => {
    expect(keepStreamAliveOnSwitch(streamingMsg('s1'), null)).toBe(false)
  })

  it('不同会话的 sessionId 不匹配 → 不保活（per-session 精确性）', () => {
    const transport = { isStreaming: (sid) => sid === 's-other' }
    expect(keepStreamAliveOnSwitch(streamingMsg('s1'), transport)).toBe(false)
  })
})
