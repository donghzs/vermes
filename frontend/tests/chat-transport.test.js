import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SSETransport } from '@/services/chat-transport'

describe('SSETransport', () => {
  let transport

  beforeEach(() => {
    transport = new SSETransport()
    vi.restoreAllMocks()
  })

  it('constructs with empty base url by default', () => {
    const t = new SSETransport()
    expect(t._baseUrl).toBe('')
  })

  it('constructs with custom base url', () => {
    const t = new SSETransport('https://api.example.com')
    expect(t._baseUrl).toBe('https://api.example.com')
  })

  it('on() registers handlers for a session', () => {
    const handlers = { onMessage: vi.fn(), onDone: vi.fn(), onError: vi.fn() }
    transport.on('s1', handlers)
    // Internal: handlers map should contain the session
    expect(transport._handlers.has('s1')).toBe(true)
  })

  it('off() removes handlers for a session', () => {
    transport.on('s1', { onMessage: vi.fn() })
    transport.off('s1')
    expect(transport._handlers.has('s1')).toBe(false)
  })

  it('_emit calls registered handler', () => {
    const cb = vi.fn()
    transport.on('s1', { onMessage: cb })
    transport._emit('s1', 'onMessage', { content: 'hello' })
    expect(cb).toHaveBeenCalledWith({ content: 'hello' })
  })

  it('_emit does nothing for unregistered session', () => {
    expect(() => transport._emit('unknown', 'onMessage', {}).not.toThrow()
    )
  })

  it('_emit does nothing when handler is missing', () => {
    transport.on('s1', { onMessage: vi.fn() })
    expect(() => transport._emit('s1', 'onDone', {})).not.toThrow()
  })

  it('stop() does not throw when no active controller', async () => {
    await expect(transport.stop('s1')).resolves.not.toThrow()
  })

  it('stop() aborts active controller', async () => {
    // Create a fake controller
    const ac = new AbortController()
    const abortSpy = vi.spyOn(ac, 'abort')
    transport._controllers.set('s1', ac)
    await transport.stop('s1')
    expect(abortSpy).toHaveBeenCalled()
    expect(transport._controllers.has('s1')).toBe(false)
  })
})
