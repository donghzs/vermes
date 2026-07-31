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
import { loadFromStorage, saveToStorage, loadMessagesFromIDB, fileToBase64, listChannelSessionsFromAPI, loadChannelMessagesFromAPI, deleteChannelSessionFromAPI, sendFromDesktopAPI, getRelayStateAPI } from './chat-storage'
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

/**
 * 切换会话时，判断某条仍在 streaming 的消息是否应保活其流式状态与刷新定时器。
 *
 * 背景：文本 delta 从 _streamBuffer 落到 am.content 的唯一通道是 _streamBufTimer；
 * reasoning（思考）则 direct append 不过定时器。若切换会话时清掉仍在后台流式输出
 * 的会话的定时器，模型进入长推理、暂不发文本的阶段时，文本会表现为「冻住/不流式」，
 * 但后端任务与思考照常推进——即「文本不流式但思考还在进行」的根因。
 *
 * 规则：仅当 transport 明确报告该会话已无活动流（真孤儿）时才允许清理定时器防泄漏；
 * 否则保活。定时器/streaming 标记的真正清理由 onDone/onError 负责。
 */
export function keepStreamAliveOnSwitch(msg, transport) {
  if (!msg || !msg.streaming) return false
  const stillStreaming = !!(
    transport &&
    typeof transport.isStreaming === 'function' &&
    transport.isStreaming(msg.sessionId)
  )
  return stillStreaming
}

/**
 * 把单个 plan 步骤状态变更合并进当前 todo 列表。
 * 纯函数：不触碰响应式状态，便于单测。
 *
 * 返回更新后的【新数组】；当步骤 id 不在列表中(idx < 0)时返回 null，
 * 调用方据此跳过重赋值，避免「无变化的副本」白触发一次响应式更新（P2#1）。
 */
export function applyPlanStepUpdate(curItems, step) {
  if (!step || !Array.isArray(curItems)) return null
  const idx = curItems.findIndex((i) => i.id === step.id)
  if (idx < 0) return null
  const newItems = curItems.slice()
  newItems[idx] = {
    ...newItems[idx],
    status: step.status,
    started_at:
      step.started_at ||
      (step.status === 'in_progress' ? Math.floor(Date.now() / 1000) : null),
    finished_at: step.finished_at || null,
  }
  return newItems
}

export const useChatStore = defineStore('chat', () => {
  const sessions = ref(loadFromStorage(SESSIONS_KEY))
  // ── 步骤1：state.db 渠道会话（telegram/discord/cli 等，统一视图只读+续聊桥）──
  // 与本地 web 会话（sessions）分离存放：channelSessions 永不写入 localStorage，
  // 避免 persistSessions 把渠道会话持久化造成双源污染。
  const channelSessions = ref([])
  // 渠道会话未读计数（session_id -> 条数），由 WS 实时同步推送维护
  const channelUnread = ref({})
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
  // ── per-session 任务面板状态（方案 B：分片隔离）──
  // 每个会话独立维护任务进度，避免并行会话串台
  const sessionTodoItems = ref({})            // sessionId → todo[]
  const sessionTodoStepActivities = ref({})   // sessionId → { step_id: toolCall[] }
  const sessionTodoAllDone = ref({})          // sessionId → boolean
  const sessionTodoInterrupted = ref({})      // sessionId → boolean
  const sessionShowTaskDrawer = ref({})       // sessionId → boolean
  const sessionShowTodoPanel = ref({})        // sessionId → boolean

  // ── 全局 UI 状态（跨会话共享，不需要分片）──
  const pendingApproval = ref(null)  // 工具审批请求

  // ── 当前会话的 computed 视图（自动跟随 currentSessionId）──
  const todoItems = computed(() => sessionTodoItems.value[currentSessionId.value] || [])
  const todoStepActivities = computed(() => sessionTodoStepActivities.value[currentSessionId.value] || {})
  const todoAllDone = computed(() => !!sessionTodoAllDone.value[currentSessionId.value])
  const todoInterrupted = computed(() => !!sessionTodoInterrupted.value[currentSessionId.value])
  const showTaskDrawer = computed({
    get: () => !!sessionShowTaskDrawer.value[currentSessionId.value],
    set: (v) => { sessionShowTaskDrawer.value = { ...sessionShowTaskDrawer.value, [currentSessionId.value]: v } },
  })
  const showTodoPanel = computed({
    get: () => !!sessionShowTodoPanel.value[currentSessionId.value],
    set: (v) => { sessionShowTodoPanel.value = { ...sessionShowTodoPanel.value, [currentSessionId.value]: v } },
  })
  const currentStatusMessages = computed(() => 
    sessionStatusMessages.value[currentSessionId.value] || []
  )
  const currentActiveStreamId = computed(() =>
    sessionActiveStreamIds.value[currentSessionId.value] || null
  )
  // 当前进行中的 todo 步骤 id（工具事件未带 step_id 时回退用）
  const currentTodoStepId = computed(() => {
    const items = sessionTodoItems.value[currentSessionId.value] || []
    const it = items.find(i => i.status === 'in_progress')
    return it ? it.id : null
  })
  // 进行中步骤数（头部徽标）
  const todoInProgressCount = computed(() => {
    const items = sessionTodoItems.value[currentSessionId.value] || []
    return items.filter(i => i.status === 'in_progress').length
  })
  const lastTokenUsage = ref(null)

  const isOnline = typeof window !== 'undefined' && window.__VERMES_ONLINE__ === true
  const isWindows = typeof navigator !== 'undefined' && /Windows/i.test(navigator.userAgent)

  // ── 缓存性能指标 ──
  const cacheMetrics = ref(null)

  const currentSession = computed(() =>
    sessions.value.find(s => s.id === currentSessionId.value)
      || channelSessions.value.find(s => s.id === currentSessionId.value)
  )

  // ── 步骤1：渠道会话（state.db）加载与判定 ──
  // 本地会话优先：同 id 同时存在于本地与 state.db（步骤2 之后 web 会话双写）时视为本地会话
  function isChannelSession(id) {
    if (!id) return false
    if (sessions.value.find(s => s.id === id)) return false
    const cs = channelSessions.value.find(s => s.id === id)
    if (!cs) return false
    // desktop source = 本地桌面会话（虽在 state.db 但非远程渠道）
    if (cs.source === 'desktop') return false
    return true
  }

  async function loadChannelSessions() {
    try {
      const rows = await listChannelSessionsFromAPI(200)
      const localIds = new Set(sessions.value.map(s => s.id))
      channelSessions.value = rows
        .filter(r => r && r.id && !localIds.has(r.id) && (r.message_count || 0) > 0)
        .map(r => ({
          id: r.id,
          name: r.title || (r.preview ? String(r.preview).slice(0, 40) : `${r.source || '渠道'} 会话`),
          createdAt: new Date((r.started_at || 0) * 1000).toISOString(),
          lastActive: new Date((r.last_active || r.started_at || 0) * 1000).toISOString(),
          channel: true,
          source: r.source || 'unknown',
          model: r.model || '',
          messageCount: r.message_count || 0,
          preview: r.preview || '',
        }))
    } catch (e) {
      // 后端不可达时保留旧列表，不清空——避免后端短暂抖动导致渠道会话"消失"
      logger.warn('[Vermes] 渠道会话列表刷新失败，保留旧列表:', e)
    }
  }

  // state.db 消息行 → 前端消息格式（只呈现可读对话，跳过 tool/system 底噪）
  function _mapChannelMessages(sessionId, rows) {
    const out = []
    for (const r of rows || []) {
      if (!r) continue
      if (r.role !== 'user' && r.role !== 'assistant') continue
      const content = (r.content || '').trim()
      if (!content) continue
      out.push({
        id: `${sessionId}-db-${r.id}`,
        role: r.role,
        content,
        sessionId,
        timestamp: Math.round((r.timestamp || 0) * 1000),
        streaming: false,
        toolInvocations: [],
        reasoning: r.reasoning || r.reasoning_content || '',
        _fromStateDB: true,
      })
    }
    return out
  }

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
        await switchSession(lastId, { hydrate: false })
      } else {
        await createSession('新 Agent')
      }
      // 注入进化简报（非阻塞：后台拉取，不挡首屏）
      injectEvolutionBriefing().catch(() => {})
      // 步骤1：加载 state.db 渠道会话（非阻塞，失败不影响本地会话）
      loadChannelSessions().catch(() => {})
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

  async function switchSession(id, options = {}) {
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
      // 关键修复：后台仍在流式输出的会话必须保持流式状态与刷新定时器！
      // onMessage 仍会把文本 delta 写入 _streamBuffer，而 _streamBufTimer 是把
      // _streamBuffer 落到 am.content 的唯一通道；reasoning（思考）则是 direct append
      // 到 am.reasoning，不经过定时器。若这里清掉定时器，在「模型长推理、暂不发文本」
      // 的阶段切走再切回，文本会表现为「不流式输出」，但后端任务 / 思考仍在跑——
      // 这正是切换/并行会话时原会话「文本不流式但思考还在进行」的根因。
      // 定时器与 streaming 标记的真正清理由 onDone / onError 负责（已防内存泄漏）。
      const _bgTransport = getChatTransport()
      messages.value.filter(m => m.streaming).forEach(m => {
        // 仅把当前已缓冲文本落盘，避免切换瞬间的内容抖动；保留定时器与 streaming 标记
        if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
        if (!keepStreamAliveOnSwitch(m, _bgTransport)) {
          // 真孤儿流（后端已无该会话活动流）：安全清理定时器，防泄漏
          if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
          m.streaming = false
        }
      })
      // 渠道会话（state.db）只读呈现，不回写 GUI 存储（避免双源复制）
      if (!isChannelSession(oldSessionId)) {
        await persistMessages(oldSessionId, messages.value, currentSessionId.value, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      }
    }

    currentSessionId.value = id
    localStorage.setItem('vermes-last-session', id)

    // 恢复新会话的模型选择
    const newSession = sessions.value.find(s => s.id === id) || channelSessions.value.find(s => s.id === id)
    if (newSession && newSession.model) {
      currentModel.value = newSession.model
      currentProvider.value = newSession.provider || ''
      try { localStorage.setItem('vermes-current-model', newSession.model) } catch(e) {}
      try { localStorage.setItem('vermes-current-provider', newSession.provider || '') } catch(e) {}
    }

    // 渠道会话：启动消息轮询；本地会话：停止轮询
    if (isChannelSession(id)) {
      startChannelMessagePolling(id)
    } else {
      stopChannelMessagePolling()
    }
    // 加载新会话消息 — 合并到全局消息池（不替换）
    // P0-2: 首屏冷启动(init)传 hydrate:false → 历史消息后台异步补，不阻塞首屏渲染
    if (options.hydrate === false) {
      _hydrateMessages(id).catch(e => console.error('[Vermes] 后台补历史失败:', e))
    } else {
      try { await _hydrateMessages(id) } catch (e) { console.error('[Vermes] 加载会话失败:', e) }
    }
    // 恢复新会话的 loading 状态
    // 检查新会话是否已有 loading 状态
    if (!sessionLoading.value[id]) {
      sessionLoading.value[id] = false
    }
  }

  // 历史消息异步填充：与 switchSession 解耦，首屏可先渲染空壳再由它补历史
  async function _hydrateMessages(id) {
    // 渠道会话和 desktop 会话都从 state.db 加载消息
    const isRemoteChannel = isChannelSession(id)
    const isDesktopInStateDB = !isRemoteChannel && channelSessions.value.find(s => s.id === id && s.source === 'desktop')
    const loaded = (isRemoteChannel || isDesktopInStateDB)
      ? _mapChannelMessages(id, await loadChannelMessagesFromAPI(id))
      : await loadMessagesFromIDB(id)
    if (loaded && loaded.length > 0) {
      // 去重合并: 已在池中的跳过
      const existingIds = new Set(messages.value.map(m => m.id))
      for (const m of loaded) {
        if (!existingIds.has(m.id)) messages.value.push(m)
      }
    }
    // 渠道会话已打开并呈现历史 → 清除其未读角标
    if (isChannelSession(id)) markChannelRead(id)
  }

  async function deleteSession(id) {
    // 带 token 删除 state.db 侧记录（修复此前裸 fetch 吃 401）；纯本地会话 404 无害
    await deleteChannelSessionFromAPI(id)
    // 渠道会话：仅从 state.db + 内存列表移除，不走本地 IDB/localStorage 清理
    if (isChannelSession(id)) {
      channelSessions.value = channelSessions.value.filter(s => s.id !== id)
      messages.value = messages.value.filter(m => m.sessionId !== id)
      if (currentSessionId.value === id) {
        if (sessions.value.length > 0) await switchSession(sessions.value[0].id)
        else await createSession('新 Agent')
      }
      return
    }
    // 清理被删会话的 streaming 定时器
    messages.value.filter(m => m.sessionId === id).forEach(m => {
      if (m._streamBufTimer) { clearInterval(m._streamBufTimer); m._streamBufTimer = null }
      if (m._streamBuffer) { m.content += m._streamBuffer; m._streamBuffer = '' }
    })
    // 清理被删会话的任务面板分片
    const _d = (obj) => { const n = { ...obj }; delete n[id]; return n }
    sessionTodoItems.value = _d(sessionTodoItems.value)
    sessionTodoStepActivities.value = _d(sessionTodoStepActivities.value)
    sessionTodoAllDone.value = _d(sessionTodoAllDone.value)
    sessionTodoInterrupted.value = _d(sessionTodoInterrupted.value)
    sessionShowTaskDrawer.value = _d(sessionShowTaskDrawer.value)
    sessionShowTodoPanel.value = _d(sessionShowTodoPanel.value)
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

  // ── 步骤3：渠道会话续聊（send-from-desktop 桥） ──
  // 写 relay 信号 → gateway 进程消费（agent 运行 + adapter.send 回原渠道 +
  // user/assistant 落 state.db + 记忆摄入）→ 桌面轮询 state.db 显示回复。
  async function sendToChannelSession(sid, text) {
    // 本地立即显示用户消息（gateway 落库的同文本 user 行轮询时去重跳过）
    messages.value.push({
      id: uid(), role: 'user', content: text,
      sessionId: sid, timestamp: Date.now(), _fromDesktopRelay: true,
    })
    scheduleScroll()
    sessionLoading.value[sid] = true
    try {
      const res = await sendFromDesktopAPI(sid, text)
      if (!res.ok) {
        showToast(`渠道代发失败: ${res.detail || 'HTTP ' + res.status}`, 'error')
        messages.value.push({
          id: uid(), role: 'system', sessionId: sid, timestamp: Date.now(),
          content: `❌ 渠道代发失败: ${res.detail || 'HTTP ' + res.status}`,
        })
        return
      }
      const sentAtMs = Date.now()
      const knownIds = new Set(
        messages.value.filter(m => m.sessionId === sid && m._fromStateDB).map(m => m.id)
      )
      const deadline = Date.now() + 300000  // 5 分钟超时护栏（与后端 ttl 对齐）
      let gotAssistant = false
      while (Date.now() < deadline && !gotAssistant) {
        await new Promise(r => setTimeout(r, 2000))
        const mapped = _mapChannelMessages(sid, await loadChannelMessagesFromAPI(sid))
        for (const m of mapped) {
          if (knownIds.has(m.id)) continue
          knownIds.add(m.id)
          // gateway 落库的 user 行若与刚发文本相同则跳过（本地已显示）
          if (m.role === 'user' && m.content.trim() === text.trim()) continue
          messages.value.push(m)
          if (m.role === 'assistant' && m.timestamp >= sentAtMs - 10000) gotAssistant = true
        }
        if (gotAssistant) { scheduleScroll(); break }
        const relay = await getRelayStateAPI(sid)
        if (relay && relay.state === 'failed') {
          messages.value.push({
            id: uid(), role: 'system', sessionId: sid, timestamp: Date.now(),
            content: `❌ 渠道代发失败: ${relay.error || '未知错误'}（gateway 未运行或该渠道未连接）`,
          })
          return
        }
      }
      if (!gotAssistant) {
        messages.value.push({
          id: uid(), role: 'system', sessionId: sid, timestamp: Date.now(),
          content: '⏱ 渠道回复超时（5 分钟）。消息可能仍在处理，稍后重新打开本会话查看。',
        })
      }
    } finally {
      sessionLoading.value[sid] = false
    }
  }

  // ── 渠道会话消息轮询：当前打开渠道会话时，定时拉取新消息 ──
  let _channelMsgPollTimer = null
  function startChannelMessagePolling(sid) {
    stopChannelMessagePolling()
    _channelMsgPollTimer = setInterval(async () => {
      try {
        const mapped = _mapChannelMessages(sid, await loadChannelMessagesFromAPI(sid))
        const existingIds = new Set(messages.value.filter(m => m.sessionId === sid).map(m => m.id))
        let added = false
        for (const m of mapped) {
          if (!existingIds.has(m.id)) {
            messages.value.push(m)
            added = true
          }
        }
        if (added) scheduleScroll()
      } catch (e) { /* ignore */ }
    }, 3000)
  }
  function stopChannelMessagePolling() {
    if (_channelMsgPollTimer) { clearInterval(_channelMsgPollTimer); _channelMsgPollTimer = null }
  }

  // ── 渠道会话消息即时刷新（WS 推送触发，替代轮询等待）──
  async function refreshChannelMessages(sid) {
    if (!sid) return
    try {
      const mapped = _mapChannelMessages(sid, await loadChannelMessagesFromAPI(sid))
      const existingIds = new Set(messages.value.filter(m => m.sessionId === sid).map(m => m.id))
      let added = false
      for (const m of mapped) {
        if (!existingIds.has(m.id)) {
          messages.value.push(m)
          added = true
        }
      }
      if (added) scheduleScroll()
    } catch (e) { /* ignore */ }
  }

  // ── 全渠道实时同步：独立 WS 连接（与聊天传输层解耦）──
  // 后端每 2s 轮询 state.db 渠道会话，有新消息即广播轻量通知；
  // 本连接仅维护未读角标，不触发任何模型调用。
  let _syncWs = null
  let _syncRetry = 0
  const _SYNC_MAX_RETRIES = 20
  let _sessionListRefreshing = false  // 防 channel_update 并发重复拉取会话列表

  function _syncWsUrl() {
    const proto = (typeof location !== 'undefined' && location.protocol === 'https:') ? 'wss:' : 'ws:'
    return `${proto}//${location.host}/api/ws/chat`
  }

  function markChannelRead(sid) {
    if (!sid) return
    // 本地即时清零，避免角标闪烁
    channelUnread.value = { ...channelUnread.value, [sid]: 0 }
    if (_syncWs && _syncWs.readyState === WebSocket.OPEN) {
      try { _syncWs.send(JSON.stringify({ type: 'mark_read', session_id: sid })) } catch (e) {}
    }
  }

  function initChannelSync() {
    if (_syncWs) return  // 单例
    const connect = () => {
      let ws
      try {
        ws = new WebSocket(_syncWsUrl())
      } catch (e) {
        _scheduleSyncReconnect(connect)
        return
      }
      _syncWs = ws
      ws.onopen = () => { _syncRetry = 0 }
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'channel_unread_snapshot') {
            channelUnread.value = { ...(msg.unread || {}) }
          } else if (msg.type === 'channel_update') {
            const sid = msg.session_id
            if (!sid) return
            channelUnread.value = { ...channelUnread.value, [sid]: msg.unread || 0 }
            // 当前正打开该渠道会话 → 即时刷新内容并清除未读
            if (sid === currentSessionId.value && isChannelSession(sid)) {
              refreshChannelMessages(sid)
              markChannelRead(sid)
            }
            // 新渠道会话首次出现：即时拉取会话列表，把入口发现延迟
            // 从 ≤5s 轮询降到 ≤0.5s（对齐事件即时广播会话列表变更）。
            // 幂等：loadChannelSessions 整体替换列表；in-flight 守卫防并发重复拉取。
            if (!channelSessions.value.some(s => s.id === sid) && !_sessionListRefreshing) {
              _sessionListRefreshing = true
              loadChannelSessions()
                .catch(() => {})
                .finally(() => { _sessionListRefreshing = false })
            }
          }
        } catch (e) {}
      }
      ws.onclose = () => _scheduleSyncReconnect(connect)
      ws.onerror = () => { try { ws.close() } catch (e) {} }
    }
    const _scheduleSyncReconnect = (fn) => {
      if (_syncRetry >= _SYNC_MAX_RETRIES) return
      const delay = Math.min(1000 * Math.pow(2, _syncRetry), 30000)
      _syncRetry++
      setTimeout(fn, delay)
    }
    connect()
  }

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

    // 步骤3：渠道会话续聊 → send-from-desktop 桥（gateway 消费，绝不走 chat.py）
    if (isChannelSession(currentSessionId.value)) {
      if (attachments && attachments.length > 0) {
        showToast('渠道代发暂不支持附件，仅发送文本', 'info')
      }
      if (content && content.trim()) {
        await sendToChannelSession(currentSessionId.value, content.trim())
      }
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
    // 重置当前会话的实时任务面板状态（per-session 分片）
    const _sid_reset = currentSessionId.value
    sessionTodoStepActivities.value = { ...sessionTodoStepActivities.value, [_sid_reset]: {} }
    sessionTodoAllDone.value = { ...sessionTodoAllDone.value, [_sid_reset]: false }
    sessionTodoInterrupted.value = { ...sessionTodoInterrupted.value, [_sid_reset]: false }
    sessionTodoItems.value = { ...sessionTodoItems.value, [_sid_reset]: [] }
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
          } else if (chunk?.type === 'turn_boundary') {
            // 工具输出与 Agent 回复之间的分隔：刷新 buffer
            // 分隔符由 _fire_stream_delta 的 _stream_needs_break 机制处理
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
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
          // P0-2: merge 而非 replace，避免 plan 与 todo 竞态覆盖
          // 方案 B: per-session 分片写入，用 sendSessionId 定位
          if (!data.todos || !Array.isArray(data.todos)) return
          const cur = sessionTodoItems.value[sendSessionId] || []
          const existingMap = new Map(cur.map(i => [i.id, i]))
          for (const t of data.todos) {
            const old = existingMap.get(t.id)
            if (old) {
              existingMap.set(t.id, { ...old, ...t,
                started_at: t.started_at ?? old.started_at,
                finished_at: t.finished_at ?? old.finished_at,
              })
            } else {
              existingMap.set(t.id, { ...t, tool_calls: t.tool_calls || [] })
            }
          }
          const oldOrder = cur.map(i => i.id)
          const merged = []
          for (const id of oldOrder) {
            const item = existingMap.get(id)
            if (item) { merged.push(item); existingMap.delete(id) }
          }
          for (const item of existingMap.values()) merged.push(item)
          sessionTodoItems.value = { ...sessionTodoItems.value, [sendSessionId]: merged }
          if (data.todos.length > 0) {
            sessionShowTodoPanel.value = { ...sessionShowTodoPanel.value, [sendSessionId]: true }
            sessionShowTaskDrawer.value = { ...sessionShowTaskDrawer.value, [sendSessionId]: true }
          }
          // 计划未全部完成则清除庆祝态
          const s = data.summary || {}
          if (!(s.total > 0 && s.completed === s.total && s.in_progress === 0)) {
            sessionTodoAllDone.value = { ...sessionTodoAllDone.value, [sendSessionId]: false }
          }
          scheduleScroll()
        },
        onToolCall: (data) => {
          // 工具调用实时事件：挂到当前进行中的步骤下，形成"步骤 → 子任务"树
          // 方案 B: per-session 分片写入
          const items = sessionTodoItems.value[sendSessionId] || []
          const fallbackStep = items.find(i => i.status === 'in_progress')
          const sid = data.step_id || (fallbackStep ? fallbackStep.id : null)
          if (!sid) return
          const curActs = sessionTodoStepActivities.value[sendSessionId] || {}
          const acts = { ...curActs }
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
          sessionTodoStepActivities.value = { ...sessionTodoStepActivities.value, [sendSessionId]: acts }
        },
        onTaskComplete: (data) => {
          // 全部步骤完成 → 庆祝态（per-session）
          sessionTodoAllDone.value = { ...sessionTodoAllDone.value, [sendSessionId]: true }
        },
        onPlanCreated: (plan) => {
          // 任务规划已创建 → 填充当前会话的 todoItems 并自动打开抽屉
          if (!plan || !plan.steps) return
          const items = plan.steps.map(s => ({
            id: s.id,
            content: s.title,
            status: s.status || 'pending',
            agent_role: s.agent_role,
            started_at: s.started_at ? s.started_at : null,
            finished_at: s.finished_at ? s.finished_at : null,
            description: s.description || '',
          }))
          // 标记第一个步骤为进行中
          if (items.length > 0) {
            items[0].status = 'in_progress'
            items[0].started_at = Math.floor(Date.now() / 1000)
          }
          sessionTodoItems.value = { ...sessionTodoItems.value, [sendSessionId]: items }
          sessionShowTaskDrawer.value = { ...sessionShowTaskDrawer.value, [sendSessionId]: true }
          scheduleScroll()
        },
        onPlanUpdate: (data) => {
          // plan_step_update / plan_tool_started / plan_completed 等
          // 方案 B: per-session 分片写入
          const subtype = data.subtype
          const curItems = sessionTodoItems.value[sendSessionId] || []
          const curActs = sessionTodoStepActivities.value[sendSessionId] || {}
          if (subtype === 'step_update' || subtype === 'step_started' || subtype === 'step_completed') {
            const step = data.step
            if (!step) return
            // P2#1：步骤不存在(idx<0)时 applyPlanStepUpdate 返回 null，
            // 跳过「无变化副本」的重赋值，避免白触发一次响应式更新。
            const newItems = applyPlanStepUpdate(curItems, step)
            if (newItems !== null) {
              sessionTodoItems.value = { ...sessionTodoItems.value, [sendSessionId]: newItems }
            }
            if (subtype === 'step_started') {
              sessionShowTaskDrawer.value = { ...sessionShowTaskDrawer.value, [sendSessionId]: true }
            }
          } else if (subtype === 'tool_started') {
            const sid = data.step_id
            if (!sid) return
            const acts = { ...curActs }
            const list = acts[sid] ? [...acts[sid]] : []
            list.push({
              id: data.tool?.id || uid(),
              name: data.tool?.name || 'tool',
              status: 'running', start: Date.now(), duration: 0, is_error: false,
            })
            acts[sid] = list
            sessionTodoStepActivities.value = { ...sessionTodoStepActivities.value, [sendSessionId]: acts }
          } else if (subtype === 'tool_completed') {
            const sid = data.step_id
            if (!sid) return
            const acts = { ...curActs }
            const list = acts[sid] ? [...acts[sid]] : []
            const idx = list.findIndex(a => a.id === (data.tool?.id || ''))
            const done = {
              id: data.tool?.id || '',
              name: data.tool?.name || '',
              status: 'done',
              start: Date.now(),
              duration: data.tool?.duration || 0,
              is_error: data.tool?.is_error || false,
              preview: data.tool?.result_summary || '',
            }
            if (idx >= 0) list[idx] = done
            else list.push(done)
            acts[sid] = list
            sessionTodoStepActivities.value = { ...sessionTodoStepActivities.value, [sendSessionId]: acts }
          } else if (subtype === 'completed') {
            sessionTodoAllDone.value = { ...sessionTodoAllDone.value, [sendSessionId]: true }
          }
          scheduleScroll()
        },
        onStage: (data) => {
          // Pipeline stage event (from Pipeline abstraction)
          // data: { stage, pipeline: 'start'|'done'|'error', papers?, message? }
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          if (!am.pipelineStages) am.pipelineStages = []
          if (data.pipeline === 'start') {
            am.pipelineStages.push({
              name: data.stage,
              status: 'running',
              startedAt: Date.now(),
            })
          } else if (data.pipeline === 'done') {
            const s = am.pipelineStages.find(s => s.name === data.stage && s.status === 'running')
            if (s) { s.status = 'done'; s.completedAt = Date.now(); s.papers = data.papers }
          } else if (data.pipeline === 'error') {
            const s = am.pipelineStages.find(s => s.name === data.stage && s.status === 'running')
            if (s) { s.status = 'error'; s.error = data.message || '' }
          }
          scheduleScroll()
        },
        onCheckpoint: (data) => {
          // Pipeline checkpoint: pause between stages
          // data: { stage, next, message, completed, remaining }
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          am.checkpoint = data
          scheduleScroll()
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            if (am._streamBufTimer) { clearInterval(am._streamBufTimer); am._streamBufTimer = null }
            if (am._streamBuffer) { am.content += am._streamBuffer; am._streamBuffer = '' }
            am.streaming = false; am._currentStep = null; am._streamStartTime = null; am._toolCount = null
            am.checkpoint = null  // clear checkpoint on done
            // 检测空回复：后端流结束但没有任何内容输出
            if (!am.content || !am.content.trim()) {
              // 带上后端最后一条 warn（空响应重试/切换服务商/最终失败原因），
              // 让用户看到真实原因而非笼统提示
              const _warns = (sessionStatusMessages.value[sendSessionId] || []).filter(s => s.type === 'warn')
              const _lastWarn = _warns.length ? _warns[_warns.length - 1].message : ''
              am.content = '⚠️ 回复为空，可能是后端处理异常。'
                + (_lastWarn ? `\n\n后端状态：${_lastWarn}` : '')
                + '\n\n请重试或更换模型。'
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
          // P1-3: If reconnect flagged, show status but don't show error UI
          if (error && error.reconnect) {
            const errMsgs = sessionStatusMessages.value[sendSessionId] || []
            errMsgs.push({ id: uid(), type: 'info', message: error.message || '重连中', timestamp: Date.now() })
            sessionStatusMessages.value[sendSessionId] = errMsgs
            return  // Don't mark as error — caller may retry
          }
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
    if (sid) {
      sessionTodoInterrupted.value = { ...sessionTodoInterrupted.value, [sid]: true }
    }
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
    sessions, channelSessions, loadChannelSessions, isChannelSession,
    currentSessionId, currentSession, messages, loading, filteredMessages,
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
    startChannelMessagePolling, stopChannelMessagePolling,
    refreshChannelMessages, initChannelSync, markChannelRead, channelUnread,
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
