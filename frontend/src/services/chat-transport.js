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

  on(sessionId, { onMessage, onDone, onError, onStatus, onEvolution, onTodoUpdate, onApprovalRequest, onToolCall, onTaskComplete, onReasoning, onPlanCreated, onPlanUpdate }) {
    this._handlers.set(sessionId, { onMessage, onDone, onError, onStatus, onEvolution, onTodoUpdate, onApprovalRequest, onToolCall, onTaskComplete, onReasoning, onPlanCreated, onPlanUpdate })
  }

  off(sessionId) {
    this._handlers.delete(sessionId)
  }

  // 默认实现：该会话是否仍注册了消息处理器（即处于活动对话中）。
  // 子类可覆写得更精确（SSE 用 _controllers 跟踪在途 fetch）。
  isStreaming(sessionId) {
    return this._handlers.has(sessionId)
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
    this._reconnectAttempts = new Map()  // sessionId → attempt count
    this._maxReconnects = 2  // P1-3: max auto-reconnect attempts
  }

  // P1-3: Fetch plan snapshot for session (used on reconnect)
  async fetchSnapshot(sessionId) {
    try {
      const resp = await fetch(`${this._baseUrl}/api/session/${sessionId}/plan_snapshot`)
      if (!resp.ok) return null
      return await resp.json()
    } catch {
      return null
    }
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
            this._reconnectAttempts.delete(sessionId)  // P1-3: reset on success
            return
          }
          try {
            const data = JSON.parse(raw)
            const deltaContent = data.choices?.[0]?.delta?.content
            
            if (data.type === 'stream_start' || data.type === 'lifecycle' || data.type === 'warn') {
              // warn = 后端关键状态（模型空响应重试/切换备用服务商/最终失败原因）。
              // 修复：此前无 warn 分支，事件被静默丢弃，空回复时用户看不到任何原因。
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
            } else if (data.type === 'plan_created') {
              this._emit(sessionId, 'onPlanCreated', data.plan || data)
            } else if (data.type && data.type.startsWith('plan_')) {
              // plan_step_update, plan_tool_started, plan_tool_completed, plan_completed, etc.
              this._emit(sessionId, 'onPlanUpdate', { subtype: data.type.replace('plan_', ''), ...data })
            } else if (data.type === 'stage') {
              // Pipeline stage event: { stage, pipeline: 'start'|'done'|'error', papers? }
              this._emit(sessionId, 'onStage', data)
            } else if (data.type === 'checkpoint') {
              // Pipeline checkpoint: { stage, next, message, completed, remaining }
              this._emit(sessionId, 'onCheckpoint', data)
            } else if (data.type === 'turn_boundary') {
              // 工具输出与 Agent 回复之间的分隔标记
              this._emit(sessionId, 'onMessage', { type: 'turn_boundary' })
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
      this._reconnectAttempts.delete(sessionId)  // P1-3: reset on success
    } catch (e) {
      if (e.name !== 'AbortError') {
        // P1-3: Auto-reconnect with snapshot merge
        const attempts = (this._reconnectAttempts.get(sessionId) || 0) + 1
        this._reconnectAttempts.set(sessionId, attempts)
        if (attempts <= this._maxReconnects) {
          const snapshot = await this.fetchSnapshot(sessionId)
          if (snapshot && snapshot.plan) {
            this._emit(sessionId, 'onPlanCreated', snapshot.plan)
            // Reconstruct plan_step_update for each step with snapshot status
            const todoStates = snapshot.todo_states || {}
            for (const step of (snapshot.plan.steps || [])) {
              const status = todoStates[step.id] || step.status || 'pending'
              this._emit(sessionId, 'onPlanUpdate', { subtype: 'step_update', step: { id: step.id, status } })
            }
          }
          this._emit(sessionId, 'onStatus', { type: 'reconnecting', message: `重连中 (${attempts}/${this._maxReconnects})...` })
          const delay = Math.min(1000 * Math.pow(2, attempts - 1), 5000)
          await new Promise(r => setTimeout(r, delay))
          // Retry with same payload (messages will be resent by caller)
          this._reconnectAttempts.set(sessionId, attempts)
          // Don't emit error if reconnected successfully — caller decides
          this._emit(sessionId, 'onError', { reconnect: true, message: e.message || '连接中断', attempts })
        } else {
          this._reconnectAttempts.delete(sessionId)
          this._emit(sessionId, 'onError', e.message || '连接中断')
        }
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
