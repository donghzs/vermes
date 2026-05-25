import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, { isCloudModel, checkQuota, useQuota } from '../services/api'

const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-msgs-'

function loadFromStorage(key) {
  try { return JSON.parse(localStorage.getItem(key)) || [] } catch(e) { return [] }
}
function saveToStorage(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)) } catch(e) {}
}

// 文件转 base64
function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result.split(',')[1] // 去掉 data:xxx;base64, 前缀
      resolve({
        name: file.name,
        size: file.size,
        mimeType: file.type || 'application/octet-stream',
        base64: base64,
        type: file.type.startsWith('image/') ? 'image' : 'file',
      })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref(loadFromStorage(SESSIONS_KEY))
  const currentSessionId = ref(null)
  const messages = ref([])
  const loading = ref(false)
  const abortController = ref(null)
  const sidebarOpen = ref(true)
  const theme = ref('dark')
  const currentModel = ref(localStorage.getItem('vermes-current-model') || 'deepseek/deepseek-v4-flash')
  const currentProvider = ref(localStorage.getItem('vermes-current-provider') || 'vbit.top')
  const uploading = ref(false)
  const showQuotaModal = ref(false)
  const quotaModalType = ref('') // 'trial_expired' | 'wechat_expired'

  // 在线模式标志
  const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true

  const currentSession = computed(() =>
    sessions.value.find(s => s.id === currentSessionId.value)
  )

  const filteredMessages = computed(() => {
    if (!currentSessionId.value) return []
    return messages.value.filter(m => m.sessionId === currentSessionId.value)
  })

  async function autoClaimIfNeeded() {
    // Already claimed before
    if (localStorage.getItem('vermes-trial-claimed')) return
    try {
      // Call api.vbit.top directly for trial token
      const resp = await fetch('/api/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ device_id: 'vermes-' + (localStorage.getItem('vermes-device-id') || Date.now().toString()) })
      })
      const data = await resp.json()
      // Handle both full token and token_prefix (repeat claim)
      let token = null
      if (data.success && data.data?.token) {
        token = data.data.token
      } else if (data.success && data.data?.token_prefix) {
        // Repeat claim — try saved token from previous session
        token = localStorage.getItem('vermes-trial-token')
      }
      if (token) {
        // Save device ID for reuse
        if (!localStorage.getItem('vermes-device-id')) {
          localStorage.setItem('vermes-device-id', Date.now().toString())
        }
        localStorage.setItem('vermes-trial-token', token)  // save for repeat claim
        // Save trial token as vbit provider key in backend .env
        await fetch('/api/env', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: 'VBIT_API_KEY', value: token })
        }).catch(() => {})
        // Set model to free OpenRouter model via vbit provider
        await fetch('/api/model/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope: 'main', provider: 'vbit', model: 'deepseek/deepseek-v4-flash' })
        }).catch(() => {})
        // Set frontend state
        localStorage.setItem('vermes-current-model', 'deepseek/deepseek-v4-flash')
        localStorage.setItem('vermes-current-provider', 'vbit.top')
        currentModel.value = 'deepseek/deepseek-v4-flash'
        currentProvider.value = 'vbit.top'
        // Mark claimed
        localStorage.setItem('vermes-trial-claimed', '1')
        // Also update the providers list for model dropdown
        const saved = localStorage.getItem('vermes-providers')
        let providers = saved ? JSON.parse(saved) : []
        const vbit = providers.find(p => p.id === 'vbit')
        if (vbit) {
          vbit.key = '***saved***'
          vbit.models = ['deepseek/deepseek-v4-flash', 'openrouter/owl-alpha']
        } else {
          providers.push({ id: 'vbit', name: 'vbit.top', key: '***saved***', baseUrl: 'https://api.vbit.top/v1', models: ['deepseek/deepseek-v4-flash', 'openrouter/owl-alpha'] })
        }
        localStorage.setItem('vermes-providers', JSON.stringify(providers.map(p => ({
          id: p.id, name: p.name,
          key: p.key ? '***saved***' : '',
          baseUrl: p.baseUrl,
          models: p.models || []
        }))))
        console.log('[Vermes✅] Trial token claimed, using deepseek-chat via vbit.top')
      }
    } catch (e) {
      console.warn('[Vermes⚠️] Auto-claim failed:', e.message)
    }
  }

  async function init() {
    try {
      const t = await fetchToken()
      api.setToken(t)
      // 在线模式：不自动领 token，强制微信登录
      if (isOnline) {
        const wechatToken = localStorage.getItem('vermes_wechat_token') || localStorage.getItem('vermes_token')
        if (!wechatToken) {
          // 未登录：清空之前用户的会话数据，保证每个访客独立
          localStorage.removeItem('vermes-sessions')
          localStorage.removeItem('vermes-msgs-')
          localStorage.removeItem('vermes-last-session')
          localStorage.removeItem('vermes-trial-claimed')
          localStorage.removeItem('vermes-providers')
          localStorage.removeItem('vermes-current-model')
          localStorage.removeItem('vermes-current-provider')
          sessions.value = []
          // 不创建会话、不加载 UI，只显示登录界面
          return
        }
      } else {
        // 桌面模式：自动领试用 token
        await autoClaimIfNeeded()
      }
      sessions.value = loadFromStorage(SESSIONS_KEY)
      if (sessions.value.length > 0) {
        const lastId = localStorage.getItem('vermes-last-session') || sessions.value[0].id
        await switchSession(lastId)
      } else {
        await createSession('新会话')
      }
    } catch (e) {
      console.error('❌ init failed:', e)
      // 即使 token 获取失败也创建默认会话，保证 UI 可用
      if (sessions.value.length === 0) {
        createSession('新会话')
      }
    }
  }

  async function fetchToken() {
    try {
      const resp = await fetch('/')
      const html = await resp.text()
      const m = html.match(/window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/)
              || html.match(/window\.__OPENCLAW_SESSION_KEY__\s*=\s*"([^"]+)"/)
      return m ? m[1] : ''
    } catch(e) {
      return ''
    }
  }

  function persistSessions() {
    saveToStorage(SESSIONS_KEY, sessions.value)
  }

  function persistMessages(sessionId) {
    saveToStorage(MESSAGES_KEY_PREFIX + sessionId,
      messages.value.filter(m => m.sessionId === sessionId))
  }

  function createSession(name) {
    const s = { id: Date.now().toString(), name: name || '新会话', createdAt: new Date().toISOString() }
    sessions.value.unshift(s)
    persistSessions()
    switchSession(s.id)
  }

  async function switchSession(id) {
    if (currentSessionId.value) persistMessages(currentSessionId.value)
    currentSessionId.value = id
    localStorage.setItem('vermes-last-session', id)
    messages.value = loadFromStorage(MESSAGES_KEY_PREFIX + id)
  }

  async function sendMessage(content, attachments) {
    console.log('[Vermes💬 sendMessage] content:', JSON.stringify(content), 'attachments:', attachments?.length, 'currentSessionId:', currentSessionId.value, 'messages count:', messages.value.length)
    if ((!content || !content.trim()) && (!attachments || attachments.length === 0)) {
      console.warn('[Vermes💬 sendMessage] BLOCKED: empty content and no attachments')
      return
    }
    if (loading.value) {
      console.warn('[Vermes💬 sendMessage] BLOCKED: already loading')
      return
    }
    if (!currentSessionId.value) {
      console.error('[Vermes💬 sendMessage] ERROR: currentSessionId is null! sessions:', sessions.value.length)
      alert('会话未初始化，请刷新页面重试')
      return
    }

    // ✅ 云端模型配额检查
    const isCloud = isCloudModel(currentProvider.value)
    const quotaCheck = checkQuota(isCloud)
    if (!quotaCheck.allowed) {
      // 触发配额弹窗而非插入消息
      const isLoggedIn = !!localStorage.getItem('vermes_token')
      quotaModalType.value = isLoggedIn ? 'wechat_expired' : 'trial_expired'
      showQuotaModal.value = true
      return
    }

    const uid = Date.now().toString()

    // 处理附件：转 base64 并构建用户消息
    let userContent = content?.trim() || ''
    let processedAttachments = []

    if (attachments && attachments.length > 0) {
      uploading.value = true
      try {
        for (const att of attachments) {
          // 如果已经是处理过的格式（有 base64），直接用
          if (att.base64) {
            processedAttachments.push(att)
          } else if (att.file instanceof File) {
            // 原始 File 对象，需要转 base64
            const converted = await fileToBase64(att.file)
            processedAttachments.push(converted)
          }
        }

        // 构建带附件的用户消息内容
        const parts = []
        for (const att of processedAttachments) {
          if (att.type === 'image') {
            parts.push(`![${att.name}](data:${att.mimeType};base64,${att.base64})`)
          } else {
            parts.push(`📎 **附件:** ${att.name} (${formatSize(att.size)})`)
          }
        }
        if (userContent) parts.unshift(userContent)
        userContent = parts.join('\n\n')
      } finally {
        uploading.value = false
      }
    }

    // 添加用户消息
    console.log('[Vermes💬 sendMessage] Adding user message, sessionId:', currentSessionId.value)
    messages.value.push({
      id: uid, role: 'user', content: userContent,
      sessionId: currentSessionId.value, timestamp: Date.now(),
      attachments: processedAttachments
    })
    persistMessages(currentSessionId.value)

    loading.value = true

    // 添加 AI 回复占位
    const aid = (Date.now() + 1).toString()
    messages.value.push({
      id: aid, role: 'assistant', content: '',
      sessionId: currentSessionId.value, timestamp: Date.now(),
      streaming: true, toolInvocations: []
    })

    const ac = new AbortController()
    abortController.value = ac

    // 构建发送给 API 的消息历史（只发文本，不含 base64 图片避免超长）
    const apiMessages = messages.value
      .filter(m => m.sessionId === currentSessionId.value && !m.streaming)
      .map(m => ({
        role: m.role,
        // 对于用户消息中的图片，用简短描述替代 base64
        content: m.role === 'user' && m.content.includes('data:image')
          ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '[图片]')
          : m.content,
      }))

    try {
      await api.sendMessage({
        model: currentModel.value,
        provider: currentProvider.value,
        messages: apiMessages,
        attachments: processedAttachments.map(a => ({
          name: a.name,
          type: a.type,
          data: a.base64,
          mime: a.mimeType || 'application/octet-stream',
          size: a.size,
        })),
        stream: false,
        signal: ac.signal,
        onChunk: (chunk) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) am.content += chunk
        },
        onTool: (tool) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) am.toolInvocations.push(tool)
        },
        onDone: (data) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (data?.choices?.[0]?.message?.content) {
              am.content = data.choices[0].message.content
            }
            am.streaming = false
          }
          loading.value = false
          abortController.value = null
          if (quotaCheck.source === 'free_daily') {
            useQuota(1)
          }
          persistMessages(currentSessionId.value)
        },
        onError: (err) => {
          console.error('❌ API error:', err)
          const am = messages.value.find(m => m.id === aid)
          if (am) { am.content = '❌ 错误: ' + err.message; am.streaming = false }
          loading.value = false
          abortController.value = null
          persistMessages(currentSessionId.value)
        }
      }).catch(e => {
        // 外层 catch：SSE 流异常时的兜底
        console.error('❌ sendMessage outer catch:', e)
        const am = messages.value.find(m => m.id === aid)
        if (am) { am.content = '❌ 发送失败: ' + e.message; am.streaming = false }
        loading.value = false
        abortController.value = null
        persistMessages(currentSessionId.value)
      })
    } catch (e) {
      console.error('Send error:', e)
      const am = messages.value.find(m => m.id === aid)
      if (am) { am.content = '❌ 发送失败: ' + e.message; am.streaming = false }
      loading.value = false
      abortController.value = null
      persistMessages(currentSessionId.value)
    }
  }

  async function stopGeneration() {
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    loading.value = false
    const am = messages.value.find(m => m.streaming)
    if (am) am.streaming = false
  }

  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    // Tailwind darkMode: 'class' 需要在 <html> 上加/删 dark class
    if (theme.value === 'dark') {
      document.documentElement.classList.add('dark')
    } else {
      document.documentElement.classList.remove('dark')
    }
    localStorage.setItem('vermes-theme', theme.value)
  }

  function toggleSidebar() {
    sidebarOpen.value = !sidebarOpen.value
  }

  function deleteSession(id) {
    const idx = sessions.value.findIndex(s => s.id === id)
    if (idx === -1) return
    sessions.value.splice(idx, 1)
    localStorage.removeItem(MESSAGES_KEY_PREFIX + id)
    persistSessions()
    if (currentSessionId.value === id) {
      if (sessions.value.length > 0) {
        switchSession(sessions.value[0].id)
      } else {
        createSession('新会话')
      }
    }
  }

  function renameSession(id, name) {
    const s = sessions.value.find(s => s.id === id)
    if (s) { s.name = name; persistSessions() }
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  // 初始化主题：同步 <html> 的 dark class
  const saved = localStorage.getItem('vermes-theme')
  if (saved) {
    theme.value = saved
    if (saved === 'dark') document.documentElement.classList.add('dark')
    else document.documentElement.classList.remove('dark')
  } else {
    // 默认跟随系统
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme.value = 'dark'
      document.documentElement.classList.add('dark')
    }
  }

  return {
    sessions, currentSessionId, messages, loading, abortController,
    sidebarOpen, theme, currentModel, currentProvider, uploading,
    showQuotaModal, quotaModalType,
    currentSession, filteredMessages,
    init, createSession, switchSession, sendMessage, stopGeneration,
    toggleTheme, toggleSidebar, deleteSession, renameSession, formatSize,
  }
})
