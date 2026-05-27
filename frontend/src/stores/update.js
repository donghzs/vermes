import { ref } from 'vue'

/* global __APP_VERSION__ */
const CURRENT_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0'
const VERSION_URL = 'https://vbit.top/vermes/version.json'
const DISMISS_KEY = 'vermes_update_dismissed'

export const useUpdateStore = () => {
  const hasUpdate = ref(false)
  const latestVersion = ref('')
  const checked = ref(false)

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
    // 记住用户关闭了此版本的提示，下次不再打扰
    localStorage.setItem(DISMISS_KEY, latestVersion.value)
  }

  return {
    hasUpdate,
    latestVersion,
    currentVersion: CURRENT_VERSION,
    checkUpdate,
    dismissUpdate,
    checked
  }
}
