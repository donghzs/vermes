import { ref } from 'vue'

const CURRENT_VERSION = '1.1.0'
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
      // 添加缓存破坏参数，确保获取最新版本信息
      const cacheBuster = '?t=' + Date.now()
      const res = await fetch(VERSION_URL + cacheBuster, { signal: AbortSignal.timeout(5000) })
        .then(r => r.json())
        .catch(() => null)

      if (res && res.version && isNewer(res.version, CURRENT_VERSION)) {
        // 用户已手动关闭过此版本的提示，不再显示
        if (localStorage.getItem(DISMISS_KEY) === res.version) return
        latestVersion.value = res.version
        hasUpdate.value = true
      }
    } catch (e) {
      // 网络问题静默忽略
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
