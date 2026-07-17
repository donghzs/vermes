import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { scheduleScroll, flushScroll, setScrollTarget } from '@/stores/chat-scroll'

describe('chat-scroll', () => {
  let mockEl

  beforeEach(() => {
    mockEl = {
      scrollHeight: 1000,
      scrollTop: 0,
      clientHeight: 800,
    }
    setScrollTarget(mockEl)
    vi.useFakeTimers()
  })

  afterEach(() => {
    setScrollTarget(null)
    vi.useRealTimers()
  })

  it('scheduleScroll scrolls to bottom when near bottom', () => {
    mockEl.scrollTop = 100 // 1000 - 100 - 800 = 100 < 200 → near bottom
    scheduleScroll()
    vi.runAllTimers()
    expect(mockEl.scrollTop).toBe(1000)
  })

  it('scheduleScroll does NOT scroll when not near bottom', () => {
    mockEl.scrollTop = 0 // 1000 - 0 - 800 = 200, not < 200
    scheduleScroll()
    vi.runAllTimers()
    expect(mockEl.scrollTop).toBe(0)
  })

  it('flushScroll cancels pending RAF and scrolls immediately', () => {
    mockEl.scrollTop = 100
    scheduleScroll() // queue RAF
    flushScroll() // cancel RAF and scroll now
    expect(mockEl.scrollTop).toBe(1000)
  })

  it('scheduleScroll deduplicates RAF requests', () => {
    mockEl.scrollTop = 100
    scheduleScroll()
    scheduleScroll() // second call should not create another RAF
    vi.runAllTimers()
    expect(mockEl.scrollTop).toBe(1000)
  })

  it('flushScroll without target is safe no-op', () => {
    setScrollTarget(null)
    expect(() => flushScroll()).not.toThrow()
  })

  it('scheduleScroll without target is safe no-op', () => {
    setScrollTarget(null)
    expect(() => {
      scheduleScroll()
      vi.runAllTimers()
    }).not.toThrow()
  })
})
