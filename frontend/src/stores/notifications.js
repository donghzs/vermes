/**
 * U-P0-5 通知中心 — 应用内关键事件聚合 + 可关。
 * 信号源：Harness 降级 / Agent 变更账本 / 进化成就 / 发送失败。
 * 缺信号不编造；mute 后只入账不弹铃。
 */
import { ref, computed, watch } from 'vue'
import { envHeaders } from '../utils/env'

const PREFS_KEY = 'vermes-notify-prefs'
const ITEMS_KEY = 'vermes-notify-items'

function loadJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch { return fallback }
}

export const CATEGORIES = [
  { id: 'harness', label: 'Harness 降级' },
  { id: 'change', label: 'Agent 变更/进化' },
  { id: 'achievement', label: '成就解锁' },
  { id: 'send_fail', label: '发送失败' },
]

export function useNotifications() {
  const items = ref(loadJson(ITEMS_KEY, []).slice(0, 80))
  const prefs = ref({
    harness: true,
    change: true,
    achievement: true,
    send_fail: true,
    ...loadJson(PREFS_KEY, {}),
  })
  const panelOpen = ref(false)
  const _seenKeys = new Set(items.value.map(i => i.key).filter(Boolean))

  function persist() {
    try {
      localStorage.setItem(ITEMS_KEY, JSON.stringify(items.value.slice(0, 80)))
      localStorage.setItem(PREFS_KEY, JSON.stringify(prefs.value))
    } catch { /* quota */ }
  }

  function pushTrayBadge() {
    const n = unreadCount.value
    try {
      if (typeof window !== 'undefined' && window.vermes?.setTrayUnread) {
        window.vermes.setTrayUnread(n)
      }
    } catch { /* non-desktop */ }
  }

  function notify({ category, title, detail = '', key = null, level = 'info', ts = Date.now() }) {
    if (!prefs.value[category]) return null
    if (key && _seenKeys.has(key)) return null
    const item = {
      id: `${ts}-${Math.random().toString(36).slice(2, 8)}`,
      key,
      category,
      title,
      detail,
      level,
      ts,
      read: false,
    }
    items.value = [item, ...items.value].slice(0, 80)
    if (key) _seenKeys.add(key)
    persist()
    pushTrayBadge()
    return item
  }

  function markRead(id) {
    items.value = items.value.map(i => (i.id === id ? { ...i, read: true } : i))
    persist(); pushTrayBadge()
  }

  function markAllRead() {
    items.value = items.value.map(i => ({ ...i, read: true }))
    persist(); pushTrayBadge()
  }

  function clearAll() {
    items.value = []
    _seenKeys.clear()
    persist(); pushTrayBadge()
  }

  function setPref(category, on) {
    prefs.value = { ...prefs.value, [category]: !!on }
    persist()
  }

  const unreadCount = computed(() => items.value.filter(i => !i.read).length)

  /** 拉取 change_ledger 未读并入通知中心（与 EvolutionPanel 同源） */
  async function syncChangeLedger() {
    if (!prefs.value.change && !prefs.value.achievement) return
    try {
      const r = await fetch('/api/changes?limit=20', { headers: envHeaders() })
      if (!r.ok) return
      const data = await r.json()
      const rows = data.changes || []
      for (const c of rows) {
        const cat = (c.kind === 'achievement' || (c.title || '').includes('🏆')) ? 'achievement' : 'change'
        notify({
          category: cat,
          title: c.title || c.summary || 'Agent 变更',
          detail: c.summary || c.kind || '',
          key: `chg:${c.id}`,
          level: 'info',
          ts: c.created ? Number(c.created) * 1000 : Date.now(),
        })
      }
    } catch { /* backend offline */ }
  }

  function notifyHarnessDegraded(status) {
    if (!status || status.signal === 'unavailable') return
    if (!status.degraded || !(status.fail_total > 0)) return
    notify({
      category: 'harness',
      title: `Harness 出现 fail-open（${status.fail_total}）`,
      detail: JSON.stringify(status.fail_counts || {}),
      key: `harness:${status.fail_total}:${JSON.stringify(status.fail_counts || {})}`,
      level: 'warning',
    })
  }

  function notifySendFailure(classified) {
    if (!classified) return
    notify({
      category: 'send_fail',
      title: classified.text || '发送失败',
      detail: classified.code ? `code=${classified.code}` : '',
      key: `send:${classified.code}:${Date.now()}`,
      level: 'error',
    })
  }

  watch(unreadCount, pushTrayBadge, { immediate: true })

  return {
    items, prefs, panelOpen, unreadCount, CATEGORIES,
    notify, markRead, markAllRead, clearAll, setPref,
    syncChangeLedger, notifyHarnessDegraded, notifySendFailure,
  }
}
