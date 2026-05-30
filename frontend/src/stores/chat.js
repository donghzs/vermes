import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api, { isCloudModel, checkQuota, checkQuotaServer, getWechatDailyQuota, WECHAT_QUOTA_KEY } from '../services/api'
import { showToast } from '../utils/toast'
import { saveImage, loadImage, deleteImages, loadFromStorage, saveToStorage, stripBase64FromContent, fileToBase64 } from './chat-storage'

// ── H2/M11 修复：避免 Date.now() 碰撞 ──
function uid() { return Date.now().toString(36) + Math.random().toString(36).slice(2, 8) }

// ── v2: 未登录拦截弹窗类型 ──
const QUOTA_NEED_LOGIN = 'need_login'

const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-msgs-'

// ── 会话模板 ──
export const SESSION_TEMPLATES = [
  { id: 'blank', name: '空白会话', icon: '💬', systemPrompt: '' },
  { id: 'translator', name: '翻译助手', icon: '🌐', systemPrompt: '你是一位专业的翻译助手。请将用户输入的内容准确翻译为目标语言。如果用户没有指定目标语言，请将中文翻译为英文，或将非中文内容翻译为中文。保持原文的语气和风格。' },
  { id: 'coder', name: '代码助手', icon: '💻', systemPrompt: '你是一位专业的编程助手。帮助用户编写、调试和优化代码。提供清晰的代码示例和详细解释。使用最佳实践和设计模式。' },
  { id: 'writer', name: '写作助手', icon: '✍️', systemPrompt: '你是一位专业的写作助手。帮助用户撰写、润色和改进各类文本。注意语法、逻辑和表达的准确性与优美性。' },
  { id: 'custom', name: '自定义', icon: '⚙️', systemPrompt: '' },
]

// ── 快速开始建议 ──
export const QUICK_START_SUGGESTIONS = [
  { text: '帮我写一封邮件', icon: '📧' },
  { text: '解释量子计算', icon: '🔬' },
  { text: '翻译这段话', icon: '🌐' },
  { text: '写一段 Python 代码', icon: '💻' },
]

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
  const quotaModalType = ref('need_login') // 'need_login' | 'trial_expired' | 'wechat_expired'
  const compareModels = ref([]) // P3-8: 多模型对比

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
        await createSession('新会话')
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

  async function createSession(name, template) {
    const tpl = template || SESSION_TEMPLATES[0]
    const s = {
      id: uid(),
      name: name || tpl.name || '新会话',
      createdAt: new Date().toISOString(),
      templateId: tpl.id,
    }
    sessions.value.unshift(s)
    persistSessions()
    await switchSession(s.id)
    // 如果模板有 systemPrompt，添加一条系统消息
    if (tpl.systemPrompt) {
      messages.value.push({
        id: uid(), role: 'system', content: tpl.systemPrompt,
        sessionId: s.id, timestamp: Date.now(),
      })
      persistMessages(s.id)
    }
  }

  async function switchSession(id) {
    if (currentSessionId.value) persistMessages(currentSessionId.value)
    currentSessionId.value = id
    localStorage.setItem('vermes-last-session', id)
    const stored = loadFromStorage(MESSAGES_KEY_PREFIX + id)
    
    // P2-20: 先显示文本消息，图片异步加载
    // 先设置消息（不含图片）
    messages.value = stored.map(m => ({ ...m }))

    // 修复：重置中断的工具调用状态（页面重载说明工具已中断）
    for (const m of messages.value) {
      if (m.toolInvocations) {
        for (const t of m.toolInvocations) {
          if (t.status === 'running') {
            t.status = 'error'
            t.duration = t.duration || 0
          }
        }
      }
      // 同时清理残留的 streaming 状态
      if (m.streaming) m.streaming = false
    }
    
    // 异步加载图片（不阻塞UI）
    const imageLoadPromises = []
    for (let i = 0; i < stored.length; i++) {
      const m = stored[i]
      if (m._imageKeys && m._imageKeys.length > 0) {
        // 为每个消息创建图片加载Promise
        const promise = (async () => {
          const parts = [m.content]
          // 并行加载该消息的所有图片
          const imgPromises = m._imageKeys.map(key => loadImage(key))
          const imgs = await Promise.all(imgPromises)
          for (const img of imgs) {
            if (img) parts.push(img)
          }
          // 更新消息内容
          const msgIndex = messages.value.findIndex(msg => msg.id === m.id)
          if (msgIndex >= 0) {
            messages.value[msgIndex].content = parts.join('\n\n')
            delete messages.value[msgIndex]._imageKeys
          }
        })()
        imageLoadPromises.push(promise)
      }
    }
    
    // 不等待图片加载完成，让UI立即响应
    // 图片会在加载完成后自动更新显示
    if (imageLoadPromises.length > 0) {
      Promise.all(imageLoadPromises).catch(e => {
        console.warn('[Vermes] 图片加载失败:', e)
      })
    }
  }

  async function sendMessage(content, attachments, _model_, _provider_, _isRegenerate_) {
    const modelId = _model_ || currentModel.value
    const providerId = _provider_ || currentProvider.value
    console.log('[Vermes💬 sendMessage] content:', JSON.stringify(content), 'attachments:', attachments?.length, 'currentSessionId:', currentSessionId.value, 'messages count:', messages.value.length, 'model:', modelId, 'provider:', providerId)
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
      showToast('会话未初始化，请刷新页面重试', 'error')
      return
    }

    // ✅ v2: 云端模型配额检查（仅 vbit 免费体验通道检查积分，用户自带 Key 不检查）
    const isCloud = isCloudModel(providerId)
    const isVbitFreeTrial = String(providerId).toLowerCase() === 'vbit'
    if (isCloud && isVbitFreeTrial) {
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
    
    // 仅 vbit 免费体验通道检查本地配额缓存，用户自带 Key 跳过
    if (isVbitFreeTrial) {
      const quotaCheck = checkQuota(isCloud)
      if (!quotaCheck.allowed) {
        quotaModalType.value = 'wechat_expired'
        showQuotaModal.value = true
        return
      }
    }

    const msgId = uid()

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

    // 添加用户消息（regenerate 时跳过，已有 user 消息）
    if (!_isRegenerate_) {
      console.log('[Vermes💬 sendMessage] Adding user message, sessionId:', currentSessionId.value)
      messages.value.push({
        id: msgId, role: 'user', content: userContent,
        sessionId: currentSessionId.value, timestamp: Date.now(),
        attachments: processedAttachments
      })
      persistMessages(currentSessionId.value)
    }

    loading.value = true

    // 添加 AI 回复占位
    const aid = uid()
    messages.value.push({
      id: aid, role: 'assistant', content: '',
      sessionId: currentSessionId.value, timestamp: Date.now(),
      streaming: true, toolInvocations: []
    })

    const ac = new AbortController()
    abortController.value = ac

    // 构建发送给 API 的消息历史
    // #2 修复：保留图片上下文，但限制最近 5 条用户消息的图片避免请求体过大
    const allMsgs = messages.value
      .filter(m => m.sessionId === currentSessionId.value && !m.streaming)
    
    // 找出最近 5 条有图片的用户消息 ID
    const recentImageMsgIds = new Set(
      allMsgs.filter(m => m.role === 'user' && m.content?.includes('data:image'))
        .slice(-5).map(m => m.id)
    )
    
    const apiMessages = allMsgs.map(m => ({
      role: m.role,
      // 超过最近 5 条的图片消息，剥离 base64 避免超长
      content: m.role === 'user' && m.content?.includes('data:image') && !recentImageMsgIds.has(m.id)
        ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '[图片]').trim()
        : m.content,
    }))

    try {
      await api.sendMessage({
        model: modelId,
        provider: providerId,
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
        onStreamStart: (streamId) => {
          // P1-10: 记录 stream_id 用于 stop 通知
          activeStreamId.value = streamId
        },
        onChunk: (chunk) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) am.content += chunk
        },
        onTool: (tool) => {
          const am = messages.value.find(m => m.id === aid)
          if (!am) return
          // api.js传入的type: 'tool_start' | 'tool_end'
          // 转换为MessageList模板需要的status: 'running' | 'done' | 'error'
          if (tool.type === 'tool_start') {
            // 新工具开始时，自动结束之前的 thinking 卡片
            for (const t of am.toolInvocations) {
              if (t.name === 'thinking' && t.status === 'running') {
                t.status = 'done'
                t.duration = Math.round((Date.now() - t.startTime) / 1000)
              }
            }
            am.toolInvocations.push({
              id: tool.tool_call_id || tool.name,
              name: tool.name,
              arguments: tool.arguments,
              status: 'running',
              startTime: Date.now()
            })
          } else if (tool.type === 'tool_end') {
            const inv = am.toolInvocations.find(t => t.id === tool.tool_call_id || t.name === tool.name)
            if (inv) {
              inv.status = tool.is_error ? 'error' : 'done'
              inv.duration = tool.duration
              inv.result_preview = tool.result_preview || ''
            }
          }
        },
        onDone: (usageInfo) => {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            // 关闭所有仍在 running 的 thinking 卡片
            for (const t of am.toolInvocations || []) {
              if (t.name === 'thinking' && t.status === 'running') {
                t.status = 'done'
                t.duration = Math.round((Date.now() - t.startTime) / 1000)
              }
            }
            am.streaming = false
          }
          loading.value = false
          abortController.value = null
          activeStreamId.value = null
          const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
          if (wechatOpenid && isVbitFreeTrial) {
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
          const msg = err.message || ''
          // One-API 额度耗尽 / 积分用完 / Token 失效 → 弹窗提示
          if (msg.includes('额度已用尽') || msg.includes('insufficient_quota') || msg.includes('体验额度已用完') || msg.includes('402') || msg.includes('免费体验Token')) {
            quotaModalType.value = 'wechat_expired'
            showQuotaModal.value = true
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = '💡 今日免费额度已用完，请明天再来或配置自己的 API Key'; am.streaming = false }
          } else {
            const am = messages.value.find(m => m.id === aid)
            if (am) { am.content = '❌ 错误: ' + msg; am.streaming = false }
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

  // P3-8: 多模型对比 — 并行向多个模型发送同一问题
  async function sendCompareMessage(content, attachments, models) {
    // models: [{id, provider, name}, ...]
    if (!models || models.length < 2) return
    if (loading.value) return
    if (!currentSessionId.value) {
      showToast('会话未初始化', 'error')
      return
    }

    // Quota check: vbit 通道按模型数倍增
    const providerId = currentProvider.value
    const isVbitFreeTrial = String(providerId).toLowerCase() === 'vbit'
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
        if (serverCheck.data.trial_expired) {
          quotaModalType.value = 'trial_expired'
          showQuotaModal.value = true
          return
        }
        if (serverCheck.data.remaining < models.length) {
          toast.error(`对比需要 ${models.length} 积分，当前仅剩 ${serverCheck.data.remaining}`)
          return
        }
      }
    }

    // 构建用户消息（只创建一次）
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
    persistMessages(currentSessionId.value)

    loading.value = true

    // 存储所有对比模式的 AbortController
    const compareAbortControllers = []
    _compareAbortControllers.value = compareAbortControllers

    // 为每个模型创建独立消息并并行发送
    const modelLabel = (m) => `**[🔬 ${m.name || m.id}]**\n`
    const aides = []
    for (const m of models) {
      const aid = uid()
      aides.push(aid)
      messages.value.push({
        id: aid, role: 'assistant', content: modelLabel(m),
        sessionId: currentSessionId.value, timestamp: Date.now(),
        streaming: true, toolInvocations: [], _compareModel: m.name || m.id,
      })
    }

    // 构建 API 消息（不含 compare 模型消息）
    const apiMessages = messages.value
      .filter(m => m.sessionId === currentSessionId.value && !m.streaming)
      .map(m => ({
        role: m.role,
        content: m.role === 'user' && m.content.includes('data:image')
          ? m.content.replace(/!\[.*?\]\(data:image[^)]+\)/g, '').trim()
          : m.content,
      }))

    const attachPayload = processedAttachments.map(a => ({
      name: a.name, type: a.type, data: a.base64,
      mime: a.mimeType || 'application/octet-stream', size: a.size,
    }))

    const tasks = models.map((model, idx) => {
      const aid = aides[idx]
      return (async () => {
        const ac = new AbortController()
        compareAbortControllers.push(ac)
        try {
          await api.sendMessage({
            model: model.id,
            provider: model.provider || providerId,
            messages: apiMessages,
            attachments: attachPayload,
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
            onDone: () => {
              const am = messages.value.find(m => m.id === aid)
              if (am) am.streaming = false
            },
            onError: (err) => {
              const am = messages.value.find(m => m.id === aid)
              if (am) {
                am.content = modelLabel(model) + '❌ 错误: ' + (err.message || '未知')
                am.streaming = false
              }
            },
          })
        } catch (e) {
          const am = messages.value.find(m => m.id === aid)
          if (am) {
            am.content = modelLabel(model) + '❌ 发送失败: ' + e.message
            am.streaming = false
          }
        }
      })()
    })

    await Promise.allSettled(tasks)
    loading.value = false
    const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
    if (wechatOpenid && isVbitFreeTrial) {
      window.dispatchEvent(new Event('quota-updated'))
    }
    persistMessages(currentSessionId.value)
    compareModels.value = []
  }

  const activeStreamId = ref(null)
  const _compareAbortControllers = ref([])

  async function stopGeneration() {
    // P1-10: 通知后端停止生成
    if (activeStreamId.value) {
      try {
        const token = localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')
        const headers = { 'Content-Type': 'application/json' }
        if (token) headers['X-Hermes-Session-Token'] = token
        const apiPrefix = (typeof window !== 'undefined' && window.__VERMES_ONLINE__) ? '/v1' : '/api'
        fetch(`${apiPrefix}/stop-generation`, {
          method: 'POST',
          headers,
          body: JSON.stringify({ stream_id: activeStreamId.value })
        }).catch(() => {})  // best effort, don't block
        activeStreamId.value = null
      } catch (e) {
        // ignore
      }
    }
    // 中断普通模式的 AbortController
    if (abortController.value) {
      abortController.value.abort()
      abortController.value = null
    }
    // 中断对比模式的所有 AbortController
    for (const ac of _compareAbortControllers.value) {
      try { ac.abort() } catch(e) {}
    }
    _compareAbortControllers.value = []
    loading.value = false
    // 关闭所有 streaming 消息
    messages.value.filter(m => m.streaming).forEach(m => { m.streaming = false })
    persistMessages(currentSessionId.value)
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

  async function deleteSession(id) {
    const idx = sessions.value.findIndex(s => s.id === id)
    if (idx === -1) return
    // 清理 IndexedDB 中的图片数据
    try {
      const msgs = loadFromStorage(MESSAGES_KEY_PREFIX + id)
      const imageKeys = msgs.flatMap(m => m._imageKeys || [])
      if (imageKeys.length > 0) await deleteImages(imageKeys)
    } catch(e) { console.warn('[Vermes] 清理图片数据失败:', e) }
    sessions.value.splice(idx, 1)
    localStorage.removeItem(MESSAGES_KEY_PREFIX + id)
    persistSessions()
    if (currentSessionId.value === id) {
      if (sessions.value.length > 0) {
        await switchSession(sessions.value[0].id)
      } else {
        await createSession('新会话')
      }
    }
  }

  function renameSession(id, name) {
    const s = sessions.value.find(s => s.id === id)
    if (s) { s.name = name; persistSessions() }
  }

  function pinSession(id, pinned) {
    const s = sessions.value.find(s => s.id === id)
    if (s) { s.pinned = pinned; persistSessions() }
  }

  function getMessageCount(sessionId) {
    try {
      const msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || []
      return msgs.length
    } catch { return 0 }
  }

  function getFirstMessage(sessionId) {
    try {
      const msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || []
      const userMsg = msgs.find(m => m.role === 'user')
      if (userMsg) {
        const text = userMsg.content.replace(/!\[[^\]]*\]\([^)]+\)/g, '🖼️图片').replace(/📎[^\n]*/g, '📎附件')
        return text.length > 40 ? text.slice(0, 40) + '...' : text
      }
      return ''
    } catch { return '' }
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B'
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
  }

  // ── 跨会话历史搜索 ──
  function searchAllMessages(keyword, dateFilter) {
    const results = []
    const now = Date.now()
    let cutoff = 0
    if (dateFilter === 'today') cutoff = now - 86400000
    else if (dateFilter === 'week') cutoff = now - 7 * 86400000
    else if (dateFilter === 'month') cutoff = now - 30 * 86400000
    for (const s of sessions.value) {
      try {
        const msgs = loadFromStorage(MESSAGES_KEY_PREFIX + s.id)
        for (const m of msgs) {
          if (m.role === 'system') continue
          if (cutoff && m.timestamp < cutoff) continue
          if (keyword && !m.content?.toLowerCase().includes(keyword.toLowerCase())) continue
          results.push({
            ...m,
            sessionName: s.name,
            sessionId: s.id,
            snippet: (m.content || '').slice(0, 50),
          })
        }
      } catch {}
    }
    results.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
    return results
  }

  // ── 会话统计 ──
  function getSessionStats(sessionId) {
    const msgs = messages.value.filter(m => m.sessionId === sessionId && m.role !== 'system')
    if (msgs.length === 0) return { count: 0, duration: '0 分钟', model: currentModel.value }
    const first = msgs[0].timestamp
    const last = msgs[msgs.length - 1].timestamp
    const diffMs = last - first
    let duration
    if (diffMs < 60000) duration = `${Math.max(1, Math.round(diffMs / 1000))} 秒`
    else if (diffMs < 3600000) duration = `${Math.round(diffMs / 60000)} 分钟`
    else duration = `${(diffMs / 3600000).toFixed(1)} 小时`
    return { count: msgs.length, duration, model: currentModel.value }
  }

  // ── 会话导出（从 IndexedDB 恢复图片数据） ──
  async function exportSession(sessionId, format) {
    const session = sessions.value.find(s => s.id === sessionId)
    if (!session) return
    const msgs = loadFromStorage(MESSAGES_KEY_PREFIX + sessionId).filter(m => m.role !== 'system')

    // 恢复 IndexedDB 中的图片到消息内容
    const restoredMsgs = []
    for (const m of msgs) {
      const restored = { ...m }
      if (m._imageKeys && m._imageKeys.length > 0) {
        // 从 IndexedDB 加载图片并替换占位符
        let content = m.content || ''
        for (const key of m._imageKeys) {
          const base64 = await loadImage(key)
          if (base64) {
            // 替换第一个 "🖼️ 图片" 占位符为原始 base64
            content = content.replace('🖼️ 图片', base64)
          }
        }
        restored.content = content
        delete restored._imageKeys
      }
      restoredMsgs.push(restored)
    }

    let content, filename, mimeType
    if (format === 'json') {
      content = JSON.stringify({ session, messages: restoredMsgs }, null, 2)
      filename = `${session.name || '会话'}.json`
      mimeType = 'application/json'
    } else {
      const lines = [`# ${session.name || '会话'}`, '', `导出时间: ${new Date().toLocaleString('zh-CN')}`, '']
      for (const m of restoredMsgs) {
        lines.push(`## ${m.role === 'user' ? 'User' : 'Assistant'}`)
        lines.push('')
        lines.push(m.content || '')
        lines.push('')
      }
      content = lines.join('\n')
      filename = `${session.name || '会话'}.md`
      mimeType = 'text/markdown'
    }
    const blob = new Blob([content], { type: mimeType + ';charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = filename; a.click()
    URL.revokeObjectURL(url)
  }

  // ── 会话导入 ──
  async function importSession(jsonText) {
    try {
      const data = JSON.parse(jsonText)
      if (!data.session || !Array.isArray(data.messages)) {
        throw new Error('无效的会话格式')
      }
      const s = {
        id: uid(),
        name: (data.session.name || '导入会话') + ' (导入)',
        createdAt: data.session.createdAt || new Date().toISOString(),
        templateId: data.session.templateId || 'blank',
      }
      sessions.value.unshift(s)
      persistSessions()
      const importedMsgs = data.messages.map(m => ({
        ...m,
        id: uid(),
        sessionId: s.id,
        streaming: false,
      }))
      messages.value.push(...importedMsgs)
      persistMessages(s.id)
      return { success: true, name: s.name }
    } catch (e) {
      return { success: false, error: e.message }
    }
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

  // P1-13: 监听系统主题变化（仅在用户未手动设置主题时跟随系统）
  if (!saved) {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (e) => {
      theme.value = e.matches ? 'dark' : 'light'
      if (e.matches) {
        document.documentElement.classList.add('dark')
      } else {
        document.documentElement.classList.remove('dark')
      }
    })
  }

  return {
    sessions, currentSessionId, messages, loading, abortController,
    sidebarOpen, theme, currentModel, currentProvider, uploading,
    showQuotaModal, quotaModalType, compareModels,
    currentSession, filteredMessages,
    init, createSession, switchSession, sendMessage, sendCompareMessage, stopGeneration,
    toggleTheme, toggleSidebar, deleteSession, renameSession, pinSession,
    getMessageCount, getFirstMessage, formatSize,
    searchAllMessages, getSessionStats, exportSession, importSession,
  }
})
