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

  on(sessionId, { onMessage, onDone, onError, onStatus, onEvolution, onTodoUpdate, onApprovalRequest, onToolCall, onTaskComplete, onReasoning }) {
    this._handlers.set(sessionId, { onMessage, onDone, onError, onStatus, onEvolution, onTodoUpdate, onApprovalRequest, onToolCall, onTaskComplete, onReasoning })
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

  async send(sessionId, { messages, model, provider, attachments, reasoning_effort, web_search }) {
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
      if (reasoning_effort) body.reasoning_effort = reasoning_effort
      if (web_search) body.web_search = true

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
            const deltaContent = data.choices?.[0]?.delta?.content
            
            if (data.type === 'stream_start' || data.type === 'lifecycle') {
              this._emit(sessionId, 'onStatus', { type: data.type, message: data.message || '' })
            } else if (data.type === 'thinking') {
              // thinking = 迭代步数事件，走 onStatus
              this._emit(sessionId, 'onStatus', { type: 'thinking', message: data.message || '', iteration: data.iteration })
            } else if (data.type === 'reasoning') {
              // reasoning = 真实推理内容 delta，走 onReasoning
              this._emit(sessionId, 'onReasoning', data.content || '')
            } else if (data.type === 'delta' || data.type === 'text') {
              this._emit(sessionId, 'onMessage', { type: 'delta', content: data.content || deltaContent || '' })
            } else if (data.type === 'tool' || data.type === 'tool_start' || data.type === 'tool_end') {
              this._emit(sessionId, 'onToolCall', data)
            } else if (data.type === 'evolution') {
              this._emit(sessionId, 'onEvolution', data)
            } else if (data.type === 'todo_update') {
              this._emit(sessionId, 'onTodoUpdate', data)
            } else if (data.type === 'task_complete') {
              this._emit(sessionId, 'onTaskComplete', data)
            } else if (data.type === 'approval_request') {
              this._emit(sessionId, 'onApprovalRequest', data.data || data)
            } else if (data.type === 'status') {
              this._emit(sessionId, 'onStatus', data)
            } else if (data.type === 'error') {
              this._emit(sessionId, 'onError', data.message || data.content)
            } else if (deltaContent) {
              // OpenAI 格式 — 内容在 choices[0].delta.content
              this._emit(sessionId, 'onMessage', { type: 'delta', content: deltaContent })
            } else if (data.usage) {
              this._emit(sessionId, 'onDone', data)
            }
            // stream_start/thinking/lifecycle 等元事件已在上面处理
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
    const ac = this._controllers.get(sessionId)
    if (ac) ac.abort()
    this._controllers.delete(sessionId)

    try {
      await fetch(`${this._baseUrl}/api/stop-generation`, {
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

// ── WebSocket 实现（HTTPS 生产环境）────────────────────

export class WebSocketTransport extends ChatTransport {
  constructor(url) {
    super()
    this._url = url
    this._ws = null
    this._reconnectTimer = null
    this._retryCount = 0
    this._maxRetries = 20
    this._connect()
  }

  _connect() {
    try {
      this._ws = new WebSocket(this._url)

      this._ws.onopen = () => {
        this._retryCount = 0  // reset on successful connection
      }

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
        if (this._retryCount >= this._maxRetries) return  // give up after max retries
        const delay = Math.min(1000 * Math.pow(2, this._retryCount), 30000)  // 1s→2s→4s→...→30s cap
        this._retryCount++
        this._reconnectTimer = setTimeout(() => this._connect(), delay)
      }

      this._ws.onerror = () => {
        this._ws?.close()
      }
    } catch {}
  }

  async send(sessionId, payload) {
    if (this._ws?.readyState === WebSocket.OPEN) {
      this._ws.send(JSON.stringify({ type: 'chat', session_id: sessionId, ...payload }))
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
    // HTTPS 生产环境用 WebSocket，本地用 SSE（更稳定）
    if (typeof window !== 'undefined' && window.location.protocol === 'https:') {
      const wsProto = 'wss:'
      _instance = new WebSocketTransport(`${wsProto}//${window.location.host}/api/ws/chat`)
    } else {
      _instance = new SSETransport()
    }
  }
  return _instance
}
