import { describe, it, expect, beforeEach } from 'vitest'
import { useNotifications, CATEGORIES } from '../src/stores/notifications.js'

describe('U-P0-5 notification center', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('CATEGORIES 覆盖 harness/change/achievement/send_fail', () => {
    const ids = CATEGORIES.map(c => c.id)
    expect(ids).toEqual(expect.arrayContaining(['harness', 'change', 'achievement', 'send_fail']))
  })

  it('notify 入账并累计未读；markAllRead 清零', () => {
    const n = useNotifications()
    n.clearAll()
    n.notify({ category: 'harness', title: 'Harness 降级', key: 'h1' })
    n.notify({ category: 'send_fail', title: '发送失败', key: 's1' })
    expect(n.unreadCount.value).toBe(2)
    n.markAllRead()
    expect(n.unreadCount.value).toBe(0)
  })

  it('mute 后同类事件不入账（关键事件可关）', () => {
    const n = useNotifications()
    n.clearAll()
    n.setPref('harness', false)
    const r = n.notify({ category: 'harness', title: 'x', key: 'm1' })
    expect(r).toBeNull()
    expect(n.unreadCount.value).toBe(0)
  })

  it('同 key 去重，避免轮询刷屏', () => {
    const n = useNotifications()
    n.clearAll()
    n.setPref('harness', true)
    n.notify({ category: 'harness', title: 'a', key: 'dup' })
    n.notify({ category: 'harness', title: 'a', key: 'dup' })
    expect(n.unreadCount.value).toBe(1)
  })

  it('notifyHarnessDegraded：无信号/未降级不编造通知', () => {
    const n = useNotifications()
    n.clearAll(); n.setPref('harness', true)
    n.notifyHarnessDegraded({ signal: 'unavailable' })
    n.notifyHarnessDegraded({ ok: true, degraded: false, fail_total: 0 })
    expect(n.unreadCount.value).toBe(0)
    n.notifyHarnessDegraded({ degraded: true, fail_total: 3, fail_counts: { circuit_breaker: 3 } })
    expect(n.unreadCount.value).toBe(1)
  })

  it('prefs 持久化到 localStorage', () => {
    const n = useNotifications()
    n.setPref('send_fail', false)
    const raw = JSON.parse(localStorage.getItem('vermes-notify-prefs') || '{}')
    expect(raw.send_fail).toBe(false)
  })
})
