/**
 * Brick Events 订阅 — 前端轻量响应式刷新。
 *
 * 后端在技能安装/卸载、模块安装/卸载、工具注册/deregister、MCP 变更时
 * 通过 SSE /api/bricks/events 广播事件。
 *
 * 各 Agent 管理组件注册回调，收到事件后自动刷新对应数据。
 *
 * 用法：
 *   import { useBrickEvents } from '@/utils/brick-events'
 *   const { onEvent } = useBrickEvents()
 *   onEvent('skill.installed', () => loadSkills())
 *   onEvent('skill.uninstalled', () => loadSkills())
 *   onEvent('module.installed', () => loadModules())
 *   onEvent('tool.registered', () => loadToolsets())
 *   onEvent('*', () => loadAll())  // 通配符：所有事件都刷新
 */

import { ref, onUnmounted } from 'vue'

type EventHandler = (payload: any) => void

let es: EventSource | null = null
const handlers = new Map<string, Set<EventHandler>>()
const connected = ref(false)

function ensureConnected() {
  if (es) return
  try {
    es = new EventSource('/api/bricks/events')
    es.onopen = () => { connected.value = true }
    es.onerror = () => {
      connected.value = false
      // 断线重连（EventSource 会自动重连，这里只更新状态）
    }
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        const type = data.type || ''
        const payload = data.payload || {}

        // 精确匹配
        const exact = handlers.get(type)
        if (exact) exact.forEach(h => { try { h(payload) } catch {} })

        // 通配符
        const wildcard = handlers.get('*')
        if (wildcard) wildcard.forEach(h => { try { h(payload) } catch {} })

        // 快照事件：首次连接，触发全量刷新
        if (type === 'snapshot') {
          const snap = handlers.get('snapshot')
          if (snap) snap.forEach(h => { try { h(payload) } catch {} })
        }
      } catch {}
    }
  } catch {}
}

export function useBrickEvents() {
  ensureConnected()

  function onEvent(type: string, handler: EventHandler) {
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type)!.add(handler)

    // 返回取消订阅函数
    return () => {
      handlers.get(type)?.delete(handler)
    }
  }

  return { onEvent, connected }
}
