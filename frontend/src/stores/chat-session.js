/**
 * chat-session.js — 会话管理 + localStorage 三级清理
 */

import { saveToStorage, loadFromStorage, stripBase64FromContent, fileToBase64, flushStorageWrites, onStorageWriteFailure, saveImage, loadImage, deleteImages, saveMessagesToIDB, loadMessagesFromIDB, deleteMessagesFromIDB, migrateFromLocalStorage, saveMessagesToAPI, loadMessagesFromAPI, deleteMessagesFromAPI } from './chat-storage'
import { logger } from '@/utils/logger'

// ── 常量 ──
const SESSIONS_KEY = 'vermes-sessions'
const MESSAGES_KEY_PREFIX = 'vermes-msgs-'
const MAX_SESSIONS = 30  // localStorage 约 5MB，中等会话 ~100-200KB，30 个较安全

const QUOTA_NEED_LOGIN = 'need_login'

// G6 惰性降级占位图（图片被老化淘汰 / IDB miss 时用）。
// 注意：SVG 含中文，btoa 是 Latin1-only 会抛 DOMException("Invalid character")，
// 必须用 utf8 + encodeURIComponent 编码 data URI。模块级求值一次，导出循环零开销。
const EVICTED_IMAGE_PLACEHOLDER = 'data:image/svg+xml;utf8,' + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"><rect width="100%" height="100%" fill="#eee"/><text x="50%" y="50%" font-size="12" text-anchor="middle" fill="#999">图片不可用</text></svg>'
)

// ── 会话模板 ──
export const SESSION_TEMPLATES = [
  { id: 'blank', name: '空白会话', icon: '💬', systemPrompt: '' },
  { id: 'translator', name: '翻译助手', icon: '🌐', systemPrompt: '你是一位专业的翻译助手。请将用户输入的内容准确翻译为目标语言。如果用户没有指定目标语言，请将中文翻译为英文，或将非中文内容翻译为中文。保持原文的语气和风格。' },
  { id: 'coder', name: '代码助手', icon: '💻', systemPrompt: '你是一位专业的编程助手。帮助用户编写、调试和优化代码。提供清晰的代码示例和详细解释。使用最佳实践和设计模式。' },
  { id: 'writer', name: '写作助手', icon: '✍️', systemPrompt: '你是一位专业的写作助手。帮助用户撰写、润色和改进各类文本。注意语法、逻辑和表达的准确性与优美性。' },
  { id: 'research', name: '研究论文', icon: '🎓', systemPrompt: '你是一位学术研究助手。遇到论文写作任务时，先用 skill_view("research-paper-writing") 加载完整流程，用 skill_view("arxiv") 搜索文献。引用必须程序化验证，绝不从记忆生成。' },
  { id: 'custom', name: '自定义', icon: '⚙️', systemPrompt: '' },
]

// ── 快速开始建议 ──
export const QUICK_START_SUGGESTIONS = [
  { text: '帮我写一封邮件', icon: '📧' },
  { text: '解释量子计算', icon: '🔬' },
  { text: '翻译这段话', icon: '🌐' },
  { text: '写一段 Python 代码', icon: '💻' },
]

// ── 三级清理策略 ──

/**
 * 第一级：裁剪旧会话工具结果（result_preview > 500 字符的截断）
 * 返回：是否成功释放了空间
 */
function evictOldSessions(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId) {
  try {
    logger.warn('[Vermes] localStorage 满，开始智能清理...')
    if (trimOldSessionResults(MESSAGES_KEY_PREFIX, currentSessionId)) return true
    deleteOldestSession(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId)
  } catch (e) {
    console.error('[Vermes] 清理失败:', e)
  }
  return false
}

function trimOldSessionResults(MESSAGES_KEY_PREFIX, currentSessionId) {
  let freed = false
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (!key || !key.startsWith(MESSAGES_KEY_PREFIX)) continue
    const sid = key.slice(MESSAGES_KEY_PREFIX.length)
    if (sid === currentSessionId) continue
    try {
      const msgs = JSON.parse(localStorage.getItem(key))
      if (!Array.isArray(msgs)) continue
      let changed = false
      for (const m of msgs) {
        if (m.toolInvocations) {
          for (const t of m.toolInvocations) {
            if (t.result_preview && t.result_preview.length > 500) {
              t.result_preview = t.result_preview.slice(0, 500) + '\n... (已裁剪)'
              changed = true
            }
          }
        }
      }
      if (changed) {
        try { localStorage.setItem(key, JSON.stringify(msgs)) } catch { /* still full, skip */ }
        freed = true
        try {
          localStorage.setItem('__vermes_quotacheck', '1')
          localStorage.removeItem('__vermes_quotacheck')
          logger.warn('[Vermes] 裁剪旧会话工具结果后空间恢复')
          return true
        } catch { /* 还是满的，继续裁剪下一个会话 */ }
      }
    } catch { /* 解析失败跳过 */ }
  }
  return false
}

function deleteOldestSession(SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId) {
  const keysToDelete = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key && key.startsWith(MESSAGES_KEY_PREFIX)) {
      const sid = key.slice(MESSAGES_KEY_PREFIX.length)
      if (sid !== currentSessionId) keysToDelete.push({ key, sid })
    }
  }
  const sessionIds = loadFromStorage(SESSIONS_KEY).map(s => s.id)
  keysToDelete.sort((a, b) => {
    const ia = sessionIds.indexOf(a.sid), ib = sessionIds.indexOf(b.sid)
    return (ib === -1 ? 999 : ib) - (ia === -1 ? 999 : ia)
  })
  let deleted = 0
  for (const { key, sid } of keysToDelete) {
    localStorage.removeItem(key)
    deleteMessagesFromIDB(sid).catch(() => {})  // 同步清理 IDB
    deleted++
    try {
      localStorage.setItem('__vermes_quotacheck', '1')
      localStorage.removeItem('__vermes_quotacheck')
      logger.warn(`[Vermes] 删除旧会话 ${sid.slice(0,8)} 后空间恢复（共删 ${deleted} 个）`)
      return
    } catch { /* 继续 */ }
  }
  logger.warn(`[Vermes] 已删除 ${deleted} 个旧会话`)
}

/**
 * 会话数量上限检查：超出 MAX_SESSIONS 时删最旧的非当前会话
 */
function enforceSessionLimit(sessions, currentSessionId, SESSIONS_KEY, MESSAGES_KEY_PREFIX) {
  while (sessions.length >= MAX_SESSIONS) {
    const victim = [...sessions].reverse().find(s => s.id !== currentSessionId)
    if (!victim) break
    sessions.splice(sessions.findIndex(s => s.id === victim.id), 1)
    try { localStorage.removeItem(MESSAGES_KEY_PREFIX + victim.id) } catch {}
    logger.warn(`[Vermes] 会话超限，删除旧会话: ${victim.name} (${victim.id.slice(0,8)})`)
  }
  saveToStorage(SESSIONS_KEY, sessions)
}

/**
 * 保存指定会话的消息到 localStorage（剥离 base64 图片到 IndexedDB）
 * 如果 localStorage 满了，触发三级清理
 */
async function persistMessages(sessionId, messages, currentSessionId, SESSIONS_KEY, MESSAGES_KEY_PREFIX) {
  let msgs = messages.filter(m => m.sessionId === sessionId)
  const lean = []
  const imageSavePromises = []
  for (const m of msgs) {
    if (m.role === 'user' && m.content && m.content.includes('data:image')) {
      const { stripped, images } = stripBase64FromContent(m.content, m.id)
      for (const [key, data] of Object.entries(images)) {
        imageSavePromises.push(saveImage(key, data))
      }
      lean.push({ ...m, content: stripped, _imageKeys: Object.keys(images) })
    } else {
      lean.push(m)
    }
  }
  // 优先写 IndexedDB（无大小限制）
  await saveMessagesToIDB(sessionId, lean)
  // 同时写后端 API（pywebview macOS 不持久化 IndexedDB）
  await saveMessagesToAPI(sessionId, lean)
  if (imageSavePromises.length > 0) {
    try { await Promise.all(imageSavePromises) } catch(e) { logger.warn('[Vermes] 图片批量存储失败:', e) }
  }
}

/**
 * 第三级：裁剪当前会话消息（保留 system + 最近 N 条）
 */
function trimCurrentSessionMessages(sessionId, lean, MESSAGES_KEY_PREFIX) {
  const systemMsgs = lean.filter(m => m.role === 'system')
  const otherMsgs = lean.filter(m => m.role !== 'system')
  const keepCount = Math.max(20, 50 - systemMsgs.length)
  const trimmed = [...systemMsgs, ...otherMsgs.slice(-keepCount)]
  const keepIds = new Set(trimmed.map(m => m.id))
  // 如果调用方需要同步裁剪内存，在外部做
  if (saveToStorage(MESSAGES_KEY_PREFIX + sessionId, trimmed)) {
    logger.warn(`[Vermes] 当前会话消息裁剪: ${lean.length} → ${trimmed.length} 条`)
  } else {
    const minimal = [...systemMsgs, ...otherMsgs.slice(-10)]
    saveToStorage(MESSAGES_KEY_PREFIX + sessionId, minimal)
    logger.warn(`[Vermes] 当前会话极端裁剪: ${lean.length} → ${minimal.length} 条`)
  }
}

// ── 会话 CRUD ──

async function createSession(sessions, messages, name, template, SESSIONS_KEY, MESSAGES_KEY_PREFIX, currentSessionId) {
  const tpl = template || SESSION_TEMPLATES[0]
  const s = {
    id: uid(),
    name: name || tpl.name || '新 Agent',
    createdAt: new Date().toISOString(),
    lastActive: new Date().toISOString(),
    templateId: tpl.id,
  }
  enforceSessionLimit(sessions, currentSessionId, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
  sessions.unshift(s)
  saveToStorage(SESSIONS_KEY, sessions)
  return s
}

async function deleteSession(sessions, messages, id, SESSIONS_KEY, MESSAGES_KEY_PREFIX) {
  const idx = sessions.findIndex(s => s.id === id)
  if (idx === -1) return
  try {
    const msgs = await loadMessagesFromIDB(id)
    const imageKeys = (msgs || []).flatMap(m => m._imageKeys || [])
    if (imageKeys.length > 0) await deleteImages(imageKeys)
  } catch(e) { logger.warn('[Vermes] 清理图片数据失败:', e) }
  sessions.splice(idx, 1)
  localStorage.removeItem(MESSAGES_KEY_PREFIX + id)  // 兼容旧数据
  await deleteMessagesFromIDB(id)
  saveToStorage(SESSIONS_KEY, sessions)
}

function renameSession(sessions, id, name, SESSIONS_KEY) {
  const s = sessions.find(s => s.id === id)
  if (s) { s.name = name; saveToStorage(SESSIONS_KEY, sessions) }
}

function pinSession(sessions, id, pinned, SESSIONS_KEY) {
  const s = sessions.find(s => s.id === id)
  if (s) { s.pinned = pinned; saveToStorage(SESSIONS_KEY, sessions) }
}

function getMessageCount(sessionId) {
  // 同步函数，兼容旧 localStorage 数据
  try {
    const msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || []
    return msgs.length
  } catch { return 0 }
}

async function getMessageCountAsync(sessionId) {
  const msgs = await loadMessagesFromIDB(sessionId)
  if (msgs && msgs.length > 0) return msgs.length
  // 降级到 localStorage
  try {
    const localMsgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || []
    return localMsgs.length
  } catch { return 0 }
}

function getFirstMessage(sessionId) {
  // 同步函数，兼容旧 localStorage 数据
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

async function getFirstMessageAsync(sessionId) {
  let msgs = await loadMessagesFromIDB(sessionId)
  if (!msgs || msgs.length === 0) {
    try { msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || [] } catch { msgs = [] }
  }
  const userMsg = msgs.find(m => m.role === 'user')
  if (userMsg) {
    const text = userMsg.content.replace(/!\[[^\]]*\]\([^)]+\)/g, '🖼️图片').replace(/📎[^\n]*/g, '📎附件')
    return text.length > 40 ? text.slice(0, 40) + '...' : text
  }
  return ''
}

// ── 跨会话搜索 ──
function searchAllMessages(sessions, keyword, dateFilter, MESSAGES_KEY_PREFIX) {
  // 同步版本（兼容旧 localStorage 数据）
  const results = []
  const now = Date.now()
  let cutoff = 0
  if (dateFilter === 'today') cutoff = now - 86400000
  else if (dateFilter === 'week') cutoff = now - 7 * 86400000
  else if (dateFilter === 'month') cutoff = now - 30 * 86400000
  for (const s of sessions) {
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

async function searchAllMessagesAsync(sessions, keyword, dateFilter, MESSAGES_KEY_PREFIX) {
  const results = []
  const now = Date.now()
  let cutoff = 0
  if (dateFilter === 'today') cutoff = now - 86400000
  else if (dateFilter === 'week') cutoff = now - 7 * 86400000
  else if (dateFilter === 'month') cutoff = now - 30 * 86400000
  for (const s of sessions) {
    let msgs = await loadMessagesFromIDB(s.id)
    if (!msgs || msgs.length === 0) {
      try { msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + s.id)) || [] } catch { msgs = [] }
    }
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
  }
  results.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0))
  return results
}

// ── 会话统计 ──
function getSessionStats(messages, sessionId, currentModel) {
  const msgs = messages.filter(m => m.sessionId === sessionId && m.role !== 'system')
  if (msgs.length === 0) return { count: 0, duration: '0 分钟', model: currentModel }
  const first = msgs[0].timestamp
  const last = msgs[msgs.length - 1].timestamp
  const diffMs = last - first
  let duration
  if (diffMs < 60000) duration = `${Math.max(1, Math.round(diffMs / 1000))} 秒`
  else if (diffMs < 3600000) duration = `${Math.round(diffMs / 60000)} 分钟`
  else duration = `${(diffMs / 3600000).toFixed(1)} 小时`
  return { count: msgs.length, duration, model: currentModel }
}

// ── 会话导出（从 IndexedDB 恢复图片数据） ──
async function exportSession(sessions, sessionId, format) {
  const session = sessions.find(s => s.id === sessionId)
  if (!session) return
  // 优先从 IndexedDB 读取
  let msgs = await loadMessagesFromIDB(sessionId)
  if (!msgs || msgs.length === 0) {
    try { msgs = JSON.parse(localStorage.getItem(MESSAGES_KEY_PREFIX + sessionId)) || [] } catch { msgs = [] }
  }
  msgs = msgs.filter(m => m.role !== 'system')
  const restoredMsgs = []
  for (const m of msgs) {
    const restored = { ...m }
    if (m._imageKeys && m._imageKeys.length > 0) {
      let content = m.content || ''
      for (const key of m._imageKeys) {
        const base64 = await loadImage(key)
        // G6 惰性降级：图片已被老化淘汰 / IDB miss → 占位（不报错、不断裂消息流）
        content = content.replace('🖼️ 图片', base64 || EVICTED_IMAGE_PLACEHOLDER)
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
async function importSession(sessions, messages, jsonText, SESSIONS_KEY, MESSAGES_KEY_PREFIX) {
  try {
    const data = JSON.parse(jsonText)
    if (!data.session || !Array.isArray(data.messages)) {
      throw new Error('无效的会话格式')
    }
    const s = {
      id: uid(),
      name: (data.session.name || '导入会话') + ' (导入)',
      createdAt: data.session.createdAt || new Date().toISOString(),
      lastActive: data.session.lastActive || new Date().toISOString(),
      templateId: data.session.templateId || 'blank',
    }
    sessions.unshift(s)
    saveToStorage(SESSIONS_KEY, sessions)
    const importedMsgs = data.messages.map(m => ({
      ...m,
      id: uid(),
      sessionId: s.id,
      streaming: false,
    }))
    messages.push(...importedMsgs)
    persistMessages(s.id, messages, s.id, SESSIONS_KEY, MESSAGES_KEY_PREFIX)
    return { success: true, name: s.name }
  } catch (e) {
    return { success: false, error: e.message }
  }
}

// ── 辅助函数 uid()（chat.js 也用，放这里 export） ──
export function uid() { 
  // 优先 crypto.randomUUID，fallback 到时间戳+随机
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID()
  }
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8) 
}

export {
  SESSIONS_KEY,
  MESSAGES_KEY_PREFIX,
  MAX_SESSIONS,
  QUOTA_NEED_LOGIN,
  evictOldSessions,
  enforceSessionLimit,
  persistMessages,
  createSession,
  deleteSession,
  renameSession,
  pinSession,
  getMessageCount,
  getFirstMessage,
  searchAllMessages,
  getSessionStats,
  exportSession,
  importSession,
  trimCurrentSessionMessages,
  // 异步版本（IndexedDB 优先）
  getMessageCountAsync,
  getFirstMessageAsync,
  searchAllMessagesAsync,
  migrateFromLocalStorage,
}
