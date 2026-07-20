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
import { loadFromStorage, saveToStorage, loadMessagesFromIDB, fileToBase64 } from './chat-storage'
import { uid, persistMessages } from './chat-session'
import { scheduleScroll, flushScroll, setScrollTarget } from './chat-scroll'
import { flushStorageWrites } from './chat-storage'
import { getChatTransport } from '../services/chat-transport'

// 常量
const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-messages-'
const DEFAULT_MODEL_ID = localStorage.getItem('vermes-default-model') || ''
const DEFAULT_PROVIDER_ID = localStorage.getItem('vermes-default-provider') || ''

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
  const sidebarOpen = ref(true)
  const theme = ref(localStorage.getItem('vermes-theme') || 'dark')
  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
    localStorage.setItem('vermes-theme', theme.value)
    if (typeof document !== 'undefined') {
      document.documentElement.classList.toggle('dark', theme.value === 'dark')
    }
  }
  let _beforeunloadRegistered = false
  const currentModel = ref(localStorage.getItem('vermes-current-model') || DEFAULT_MODEL_ID)
  const currentProvider = ref(localStorage.getItem('vermes-current-provider') || DEFAULT_PROVIDER_ID)
  const reasoningEffort = ref(localStorage.getItem('vermes-reasoning-effort') || '') // '' = auto/default, 'low'/'medium'/'high'
  const searchEnabled = ref(localStorage.getItem('vermes-search-enabled') === 'true')

  // ── nextTurnSnapshot: 轮内模型一致性 ──
  // 当会话正在 streaming 时，用户改模型不会打断当前轮，而是存到 pendingModel
  // 当前轮 onDone 后，如果有 pendingModel 则自动切换
  const pendingModel = ref(null)    // { model, provider, sessionId } 或 null

  // ── appendModelChange: 模型变更记录到消息流 ──
  // 切换模型时插入一条轻量 system 消息，记录"这段对话在这里换了模型"
  function appendModelChange(sessionId, fromModel, toModel) {
    if (!sessionId || fromModel === toModel) return
    messages.value.push({
      id: uid(),
      role: 'system',
      content: `⚙️ 模型已切换：${fromModel || '(默认)'} → ${toModel}`,
      sessionId,
      timestamp: Date.now(),
      _isModelChange: true,
      _modelFrom: fromModel || '',
      _modelTo: toModel,
    })
    persistMessages(sessionId, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
  }

  // nextTurnSnapshot: 应用 pending 模型切换（onDone/onError/stopGeneration 三处共用）
  function _applyPendingModel(sid) {
    if (!pendingModel.value || pendingModel.value.sessionId !== sid) return
    const pm = pendingModel.value
    const oldModel = currentModel.value
    currentModel.value = pm.model
    currentProvider.value = pm.provider
    const session = sessions.value.find(s => s.id === sid)
    if (session) {
      session.model = pm.model
      session.provider = pm.provider
      persistSessions()
    }
    appendModelChange(sid, oldModel, pm.model)
    try { localStorage.setItem('vermes-current-model', pm.model) } catch(e) {}
    try { localStorage.setItem('vermes-current-provider', pm.provider) } catch(e) {}
    pendingModel.value = null
  }
  const searchMode = ref(false)
  const searchQuery = ref('')
  const uploading = ref(false)
  const showQuotaModal = ref(false)
  const quotaModalType = ref('need_login')
  const compareModels = ref([])
  const activeStreamId = ref(null)  // deprecated, use sessionActiveStreamIds
  const statusMessages = ref([])     // deprecated, use sessionStatusMessages
  const sessionStatusMessages = ref({})
  const sessionActiveStreamIds = ref({})
  const evolutionEvents = ref([])    // 进化事件（成就/建议）
  const showAchievement = ref(false) // 成就弹窗
  const achievementData = ref(null)  // 当前展示的成就
  const pendingApproval = ref(null)  // 工具审批请求
  const todoItems = ref([])          // Agent todo 列表
  const showTodoPanel = ref(false)   // Todo 面板显隐（兼容旧逻辑）
  // ── 实时任务面板（长任务分步骤 + 进度）──
  const showTaskDrawer = ref(false)       // 任务抽屉显隐
  const todoStepActivities = ref({})      // step_id → 该步骤下的实时工具调用列表
  const todoAllDone = ref(false)          // 全部步骤完成（庆祝态）
  const todoInterrupted = ref(false)      // 用户停止/中断（保留已做部分）
  const currentStatusMessages = computed(() => 
    sessionStatusMessages.value[currentSessionId.value] || []
  )
  const currentActiveStreamId = computed(() =>
    sessionActiveStreamIds.value[currentSessionId.value] || null
  )
  // 当前进行中的 todo 步骤 id（工具事件未带 step_id 时回退用）
  const currentTodoStepId = computed(() => {
    const it = todoItems.value.find(i => i.status === 'in_progress')
    return it ? it.id : null
  })
  // 进行中步骤数（头部徽标）
  const todoInProgressCount = computed(() =>
    todoItems.value.filter(i => i.status === 'in_progress').length
  )
  const lastTokenUsage = ref(null)

  const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true
  const isWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent)

  // ── 缓存性能指标 ──
  const cacheMetrics = ref(null)

  const currentSession = computed(() =>
    sessions.value.find(s => s.id === currentSessionId.value)
  )

  const filteredMessages = computed(() => {
    if (!currentSessionId.value) return []
    let msgs = messages.value.filter(m => m.sessionId === currentSessionId.value)
    if (searchMode.value && searchQuery.value.trim()) {
      const q = searchQuery.value.toLowerCase()
      msgs = msgs.filter(m => m.content?.toLowerCase().includes(q))
    }
    return msgs
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
        let lastId = localStorage.getItem('vermes-last-session')
        // 验证 lastId 是有效会话（排除 "undefined"/"null" 字符串和不存在的 id）
        if (!lastId || lastId === 'undefined' || lastId === 'null' || !sessions.value.find(s => s.id === lastId)) {
          lastId = sessions.value[0].id
        }
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
    // 新会话继承当前选中的模型（用户可能先选好模型再建会话）
    const s = _createSession(sessions.value, messages.value, name, template, SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId.value)
    // 记录当前模型到会话对象
    s.model = currentModel.value
    s.provider = currentProvider.value
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
    if (!id || id === 'undefined' || id === 'null') return
    flushStorageWrites()
    const oldSessionId = currentSessionId.value
    // 保存旧会话的模型选择
    if (oldSessionId && oldSessionId !== id) {
      const oldSession = sessions.value.find(s => s.id === oldSessionId)
      if (oldSession) {
        oldSession.model = currentModel.value
        oldSession.provider = currentProvider.value
      }
      // 清理旧会话的 streaming 状态和定时器，防止内存泄漏
      messages.value.filter(m => m.streaming).forEach(m => {
        m.streaming = false
        if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
        if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
      })
      await persistMessages(oldSessionId, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    }

    currentSessionId.value = id
    localStorage.setItem('vermes-last-session', id)

    // 恢复新会话的模型选择
    const newSession = sessions.value.find(s => s.id === id)
    if (newSession && newSession.model) {
      currentModel.value = newSession.model
      currentProvider.value = newSession.provider || ''
      try { localStorage.setItem('vermes-current-model', newSession.model) } catch(e) {}
      try { localStorage.setItem('vermes-current-provider', newSession.provider || '') } catch(e) {}
    }

    // 加载新会话消息 — 合并到全局消息池（不替换）
    try {
      const loaded = await loadMessagesFromIDB(id)
      if (loaded && loaded.length > 0) {
        // 去重合并: 已在池中的跳过
        const existingIds = new Set(messages.value.map(m => m.id))
        for (const m of loaded) {
          if (!existingIds.has(m.id)) messages.value.push(m)
        }
      }
    } catch (e) {
      console.error('[Vermes] 加载会话失败:', e)
    }
    // 恢复新会话的 loading 状态
    // 检查新会话是否已有 loading 状态
    if (!sessionLoading.value[id]) {
      sessionLoading.value[id] = false
    }
  }

  async function deleteSession(id) {
    try { await fetch('/api/sessions/' + id, { method: 'DELETE' }) } catch {}
    // 清理被删会话的 streaming 定时器
    messages.value.filter(m => m.sessionId === id).forEach(m => {
      if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
      if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
    })
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
    // CLI 体验: 如果 agent 正在工作，先停止再发新消息（等效 CLI 中直接输入）
    if (currentSessionId.value && sessionLoading.value[currentSessionId.value]) {
      stopGeneration()
    }
    if (!currentSessionId.value) {
      showToast('会话未初始化，请刷新页面重试', 'error')
      return
    }

    const msgId = uid()

    // 处理 attachments — 将 File 对象转为 base64
    let processedAttachments = []
    if (attachments && attachments.length > 0) {
      processedAttachments = await Promise.all(
        attachments.map(async (a) => {
          // 如果有 file 对象，用 fileToBase64 转换
          if (a.file instanceof File) {
            const b64 = await fileToBase64(a.file)
            return {
              name: b64.name,
              type: b64.type,
              data: b64.base64,
              mime: b64.mimeType,
              size: b64.size,
            }
          }
          // 已有 base64 数据
          return {
            name: a.name || '',
            type: a.type || 'file',
            data: a.data || a.base64 || '',
            mime: a.mime || a.mimeType || '',
            size: a.size || 0,
          }
        })
      )
    }

    const userContent = content

    if (!_isRegenerate_) {
      messages.value.push({
        id: msgId, role: 'user', content: userContent,
        sessionId: currentSessionId.value, timestamp: Date.now(),
        attachments: processedAttachments
      })
      await persistMessages(currentSessionId.value, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      // 自动生成会话标题（首次消息时）
      const _session = sessions.value.find(s => s.id === currentSessionId.value)
      if (_session && (!_session.name || _session.name === '新会话' || _session.name === 'New Session')) {
        const autoTitle = userContent.trim().slice(0, 30) + (userContent.trim().length > 30 ? '...' : '')
        _session.name = autoTitle
        persistSessions(sessions.value, SESSIONS_KEY)
      }
      // 更新 Agent 最后活跃时间
      if (_session) _session.lastActive = new Date().toISOString()
      scheduleScroll()
    }

    // P4: per-session loading
    if (currentSessionId.value) sessionLoading.value[currentSessionId.value] = true
    // 新一轮：重置实时任务面板状态（保留抽屉开关由 todo_update 决定是否自动展开）
    todoStepActivities.value = {}
    todoAllDone.value = false
    todoInterrupted.value = false
    todoItems.value = []
    const sendSessionId = currentSessionId.value  // 锁定发送时所在的会话
    const aid = uid()
    try {
      messages.value.push({
        id: aid, role: 'assistant', content: '',
        sessionId: currentSessionId.value, timestamp: Date.now(),
        streaming: true, toolInvocations: []
      })

      const allMsgsFiltered = messages.value.filter(m => m.sessionId === currentSessionId.value && !m.streaming)
      const recentImageMsgIds = new Set(
        allMsgsFiltered.filter(m => m.role === 'user' && m.content?.includes('data:image') && m.id !== msgId).slice(-5).map(m => m.id)
      )
      const apiMessages = allMsgsFiltered
        .filter(m => !(m.role === 'assistant' && !m.content)) // 过滤空助手消息
        .map(m => ({
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
            if (!am.toolInvocations) am.toolInvocations = []
            am.toolInvocations.push({ name: chunk.name, status: 'running' })
          }
          scheduleScroll()
        },
        onStatus: (event) => {
          // 只保留最新一条，避免推理步数淹没聊天
          if (event.type === 'stream_start') return // 跳过 stream_start
          const arr = sessionStatusMessages.value[sendSessionId] || []
          if (arr.length > 0 && arr[arr.length - 1].type === 'thinking') {
            arr[arr.length - 1].message = event.message || ''
          } else {
            arr.push({
              id: uid(), type: event.type || 'info',
              message: event.message || event.content || '',
              timestamp: Date.now(),
            })
          }
          sessionStatusMessages.value[sendSessionId] = arr
          scheduleScroll()
        },
        onReasoning: (text) => {
          // 推理链内容 delta：累加到 assistant message 的 reasoning 字段
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          if (!am.reasoning) am.reasoning = ''
          am.reasoning += text
          scheduleScroll()
        },
        onEvolution: (event) => {
          // 进化事件：成就解锁或策略建议
          const evoEvent = {
            id: uid(),
            message: event.message || '',
            tool_name: event.tool_name || '',
            is_error: event.is_error || false,
            timestamp: Date.now(),
          }
          evolutionEvents.value.push(evoEvent)
          // 限制进化事件列表最多 5 条，避免堆积
          if (evolutionEvents.value.length > 5) {
            evolutionEvents.value = evolutionEvents.value.slice(-5)
          }
          // 如果是成就解锁（消息含🏆），弹出通知（5秒自动消失）
          if (evoEvent.message.includes('🏆')) {
            achievementData.value = evoEvent
            showAchievement.value = true
            // 5 秒后自动消失，避免长期遮挡
            setTimeout(() => { showAchievement.value = false }, 5000)
          }
          // 非成就的进化事件 8 秒后从列表移除（保持界面干净）
          if (!evoEvent.message.includes('🏆')) {
            setTimeout(() => {
              const idx = evolutionEvents.value.findIndex(e => e.id === evoEvent.id)
              if (idx !== -1) evolutionEvents.value.splice(idx, 1)
            }, 8000)
          }
          scheduleScroll()
        },
        onApprovalRequest: (approvalData) => {
          // 工具审批请求：弹出审批对话框
          pendingApproval.value = {
            ...approvalData,
            session_key: approvalData.session_key || ('gui-' + (sendSessionId || 'default')),
            timestamp: Date.now(),
          }
        },
        onTodoUpdate: (data) => {
          // Agent todo 列表更新
          if (data.todos && Array.isArray(data.todos)) {
            todoItems.value = data.todos
            if (data.todos.length > 0) {
              showTodoPanel.value = true
              // 小白友好：多步骤任务出现时自动展开任务抽屉，便于一眼看到进度
              showTaskDrawer.value = true
            }
            // 计划未全部完成则清除庆祝态
            const s = data.summary || {}
            if (!(s.total > 0 && s.completed === s.total && s.in_progress === 0)) {
              todoAllDone.value = false
            }
            scheduleScroll()
          }
        },
        onToolCall: (data) => {
          // 工具调用实时事件：挂到当前进行中的步骤下，形成"步骤 → 子任务"树
          const sid = data.step_id || currentTodoStepId.value
          if (!sid) return
          const acts = { ...todoStepActivities.value }
          const list = acts[sid] ? acts[sid].slice() : []
          if (data.type === 'tool_start') {
            list.push({
              id: data.tool_call_id, name: data.tool_name,
              status: 'running', start: Date.now(), duration: 0, is_error: false,
            })
          } else if (data.type === 'tool_end') {
            const idx = list.findIndex(a => a.id === data.tool_call_id)
            const done = {
              id: data.tool_call_id, name: data.tool_name,
              status: 'done', start: Date.now(),
              duration: data.duration || 0,
              is_error: data.is_error || false,
              preview: data.result_preview || '',
            }
            if (idx >= 0) list[idx] = done
            else list.push(done)
          }
          acts[sid] = list
          todoStepActivities.value = acts
        },
        onTaskComplete: (data) => {
          // 全部步骤完成 → 庆祝态
          todoAllDone.value = true
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
            am.streaming = false; am._currentStep = null; am._streamStartTime = null; am._toolCount = null
            // 检测空回复：后端流结束但没有任何内容输出
            if (!am.content || !am.content.trim()) {
              am.content = '⚠️ 回复为空，可能是后端处理异常。请重试或更换模型。'
              am._isEmpty = true
            }
          }
          flushScroll()
          if (sendSessionId) sessionLoading.value[sendSessionId] = false
          sessionActiveStreamIds.value[sendSessionId] = null
          sessionStatusMessages.value[sendSessionId] = []
          if (usageInfo && usageInfo.total_tokens > 0) {
            lastTokenUsage.value = usageInfo
          }
          // 持久化助手回复（防止崩溃丢失）
          if (sendSessionId) {
            persistMessages(sendSessionId, messages.value, sendSessionId, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
          }
          // nextTurnSnapshot: 当前轮结束后，应用 pending 模型切换
          _applyPendingModel(sendSessionId)
        },
        onError: (error) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
            am.streaming = false
            // 如果已有部分内容，追加错误信息；否则用友好提示替代空白
            if (am.content && am.content.trim()) {
              am.content += `\n\n\`\`\`error\n${error}\n\`\`\``
            } else {
              am.content = `⚠️ 回复失败：${error}\n\n请重试或更换模型。`
              am._isEmpty = true
            }
            am._streamStartTime = null
          }
          flushScroll()
          if (sendSessionId) sessionLoading.value[sendSessionId] = false
          const errMsgs = sessionStatusMessages.value[sendSessionId] || []
          errMsgs.push({ id: uid(), type: 'error', message: error, timestamp: Date.now() })
          sessionStatusMessages.value[sendSessionId] = errMsgs
          // 持久化（即使出错也保存已收到的部分内容）
          if (sendSessionId) {
            persistMessages(sendSessionId, messages.value, sendSessionId, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
          }
          // nextTurnSnapshot: 出错也应用 pending 模型切换
          _applyPendingModel(sendSessionId)
        },
      })
      await transport.send(currentSessionId.value, {
        messages: apiMessages,
        model: modelId,
        provider: providerId,
        attachments: processedAttachments.map(a => ({
          name: a.name, type: a.type, data: a.data || a.base64 || '',
          mime: a.mime || a.mimeType || 'application/octet-stream', size: a.size,
        })),
        reasoning_effort: reasoningEffort.value || undefined,
        web_search: searchEnabled.value || undefined,
      })
    } catch(e) {
      const am = messages.value.find(m => m.id === aid)
      if (am) {
        if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
        am.streaming = false
        // 网络异常导致空回复时显示友好提示
        if (!am.content || !am.content.trim()) {
          am.content = `⚠️ 发送失败：${e.message || '未知错误'}\n\n请重试或更换模型。`
          am._isEmpty = true
        }
      }
      if (sendSessionId) sessionLoading.value[sendSessionId] = false
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
    const transport = getChatTransport()
    const sid = currentSessionId.value
    if (sid) {
      transport.stop(sid)
      sessionLoading.value[sid] = false
    }
    // 清理当前会话所有 streaming 消息
    messages.value.filter(m => m.streaming && m.sessionId === sid).forEach(m => {
      m.streaming = false
      if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
      if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
    })
    if (sid) {
      sessionActiveStreamIds.value[sid] = null
      sessionStatusMessages.value[sid] = []
    }
    evolutionEvents.value = []  // 清空进化事件
    // 保留 todoItems：小白用户停止后仍需看到「已完成 / 进行中」进度，不清空
    todoInterrupted.value = true
    // nextTurnSnapshot: 停止生成也应用 pending 模型切换
    _applyPendingModel(sid)
  }
  // ── 工具函数 ──
  function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value }
  function toggleTaskDrawer() { showTaskDrawer.value = !showTaskDrawer.value }

  function persistSessions() { saveToStorage(SESSIONS_KEY, sessions.value) }

  function getMessageCount(sessionId) { return _getMessageCount(sessionId) }
  function getFirstMessage(sessionId) { return _getFirstMessage(sessionId) }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  function newSession() { createSession('新 Agent') }

  function searchAllMessages(query, dateFilter) {
    const results = []
    const sessionsList = sessions.value
    for (const s of sessionsList) {
      const msgs = messages.value.filter(m => m.sessionId === s.id)
      for (const m of msgs) {
        if (!m.content) continue
        if (query && !m.content.toLowerCase().includes(query.toLowerCase())) continue
        if (dateFilter) {
          const d = new Date(m.timestamp)
          const now = new Date()
          if (dateFilter === 'today' && d.toDateString() !== now.toDateString()) continue
          if (dateFilter === 'week' && (now - d) > 7 * 24 * 60 * 60 * 1000) continue
          if (dateFilter === 'month' && (now - d) > 30 * 24 * 60 * 60 * 1000) continue
        }
        results.push({ ...m, sessionName: s.name })
      }
    }
    return results.sort((a, b) => b.timestamp - a.timestamp)
  }

  function getSessionStats(sessionId) {
    if (!sessionId) return { total: 0, user: 0, assistant: 0, tool: 0 }
    const msgs = messages.value.filter(m => m.sessionId === sessionId)
    return {
      total: msgs.length,
      user: msgs.filter(m => m.role === 'user').length,
      assistant: msgs.filter(m => m.role === 'assistant').length,
      tool: msgs.filter(m => m.role === 'tool').length,
    }
  }

  async function sendCompareMessage(content, attachments, modelIds) {
    if (!modelIds || modelIds.length < 2) return
    const promises = modelIds.map(mid => sendMessage(content, attachments, mid, null))
    await Promise.all(promises)
  }

  // ── 缓存性能指标 ──
  async function refreshCacheMetrics() {
    try {
      const r = await fetch('/api/cache/metrics')
      if (r.ok) cacheMetrics.value = await r.json()
    } catch (e) {
      console.error('[CacheMetrics] Failed:', e)
    }
  }

  // ── 工具审批 ──
  async function resolveApproval(choice) {
    if (!pendingApproval.value) return
    const { session_key } = pendingApproval.value
    try {
      await fetch('/api/approve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_key, choice }),
      })
    } catch (e) { console.error('[Approval] Failed:', e) }
    pendingApproval.value = null
  }

  // 3.4：统一设置当前模型（响应式 + 持久化），替代 Settings 的 localStorage + window 事件中转
  function setCurrentModel(modelId, provider) {
    currentModel.value = modelId
    currentProvider.value = provider
    try { localStorage.setItem('vermes-current-model', modelId) } catch(e) {}
    try { localStorage.setItem('vermes-current-provider', provider) } catch(e) {}
  }

  return {
    sessions, currentSessionId, currentSession, messages, loading, filteredMessages,
    sessionLoading, sidebarOpen, theme, currentModel, currentProvider,
    reasoningEffort, searchEnabled, searchMode, searchQuery,
    uploading, showQuotaModal, quotaModalType, activeStreamId, compareModels,
    statusMessages, sessionStatusMessages, currentStatusMessages,
    sessionActiveStreamIds, currentActiveStreamId,
    lastTokenUsage, streamConnected, isOnline, isWindows,
    cacheMetrics,
    evolutionEvents, showAchievement, achievementData,
    todoItems, showTodoPanel,
    showTaskDrawer, todoStepActivities, todoAllDone, todoInterrupted,
    currentTodoStepId, todoInProgressCount, toggleTaskDrawer,
    pendingApproval, resolveApproval,
    pendingModel, appendModelChange,
    init, initOnce,
    createSession, switchSession, deleteSession, renameSession, pinSession,
    searchAllSessions, exportSession, importSession,
    sendMessage, stopGeneration, toggleSidebar, toggleTheme, persistSessions,
    getMessageCount, getFirstMessage, formatSize, newSession,
    searchAllMessages, getSessionStats, sendCompareMessage,
    refreshCacheMetrics,
    setCurrentModel,
  }
})

// Re-export for components that import from '../stores/chat'
export { SESSION_TEMPLATES, QUICK_START_SUGGESTIONS } from './chat-session'
export { setScrollTarget } from './chat-scroll'
