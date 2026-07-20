import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SSETransport } from '@/services/chat-transport'

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

describe('SSETransport — web_search 参数传递', () => {
  let transport

  beforeEach(() => {
    transport = new SSETransport()
    vi.restoreAllMocks()
  })

  it('web_search=true 时请求体包含 web_search', async () => {
    const mockResp = mockSSEStream([{ type: 'done' }])
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', {
      messages: [{ role: 'user', content: 'hi' }],
      model: 'm',
      provider: 'p',
      web_search: true,
    })

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.web_search).toBe(true)
  })

  it('web_search=undefined 时请求体不含 web_search', async () => {
    const mockResp = mockSSEStream([{ type: 'done' }])
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', {
      messages: [{ role: 'user', content: 'hi' }],
      model: 'm',
      provider: 'p',
    })

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.web_search).toBeUndefined()
  })

  it('web_search=false 时请求体不含 web_search（falsy 不传）', async () => {
    const mockResp = mockSSEStream([{ type: 'done' }])
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', {
      messages: [{ role: 'user', content: 'hi' }],
      model: 'm',
      provider: 'p',
      web_search: false,
    })

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.web_search).toBeUndefined()
  })

  it('reasoning_effort + web_search 可同时传递', async () => {
    const mockResp = mockSSEStream([{ type: 'done' }])
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResp)

    await transport.send('s1', {
      messages: [{ role: 'user', content: 'hi' }],
      model: 'm',
      provider: 'p',
      reasoning_effort: 'high',
      web_search: true,
    })

    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.reasoning_effort).toBe('high')
    expect(body.web_search).toBe(true)
  })
})
