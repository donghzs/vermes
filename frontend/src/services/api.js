import { ref } from 'vue'

const token = ref('')
let baseUrl = ''

// ✅ 计费模型标识（云端收费，本地免费）
const CLOUD_MODELS = ['deepseek', 'openrouter', 'vbit', 'qwen', 'openai', 'anthropic', 'gemini']
export function isCloudModel(provider) {
  if (!provider) return false
  const p = provider.toLowerCase()
  return CLOUD_MODELS.some(m => p.includes(m))
}

// 计费配额管理
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
  // 已登录用户无限
  const token = localStorage.getItem('vermes_token')
  if (token) return { allowed: true, unlimited: true, remaining: Infinity, source: 'logged_in' }
  
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
      message: '💡 今日云端免费次数已用完\n🔐 扫码登录后可继续使用（每日100→∞次）' }
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
  if (token.value) h['X-Hermes-Session-Token'] = token.value
  return h
}

async function request(path, options = {}) {
  const url = baseUrl ? `${baseUrl}/api${path}` : `/api${path}`

  // 超时处理：默认 60s，如果调用者已传 signal 则合并
  const timeoutMs = options.timeout ?? 60000
  const controller = new AbortController()
  let timeoutId = null

  if (timeoutMs > 0) {
    timeoutId = setTimeout(() => controller.abort(new Error(`请求超时（${timeoutMs / 1000}s）`)), timeoutMs)
  }

  // 合并调用者传入的 signal（如停止生成）
  const combinedSignal = options.signal
    ? AbortSignal.any([controller.signal, options.signal])
    : controller.signal

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
  async sendMessage({ model, messages, stream, signal, onChunk, onDone, onError, onTool, provider }) {
    const body = { model, messages, stream, provider: provider || '' }
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
