import { ref } from 'vue'

/* global __APP_VERSION__ */
const CURRENT_VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.0.0'
const VERSION_URL = 'https://vbit.top/vermes/version.json'
const DISMISS_KEY = 'vermes_update_dismissed'

// 是否为 Electron 桌面环境
const isDesktop = typeof window !== 'undefined' && window.vermes?.isDesktop

export const useUpdateStore = () => {
  const hasUpdate = ref(false)
  const latestVersion = ref('')
  const checked = ref(false)
  const downloadUrl = ref('')
  const releaseNotes = ref('')
  const sha256 = ref('')
  const minDataVersion = ref('')

  // 自更新状态
  const updating = ref(false)
  const updateProgress = ref(0)
  const updateStatus = ref('')
  const updateError = ref('')
  const updateMessage = ref('')
  const downloadedBytes = ref(0)
  const totalBytes = ref(0)
  const speedBps = ref(0)
  const etaSeconds = ref(0)

  // 回滚状态
  const backups = ref([])
  const showRollback = ref(false)

  // Electron 事件监听清理函数
  let _electronCleanups = []

  async function checkUpdate() {
    if (checked.value) return
    checked.value = true

    // Electron 桌面模式：使用 electron-updater
    if (isDesktop && window.vermes?.checkForUpdates) {
      return checkUpdateElectron()
    }

    // Web 模式：从 vbit.top 获取版本信息
    return checkUpdateWeb()
  }

  /** Electron 原生更新检查 */
  async function checkUpdateElectron() {
    try {
      // 监听更新事件
      if (window.vermes.onUpdateAvailable) {
        _electronCleanups.push(
          window.vermes.onUpdateAvailable((info) => {
            latestVersion.value = info.version
            hasUpdate.value = true
            releaseNotes.value = info.releaseNotes || ''
            if (localStorage.getItem(DISMISS_KEY) === info.version) {
              hasUpdate.value = false
            }
          })
        )
      }
      if (window.vermes.onUpdateNotAvailable) {
        _electronCleanups.push(window.vermes.onUpdateNotAvailable(() => {}))
      }
      if (window.vermes.onUpdateProgress) {
        _electronCleanups.push(
          window.vermes.onUpdateProgress((progress) => {
            updateProgress.value = progress.percent
            downloadedBytes.value = progress.transferred
            totalBytes.value = progress.total
            speedBps.value = progress.bytesPerSecond
            updateStatus.value = 'downloading'
            updateMessage.value = `下载中 ${progress.percent.toFixed(0)}%`
          })
        )
      }
      if (window.vermes.onUpdateDownloaded) {
        _electronCleanups.push(
          window.vermes.onUpdateDownloaded((info) => {
            updateStatus.value = 'done'
            updateMessage.value = `✅ v${info.version} 已下载，重启后安装`
          })
        )
      }
      if (window.vermes.onUpdateError) {
        _electronCleanups.push(
          window.vermes.onUpdateError((err) => {
            updateStatus.value = 'error'
            updateError.value = err.message
          })
        )
      }

      await window.vermes.checkForUpdates()
    } catch (e) {
      console.warn('[Vermes Update] Electron check error:', e)
    }
  }

  /** Web 模式：从 vbit.top 获取版本信息 */
  async function checkUpdateWeb() {
    try {
      const cacheBuster = '?t=' + Date.now()
      const res = await fetch(VERSION_URL + cacheBuster, { signal: AbortSignal.timeout(5000) })
        .then(r => r.json())
        .catch(() => null)

      if (res && res.version && isNewer(res.version, CURRENT_VERSION)) {
        if (localStorage.getItem(DISMISS_KEY) === res.version) {
          return
        }
        latestVersion.value = res.version
        hasUpdate.value = true

        if (typeof res.download_url === 'string') {
          downloadUrl.value = res.download_url
        } else if (res.download_url && typeof res.download_url === 'object') {
          const isMac = navigator.platform.includes('Mac') || navigator.userAgent.includes('Mac')
          downloadUrl.value = isMac
            ? (res.download_url.macos_dmg || res.download_url.macos_zip || '')
            : (res.download_url.windows_zip || '')
        } else {
          downloadUrl.value = res.mac_url || res.win_url || ''
        }

        if (res.sha256) {
          const isMac = navigator.platform.includes('Mac') || navigator.userAgent.includes('Mac')
          if (typeof res.sha256 === 'object') {
            sha256.value = isMac
              ? (res.sha256.macos_dmg || res.sha256.macos_zip || '')
              : (res.sha256.windows_zip || '')
          } else {
            sha256.value = res.sha256
          }
        }

        minDataVersion.value = res.min_data_version || ''
        // 支持 releaseNotes/notes 字符串 或 changelog 数组
        if (res.releaseNotes) {
          releaseNotes.value = res.releaseNotes
        } else if (res.notes) {
          releaseNotes.value = res.notes
        } else if (res.changelog && Array.isArray(res.changelog)) {
          releaseNotes.value = res.changelog.join('\n')
        }
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
    try { localStorage.setItem(DISMISS_KEY, latestVersion.value) } catch(e) {}
  }

  /**
   * 开始更新
   * - Electron 模式：electron-updater 下载
   * - Web 模式：后端 SSE 流式下载
   */
  async function startUpdate() {
    if (updating.value) return
    updating.value = true
    updateError.value = ''
    updateProgress.value = 0

    // Electron 桌面模式
    if (isDesktop && window.vermes?.downloadUpdate) {
      updateStatus.value = 'downloading'
      updateMessage.value = '准备下载...'
      try {
        const result = await window.vermes.downloadUpdate()
        if (!result.success) {
          throw new Error(result.error || '下载失败')
        }
        // 下载完成后提示安装
        updateStatus.value = 'done'
        updateMessage.value = `✅ 更新已下载，点击安装重启`
      } catch (e) {
        updateStatus.value = 'error'
        updateError.value = e.message
        updateMessage.value = `❌ ${e.message || '更新失败'}`
        updating.value = false
      }
      return
    }

    // Web 模式：后端 SSE 流式下载
    return startUpdateWeb()
  }

  /** Web 模式：SSE 流式下载 */
  async function startUpdateWeb() {
    try {
      // 1. SSE 流式下载
      const response = await fetch('/api/update/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          version: latestVersion.value,
          url: downloadUrl.value,
          sha256: sha256.value,
          min_data_version: minDataVersion.value,
        })
      })

      if (!response.ok) {
        const err = await response.json().catch(() => ({}))
        throw new Error(err.detail || `下载失败 (${response.status})`)
      }

      // 读取 SSE 流
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              updateProgress.value = data.progress || 0
              updateStatus.value = data.status || ''
              updateMessage.value = data.message || ''
              updateError.value = data.error || ''
              downloadedBytes.value = data.downloaded_bytes || 0
              totalBytes.value = data.total_bytes || 0
              speedBps.value = data.speed_bps || 0
              etaSeconds.value = data.eta_seconds || 0

              if (data.status === 'error') {
                throw new Error(data.error || '下载失败')
              }
            } catch (parseErr) {
              if (parseErr.message && !parseErr.message.includes('JSON')) {
                throw parseErr
              }
            }
          }
        }
      }

      // 2. 下载完成，调 apply
      updateStatus.value = 'applying'
      updateMessage.value = '正在应用更新...'
      updateProgress.value = 100

      const applyRes = await fetch('/api/update/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: latestVersion.value })
      })

      if (!applyRes.ok) {
        const err = await applyRes.json().catch(() => ({}))
        throw new Error(err.detail || `应用失败 (${applyRes.status})`)
      }

      updateStatus.value = 'done'
      updateMessage.value = '✅ 更新完成，即将重启...'
      // 更新已提交，应用将自动重启

    } catch (e) {
      console.error('[Vermes Update] error:', e)
      updateStatus.value = 'error'
      updateError.value = e.message || '更新失败'
      updateMessage.value = `❌ ${e.message || '更新失败'}`
      updating.value = false
    }
  }

  /**
   * 加载可用备份列表
   */
  async function loadBackups() {
    try {
      const res = await fetch('/api/update/backups')
      const data = await res.json()
      if (data.ok) {
        backups.value = data.backups || []
        showRollback.value = backups.value.length > 0
      }
    } catch (e) {
      console.warn('[Vermes Update] loadBackups error:', e)
    }
  }

  /**
   * 回滚到指定版本
   */
  async function rollback(version) {
    if (!confirm(`确定要回滚到 v${version} 吗？应用将自动重启。`)) return

    try {
      updateStatus.value = 'applying'
      updateMessage.value = `正在回滚到 v${version}...`

      const res = await fetch('/api/update/rollback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version })
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || '回滚失败')
      }

      updateStatus.value = 'done'
      updateMessage.value = `✅ 回滚到 v${version}，即将重启...`

    } catch (e) {
      updateStatus.value = 'error'
      updateError.value = e.message
      updateMessage.value = `❌ ${e.message}`
    }
  }

  /**
   * 格式化文件大小
   */
  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + ' MB'
    return (bytes / 1024 / 1024 / 1024).toFixed(1) + ' GB'
  }

  function formatSpeed(bps) {
    if (bps < 1024) return bps.toFixed(0) + ' B/s'
    if (bps < 1024 * 1024) return (bps / 1024).toFixed(1) + ' KB/s'
    return (bps / 1024 / 1024).toFixed(1) + ' MB/s'
  }

  function formatEta(seconds) {
    if (seconds < 60) return Math.round(seconds) + '秒'
    if (seconds < 3600) return Math.round(seconds / 60) + '分钟'
    return Math.round(seconds / 3600) + '小时'
  }

  return {
    hasUpdate,
    latestVersion,
    currentVersion: CURRENT_VERSION,
    downloadUrl,
    releaseNotes,
    sha256,
    minDataVersion,
    checkUpdate,
    dismissUpdate,
    checked,
    // 自更新
    updating,
    updateProgress,
    updateStatus,
    updateError,
    updateMessage,
    downloadedBytes,
    totalBytes,
    speedBps,
    etaSeconds,
    startUpdate,
    // Electron: 安装已下载的更新并重启
    installUpdate: () => {
      if (isDesktop && window.vermes?.installUpdate) {
        window.vermes.installUpdate()
      }
    },
    // 回滚
    backups,
    showRollback,
    loadBackups,
    rollback,
    // 格式化
    formatBytes,
    formatSpeed,
    formatEta,
  }
}
