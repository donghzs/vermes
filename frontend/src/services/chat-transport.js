/**
 * chat-transport.js — 聊天传输层抽象
 *
 * 当前使用 SSE，后期切 WebSocket 只需改工厂函数 1 行。
 *
 * 设计原则：
 * - transport.send(sessionId, payload) → 发送消息
 * - transport.stop(sessionId) → 停止生成
 * - transport.on(sessionId, { onMessage, onDone, onError }) → 注册回调
 * - transport.off(sessionId) → 清理回调
 */

// ── 基类 ──────────────────────────────────────────────

class ChatTransport {
  constructor() {
    this._handlers = new Map()  // sessionId → { onMessage, onDone, onError }
  }

  async send(sessionId, payload) {
    throw new Error('implement in subclass')
  }

  async stop(sessionId) {
    throw new Error('implement in subclass')
  }

  on(sessionId, { onMessage, onDone, onError }) {
    this._handlers.set(sessionId, { onMessage, onDone, onError })
  }

  off(sessionId) {
    this._handlers.delete(sessionId)
  }

  _emit(sessionId, event, data) {
    const h = this._handlers.get(sessionId)
    if (h && h[event]) h[event](data)
  }
}

// ── SSE 实现 ──────────────────────────────────────────

export class SSETransport extends ChatTransport {
  constructor(baseUrl = '') {
    super()
    this._baseUrl = baseUrl
    this._controllers = new Map()  // sessionId → AbortController
  }

  async send(sessionId, { messages, model, provider, attachments }) {
    // 旧 session 的控制器还在，先清理
    const oldAc = this._controllers.get(sessionId)
    if (oldAc) oldAc.abort()
    this._controllers.delete(sessionId)

    const ac = new AbortController()
    this._controllers.set(sessionId, ac)

    this._emit(sessionId, 'onStreamStart', sessionId)

    try {
      const body = {
        session_id: sessionId,
        messages,
        model,
        provider,
        attachments: attachments || [],
        stream: true,
      }

      const resp = await fetch(`${this._baseUrl}/api/chat/completions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ac.signal,
      })

      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: `HTTP ${resp.status}` }))
        this._emit(sessionId, 'onError', err.detail || `请求失败 (${resp.status})`)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const raw = line.slice(6).trim()
          if (raw === '[DONE]') {
            this._emit(sessionId, 'onDone', {})
            this._controllers.delete(sessionId)
            return
          }
          try {
            const data = JSON.parse(raw)
            if (data.type === 'delta' || data.type === 'text') {
              this._emit(sessionId, 'onMessage', data)
            } else if (data.type === 'tool') {
              this._emit(sessionId, 'onToolCall', data)
            } else if (data.type === 'status') {
              this._emit(sessionId, 'onStatus', data)
            } else if (data.type === 'error') {
              this._emit(sessionId, 'onError', data.message || data.content)
            } else {
              this._emit(sessionId, 'onMessage', data)
            }
          } catch {}
        }
      }
      this._emit(sessionId, 'onDone', {})
    } catch (e) {
      if (e.name !== 'AbortError') {
        this._emit(sessionId, 'onError', e.message || '连接中断')
      }
    } finally {
      this._controllers.delete(sessionId)
    }
  }

  async stop(sessionId) {
    // 中止当前请求
    const ac = this._controllers.get(sessionId)
    if (ac) ac.abort()
    this._controllers.delete(sessionId)

    // 通知后端中断 agent
    try {
      await fetch(`${this._baseUrl}/api/chat/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId }),
      })
    } catch {}
  }

  isStreaming(sessionId) {
    return this._controllers.has(sessionId)
  }
}

// ── WebSocket 实现（后期启用）────────────────────────

export class WebSocketTransport extends ChatTransport {
  constructor(url) {
    super()
    this._url = url
    this._ws = null
    this._reconnectTimer = null
    this._connect()
  }

  _connect() {
    try {
      this._ws = new WebSocket(this._url)

      this._ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          const sid = msg.sessionId || msg.session_id
          if (!sid) return

          if (msg.type === 'done' || msg.type === 'finish') {
            this._emit(sid, 'onDone', msg)
          } else if (msg.type === 'error') {
            this._emit(sid, 'onError', msg.message || '未知错误')
          } else {
            this._emit(sid, 'onMessage', msg)
          }
        } catch {}
      }

      this._ws.onclose = () => {
        this._reconnectTimer = setTimeout(() => this._connect(), 1000)
      }

      this._ws.onerror = () => {
        this._ws?.close()
      }
    } catch {}
  }

  async send(sessionId, payload) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({
        type: 'chat',
        session_id: sessionId,
        ...payload,
      }))
    }
  }

  async stop(sessionId) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: 'stop', session_id: sessionId }))
    }
  }

  disconnect() {
    if (this._reconnectTimer) clearTimeout(this._reconnectTimer)
    this._ws?.close()
    this._ws = null
  }
}

// ── 工厂 ──

let _instance = null

export function getChatTransport() {
  if (!_instance) {
    // 后期切 WebSocket 只需改这一行：
    // 生产环境启用 WebSocket
    if (typeof window !== 'undefined' && window.location.protocol === 'https:') {
      const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      _instance = new WebSocketTransport(`${wsProto}//${window.location.host}/api/ws/chat`)
    } else {
      _instance = new WebSocketTransport('ws://127.0.0.1:9119/api/ws/chat')
    }
  }
  return _instance
}
