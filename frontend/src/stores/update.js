import { ref } from 'vue'

/* global __APP_VERSION__ */
const CURRENT_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0'
const VERSION_URL = 'https://vbit.top/vermes/version.json'
const DISMISS_KEY = 'vermes_update_dismissed'

export const useUpdateStore = () => {
  const hasUpdate = ref(false)
  const latestVersion = ref('')
  const checked = ref(false)
  const downloadUrl = ref('')
  const releaseNotes = ref('')
  // 自更新状态
  const updating = ref(false)
  const updateProgress = ref(0)     // 0-100
  const updateStatus = ref('')      // 'downloading' | 'applying' | 'done' | 'error'
  const updateError = ref('')

  async function checkUpdate() {
    if (checked.value) return
    checked.value = true

    try {
      const cacheBuster = '?t=' + Date.now()
      const res = await fetch(VERSION_URL + cacheBuster, { signal: AbortSignal.timeout(5000) })
        .then(r => r.json())
        .catch(() => null)

      console.log('[Vermes Update] current:', CURRENT_VERSION, 'remote:', res?.version, 'isNewer:', res?.version ? isNewer(res.version, CURRENT_VERSION) : 'N/A')

      if (res && res.version && isNewer(res.version, CURRENT_VERSION)) {
        if (localStorage.getItem(DISMISS_KEY) === res.version) {
          console.log('[Vermes Update] dismissed, skip')
          return
        }
        latestVersion.value = res.version
        hasUpdate.value = true
        downloadUrl.value = res.download_url || res.mac_url || res.win_url || ''
        releaseNotes.value = res.releaseNotes || ''
        console.log('[Vermes Update] showing banner for', res.version)
      }
    } catch (e) {
      console.warn('[Vermes Update] error:', e)
    }
  }

  function isNewer(latest, current) {
    const l = latest.split('.').map(Number)
    const c = current.split('.').map(Number)
    for (let i = 0; i < 3; i++) {
      if ((l[i] || 0) > (c[i] || 0)) return true
      if ((l[i] || 0) < (c[i] || 0)) return false
    }
    return false
  }

  function dismissUpdate() {
    hasUpdate.value = false
    localStorage.setItem(DISMISS_KEY, latestVersion.value)
  }

  /**
   * 自更新：下载 → 写 pending.json → shutdown → 重启时自动应用
   */
  async function startUpdate() {
    if (updating.value) return
    updating.value = true
    updateError.value = ''
    updateProgress.value = 0
    updateStatus.value = 'downloading'

    try {
      // 1. 调后端下载接口
      const res = await fetch('/api/update/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: latestVersion.value,
          url: downloadUrl.value
        })
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.error || `下载失败 (${res.status})`)
      }

      // 2. SSE 进度流（如果后端支持）
      // 或者简单轮询
      updateProgress.value = 50
      updateStatus.value = 'applying'

      // 3. 调后端应用接口（写 pending.json + shutdown）
      const applyRes = await fetch('/api/update/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: latestVersion.value })
      })

      if (!applyRes.ok) {
        const err = await applyRes.json().catch(() => ({}))
        throw new Error(err.error || `应用失败 (${applyRes.status})`)
      }

      updateProgress.value = 100
      updateStatus.value = 'done'

      // 4. 后端会自动 shutdown，前端显示提示
      console.log('[Vermes Update] 更新已提交，应用将自动重启...')

    } catch (e) {
      console.error('[Vermes Update] error:', e)
      updateStatus.value = 'error'
      updateError.value = e.message || '更新失败'
      updating.value = false
    }
  }

  return {
    hasUpdate,
    latestVersion,
    currentVersion: CURRENT_VERSION,
    downloadUrl,
    releaseNotes,
    checkUpdate,
    dismissUpdate,
    checked,
    // 自更新
    updating,
    updateProgress,
    updateStatus,
    updateError,
    startUpdate
  }
}
