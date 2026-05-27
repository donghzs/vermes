import { ref } from 'vue'

const token = ref('')
let baseUrl = ''

// 在线模式标志（通过 window.__VERMES_ONLINE__ 设置）
const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true

// ✅ 计费模型标识（云端收费，本地免费）
const CLOUD_MODELS = ['deepseek', 'openrouter', 'vbit', 'qwen', 'openai', 'anthropic', 'gemini', 'xiaomi']

// ✅ 免费试用截止日期
export const TRIAL_EXPIRY = new Date('2026-06-26T23:59:59+08:00')
export function isTrialExpired() {
  return new Date() > TRIAL_EXPIRY
}
export function getTrialDaysLeft() {
  const diff = TRIAL_EXPIRY - new Date()
  return Math.max(0, Math.ceil(diff / 86400000))
}
export function isCloudModel(provider) {
  if (!provider) return false
  const p = provider.toLowerCase()
  return CLOUD_MODELS.some(m => p.includes(m))
}

// 计费配额管理
const WECHAT_QUOTA_KEY = 'vermes_wechat_quota'
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
  localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: newRemaining, date: quota.date }))
}
export function getRemainingQuota() {
  const data = localStorage.getItem('vermes_quota')
  if (!data) return null
  try { return JSON.parse(data) } catch { return null }
}

export function saveQuota(remaining) {
  localStorage.setItem('vermes_quota', JSON.stringify({ remaining, date: new Date().toDateString() }))
}

  // 检查云端模型请求是否允许
export function checkQuota(isCloud) {
  // 试用已过期
  if (isTrialExpired() && !localStorage.getItem('vermes_token')) {
    return { allowed: false, unlimited: false, remaining: 0, source: 'trial_expired' }
  }
  // 在线模式：必须微信登录
  if (isOnline) {
    const token = localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')
    if (!token) {
      return { allowed: false, unlimited: false, remaining: 0, source: 'trial_expired', requireLogin: true }
    }
    // 已登录：通过 One-API 的剩余配额（由 One-API 管理，不做本地限流）
    return { allowed: true, unlimited: true, remaining: 999, source: 'wechat_online' }
  }
  // 已登录用户每天 500 次（桌面版）
  const token = localStorage.getItem('vermes_token')
  if (token) {
    const quota = getWechatDailyQuota()
    if (quota.remaining <= 0) {
      return { allowed: false, unlimited: false, remaining: 0, source: 'wechat_daily' }
    }
    useWechatQuota(1)
    return { allowed: true, unlimited: false, remaining: quota.remaining - 1, source: 'wechat_daily' }
  }
  
  // 本地模型无限
  if (!isCloud) return { allowed: true, unlimited: true, remaining: Infinity, source: 'local' }
  
  // 未登录云端模型：每天 100 次
  const quota = getRemainingQuota()
  const today = new Date().toDateString()
  
  if (!quota || quota.date !== today) {
    saveQuota(100)
    return { allowed: true, unlimited: false, remaining: 100, source: 'free_daily' }
  }
  
  if (quota.remaining <= 0) {
    return { allowed: false, unlimited: false, remaining: 0,
      message: '' } // message not used anymore, modal handles it
  }
  
  return { allowed: true, unlimited: false, remaining: quota.remaining, source: 'free_daily' }
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
      // AbortSignal.any() 需要 Safari 15.4+
      combinedSignal = AbortSignal.any([controller.signal, options.signal])
    } catch {
      // 降级：用 controller.timeout 替代
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
    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      if (resp.status === 429 || text.includes('insufficient_quota') || text.includes('额度已用尽')) {
        throw new Error('体验额度已用完 💡 请在设置中配置自己的 API Key 继续使用')
      }
      throw new Error(`API ${resp.status}: ${text}`)
    }
    return resp
  } catch (e) {
    clearTimeout(timeoutId)
    if (e.name === 'AbortError' && !options.signal?.aborted) {
      throw new Error(`请求超时（${timeoutMs / 1000}秒），请检查网络或切换模型`)
    }
    throw e
  }
}

export default {
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
  async sendMessage({ model, messages, stream, signal, onChunk, onDone, onError, onTool, provider, attachments }) {
    const body = { model, messages, stream, provider: provider || '' }
    if (attachments && attachments.length > 0) body.attachments = attachments
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

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        if (signal?.aborted) { reader.cancel(); break }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop()

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          const data = line.slice(6)
          if (data === '[DONE]') { onDone?.(); return }
          try {
            const json = JSON.parse(data)
            // 工具调用
            const toolCall = json.choices?.[0]?.delta?.tool_calls?.[0]
            if (toolCall?.function?.name) {
              onTool?.(toolCall.function)
            }
            const delta = json.choices?.[0]?.delta?.content || ''
            if (delta && onChunk) onChunk(delta)
          } catch (e) {}
        }
      }
      onDone?.()
    } catch (e) {
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
}
