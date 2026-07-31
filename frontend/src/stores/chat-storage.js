import { logger } from '@/utils/logger'
import { useBackendConnectionStore } from './backendConnection'

// ── IndexedDB 图片存储（G6 隐形老化淘汰：无大小限制→有上限，详见 docs/design-c5-g6-image-server-persist.md）──
const IMAGE_DB = 'vermes-images'
const IMAGE_STORE = 'attachments'
// G6 老化淘汰双条件：写入 ≥90 天 或 总量 ≥500MB（base64 比原图大约 1/3，重度用户一年轻松过 GB）
const IMAGE_EVICT_MAX_BYTES = 500 * 1024 * 1024
const IMAGE_EVICT_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000
// 启动后空闲单次清扫（非 per-save，避免每次粘贴开游标卡输入路径）
let _evictScheduled = false
function scheduleImageEviction() {
  if (_evictScheduled) return
  _evictScheduled = true
  const run = () => { _evictScheduled = false; evictStaleImages().catch(() => {}) }
  if (typeof requestIdleCallback === 'function') requestIdleCallback(run, { timeout: 5000 })
  else setTimeout(run, 3000)
}

// ── IndexedDB 消息存储（突破 localStorage 5MB 限制） ──
const MSG_DB = 'vermes-messages'
const MSG_STORE = 'sessions'
const MSG_DB_VERSION = 1

// ── G0/G2 · IDB 单库粒度自愈（docs/design-startup-integrity-guards-final.md §G0/G2）──
// 背景：Electron 主进程已不再无条件清 indexdb（那会每次启动删光历史图片，G0 活 bug）。
// 脏/不兼容 IDB schema 的防护改到这里：open 失败 → 只删**那一个**坏库 → 重开一次。
// 坏哪个删哪个——消息库坏了绝不动图片库，反之亦然。重开仍失败则如实 reject（调用方已有 try/catch 降级）。
function _openIDB(dbName, version, onUpgrade) {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(dbName, version)
    req.onupgradeneeded = () => onUpgrade(req)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function _openIDBWithSelfHeal(dbName, version, onUpgrade) {
  try {
    return await _openIDB(dbName, version, onUpgrade)
  } catch (e) {
    logger.warn(`[Vermes] IndexedDB 打开失败（${dbName}），单库自愈：删除后重建`, e)
    await new Promise((res) => {
      const del = indexedDB.deleteDatabase(dbName)
      del.onsuccess = del.onerror = del.onblocked = () => res()
    })
    return await _openIDB(dbName, version, onUpgrade) // 二次失败则向上抛，调用方降级
  }
}

// 连接缓存：避免每次操作都 open
let _imageDBPromise = null
function openImageDB() {
  return _imageDBPromise || (_imageDBPromise = (async () => {
    try {
      const db = await _openIDBWithSelfHeal(IMAGE_DB, 1, (req) => req.result.createObjectStore(IMAGE_STORE))
      db.onclose = () => { _imageDBPromise = null }
      return db
    } catch (e) { _imageDBPromise = null; throw e }
  })())
}

let _msgDBPromise = null
function openMsgDB() {
  return _msgDBPromise || (_msgDBPromise = (async () => {
    try {
      const db = await _openIDBWithSelfHeal(MSG_DB, MSG_DB_VERSION, (req) => {
        const store = req.result.createObjectStore(MSG_STORE)
        store.createIndex('sessionId', 'sessionId', { unique: false })
      })
      db.onclose = () => { _msgDBPromise = null }
      return db
    } catch (e) { _msgDBPromise = null; throw e }
  })())
}

export async function saveImage(key, base64Data) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    // G6 前置：value 从裸字符串升级为 { d: base64, t: 写入时间 }，
    // 供老化淘汰按时间删最旧（key=${uuid}-${idx} 无时间戳，必须存 t）。
    tx.objectStore(IMAGE_STORE).put({ d: base64Data, t: Date.now() }, key)
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej })
    // 启动后空闲单次清扫（saveImage 自身零开销，不查体积）
    scheduleImageEviction()
  } catch(e) { logger.warn('[Vermes] 图片存储失败:', e) }
}

export async function loadImage(key) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readonly')
    const req = tx.objectStore(IMAGE_STORE).get(key)
    return new Promise((res) => { req.onsuccess = () => {
      const r = req.result
      // G6 兼容旧格式：历史裸字符串直接返回；新格式取 .d
      res(typeof r === 'string' ? r : (r && r.d) || null)
    }; req.onerror = () => res(null) })
  } catch { return null }
}

// ── G6 老化淘汰（age-based FIFO，非 LRU）──
// 单次游标遍历：累计字节 + 删最旧（≥90天 或 总量≥500MB）。
// best-effort，失败仅 warn，不阻断读写。
export async function evictStaleImages() {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    const store = tx.objectStore(IMAGE_STORE)
    const now = Date.now()
    let byteSum = 0
    const toDelete = []
    const entries = []  // { key, bytes, t } —— key 是 ${uuid}-${idx}，字典序≠写入序，必须按 value.t 排
    await new Promise((res, rej) => {
      const req = store.openCursor()
      req.onsuccess = () => {
        const cursor = req.result
        if (!cursor) { res(); return }
        const v = cursor.value
        const d = typeof v === 'string' ? v : (v && v.d) || ''
        const t = typeof v === 'string' ? 0 : (v && v.t) || 0  // 旧格式无 t → 0 = 视为最旧
        byteSum += d.length
        entries.push({ key: cursor.key, bytes: d.length, t })
        if (t && now - t >= IMAGE_EVICT_MAX_AGE_MS) toDelete.push(cursor.key)
        cursor.continue()
      }
      req.onerror = rej
    })
    // 总量超 500MB：按写入时间 t 升序（最旧在前）删直到达标，逐条真实字节数（游标已拿到，零额外成本）
    if (byteSum > IMAGE_EVICT_MAX_BYTES) {
      const marked = new Set(toDelete)
      entries.sort((a, b) => a.t - b.t)
      let freed = 0
      for (const e of entries) {
        if (byteSum - freed <= IMAGE_EVICT_MAX_BYTES) break
        if (marked.has(e.key)) { freed += e.bytes; continue }  // 超龄条目已在删除队列，计入 freed 不重复入队
        toDelete.push(e.key)
        freed += e.bytes
      }
    }
    if (toDelete.length) {
      await new Promise((res) => {
        const delTx = db.transaction(IMAGE_STORE, 'readwrite')
        toDelete.forEach(k => delTx.objectStore(IMAGE_STORE).delete(k))
        delTx.oncomplete = res; delTx.onerror = res
      })
      logger.info(`[Vermes] 图片老化淘汰：${toDelete.length} 张（总量 ${Math.round(byteSum/1024/1024)}MB）`)
    }
  } catch (e) {
    logger.warn('[Vermes] 图片老化淘汰失败（可忽略）:', e)
  }
}

export async function deleteImages(keys) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    const store = tx.objectStore(IMAGE_STORE)
    keys.forEach(k => store.delete(k))
  } catch {}
}

// ── IndexedDB 消息 CRUD ──

export async function saveMessagesToIDB(sessionId, messages) {
  try {
    const db = await openMsgDB()
    const tx = db.transaction(MSG_STORE, 'readwrite')
    // 深拷贝去掉 Vue 响应式代理（IDB structuredClone 不支持 Proxy）
    const plain = JSON.parse(JSON.stringify(messages))
    tx.objectStore(MSG_STORE).put(plain, sessionId)
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej })
  } catch(e) { logger.warn('[Vermes] 消息 IndexedDB 写入失败:', e) }
}

export async function loadMessagesFromIDB(sessionId) {
  try {
    const db = await openMsgDB()
    const tx = db.transaction(MSG_STORE, 'readonly')
    const req = tx.objectStore(MSG_STORE).get(sessionId)
    return new Promise((res) => { req.onsuccess = () => res(req.result || []); req.onerror = () => res([]) })
  } catch { return [] }
}

export async function deleteMessagesFromIDB(sessionId) {
  try {
    const db = await openMsgDB()
    const tx = db.transaction(MSG_STORE, 'readwrite')
    tx.objectStore(MSG_STORE).delete(sessionId)
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej })
  } catch(e) { logger.warn('[Vermes] 消息 IndexedDB 删除失败:', e) }
}

// ── 一次性迁移：localStorage → IndexedDB ──
const MIGRATION_KEY = 'vermes-idb-migrated'

export async function migrateFromLocalStorage(MESSAGES_KEY_PREFIX) {
  if (localStorage.getItem(MIGRATION_KEY) === 'v1') return 0
  let migrated = 0
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const key = localStorage.key(i)
    if (key && key.startsWith(MESSAGES_KEY_PREFIX)) keys.push(key)
  }
  for (const key of keys) {
    try {
      const sessionId = key.slice(MESSAGES_KEY_PREFIX.length)
      const msgs = JSON.parse(localStorage.getItem(key))
      if (Array.isArray(msgs) && msgs.length > 0) {
        await saveMessagesToIDB(sessionId, msgs)
        localStorage.removeItem(key)
        migrated++
      }
    } catch(e) { logger.warn('[Vermes] 迁移会话失败:', key, e) }
  }
  if (migrated > 0) {
    logger.log(`[Vermes] 已迁移 ${migrated} 个会话从 localStorage 到 IndexedDB`)
  }
  try { localStorage.setItem(MIGRATION_KEY, 'v1') } catch(e) { /* storage full */ }
  return migrated
}

// ── localStorage 辅助 ──
export function loadFromStorage(key) {
  try { return JSON.parse(localStorage.getItem(key)) || [] } catch(e) { return [] }
}

// ── 异步写入队列：用 requestIdleCallback 延迟序列化，不阻塞主线程 ──
const _pendingWrites = new Map()
let _writeScheduled = false
let _onWriteFailure = null  // 注册回调：异步写入失败时通知调用方触发清理

function _flushWrites() {
  _writeScheduled = false
  let anyFailed = false
  for (const [key, json] of _pendingWrites) {
    try { localStorage.setItem(key, json) } catch(e) { anyFailed = true }
  }
  _pendingWrites.clear()
  // 通知调用方有写入失败，触发清理
  if (anyFailed && _onWriteFailure) {
    try { _onWriteFailure() } catch(e) { logger.warn('[Vermes] 写入失败回调异常:', e) }
  }
}

function _scheduleWrite() {
  if (_writeScheduled) return
  _writeScheduled = true
  if (typeof requestIdleCallback !== 'undefined') {
    requestIdleCallback(_flushWrites, { timeout: 200 })
  } else {
    setTimeout(_flushWrites, 0)
  }
}

// 注册写入失败回调（chat.js 调用，触发 _evictOldSessions）
export function onStorageWriteFailure(callback) {
  _onWriteFailure = callback
}

export function saveToStorage(key, val) {
  // 统一序列化一次，避免重复 stringify + 防止 Vue 代理引用变异
  const json = JSON.stringify(val)
  if (json.length < 2048) {
    // 小数据同步写，保证即时可读
    try { localStorage.setItem(key, json); return true } catch(e) { return false }
  }
  // 大数据异步写，存已序列化的字符串（非 Vue 代理引用）
  _pendingWrites.set(key, json)
  _scheduleWrite()
  return true
}

// 强制刷新所有待写入（用于 beforeunload / switchSession）
export function flushStorageWrites() {
  _writeScheduled = false
  for (const [key, json] of _pendingWrites) {
    try { localStorage.setItem(key, json) } catch(e) { /* storage full */ }
  }
  _pendingWrites.clear()
}

// ── base64 图片剥离 ──
const BASE64_RE = /!\[([^\]]*)\]\(data:image[^)]+\)/g

export function stripBase64FromContent(content, messageId) {
  const images = {}
  let idx = 0
  const prefix = messageId || Date.now().toString(36)
  const stripped = content.replace(BASE64_RE, (match, name) => {
    const key = `${prefix}-${idx++}`
    images[key] = match
    return `🖼️ ${name || '图片'}`
  })
  return { stripped, images }
}

// ── 文件转 base64 ──
export function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result.split(',')[1]
      resolve({
        name: file.name,
        size: file.size,
        mimeType: file.type || 'application/octet-stream',
        base64: base64,
        type: file.type.startsWith('image/') ? 'image' : file.type.startsWith('video/') ? 'video' : 'file',
      })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

// ── 后端 API 消息持久化 (pywebview macOS 不持久化 IndexedDB) ──

export async function saveMessagesToAPI(sessionId, messages) {
  try {
    const resp = await fetch(`/api/gui/messages/${sessionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages }),
    })
    return resp.ok
  } catch (e) {
    logger.warn('[Vermes] API 消息保存失败:', e)
    return false
  }
}

export async function loadMessagesFromAPI(sessionId) {
  try {
    const resp = await fetch(`/api/gui/messages/${sessionId}`)
    if (!resp.ok) return []
    const data = await resp.json()
    return data.messages || []
  } catch (e) {
    logger.warn('[Vermes] API 消息加载失败:', e)
    return []
  }
}

export async function deleteMessagesFromAPI(sessionId) {
  try {
    await fetch(`/api/gui/messages/${sessionId}`, { method: 'DELETE' })
  } catch (e) { /* ignore */ }
}

export async function listSessionsFromAPI() {
  try {
    const resp = await fetch('/api/gui/sessions')
    if (!resp.ok) return []
    const data = await resp.json()
    return data.sessions || []
  } catch (e) {
    return []
  }
}

// ── state.db 渠道会话数据源（步骤1：桌面端全渠道统一视图） ──
// /api/sessions* 非公开路径，必须带会话 token（web_server auth_middleware）

function stateDBHeaders() {
  const h = {}
  const t = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
  if (t) h['X-Vermes-Session-Token'] = t
  return h
}

/** 列出 state.db 会话（telegram/discord/cli 等全渠道；步骤2后含 web）
 * 失败时抛异常而非返回空数组——调用方应保留旧列表而非整体清空。 */
export async function listChannelSessionsFromAPI(limit = 200) {
  const resp = await fetch(`/api/sessions?limit=${limit}`, { headers: stateDBHeaders() })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const data = await resp.json()
  return data.sessions || []
}

/** 读取 state.db 某会话的消息（渠道会话续看） */
export async function loadChannelMessagesFromAPI(sessionId) {
  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, { headers: stateDBHeaders() })
    if (!resp.ok) return []
    const data = await resp.json()
    return data.messages || []
  } catch (e) {
    logger.warn('[Vermes] state.db 消息加载失败:', e)
    return []
  }
}

/** 步骤3：桌面代发渠道消息（写 relay 信号，gateway 消费后回复回渠道+state.db） */
export async function sendFromDesktopAPI(sessionId, text) {
  // A.4.4: 已知后端离线（主进程看门狗广播）→ 不徒劳发请求，返回 pending 让上层自动重发，
  // 避免"渠道代发失败: TypeError: Failed to fetch"刷屏（#DMG 实测根因）。
  let backendOffline = false
  try {
    const conn = useBackendConnectionStore()
    backendOffline = !conn.online
  } catch (_) {
    // 降级意图：取不到 store（Web 模式无 window.vermes / 插件未挂载）时，
    // 不假设离线，继续走下方真实请求；由 sendFromDesktopAPI 自身的退避重试吸收失败，
    // 而非在此短路返回 pending —— 否则会误吞「store 不可用但后端其实正常」的请求。
  }

  if (backendOffline) {
    return { ok: false, status: 0, pending: true, detail: 'backend offline' }
  }

  const doFetch = async () => {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/send-from-desktop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...stateDBHeaders() },
      body: JSON.stringify({ text }),
    })
    const data = await resp.json().catch(() => ({}))
    return { ok: resp.ok, status: resp.status, ...data }
  }

  // A.4.4: 网络错误（Failed to fetch）退避重试，吸收后端自愈窗口
  // （A.4.1 看门狗 ~5s 自重启；api.js 对 POST 默认 maxRetries=0 不重试，故此处自管）。
  let lastErr = null
  for (let attempt = 0; attempt <= 2; attempt++) {
    try {
      return await doFetch()
    } catch (e) {
      lastErr = e
      const msg = String(e && e.message || '')
      if (attempt < 2 && (msg.includes('Failed to fetch') || msg.includes('NetworkError'))) {
        await new Promise((r) => setTimeout(r, 1000 * Math.pow(2, attempt)))  // 1s / 2s
        continue
      }
      break
    }
  }
  return { ok: false, status: 0, detail: lastErr ? String(lastErr) : 'unknown error' }
}

/** 步骤3：轮询 relay 状态（pending/running/completed/failed） */
export async function getRelayStateAPI(sessionId) {
  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/relay-state`, { headers: stateDBHeaders() })
    if (!resp.ok) return null
    const data = await resp.json()
    return data.relay || null
  } catch (e) {
    return null
  }
}

/** 删除 state.db 会话（带 token，修复此前裸 fetch 吃 401 的问题） */
export async function deleteChannelSessionFromAPI(sessionId) {
  try {
    const resp = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'DELETE',
      headers: stateDBHeaders(),
    })
    return resp.ok
  } catch (e) {
    return false
  }
}
