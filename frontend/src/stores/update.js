import { ref } from 'vue'

const CURRENT_VERSION = '1.0.0'

export const useUpdateStore = () => {
  const hasUpdate = ref(false)
  const latestVersion = ref('')
  const checked = ref(false)

  async function checkUpdate() {
    if (checked.value) return

    try {
      // 从 vbit.top 获取最新版本号
      const res = await fetch('https://vbit.top/api/vermes/version')
        .then(r => r.json())
        .catch(() => null)

      if (res && res.version) {
        latestVersion.value = res.version
        hasUpdate.value = compareVersions(res.version, CURRENT_VERSION)
        checked.value = true
      }
    } catch (e) {
      console.warn('Version check failed:', e.message)
    }
  }

  function compareVersions(latest, current) {
    const l = latest.split('.').map(Number)
    const c = current.split('.').map(Number)
    for (let i = 0; i < 3; i++) {
      if ((l[i] || 0) > (c[i] || 0)) return true
      if ((l[i] || 0) < (c[i] || 0)) return false
    }
    return false
  }

  return {
    hasUpdate,
    latestVersion,
    currentVersion: CURRENT_VERSION,
    checkUpdate,
    checked
  }
}
