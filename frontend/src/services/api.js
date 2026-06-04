import { ref } from 'vue'

const token = ref('')
let baseUrl = ''

// 在线模式标志（通过 window.__VERMES_ONLINE__ 设置）
const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true

// ✅ 计费模型标识（云端收费，本地免费）
// 启动时从后端拉取，fallback 到本地硬编码
const CLOUD_MODELS_FALLBACK = ['deepseek', 'openrouter', 'vbit', 'qwen', 'openai', 'anthropic', 'gemini', 'xiaomi', 'agnes']
const _cloudModels = ref(CLOUD_MODELS_FALLBACK)  // reactive，Vue computed 可追踪

export function isCloudModel(provider) {
  if (!provider) return false
  const p = provider.toLowerCase()
  return _cloudModels.value.some(m => p.includes(m))
}

// ✅ 推荐提供商（启动时从后端拉取，fallback 到本地硬编码）
const RECOMMENDED_FALLBACK = [
  { id: 'vbit', free: true },
  { id: 'agnes', free: true },
  { id: 'deepseek', free: false },
  { id: 'xiaomi', free: false },
  { id: 'ollama', free: true },
]
const _recommendedProviders = ref(RECOMMENDED_FALLBACK)  // reactive
const _configLoaded = ref(false)  // 拉取完成标志

export async function fetchProviderConfig() {
  try {
    const resp = await fetch('/api/config/cloud-models')
    if (resp.ok) {
      const data = await resp.json()
      if (Array.isArray(data.cloud_models) && data.cloud_models.length > 0) {
        _cloudModels.value = data.cloud_models
      }
      if (Array.isArray(data.recommended_providers) && data.recommended_providers.length > 0) {
        _recommendedProviders.value = data.recommended_providers
      }
      _configLoaded.value = true
      return
    }
  } catch (e) {
    console.warn('[Vermes] provider config 拉取失败，使用本地 fallback:', e.message)
  }
  // fallback 不需要赋值，初始值已是 fallback
  _configLoaded.value = true
}

export function getRecommendedIds() {
  return _recommendedProviders.value.map(p => p.id)
}

export function getRecommendedProviders() {
  return _recommendedProviders.value
}

export function isConfigLoaded() {
  return _configLoaded.value
}

// ── 服务端配额查询（v2：仅限微信登录用户） ──
export async function checkQuotaServer(wechatOpenid) {
  try {
    const headers = {}
    if (wechatOpenid) headers['X-WeChat-Openid'] = wechatOpenid
    const resp = await fetch('/api/quota/check', { headers })
    return await resp.json()
  } catch (e) {
    console.warn('[Vermes] 服务端配额查询失败:', e)
    return { success: false }
  }
}

// 计费配额管理
export const WECHAT_QUOTA_KEY = 'vermes_wechat_quota'
export function getWechatDailyQuota() {
  const data = localStorage.getItem(WECHAT_QUOTA_KEY)
  const today = new Date().toDateString()
  if (!data) return { remaining: 500, date: today }
  try {
    const q = JSON.parse(data)
    if (q.date !== today) return { remaining: 500, date: today }
    return q
  } catch { return { remaining: 500, date: today } }
}

export function useWechatQuota(count = 1) {
  const quota = getWechatDailyQuota()
  const newRemaining = Math.max(0, quota.remaining - count)
  try { localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: newRemaining, date: quota.date })) } catch(e) { /* quota storage full */ }
}
export function getRemainingQuota() {
  const data = localStorage.getItem('vermes_quota')
  if (!data) return null
  try { return JSON.parse(data) } catch { return null }
}

export function saveQuota(remaining) {
  try { localStorage.setItem('vermes_quota', JSON.stringify({ remaining, date: new Date().toDateString() })) } catch(e) { /* storage full */ }
}

  // 检查云端模型请求是否允许
// v2: 配额检查（简化版，仅限微信登录用户使用免费体验）
export function checkQuota(isCloud) {
  // 本地模型无限
  if (!isCloud) return { allowed: true, unlimited: true, remaining: Infinity, source: 'local' }
  
  // 云端模型：必须有微信登录
  const token = localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')
  const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
  
  // 未登录
  if (!token || !wechatOpenid) {
    return { allowed: false, unlimited: false, remaining: 0, source: 'need_login', requireLogin: true }
  }
  
  // 在线模式：服务端控制配额，本地放行（服务端 checkQuotaServer 做精细检查）
  if (isOnline) {
    return { allowed: true, unlimited: true, remaining: 999, source: 'wechat_online' }
  }
  
  // 桌面版：本地缓存作为 UI 显示参考，服务端精确扣减（H5 双重扣减修复）
  const quota = getWechatDailyQuota()
  if (quota.remaining <= 0) {
    return { allowed: false, unlimited: false, remaining: 0, source: 'wechat_daily' }
  }
  // 不再本地 useWechatQuota(1) —— 服务端 /spend 精确扣减，本地只读不写
  return { allowed: true, unlimited: false, remaining: quota.remaining, source: 'wechat_daily' }
}

export function useQuota(count = 1) {
  const quota = getRemainingQuota()
  if (!quota) return
  const newRemaining = Math.max(0, quota.remaining - count)
  saveQuota(newRemaining)
}

function buildHeaders(extra = {}) {
  const h = { ...extra }
  if (isOnline) {
    // 在线模式：用 One-API Bearer token
    const onlineToken = localStorage.getItem('vermes_wechat_token') || localStorage.getItem('vermes_token')
    if (onlineToken) h['Authorization'] = `Bearer ${onlineToken}`
  } else if (token.value) {
    // 桌面模式：用 Hermes session token
    h['X-Hermes-Session-Token'] = token.value
  }
  return h
}

async function request(path, options = {}) {
  // 在线模式：使用 /v1/ 路径（nginx 代理到 One-API）
  const apiPrefix = isOnline ? '/v1' : '/api'
  const url = baseUrl ? `${baseUrl}${apiPrefix}${path}` : `${apiPrefix}${path}`

  const maxRetries = options.method && options.method !== 'GET' ? 0 : 3
  const retryableStatuses = [429, 500, 502, 503, 504]

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    // 超时处理：默认 60s，如果调用者已传 signal 则合并
    const timeoutMs = options.timeout ?? 60000
    const controller = new AbortController()
    let timeoutId = null

    if (timeoutMs > 0) {
      timeoutId = setTimeout(() => controller.abort(new Error(`请求超时（${timeoutMs / 1000}s）`)), timeoutMs)
    }

    // 合并调用者传入的 signal（如停止生成）— 兼容旧版 Safari/WebKit
    let combinedSignal
    if (options.signal) {
      try {
        combinedSignal = AbortSignal.any([controller.signal, options.signal])
      } catch {
        const onAbort = () => { try { controller.abort() } catch(e) {} }
        options.signal.addEventListener('abort', onAbort, { once: true })
        combinedSignal = controller.signal
      }
    } else {
      combinedSignal = controller.signal
    }

    try {
      const resp = await fetch(url, {
        ...options,
        headers: { ...buildHeaders(options.headers), ...(options.headers || {}) },
        signal: combinedSignal,
      })
      clearTimeout(timeoutId)

      // 429: 尊重 Retry-After 头
      if (resp.status === 429) {
        const retryAfter = resp.headers.get('Retry-After')
        const text = await resp.text().catch(() => '')
        if (text.includes('insufficient_quota') || text.includes('额度已用尽')) {
          throw new Error('体验额度已用完 💡 请在设置中配置自己的 API Key 继续使用')
        }
        if (attempt < maxRetries) {
          const delay = retryAfter ? parseInt(retryAfter) * 1000 : Math.min(1000 * Math.pow(2, attempt), 8000)
          await new Promise(r => setTimeout(r, delay))
          continue
        }
        throw new Error(`API 429: ${text}`)
      }

      // 5xx: 可重试
      if (!resp.ok) {
        const text = await resp.text().catch(() => '')
        if (retryableStatuses.includes(resp.status) && attempt < maxRetries) {
          const delay = Math.min(1000 * Math.pow(2, attempt), 8000)
          await new Promise(r => setTimeout(r, delay))
          continue
        }
        throw new Error(`API ${resp.status}: ${text}`)
      }
      return resp
    } catch (e) {
      clearTimeout(timeoutId)
      // 用户主动取消 → 不重试
      if (options.signal?.aborted) throw e
      // 超时 → 不重试
      if (e.name === 'AbortError') {
        throw new Error(`请求超时（${timeoutMs / 1000}秒），请检查网络或切换模型`)
      }
      // 网络错误 → 可重试
      if (attempt < maxRetries && (e.message?.includes('Failed to fetch') || e.message?.includes('NetworkError') || e.message?.includes('fetch'))) {
        const delay = Math.min(1000 * Math.pow(2, attempt), 8000)
        await new Promise(r => setTimeout(r, delay))
        continue
      }
      throw e
    }
  }
}

// SSE 断线重连计数器（防止无限递归）
let _sseRetryCount = 0
const SSE_MAX_RETRIES = 3

const api = {
  async get(path) {
    const resp = await request(path)
    return resp.json()
  },

  async post(path, data) {
    const resp = await request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    })
    return resp.json()
  },

  // 会话
  getSessions() { return this.get('/sessions') },
  getMessages(sessionId) { return this.get(`/sessions/${sessionId}/messages`) },

  // 发送消息（SSE 流式）
  async sendMessage({ model, messages, stream, signal, onChunk, onDone, onError, onTool, onStreamStart, onThinking, onStatus, provider, attachments, session_id }) {
    // 新的非重试请求，重置重试计数器
    if (!signal?._isRetry) _sseRetryCount = 0
    const body = { model, messages, stream, provider: provider || '' }
    if (session_id) body.session_id = session_id
    if (attachments && attachments.length > 0) body.attachments = attachments
    // 传 wechat_openid 让后端能在没有 API key 时自动 claim
    const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
    if (wechatOpenid) body.wechat_openid = wechatOpenid
    let usageInfo = null  // 收集 SSE 流中的 usage 数据
    try {
      const resp = await request('/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal,
      })

      if (!stream) {
        const data = await resp.json()
        onDone?.(data)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let lastReadTime = Date.now()

      // WebView2 兼容：读取超时检测（300s 无数据则中断）
      // 后端 agent_task 最多 300s，对齐；后端 ping 每 15s 保活
      const READ_TIMEOUT_MS = 300000
      let readTimer = null
      const resetReadTimer = () => { lastReadTime = Date.now() }

      try {
      while (true) {
        // 给 reader.read() 加超时保护（WebView2 可能不返回 done=true）
        const readPromise = reader.read()
        const timeoutPromise = new Promise((_, reject) => {
          readTimer = setTimeout(() => {
            if (Date.now() - lastReadTime >= READ_TIMEOUT_MS) {
              reject(new Error('stream_read_timeout'))
            }
          }, READ_TIMEOUT_MS)
        })
        let result
        try {
          result = await Promise.race([readPromise, timeoutPromise])
        } catch (e) {
          if (e.message === 'stream_read_timeout') {
            console.warn('[Vermes] 流式读取超时，中断连接')
            // 给用户提示：响应可能不完整
            if (onChunk) onChunk('\n\n⚠️ 响应超时，内容可能不完整')
            break
          }
          throw e
        } finally {
          clearTimeout(readTimer)
        }
        const { done, value } = result || {}
        if (done) break
        if (signal?.aborted) { reader.cancel(); break }
        resetReadTimer()

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone?.(usageInfo); return }
          try {
            const json = JSON.parse(data)
            // P1-10: 捕获 stream_id 用于 stop 通知
            if (json.type === 'stream_start' && json.stream_id) {
              onStreamStart?.(json.stream_id)
              continue
            }
            // 检查上游错误（如 One-API 额度耗尽）
            if (json.error) {
              const errMsg = json.error.message || '服务端错误'
              onError?.(new Error(errMsg))
              reader.cancel()
              return
            }
            // 解析 usage（最后一个 chunk 带有精确 token 统计）
            if (json.usage && json.usage.total_tokens > 0) {
              usageInfo = json.usage
            }
            // Agent 模式：推理链可视化事件
            if (json.type === 'thinking') {
              // 思考事件节流：2秒内最多显示一次
              const now = Date.now()
              resetReadTimer()  // 重置读取超时（后端还在处理）
              if (!window.__lastThinkingTime || now - window.__lastThinkingTime > 2000) {
                window.__lastThinkingTime = now
                // 只触发 onThinking 回调，不创建工具卡片（避免 toolCount 污染）
                onThinking?.(json)
              }
              continue
            }
            if (json.type === 'lifecycle' || json.type === 'warn') {
              resetReadTimer()
              onStatus?.(json)
              continue
            }
            if (json.type === 'ping') {
              // 保活心跳：重置读取超时计时器
              resetReadTimer()
              continue
            }
            if (json.type === 'tool_start') {
              resetReadTimer()  // 工具开始执行，重置超时
              onTool?.({ type: 'tool_start', tool_call_id: json.tool_call_id, name: json.tool_name, arguments: json.arguments })
              continue
            }
            if (json.type === 'tool_end') {
              resetReadTimer()  // 工具执行完成，重置超时
              onTool?.({ type: 'tool_end', tool_call_id: json.tool_call_id, name: json.tool_name, duration: json.duration, is_error: json.is_error, result_preview: json.result_preview || '' })
              continue
            }
            // 工具调用（标准OpenAI格式）
            const toolCall = json.choices?.[0]?.delta?.tool_calls?.[0]
            if (toolCall?.function?.name) {
              onTool?.(toolCall.function)
            }
            const delta = json.choices?.[0]?.delta?.content || ''
            if (delta && onChunk) onChunk(delta)
          } catch (e) {
            // SSE 数据解析失败（可能是截断的 JSON 或非 JSON 行如 "pong"）
            if (line.trim() && line.trim() !== 'pong') {
              console.warn('[Vermes SSE] parse error:', e.message, 'line:', line.slice(0, 100))
            }
          }
        }
      }
      } finally {
        // 确保 reader 被释放（WebView2 可能不自动关闭）
        try { reader.cancel() } catch(e) {}
        clearTimeout(readTimer)
      }
      onDone?.(usageInfo)
    } catch (e) {
      // ── 长任务优化 #4: 断线重连（最多3次） ──
      // 非用户主动取消 + 非 API 错误 = 网络断开，尝试重连
      if (e.name !== 'AbortError' && !signal?.aborted && _sseRetryCount < SSE_MAX_RETRIES) {
        _sseRetryCount++
        const delay = 2000 * _sseRetryCount  // 2s, 4s, 6s 递增
        console.warn(`[Vermes SSE] Connection lost, retry ${_sseRetryCount}/${SSE_MAX_RETRIES} in ${delay/1000}s...`, e.message)
        await new Promise(r => setTimeout(r, delay))
        if (!signal?.aborted) {
          try {
            // 重新发起请求（后端会创建新 stream）
            await api.sendMessage({ model, messages, stream, signal, onChunk, onDone, onError, onTool, onStreamStart, onThinking, provider, attachments })
            _sseRetryCount = 0  // 成功后重置
            return
          } catch (retryErr) {
            console.error(`[Vermes SSE] Retry ${_sseRetryCount} failed:`, retryErr)
            if (_sseRetryCount >= SSE_MAX_RETRIES) {
              onError?.(new Error(`连接中断，已重试 ${SSE_MAX_RETRIES} 次仍失败`))
              return
            }
            // 未达上限则继续循环（由外层递归处理）
            throw retryErr
          }
        }
      }
      if (e.name !== 'AbortError') onError?.(e)
      else onDone?.()
    }
  },

  // 文件上传
  async uploadFile(file) {
    const formData = new FormData()
    formData.append('file', file)
    const url = baseUrl ? `${baseUrl}/api/upload` : '/api/upload'
    const resp = await fetch(url, {
      method: 'POST',
      headers: buildHeaders(),
      body: formData,
      signal: undefined,
    })
    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      throw new Error(`Upload ${resp.status}: ${text}`)
    }
    return resp.json()
  },

  // 配置
  getConfig() { return this.get('/config') },
  getModels() { return this.get('/models') },

  // 设置 token（桌面模式用）
  setToken(t) { token.value = t },
}

export default api
