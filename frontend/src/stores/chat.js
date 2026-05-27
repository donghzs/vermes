import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, { isCloudModel, checkQuota, useQuota, checkQuotaServer, reportQuotaSpend, getWechatDailyQuota, WECHAT_QUOTA_KEY } from '../services/api'

// ── v2: 未登录拦截弹窗类型 ──
const QUOTA_NEED_LOGIN = 'need_login'

const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-msgs-'
const IMAGE_DB = 'vermes-images'
const IMAGE_STORE = 'attachments'

// ── IndexedDB 图片存储（无大小限制） ──
function openImageDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IMAGE_DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(IMAGE_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function saveImage(key, base64Data) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    tx.objectStore(IMAGE_STORE).put(base64Data, key)
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej })
  } catch(e) { console.warn('[Vermes] 图片存储失败:', e) }
}

async function loadImage(key) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readonly')
    const req = tx.objectStore(IMAGE_STORE).get(key)
    return new Promise((res) => { req.onsuccess = () => res(req.result); req.onerror = () => res(null) })
  } catch { return null }
}

async function deleteImages(keys) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    const store = tx.objectStore(IMAGE_STORE)
    keys.forEach(k => store.delete(k))
  } catch {}
}

// ── 持久化：base64 提取到 IndexedDB，localStorage 只存文本 ──
const BASE64_RE = /!\[([^\]]*)\]\(data:image[^)]+\)/g

function stripBase64FromContent(content, messageId) {
  const images = {}
  let idx = 0
  const prefix = messageId || Date.now().toString()
  const stripped = content.replace(BASE64_RE, (match, name) => {
    const key = `${prefix}-${idx++}`
    images[key] = match
    return `🖼️ ${name || '图片'}`
  })
  return { stripped, images }
}

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
  const currentModel = ref(localStorage.getItem('vermes-current-model') || 'mimo-v2.5')
  const currentProvider = ref(localStorage.getItem('vermes-current-provider') || 'vbit')
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

  // v2: 桌面版不再自动 claim trial token
  // 免费体验仅限微信登录用户，未登录用户发消息时会弹出引导登录的弹窗
  async function autoClaimIfNeeded() {
    // 不再自动 claim，改为引导微信登录
    console.log('[Vermesℹ️] v2: 免费体验仅限微信登录，跳过自动 claim')
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
    const msgs = messages.value.filter(m => m.sessionId === sessionId)
    // 剥离 base64 图片到 IndexedDB，localStorage 只存文本
    const lean = []
    for (const m of msgs) {
      if (m.role === 'user' && m.content && m.content.includes('data:image')) {
        const { stripped, images } = stripBase64FromContent(m.content, m.id)
        // 异步存图片到 IndexedDB（不阻塞）
        for (const [key, data] of Object.entries(images)) {
          saveImage(key, data)
        }
        lean.push({ ...m, content: stripped, _imageKeys: Object.keys(images) })
      } else {
        lean.push(m)
      }
    }
    saveToStorage(MESSAGES_KEY_PREFIX + sessionId, lean)
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
    const stored = loadFromStorage(MESSAGES_KEY_PREFIX + id)
    // 从 IndexedDB 恢复图片
    for (const m of stored) {
      if (m._imageKeys && m._imageKeys.length > 0) {
        const parts = [m.content]
        for (const key of m._imageKeys) {
          const img = await loadImage(key)
          if (img) parts.push(img)
        }
        m.content = parts.join('\n\n')
        delete m._imageKeys
      }
    }
    messages.value = stored
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

    // ✅ v2: 云端模型配额检查（必须微信登录）
    const isCloud = isCloudModel(currentProvider.value)
    if (isCloud) {
      const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
      const isLoggedIn = !!(localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token'))
      
      // 未登录 → 弹出「请先登录」弹窗
      if (!isLoggedIn || !wechatOpenid) {
        quotaModalType.value = QUOTA_NEED_LOGIN
        showQuotaModal.value = true
        return
      }
      
      // 已登录：查询服务端积分
      const serverCheck = await checkQuotaServer(wechatOpenid)
      if (serverCheck.success) {
        if (serverCheck.data.trial_expired) {
          quotaModalType.value = 'trial_expired'
          showQuotaModal.value = true
          return
        }
        if (serverCheck.data.remaining <= 0) {
          quotaModalType.value = 'wechat_expired'
          showQuotaModal.value = true
          return
        }
      }
    }
    
    const quotaCheck = checkQuota(isCloud)
    if (!quotaCheck.allowed) {
      quotaModalType.value = 'wechat_expired'
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
          ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '').trim()
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
        stream: true,
        signal: ac.signal,
        onChunk: (chunk) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) am.content += chunk
        },
        onTool: (tool) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) am.toolInvocations.push(tool)
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            am.streaming = false
          }
          loading.value = false
          abortController.value = null
          if (quotaCheck.source === 'free_daily') {
            useQuota(1)
          }
          // 精确计费：后端已自动上报消费（基于 SSE 流 usage token 数）
          // 前端只需刷新配额显示
          const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
          if (wechatOpenid && isCloud) {
            // 如果 onDone 传回了 usage，用精确 token 数更新本地积分
            const usageData = typeof usageInfo === 'object' ? usageInfo : null
            if (usageData && usageData.total_tokens > 0) {
              const consumedPoints = Math.max(1, Math.ceil(usageData.total_tokens / 1000))
              const localQuota = getWechatDailyQuota()
              const newRemaining = Math.max(0, localQuota.remaining - consumedPoints)
              localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: newRemaining, date: localQuota.date }))
            }
            window.dispatchEvent(new Event('quota-updated'))
          }
          persistMessages(currentSessionId.value)
        },
        onError: (err) => {
          console.error('❌ API error:', err)
          // Token 402 = trial exhausted, show quota modal
          if (err.message.includes('402') || err.message.includes('免费体验Token')) {
            quotaModalType.value = 'trial_expired'
            showQuotaModal.value = true
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = '💡 免费体验次数已用完'; am.streaming = false }
          } else {
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = '❌ 错误: ' + err.message; am.streaming = false }
          }
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
