import { ref } from 'vue'
import { logger } from '@/utils/logger'
import { useConfirm } from '@/composables/useConfirm'

// 版本号从后端 /health 运行时读取，不编译时硬编码
// 框架更新不触发前端重建
let CURRENT_VERSION = '0.0.0'
fetch('/health')
  .then(r => r.json())
  .then(d => { CURRENT_VERSION = d.version || '0.0.0' })
  .catch(() => {})
const VERSION_URL = 'https://vbit.top/vermes/version.json'
const AGENT_VERSION_URL = '/api/agent/check'
const DISMISS_KEY = 'vermes_update_dismissed'
const AGENT_DISMISS_KEY = 'vermes_agent_update_dismissed'

// 是否为 Electron 桌面环境
const isDesktop = typeof window !== 'undefined' && window.vermes?.isDesktop

/** 获取 session token，用于 Agent 更新 API 认证 */
function getAgentToken() {
  if (typeof localStorage !== 'undefined') {
    return localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token') || ''
  }
  return ''
}

import { defineStore } from 'pinia'

export const useUpdateStore = defineStore('update', () => {
  // ── 应用更新（壳更新）
  const hasUpdate = ref(false)
  const latestVersion = ref('')
  const checked = ref(false)
  const downloadUrl = ref('')
  const releaseNotes = ref('')
  const sha256 = ref('')
  const minDataVersion = ref('')

  // ── Agent 框架更新（脑更新）
  const agentHasUpdate = ref(false)
  const agentLatestVersion = ref('')
  const agentCurrentVersion = ref('')
  const agentChangelog = ref([])
  const agentDownloadUrl = ref('')
  const agentSha256 = ref('')
  const agentMirrorUrl = ref('')
  const agentSizeBytes = ref(0)
  const agentChecked = ref(false)

  // ── 通用更新状态
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

    // Electron 桌面模式：使用 electron-updater + Agent 框架更新
    if (isDesktop && window.vermes?.checkForUpdates) {
      await checkUpdateElectron()
      // 桌面端也要检查 Agent 框架更新
      await checkAgentUpdate()
      return
    }

    // Web 模式：并行检查壳更新 + Agent 框架更新
    return Promise.all([
      checkUpdateWeb(),
      checkAgentUpdate(),
    ])
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
      logger.warn('[Vermes Update] Electron check error:', e)
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
        // 统一去掉 v 前缀
        latestVersion.value = res.version.replace(/^v/i, '')
        hasUpdate.value = true

        // 解析下载 URL / 校验和。
        // 历史上 version.json 出现过多种键名写法（mac / macOS / macos、url / dmg / zip），
        // 线上实际投放的是 { mac: { url, sha256 }, windows: { url, sha256, size } }，
        // 而旧解析器只认 macOS.dmg / windows.exe —— 两边对不上时 downloadUrl 会静默解析成
        // 空串，表现为"更新弹窗出来了但点下载没反应"。这里改成宽松取值，任何一种写法都能命中。
        const isMac = navigator.platform.includes('Mac') || navigator.userAgent.includes('Mac')
        const absolutize = (u) => (u && u.startsWith('/')) ? `https://vbit.top${u}` : (u || '')
        // 平台节点：按 mac / macOS / macos / darwin（或 windows / win）依次探测
        const platNode = isMac
          ? (res.mac || res.macOS || res.macos || res.darwin || null)
          : (res.windows || res.win || null)
        // 节点内的下载地址：url / dmg / zip / exe / installer 任取其一
        const nodeUrl = platNode && typeof platNode === 'object'
          ? (platNode.url || platNode.dmg || platNode.zip || platNode.exe || platNode.installer || '')
          : (typeof platNode === 'string' ? platNode : '')

        if (typeof res.download_url === 'string') {
          downloadUrl.value = res.download_url
        } else if (res.download_url && typeof res.download_url === 'object') {
          downloadUrl.value = absolutize(isMac
            ? (res.download_url.macos_dmg || res.download_url.macos_zip || '')
            : (res.download_url.windows_zip || res.download_url.windows_exe || ''))
        } else if (nodeUrl) {
          downloadUrl.value = absolutize(nodeUrl)
        } else {
          downloadUrl.value = absolutize(res.mac_url || res.win_url || '')
        }

        // 校验和：顶层 sha256（字符串/对象）优先，其次取平台节点内的 sha256
        if (typeof res.sha256 === 'string' && res.sha256) {
          sha256.value = res.sha256
        } else if (res.sha256 && typeof res.sha256 === 'object') {
          sha256.value = isMac
            ? (res.sha256.macos_dmg || res.sha256.macos_zip || '')
            : (res.sha256.windows_exe || res.sha256.windows_zip || '')
        } else if (platNode && typeof platNode === 'object') {
          sha256.value = platNode.sha256 || platNode.sha_256 || ''
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
      logger.warn('[Vermes Update] error:', e)
    }
  }

  /** 检查 Agent 框架更新 */
  async function checkAgentUpdate() {
    if (agentChecked.value) return
    try {
      let res = null

      // Electron 桌面模式：走 IPC
      if (isDesktop && window.vermes?.checkAgentUpdate) {
        res = await window.vermes.checkAgentUpdate()
      } else {
        // Web 模式：HTTP 带 token
        const token = getAgentToken()
        const headers = {}
        if (token) headers['X-Vermes-Session-Token'] = token
        res = await fetch(AGENT_VERSION_URL, {
          signal: AbortSignal.timeout(5000),
          headers,
        })
          .then(r => r.json())
          .catch(() => null)
      }

      if (res && res.has_update) {
        const ver = res.latest_version
        if (localStorage.getItem(AGENT_DISMISS_KEY) === ver) {
          return
        }
        agentHasUpdate.value = true
        agentLatestVersion.value = ver
        agentCurrentVersion.value = res.current_version
        agentChangelog.value = res.changelog || []
        agentDownloadUrl.value = res.download_url || ''
        agentSha256.value = res.sha256 || ''
        agentMirrorUrl.value = res.mirror_url || ''
        agentSizeBytes.value = res.size_bytes || 0
      } else if (res) {
        agentCurrentVersion.value = res.current_version || '0.0.0'
      }
      agentChecked.value = true
    } catch (e) {
      logger.warn('[Vermes Agent Update] check error:', e)
    }
  }

  function isNewer(latest, current) {
    // 兼容带 v 前缀的版本号（如 "v2.0.7"）和预发布后缀（如 "2.0.7-beta"）
    const stripV = (v) => v.replace(/^v/i, '')
    const stripPre = (v) => v.split(/[-+]/)[0]  // 去掉 -beta, -rc1, +build 等
    const l = stripPre(stripV(latest)).split('.').map(Number)
    const c = stripPre(stripV(current)).split('.').map(Number)
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

  function dismissAgentUpdate() {
    agentHasUpdate.value = false
    try { localStorage.setItem(AGENT_DISMISS_KEY, agentLatestVersion.value) } catch(e) {}
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

  /** Agent 框架更新（脑更新，不重启壳） */
  async function startAgentUpdate() {
    if (updating.value) return
    updating.value = true
    updateError.value = ''
    updateProgress.value = 0
    updateStatus.value = 'downloading'
    updateMessage.value = '准备下载 Agent 框架...'

    try {
      // Electron 桌面模式：走 IPC
      if (isDesktop && window.vermes?.downloadAgentUpdate) {
        // 注册 IPC 事件监听
        const unsubProgress = window.vermes.onAgentUpdateProgress((data) => {
          updateProgress.value = data.progress || 0
          updateStatus.value = data.status || ''
          updateMessage.value = data.message || ''
          downloadedBytes.value = data.downloaded_bytes || 0
          totalBytes.value = data.total_bytes || 0
          speedBps.value = data.speed_bps || 0
          etaSeconds.value = data.eta_seconds || 0
        })

        const unsubComplete = window.vermes.onAgentUpdateComplete((data) => {
          updateStatus.value = 'done'
          updateMessage.value = '✅ Agent 框架已更新，Gateway 正在重启...'
          agentHasUpdate.value = false
          agentCurrentVersion.value = agentLatestVersion.value
          setTimeout(() => { updating.value = false }, 3000)
          unsubProgress()
          unsubComplete()
          unsubError()
        })

        const unsubError = window.vermes.onAgentUpdateError((err) => {
          updateStatus.value = 'error'
          updateError.value = err
          updateMessage.value = `❌ ${err}`
          updating.value = false
          unsubProgress()
          unsubComplete()
          unsubError()
        })

        // 触发下载
        const result = await window.vermes.downloadAgentUpdate({
          version: agentLatestVersion.value,
          url: agentDownloadUrl.value,
          sha256: agentSha256.value,
          mirror_url: agentMirrorUrl.value,
        })

        if (result.error) {
          throw new Error(result.error)
        }
      } else {
        // Web 模式：HTTP 带 token
        const token = getAgentToken()
        const headers = { 'Content-Type': 'application/json' }
        if (token) headers['X-Vermes-Session-Token'] = token
        const response = await fetch('/api/agent/update', {
          method: 'POST',
          headers,
          body: JSON.stringify({
            version: agentLatestVersion.value,
            url: agentDownloadUrl.value,
            sha256: agentSha256.value,
            mirror_url: agentMirrorUrl.value,
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

        // Agent 更新完成 — gateway 自动重启，无需关闭窗口
        updateStatus.value = 'done'
        updateMessage.value = '✅ Agent 框架已更新，Gateway 正在重启...'
        agentHasUpdate.value = false
        agentCurrentVersion.value = agentLatestVersion.value

        // 等待 gateway 重启完成后重新连接
        setTimeout(() => {
          updating.value = false
        }, 3000)
      }

    } catch (e) {
      console.error('[Vermes Agent Update] error:', e)
      updateStatus.value = 'error'
      updateError.value = e.message || '更新失败'
      updateMessage.value = `❌ ${e.message || '更新失败'}`
      updating.value = false
    }
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
      logger.warn('[Vermes Update] loadBackups error:', e)
    }
  }

  // 统一确认弹窗（替代 native confirm）
  const { confirm } = useConfirm()

  /**
   * 回滚到指定版本
   */
  async function rollback(version) {
    if (!(await confirm({
      title: '回滚确认',
      message: `确定要回滚到 v${version} 吗？应用将自动重启。`,
      danger: true,
    }))) return

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
    // Agent 框架更新
    agentHasUpdate,
    agentLatestVersion,
    agentCurrentVersion,
    agentChangelog,
    agentSizeBytes,
    checkAgentUpdate,
    dismissAgentUpdate,
    agentChecked,
    startAgentUpdate,
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
})
