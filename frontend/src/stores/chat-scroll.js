/**
 * chat-scroll.js — 流式输出滚动调度
 *
 * 避免每个 SSE delta 都触发 smooth scroll，改为 RAF 节流。
 * 流式期间每 ~80ms 跳一帧（≈12fps），onDone 时立即跳到底。
 */

let _scrollRafId = null
let _scrollTarget = null // 滚动容器 DOM（由 MessageList onMounted 注入）

/** 安排一次 RAF 滚动（去重：已有待执行 RAF 时跳过） */
export function scheduleScroll() {
  if (_scrollRafId) return
  _scrollRafId = requestAnimationFrame(() => {
    _scrollRafId = null
    if (_scrollTarget) {
      const c = _scrollTarget
      const isNearBottom = c.scrollHeight - c.scrollTop - c.clientHeight < 200
      if (isNearBottom) {
        c.scrollTop = c.scrollHeight // 直接跳，不用 smooth（流式中 smooth 会累积延迟）
      }
    }
  })
}

/** 立即跳到底部（流式结束时调用） */
export function flushScroll() {
  if (_scrollRafId) { cancelAnimationFrame(_scrollRafId); _scrollRafId = null }
  if (_scrollTarget) _scrollTarget.scrollTop = _scrollTarget.scrollHeight
}

/** 注入滚动容器引用（由 MessageList 组件 onMounted 调用） */
export function setScrollTarget(el) { _scrollTarget = el }
