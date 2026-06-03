/**
 * chat.js — 核心聊天 Store（精简版）
 *
 * 拆分出的子模块：
 *   chat-scroll.js   — 流式输出滚动调度
 *   chat-session.js  — 会话管理 + localStorage 三级清理
 *   chat-quota.js    — 配额检查 + 错误友好化
 *   chat-storage.js  — IndexedDB 图片 + localStorage 异步写入
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, { isCloudModel, checkQuota, checkQuotaServer, getWechatDailyQuota, WECHAT_QUOTA_KEY } from '../services/api'
import { showToast } from '../utils/toast'
import { loadFromStorage, saveToStorage, stripBase64FromContent, fileToBase64, flushStorageWrites, onStorageWriteFailure, loadImage, deleteImages, loadMessagesFromIDB, saveMessagesToIDB, migrateFromLocalStorage, saveMessagesToAPI, loadMessagesFromAPI, deleteMessagesFromAPI } from './chat-storage'
import { scheduleScroll, flushScroll, setScrollTarget } from './chat-scroll'
import {
  uid, SESSIONS_KEY, MESSAGES_KEY_PREFIX, MAX_SESSIONS, QUOTA_NEED_LOGIN,
  SESSION_TEMPLATES, QUICK_START_SUGGESTIONS,
  evictOldSessions, enforceSessionLimit, persistMessages, trimCurrentSessionMessages,
  createSession as _createSession, deleteSession as _deleteSession,
  renameSession as _renameSession, pinSession as _pinSession,
  getMessageCount as _getMessageCount, getFirstMessage as _getFirstMessage,
  searchAllMessages as _searchAllMessages, getSessionStats as _getSessionStats,
  exportSession as _exportSession, importSession as _importSession,
  migrateFromLocalStorage as _migrateFromLocalStorage,
} from './chat-session'
import { friendlyError, formatSize } from './chat-quota'
import { DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID } from '../config/defaults'

// ── P1-6: 防御纵深 — 剥离字符串中的 HTML 标签 ──
function stripHtml(str) {
  if (!str) return str
  return str.replace(/<[^>]*>/g, '')
}

// 重导出供外部组件使用
export { setScrollTarget }
export { SESSION_TEMPLATES, QUICK_START_SUGGESTIONS }
export { formatSize }

export const useChatStore = defineStore('chat', () => {
  const sessions = ref(loadFromStorage(SESSIONS_KEY))
  const currentSessionId = ref(null)
  const messages = ref([])
  const loading = ref(false)
  const abortController = ref(null)
  const sidebarOpen = ref(true)
  const theme = ref('dark')
  let _beforeunloadRegistered = false
  const currentModel = ref(localStorage.getItem('vermes-current-model') || DEFAULT_MODEL_ID)
  const currentProvider = ref(localStorage.getItem('vermes-current-provider') || DEFAULT_PROVIDER_ID)
  const uploading = ref(false)
  const showQuotaModal = ref(false)
  const quotaModalType = ref('need_login')
  const compareModels = ref([])
  const activeStreamId = ref(null)
  const statusMessages = ref([])
  const lastTokenUsage = ref(null)
  const _compareAbortControllers = ref([])

  const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true
  const isWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent)

  const currentSession = computed(() =>
    sessions.value.find(s => s.id === currentSessionId.value)
  )

  const filteredMessages = computed(() => {
    if (!currentSessionId.value) return []
    return messages.value.filter(m => m.sessionId === currentSessionId.value)
  })

  // ── v2: 桌面版不再自动 claim trial token ──
  async function autoClaimIfNeeded() {
    // 免费体验仅限微信登录用户，未登录用户发消息时会弹出引导登录的弹窗
  }

  // ── 初始化 ──
  async function init() {
    onStorageWriteFailure(() => evictOldSessions(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId.value))
    // 启动时迁移 localStorage → IndexedDB（一次性）
    try { await _migrateFromLocalStorage(MESSAGES_KEY_PREFIX) } catch(e) {}
    try {
      const t = await fetchToken()
      api.setToken(t)
      if (isOnline) {
        const wechatToken = localStorage.getItem('vermes_wechat_token') || localStorage.getItem('vermes_token')
        if (!wechatToken) {
          localStorage.removeItem('vermes-sessions')
          localStorage.removeItem('vermes-msgs-')
          localStorage.removeItem('vermes-last-session')
          localStorage.removeItem('vermes-trial-claimed')
          localStorage.removeItem('vermes-providers')
          localStorage.removeItem('vermes-current-model')
          localStorage.removeItem('vermes-current-provider')
          sessions.value = []
          return
        }
      } else {
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
      if (sessions.value.length === 0) {
        await createSession('新会话')
      }
    }

    if (!_beforeunloadRegistered) {
      _beforeunloadRegistered = true
      window.addEventListener('beforeunload', () => {
        flushStorageWrites()
        if (currentSessionId.value && messages.value.length > 0) {
          const msgs = messages.value.filter(m => m.sessionId === currentSessionId.value)
          if (msgs.length > 0) {
            const lean = []
            for (const m of msgs) {
              if (m.role === 'user' && m.content && m.content.includes('data:image')) {
                const { stripped, images } = stripBase64FromContent(m.content, m.id)
                lean.push({ ...m, content: stripped, _imageKeys: Object.keys(images) })
              } else {
                lean.push(m)
              }
            }
            try { localStorage.setItem(MESSAGES_KEY_PREFIX + currentSessionId.value, JSON.stringify(lean)) } catch {}
          }
        }
      })
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

  // ── 会话管理（委托 chat-session.js） ──

  async function createSession(name, template) {
    const s = _createSession(sessions.value, messages.value, name, template, SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId.value)
    persistSessions()
    await switchSession(s.id)
    const tpl = template || SESSION_TEMPLATES[0]
    if (tpl.systemPrompt) {
      messages.value.push({
        id: uid(), role: 'system', content: tpl.systemPrompt,
        sessionId: s.id, timestamp: Date.now(),
      })
      await persistMessages(s.id, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    }
  }

  async function switchSession(id) {
    flushStorageWrites()
    const oldSessionId = currentSessionId.value
    if (oldSessionId && oldSessionId !== id) {
      await persistMessages(oldSessionId, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    }
    currentSessionId.value = id
    try { localStorage.setItem('vermes-last-session', id) } catch(e) { evictOldSessions(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId.value) }
    // 优先从 API 读取（pywebview macOS 不持久化 IndexedDB）
    let stored = await loadMessagesFromAPI(id)
    if (!stored || stored.length === 0) {
      stored = await loadMessagesFromIDB(id)  // 降级 IndexedDB
    }
    if (!stored || stored.length === 0) {
      stored = loadFromStorage(MESSAGES_KEY_PREFIX + id)  // 降级 localStorage
    }

    if (stored.length === 0) {
      const memMsgs = messages.value.filter(m => m.sessionId === id)
      if (memMsgs.length > 0) {
        stored = memMsgs
        await saveMessagesToIDB(id, memMsgs)
        await saveMessagesToAPI(id, memMsgs)
      }
    }

    messages.value = stored.map(m => ({ ...m }))

    for (const m of messages.value) {
      if (m.toolInvocations) {
        for (const t of m.toolInvocations) {
          if (t.status === 'running') { t.status = 'error'; t.duration = t.duration || 0 }
        }
      }
      if (m.streaming) m.streaming = false
    }

    const imageLoadPromises = []
    for (let i = 0; i < stored.length; i++) {
      const m = stored[i]
      if (m._imageKeys && m._imageKeys.length > 0) {
        const promise = (async () => {
          const parts = [m.content]
          const imgPromises = m._imageKeys.map(key => loadImage(key))
          const imgs = await Promise.all(imgPromises)
          for (const img of imgs) { if (img) parts.push(img) }
          const msgIndex = messages.value.findIndex(msg => msg.id === m.id)
          if (msgIndex >= 0) {
            messages.value[msgIndex].content = parts.join('\n\n')
            delete messages.value[msgIndex]._imageKeys
          }
        })()
        imageLoadPromises.push(promise)
      }
    }
    if (imageLoadPromises.length > 0) {
      Promise.all(imageLoadPromises).catch(e => { console.warn('[Vermes] 图片加载失败:', e) })
    }
  }

  async function deleteSession(id) {
    await _deleteSession(sessions.value, messages.value, id, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    if (currentSessionId.value === id) {
      if (sessions.value.length > 0) {
        await switchSession(sessions.value[0].id)
      } else {
        await createSession('新会话')
      }
    }
  }

  function renameSession(id, name) { _renameSession(sessions.value, id, name, SESSIONS_KEY) }
  function pinSession(id, pinned) { _pinSession(sessions.value, id, pinned, SESSIONS_KEY) }
  function getMessageCount(sessionId) { return _getMessageCount(sessionId) }
  function getFirstMessage(sessionId) { return _getFirstMessage(sessionId) }
  function searchAllMessages(keyword, dateFilter) { return _searchAllMessages(sessions.value, keyword, dateFilter, MESSAGES_KEY_PREFIX) }
  function getSessionStats(sessionId) { return _getSessionStats(messages.value, sessionId, currentModel.value) }
  async function exportSession(sessionId, format) { return _exportSession(sessions.value, sessionId, format) }
  async function importSession(jsonText) { return _importSession(sessions.value, messages.value, jsonText, SESSIONS_KEY, MESSAGES_KEY_PREFIX) }

  // ── 发送消息 ──

  async function sendMessage(content, attachments, _model_, _provider_, _isRegenerate_) {
    const modelId = _model_ || currentModel.value
    const providerId = _provider_ || currentProvider.value

    if ((!content || !content.trim()) && (!attachments || attachments.length === 0)) return
    if (loading.value) return
    if (!currentSessionId.value) {
      showToast('会话未初始化，请刷新页面重试', 'error')
      return
    }

    // 云端模型配额检查
    const isCloud = isCloudModel(providerId)
    const isVbitFreeTrial = ['vbit', 'vbit.top'].includes(String(providerId).toLowerCase())
    // 免费体验必须微信登录
    if (isVbitFreeTrial) {
      const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
      const isLoggedIn = !!(localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token'))
      if (!isLoggedIn || !wechatOpenid) {
        quotaModalType.value = QUOTA_NEED_LOGIN
        showQuotaModal.value = true
        return
      }
      const serverCheck = await checkQuotaServer(wechatOpenid)
      if (serverCheck.success) {
        if (serverCheck.data.remaining <= 0) { quotaModalType.value = 'wechat_expired'; showQuotaModal.value = true; return }
      }
      const quotaCheck = checkQuota(isCloud)
      if (!quotaCheck.allowed) { quotaModalType.value = 'wechat_expired'; showQuotaModal.value = true; return }
    }

    const msgId = uid()
    let userContent = content?.trim() || ''
    let processedAttachments = []

    if (attachments && attachments.length > 0) {
      uploading.value = true
      try {
        for (const att of attachments) {
          if (att.base64) processedAttachments.push(att)
          else if (att.file instanceof File) processedAttachments.push(await fileToBase64(att.file))
        }
        const parts = []
        for (const att of processedAttachments) {
          if (att.type === 'image') parts.push(`![${att.name}](data:${att.mimeType};base64,${att.base64})`)
          else parts.push(`📎 **附件:** ${att.name} (${formatSize(att.size)})`)
        }
        if (userContent) parts.unshift(userContent)
        userContent = parts.join('\n\n')
      } finally { uploading.value = false }
    }

    if (!_isRegenerate_) {
      messages.value.push({
        id: msgId, role: 'user', content: userContent,
        sessionId: currentSessionId.value, timestamp: Date.now(),
        attachments: processedAttachments
      })
      await persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      // 更新会话最后活跃时间
      const _s = sessions.value.find(s => s.id === currentSessionId.value)
      if (_s) _s.lastActive = new Date().toISOString()
      scheduleScroll()  // 用户发消息后滚到底部
    }

    loading.value = true
    const aid = uid()

    messages.value.push({
      id: aid, role: 'assistant', content: '',
      sessionId: currentSessionId.value, timestamp: Date.now(),
      streaming: true, toolInvocations: []
    })

    const ac = new AbortController()
    abortController.value = ac

    const allMsgs = messages.value.filter(m => m.sessionId === currentSessionId.value && !m.streaming)
    const recentImageMsgIds = new Set(
      allMsgs.filter(m => m.role === 'user' && m.content?.includes('data:image') && m.id !== msgId).slice(-5).map(m => m.id)
    )
    const apiMessages = allMsgs.map(m => ({
      role: m.role,
      // Strip ALL base64 images: current msg (already in attachments) + old msgs (save tokens)
      // Keep last 5 recent image messages' base64 only for visual context continuity
      content: m.role === 'user' && m.content?.includes('data:image') && !recentImageMsgIds.has(m.id)
        ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '[图片]').trim() : m.content,
    }))

    try {
      await api.sendMessage({
        model: modelId, provider: providerId, messages: apiMessages,
        session_id: currentSessionId.value,
        attachments: processedAttachments.map(a => ({
          name: a.name, type: a.type, data: a.base64,
          mime: a.mimeType || 'application/octet-stream', size: a.size,
        })),
        stream: true, signal: ac.signal,
        onStreamStart: (streamId) => { activeStreamId.value = streamId },
        onChunk: (chunk) => {
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          if (!am._streamBuffer) {
            am._streamBuffer = ''
            am._streamBufTimer = setInterval(() => {
              if (am._streamBuffer) {
                am.content += am._streamBuffer; am._streamBuffer = ''
                scheduleScroll()
              }
            }, 80)
          }
          am._streamBuffer += chunk
        },
        onTool: (tool) => {
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          if (tool.type === 'tool_start') {
            am.toolInvocations.push({
              id: tool.tool_call_id || tool.name, name: tool.name,
              arguments: tool.arguments, status: 'running', startTime: Date.now()
            })
            am._toolCount = (am._toolCount || 0) + 1
            scheduleScroll()
          } else if (tool.type === 'tool_end') {
            const inv = am.toolInvocations.find(t => t.id === tool.tool_call_id || t.name === tool.name)
            if (inv) { inv.status = tool.is_error ? 'error' : 'done'; inv.duration = tool.duration; inv.result_preview = tool.result_preview || '' }
          }
        },
        onThinking: (event) => {
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          am._currentStep = event.iteration || ((am._currentStep || 0) + 1)
          if (!am._streamStartTime) am._streamStartTime = Date.now()
        },
        onStatus: (event) => {
          statusMessages.value.push({
            id: uid(),
            type: event.type,
            message: event.message,
            timestamp: Date.now(),
          })
          scheduleScroll()
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
            am.streaming = false; am._currentStep = null; am._streamStartTime = null; am._toolCount = null
          }
          flushScroll()
          loading.value = false; abortController.value = null; activeStreamId.value = null
          statusMessages.value = []
          if (usageInfo && usageInfo.total_tokens > 0) {
            lastTokenUsage.value = usageInfo
          }
          const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
          if (wechatOpenid && isVbitFreeTrial) {
            const usageData = typeof usageInfo === 'object' ? usageInfo : null
            if (usageData && usageData.total_tokens > 0) {
              const consumedPoints = Math.max(1, Math.ceil(usageData.total_tokens / 1000))
              const localQuota = getWechatDailyQuota()
              const newRemaining = Math.max(0, localQuota.remaining - consumedPoints)
              try { localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: newRemaining, date: localQuota.date })) } catch(e) { evictOldSessions(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId.value) }
            }
            window.dispatchEvent(new Event('quota-updated'))
          }
          persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
        },
        onError: (err) => {
          statusMessages.value = []
          console.error('❌ API error:', err)
          const msg = stripHtml(err.message || '')  // P1-6: 剥离 HTML 标签防 XSS
          if (msg.includes('额度已用尽') || msg.includes('insufficient_quota') || msg.includes('体验额度已用完') || msg.includes('402') || msg.includes('免费体验Token')) {
            quotaModalType.value = 'wechat_expired'; showQuotaModal.value = true
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = '💡 今日免费额度已用完，请明天再来或配置自己的 API Key'; am.streaming = false }
          } else {
            const friendlyMsg = friendlyError(msg)
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = friendlyMsg; am.streaming = false }
          }
          const am = messages.value.find(m => m.id === aid)
          if (am && am._streamBufTimer) {
            clearInterval(am._streamBufTimer); am._streamBufTimer = null
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
          }
          loading.value = false; abortController.value = null
          persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
        }
      }).catch(e => {
        console.error('❌ sendMessage outer catch:', e)
        const am = messages.value.find(m => m.id === aid)
        if (am) {
          if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
          if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
          am.content = '❌ 发送失败: ' + e.message; am.streaming = false
        }
        loading.value = false; abortController.value = null
        persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      })
    } catch (e) {
      console.error('Send error:', e)
      const am = messages.value.find(m => m.id === aid)
      if (am) { am.content = '❌ 发送失败: ' + e.message; am.streaming = false }
      loading.value = false; abortController.value = null
      persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    }
  }

  // ── 多模型对比 ──

  async function sendCompareMessage(content, attachments, models) {
    if (!models || models.length < 2) return
    if (loading.value) return
    if (!currentSessionId.value) { showToast('会话未初始化', 'error'); return }

    const providerId = currentProvider.value
    const isVbitFreeTrial = ['vbit', 'vbit.top'].includes(String(providerId).toLowerCase())
    if (isVbitFreeTrial) {
      const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
      const isLoggedIn = !!(localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token'))
      if (!isLoggedIn || !wechatOpenid) { quotaModalType.value = QUOTA_NEED_LOGIN; showQuotaModal.value = true; return }
      const serverCheck = await checkQuotaServer(wechatOpenid)
      if (serverCheck.success) {
        if (serverCheck.data.remaining < models.length) {
          showToast(`对比需要 ${models.length} 积分，当前仅剩 ${serverCheck.data.remaining}`, 'error')
          return
        }
      }
    }

    let userContent = content?.trim() || ''
    let processedAttachments = []
    if (attachments && attachments.length > 0) {
      uploading.value = true
      try {
        for (const att of attachments) {
          if (att.base64) processedAttachments.push(att)
          else if (att.file instanceof File) processedAttachments.push(await fileToBase64(att.file))
        }
        const parts = []
        for (const att of processedAttachments) {
          if (att.type === 'image') parts.push(`![${att.name}](data:${att.mimeType};base64,${att.base64})`)
          else parts.push(`📎 **附件:** ${att.name} (${formatSize(att.size)})`)
        }
        if (userContent) parts.unshift(userContent)
        userContent = parts.join('\n\n')
      } finally { uploading.value = false }
    }

    const msgId = uid()
    messages.value.push({
      id: msgId, role: 'user', content: userContent,
      sessionId: currentSessionId.value, timestamp: Date.now(),
    })
    persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    scheduleScroll()  // 用户发消息后滚到底部

    loading.value = true
    const compareAbortControllers = []
    _compareAbortControllers.value = compareAbortControllers
    const modelLabel = (m) => `**[🔬 ${m.name || m.id}]**\n`
    const aides = []
    for (const m of models) {
      const aid = uid(); aides.push(aid)
      messages.value.push({
        id: aid, role: 'assistant', content: modelLabel(m),
        sessionId: currentSessionId.value, timestamp: Date.now(),
        streaming: true, toolInvocations: [], _compareModel: m.name || m.id,
      })
    }

    const apiMessages = messages.value
      .filter(m => m.sessionId === currentSessionId.value && !m.streaming)
      .map(m => ({
        role: m.role,
        content: m.role === 'user' && m.content.includes('data:image')
          ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '').trim() : m.content,
      }))

    const attachPayload = processedAttachments.map(a => ({
      name: a.name, type: a.type, data: a.base64,
      mime: a.mimeType || 'application/octet-stream', size: a.size,
    }))

    const tasks = models.map((model, idx) => {
      const aid = aides[idx]
      return (async () => {
        const ac = new AbortController(); compareAbortControllers.push(ac)
        try {
          await api.sendMessage({
            model: model.id, provider: model.provider || providerId,
            messages: apiMessages, attachments: attachPayload,
            session_id: currentSessionId.value,
            stream: true, signal: ac.signal,
            onChunk: (chunk) => { const am = messages.value.find(m => m.id === aid); if (am) am.content += chunk },
            onTool: (tool) => { const am = messages.value.find(m => m.id === aid); if (am) am.toolInvocations.push(tool) },
            onDone: () => { const am = messages.value.find(m => m.id === aid); if (am) am.streaming = false },
            onError: (err) => {
              const am = messages.value.find(m => m.id === aid)
              if (am) { am.content = modelLabel(model) + '❌ 错误: ' + (err.message || '未知'); am.streaming = false }
            },
          })
        } catch (e) {
          const am = messages.value.find(m => m.id === aid)
          if (am) { am.content = modelLabel(model) + '❌ 发送失败: ' + e.message; am.streaming = false }
        }
      })()
    })

    await Promise.allSettled(tasks)
    loading.value = false
    const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
    if (wechatOpenid && isVbitFreeTrial) window.dispatchEvent(new Event('quota-updated'))
    persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    compareModels.value = []
  }

  // ── 停止生成 ──

  async function stopGeneration() {
    if (activeStreamId.value) {
      try {
        const token = localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')
        const headers = { 'Content-Type': 'application/json' }
        if (token) headers['X-Hermes-Session-Token'] = token
        const apiPrefix = (typeof window !== 'undefined' && window.__VERMES_ONLINE__) ? '/v1' : '/api'
        fetch(`${apiPrefix}/stop-generation`, {
          method: 'POST', headers,
          body: JSON.stringify({ stream_id: activeStreamId.value })
        }).catch(() => {})
        activeStreamId.value = null
      } catch (e) { /* ignore */ }
    }
    if (abortController.value) { abortController.value.abort(); abortController.value = null }
    for (const ac of _compareAbortControllers.value) { try { ac.abort() } catch(e) {} }
    _compareAbortControllers.value = []
    loading.value = false
    // Clean up streaming state on all active messages
    messages.value.filter(m => m.streaming).forEach(m => {
      m.streaming = false
      if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
      if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
    })
    persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
  }

  // ── 主题 & 侧边栏 ──

  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    if (theme.value === 'dark') document.documentElement.classList.add('dark')
    else document.documentElement.classList.remove('dark')
    try { localStorage.setItem('vermes-theme', theme.value) } catch(e) {}
  }

  function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value }

  // 初始化主题
  const saved = localStorage.getItem('vermes-theme')
  if (saved) {
    theme.value = saved
    if (saved === 'dark') document.documentElement.classList.add('dark')
    else document.documentElement.classList.remove('dark')
  } else {
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme.value = 'dark'
      document.documentElement.classList.add('dark')
    }
  }

  if (!saved) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      theme.value = e.matches ? 'dark' : 'light'
      if (e.matches) document.documentElement.classList.add('dark')
      else document.documentElement.classList.remove('dark')
    })
  }

  return {
    sessions, currentSessionId, messages, loading, abortController,
    sidebarOpen, theme, currentModel, currentProvider, uploading,
    showQuotaModal, quotaModalType, compareModels, activeStreamId, isOnline,
    statusMessages, lastTokenUsage,
    currentSession, filteredMessages,
    init, createSession, switchSession, sendMessage, sendCompareMessage, stopGeneration,
    toggleTheme, toggleSidebar, deleteSession, renameSession, pinSession,
    getMessageCount, getFirstMessage, formatSize,
    searchAllMessages, getSessionStats, exportSession, importSession,
  }
})
