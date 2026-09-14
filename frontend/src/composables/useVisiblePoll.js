import { onMounted, onUnmounted } from 'vue'

/**
 * 仅在「页面/窗口可见」时按固定间隔轮询；隐藏时暂停；恢复可见时立即补刷一次。
 *
 * 为什么需要：C4 之后「关窗 = 隐藏到托盘、不退出」，且主进程关掉了后台定时器节流
 * （backgroundThrottling: false，长任务要能跑）。代价是**纯 UI 刷新也跟着全速轮询**——
 * 窗口挂在托盘里几小时，会话列表/状态徽标/看板仍在每 5~60 秒打后端、唤醒 CPU。
 * 这类数据隐藏时刷了也没人看，恢复可见时补一次即可。
 *
 * 🔴 为什么不能只信 document.hidden（Electron 专属陷阱）：
 *   Electron 官方文档：禁用 backgroundThrottling 后，document.visibilityState
 *   **永久保持 visible** —— 最小化、被遮挡、hide() 都不再翻转。
 *   而我们为了让长任务在挂托盘后继续跑，恰恰必须关掉节流。
 *   因此隐藏状态改由主进程按真实窗口状态广播（window:visibility →
 *   window.vermes.onWindowVisibility），这里两路信号取「任一为隐藏即隐藏」。
 *   纯浏览器环境没有该 API，自动跳过，退化为只用 Page Visibility。
 *
 * 适用范围（重要）：
 *   ✅ 纯 UI 数据刷新 —— 会话列表、状态徽标、看板、进化面板
 *   ❌ 流式输出缓冲、长任务状态轮询、扫码登录轮询
 *      —— 这些在隐藏时也必须继续，否则关窗再回来任务进度/扫码结果会丢。
 *
 * @param {Function} fn        每次轮询要执行的函数（应当自带错误处理）
 * @param {number}   interval  间隔毫秒
 * @param {object}   opts      immediate: 挂载时是否立刻执行一次（默认 true）
 */
export function useVisiblePoll(fn, interval, { immediate = true } = {}) {
  let timer = null
  let offWindowVis = null
  let windowHidden = false   // 主进程广播的窗口可见性（挂托盘 / 最小化）

  const isHidden = () =>
    windowHidden || (typeof document !== 'undefined' && document.hidden)

  function stop() {
    if (timer) { clearInterval(timer); timer = null }
  }
  function start() {
    stop()
    if (isHidden()) return
    timer = setInterval(fn, interval)
  }
  function onVisibilityChange() {
    if (isHidden()) stop()
    else { start(); fn() }   // 恢复可见：立即补一次，避免看到过期数据
  }

  onMounted(() => {
    if (immediate && !isHidden()) fn()
    start()
    document.addEventListener('visibilitychange', onVisibilityChange)
    offWindowVis = window.vermes?.onWindowVisibility?.((hidden) => {
      windowHidden = !!hidden
      onVisibilityChange()
    }) || null
  })
  onUnmounted(() => {
    stop()
    document.removeEventListener('visibilitychange', onVisibilityChange)
    if (offWindowVis) { offWindowVis(); offWindowVis = null }
  })

  return { start, stop }
}
