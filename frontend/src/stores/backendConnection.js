import { ref, computed } from 'vue'
import { defineStore } from 'pinia'

// 全局后端连接状态 —— A.4.3
// 唯一真相源：所有"后端死了 / 重连中"类 UI 都从这里读，
// 避免各组件各自 fetch 失败 → 各自 toast 刷屏（#DMG 实测"后端连接失败"红字根因之一）。
//
// 数据来源：
//   - Electron：主进程看门狗（main.js startBackendWatchdog）经 preload
//     window.vermes.onBackendStatus 推送 { online, restarting, detail }。
//   - Web 模式：无独立后端进程，默认在线（页面都打不开就谈不上"组件报错"）。

export const useBackendConnectionStore = defineStore('backendConnection', () => {
  const online = ref(true)
  const restarting = ref(false)
  const detail = ref(null)
  const lastChangeAt = ref(0)

  // 是否已挂载 IPC 监听（幂等）
  let _subscribed = false
  let _unsub = null

  const isOffline = computed(() => !online.value)
  const statusText = computed(() => {
    if (online.value) return ''
    if (restarting.value) return '后端重连中…'
    return detail.value || '后端连接失败'
  })

  function setStatus({ online: o, restarting: r, detail: d }) {
    if (typeof o === 'boolean') online.value = o
    if (typeof r === 'boolean') restarting.value = r
    if (d !== undefined) detail.value = d
    lastChangeAt.value = Date.now()
  }

  // 订阅主进程推送；无 window.vermes（Web 模式）时静默跳过，保持默认在线。
  function init() {
    if (_subscribed) return
    const vermes = typeof window !== 'undefined' ? window.vermes : null
    if (vermes && typeof vermes.onBackendStatus === 'function') {
      _subscribed = true
      _unsub = vermes.onBackendStatus((status) => {
        if (status && typeof status === 'object') {
          setStatus({
            online: status.online,
            restarting: status.restarting,
            detail: status.detail,
          })
        }
      })
    }
  }

  function dispose() {
    if (_unsub) {
      try { _unsub() } catch (_) {}
      _unsub = null
    }
    _subscribed = false
  }

  return {
    online,
    restarting,
    detail,
    lastChangeAt,
    isOffline,
    statusText,
    setStatus,
    init,
    dispose,
  }
})
