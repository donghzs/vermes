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
import { api } from '../api'
import { showToast } from '../utils/toast'
import {
  SESSION_TEMPLATES,
  QUICK_START_SUGGESTIONS,
  createSession as _createSession,
  deleteSession as _deleteSession,
  renameSession as _renameSession,
  pinSession as _pinSession,
  searchAllMessages as _searchAllMessages,
  getSessionStats as _getSessionStats,
  exportSession as _exportSession,
  importSession as _importSession,
  getMessageCount as _getMessageCount,
  getFirstMessage as _getFirstMessage,
  evictOldSessions as _evictOldSessions,
} from './chat-session'
import { loadFromStorage, saveToStorage, loadMessagesFromIDB, persistMessages } from './chat-storage'
import { uid, flushStorageWrites, scheduleScroll, flushScroll } from './chat-scroll'
import { checkQuota, getWechatDailyQuota } from './chat-quota'
import { checkOllamaStatus, deleteOllamaModel } from './providers'
import { getChatTransport } from '../services/chat-transport'

// 常量
const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-messages-'
const DEFAULT_MODEL_ID = localStorage.getItem('vermes-default-model') || 'miMo'
const DEFAULT_PROVIDER_ID = localStorage.getItem('vermes-default-provider') || 'xiaomi'

// ── 全局状态 ──
const streamConnected = ref(false)

export const useChatStore = defineStore('chat', () => {
  const sessions = ref(loadFromStorage(SESSIONS_KEY))
  const currentSessionId = ref(null)
  const messages = ref([])
  const sessionLoading = ref({})
  const loading = computed(() =>
    currentSessionId.value ? sessionLoading.value[currentSessionId.value] || false : false
  )
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

  // ── 进化签到简报（每日首次启动时注入） ──
  async function injectEvolutionBriefing() {
    const today = new Date().toISOString().slice(0, 10)
    if (localStorage.getItem('vermes-evo-briefing-date') === today) return
    try {
      const r = await fetch('/api/evolution/status')
      if (!r.ok) return
      const s = await r.json()
      if (!s.active || (s.total_outcomes || 0) < 5) return

      const lines = ['📊 **进化简报**\n']
      lines.push(`完成 **${s.total_outcomes}** 次工具调用，成功率 **${s.success_rate}%**`)
      if (s.current_emotion) lines.push(`😌 当前状态: ${s.current_emotion}`)
      if ((s.anti_patterns_count || 0) > 0) {
        lines.push(`⚠️ 已识别 **${s.anti_patterns_count}** 个反模式`)
      }
      if (s.recent_failures?.length) {
        const f = s.recent_failures[0]
        lines.push(`最近遇到 ${f[0]} 的 ${f[1]} 问题，下次我会注意`)
      }
      if (s.top_domains?.length) {
        lines.push(`活跃领域: ${s.top_domains.slice(0, 3).map(d => d[0]).join('、')}`)
      }

      messages.value.push({
        id: `evo-briefing-${today}`,
        role: 'assistant',
        content: lines.join('\n'),
        sessionId: currentSessionId.value,
        timestamp: Date.now(),
        streaming: false,
        toolInvocations: [],
        _isBriefing: true
      })
      localStorage.setItem('vermes-evo-briefing-date', today)
    } catch { /* 静默失败 */ }
  }

  // ── 初始化 ──
  async function init() {
    try {
      // 恢复最后使用的会话
      if (sessions.value.length > 0) {
        const lastId = localStorage.getItem('vermes-last-session') || sessions.value[0].id
        await switchSession(lastId)
      } else {
        await createSession('新 Agent')
      }
      // 注入进化简报（非阻塞）
      await injectEvolutionBriefing()
    } catch (e) {
      console.error('❌ init failed:', e)
      if (sessions.value.length === 0) {
        await createSession('新 Agent')
      }
    }
  }

  let _initDone = false
  async function initOnce() {
    if (_initDone) return
    _initDone = true
    await init()
  }

  // ── 会话管理 ──
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
    // 清理旧会话的 streaming 状态和定时器，防止内存泄漏
    if (oldSessionId && oldSessionId !== id) {
      messages.value.filter(m => m.streaming).forEach(m => {
        m.streaming = false
        if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
        if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
      })
      await persistMessages(oldSessionId, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    }

    // 持久化旧会话的 loading 状态
    if (oldSessionId && sessionLoading.value[oldSessionId]) {
      sessionLoading.value[oldSessionId] = false
    }

    currentSessionId.value = id
    localStorage.setItem('vermes-last-session', id)

    // 加载新会话的消息
    try {
      const loaded = await loadMessagesFromIDB(id)
      messages.value = loaded || []
    } catch (e) {
      console.error('[Vermes] 加载会话失败:', e)
      messages.value = []
    }
    // 恢复新会话的 loading 状态
    // 检查新会话是否已有 loading 状态
    if (!sessionLoading.value[id]) {
      sessionLoading.value[id] = false
    }
  }

  async function deleteSession(id) {
    try { await fetch('/api/agent/clean/' + id, { method: 'DELETE' }) } catch {}
    await _deleteSession(sessions.value, messages.value, id, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    if (currentSessionId.value === id) {
      if (sessions.value.length > 0) {
        await switchSession(sessions.value[0].id)
      } else {
        await createSession('新 Agent')
      }
    }
  }

  function renameSession(id, name) { _renameSession(sessions.value, id, name, SESSIONS_KEY) }

  function pinSession(id, pinned) { _pinSession(sessions.value, id, pinned, SESSIONS_KEY) }

  function searchAllSessions(query) { return _searchAllMessages(sessions.value, query, MESSAGES_KEY_PREFIX) }

  async function exportSession(id, format) { return _exportSession(sessions.value, id, format) }
  async function importSession(jsonText) { return _importSession(sessions.value, messages.value, jsonText, SESSIONS_KEY, MESSAGES_KEY_PREFIX) }

  // ── 发送消息 ──

  async function sendMessage(content, attachments, _model_, _provider_, _isRegenerate_) {
    const modelId = _model_ || currentModel.value
    const providerId = _provider_ || currentProvider.value

    if ((!content || !content.trim()) && (!attachments || attachments.length === 0)) return
    if (currentSessionId.value && sessionLoading.value[currentSessionId.value]) return
    if (!currentSessionId.value) {
      showToast('会话未初始化，请刷新页面重试', 'error')
      return
    }

    // 配额检查
    const quotaOk = await checkQuota(providerId, modelId)
    if (quotaOk === false) {
      showQuotaModal.value = true
      return
    }

    const msgId = uid()

    // 检查是否是 Ollama 待下载模型
    if (providerId === 'ollama') {
      try {
        const status = await checkOllamaStatus(modelId)
        if (!status.installed) {
          // 如果 Ollama 服务器不可用，不阻塞消息发送，通过流式状态报告
          // 让 AIAgent 来捕获并报告错误
          console.log('[Vermes] Ollama model status:', status)
        }
      } catch (e) {
        // 忽略检查错误
      }
    }

    if (!_isRegenerate_) {
      messages.value.push({
        id: msgId, role: 'user', content: userContent,
        sessionId: currentSessionId.value, timestamp: Date.now(),
        attachments: processedAttachments
      })
      await persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      // 更新 Agent 最后活跃时间
      const _s = sessions.value.find(s => s.id === currentSessionId.value)
      if (_s) _s.lastActive = new Date().toISOString()
      scheduleScroll()
    }

    // 处理 attachments（如果有）
    let processedAttachments = []
    if (attachments && attachments.length > 0) {
      processedAttachments = attachments.map(a => ({
        name: a.name || '',
        type: a.type || 'file',
        data: a.data || a.preview || '',
        mime: a.mimeType || '',
        size: a.size || 0,
      }))
    }

    // P4: per-session loading
    if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = true

    const userContent = content
    try {
      const allMsgs = messages.value
      const aid = uid()
      messages.value.push({
        id: aid, role: 'assistant', content: '',
        sessionId: currentSessionId.value, timestamp: Date.now(),
        streaming: true, toolInvocations: []
      })

      const ac = new AbortController()
      abortController.value = ac

      const allMsgsFiltered = messages.value.filter(m => m.sessionId === currentSessionId.value && !m.streaming)
      const recentImageMsgIds = new Set(
        allMsgsFiltered.filter(m => m.role === 'user' && m.content?.includes('data:image') && m.id !== msgId).slice(-5).map(m => m.id)
      )
      const apiMessages = allMsgsFiltered.map(m => ({
        role: m.role,
        content: m.role === 'user' && m.content?.includes('data:image') && !recentImageMsgIds.has(m.id)
          ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '[图片]').trim() : m.content,
      }))
      const transport = getChatTransport()
      transport.on(currentSessionId.value, {
        onMessage: (chunk) => {
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
          if (chunk?.type === 'text' || chunk?.type === 'delta') {
            am._streamBuffer += chunk.content || ''
          } else if (chunk?.type === 'tool') {
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
            am._currentStep = chunk.name || ''
          }
          scheduleScroll()
        },
        onStatus: (event) => {
          statusMessages.value.push({
            id: uid(),
            type: event.type || 'info',
            message: event.message || event.content || '',
            timestamp: Date.now(),
          })
          scheduleScroll()
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
            am.streaming = false; am._currentStep = null; am._streamStartTime = null; am._toolCount = null
          }
          flushScroll()
          if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = false
          abortController.value = null; activeStreamId.value = null
          statusMessages.value = []
          if (usageInfo && usageInfo.total_tokens > 0) {
            lastTokenUsage.value = usageInfo
          }
        },
        onError: (error) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) { am.streaming = false; am.content += `

\`\`\`error
${error}
\`\`\``; am._streamStartTime = null }
          flushScroll()
          if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = false
          abortController.value = null
          statusMessages.value.push({ id: uid(), type: 'error', message: error, timestamp: Date.now() })
        },
      })
      await transport.send(currentSessionId.value, {
        messages: apiMessages,
        model: modelId,
        provider: providerId,
        attachments: processedAttachments.map(a => ({
          name: a.name, type: a.type, data: a.base64,
          mime: a.mimeType || 'application/octet-stream', size: a.size,
        })),
      })
    } catch(e) {
      const am = messages.value.find(m => m.id === aid)
      if (am) { am.streaming = false }
      if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = false
      abortController.value = null
      if (e.name === 'AbortError') {
        showToast('已停止', 'info')
      } else {
        console.error('sendMessage error:', e)
        showToast('发送失败: ' + (e.message || '未知错误'), 'error')
      }
    }
  }

  // ── 停止生成 ──
  function stopGeneration() {
    // 通过 transport 停止（兼容 SSE 和未来 WebSocket）
    const transport = getChatTransport()
    if (currentSessionId.value) transport.stop(currentSessionId.value)
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    // 清理所有 streaming 消息
    messages.value.filter(m => m.streaming).forEach(m => {
      m.streaming = false
      if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
    })
    // P4: per-session loading reset
    if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = false
    activeStreamId.value = null
  }

  // ── 工具函数 ──
  function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value }

  function persistSessions() { saveToStorage(SESSIONS_KEY, sessions.value) }

  function getMessageCount(sessionId) { return _getMessageCount(sessionId) }
  function getFirstMessage(sessionId) { return _getFirstMessage(sessionId) }

  return {
    sessions, currentSessionId, currentSession, messages, loading,
    sessionLoading, sidebarOpen, theme, currentModel, currentProvider,
    uploading, showQuotaModal, quotaModalType, activeStreamId, compareModels,
    statusMessages, lastTokenUsage, streamConnected, isOnline, isWindows,
    init, initOnce,
    createSession, switchSession, deleteSession, renameSession, pinSession,
    searchAllSessions, exportSession, importSession,
    sendMessage, stopGeneration, toggleSidebar, persistSessions,
    getMessageCount, getFirstMessage,
  }
})
