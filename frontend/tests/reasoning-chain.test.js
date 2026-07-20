import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SSETransport } from '@/services/chat-transport'

// Mock fetch for SSE
function mockSSEStream(events) {
  const encoder = new TextEncoder()
  const chunks = events.map(e => encoder.encode(`data: ${JSON.stringify(e)}\n\n`))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () => {
          if (i < chunks.length) return { done: false, value: chunks[i++] }
          return { done: true, value: undefined }
        },
        cancel: vi.fn(),
      }),
    },
    headers: { get: () => 'text/event-stream' },
  }
}

describe('SSETransport — reasoning 事件路由', () => {
  let transport

  beforeEach(() => {
    transport = new SSETransport()
    vi.restoreAllMocks()
  })

  it('onReasoning handler 注册后可被 _emit 触发', () => {
    const cb = vi.fn()
    transport.on('s1', { onReasoning: cb })
    transport._emit('s1', 'onReasoning', 'thinking...')
    expect(cb).toHaveBeenCalledWith('thinking...')
  })

  it('reasoning SSE 事件路由到 onReasoning 而非 onMessage', async () => {
    const onMessage = vi.fn()
    const onReasoning = vi.fn()
    transport.on('s1', { onMessage, onReasoning })

    const mockResp = mockSSEStream([
      { type: 'reasoning', content: 'Step 1: ' },
      { type: 'reasoning', content: 'analyzing...' },
      { type: 'delta', content: 'Hello' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', { messages: [], model: 'm', provider: 'p' })

    // onReasoning 应被调用两次，累加推理内容
    expect(onReasoning).toHaveBeenCalledTimes(2)
    expect(onReasoning).toHaveBeenNthCalledWith(1, 'Step 1: ')
    expect(onReasoning).toHaveBeenNthCalledWith(2, 'analyzing...')

    // onMessage 只应被调用一次（delta 事件）
    expect(onMessage).toHaveBeenCalledTimes(1)
    expect(onMessage).toHaveBeenCalledWith({ type: 'delta', content: 'Hello' })
  })

  it('reasoning 事件 content 为空时仍正确路由', async () => {
    const onReasoning = vi.fn()
    const onMessage = vi.fn()
    transport.on('s1', { onMessage, onReasoning })

    const mockResp = mockSSEStream([
      { type: 'reasoning', content: '' },
      { type: 'done' },
    ])
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', { messages: [], model: 'm', provider: 'p' })

    expect(onReasoning).toHaveBeenCalledWith('')
    expect(onMessage).not.toHaveBeenCalled()
  })
})
