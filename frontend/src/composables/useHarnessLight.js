/**
 * U-P0-6：场景内轻量 Harness 信号（与 ChatHeader 同源 HTTP 轮询）。
 * 缺信号/未降级 → null，UI 不画状态，避免刷屏。
 */
import { ref } from 'vue'
import { useVisiblePoll } from './useVisiblePoll'

export function useHarnessLight(intervalMs = 45000) {
  const harnessStatus = ref(null)

  async function fetchHarnessStatus() {
    try {
      const r = await fetch('/api/harness/status')
      if (r.ok) harnessStatus.value = await r.json()
    } catch { /* 缺信号 */ }
  }

  useVisiblePoll(fetchHarnessStatus, intervalMs)

  /** 仅 degraded 时返回文案；否则 null（界面保持干净） */
  function harnessChip() {
    const h = harnessStatus.value
    if (!h || h.signal === 'unavailable') return null
    if (h.degraded === true && (h.fail_total ?? 0) > 0) {
      return { key: 'harness', icon: '🛡', label: `Harness ${h.fail_total}`, title: 'fail-open 计数，点击聊天页顶栏可刷新' }
    }
    return null
  }

  return { harnessStatus, fetchHarnessStatus, harnessChip }
}
