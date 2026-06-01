// ── IndexedDB 图片存储（无大小限制） ──
const IMAGE_DB = 'vermes-images'
const IMAGE_STORE = 'attachments'

function openImageDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(IMAGE_DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(IMAGE_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

export async function saveImage(key, base64Data) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    tx.objectStore(IMAGE_STORE).put(base64Data, key)
    await new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej })
  } catch(e) { console.warn('[Vermes] 图片存储失败:', e) }
}

export async function loadImage(key) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readonly')
    const req = tx.objectStore(IMAGE_STORE).get(key)
    return new Promise((res) => { req.onsuccess = () => res(req.result); req.onerror = () => res(null) })
  } catch { return null }
}

export async function deleteImages(keys) {
  try {
    const db = await openImageDB()
    const tx = db.transaction(IMAGE_STORE, 'readwrite')
    const store = tx.objectStore(IMAGE_STORE)
    keys.forEach(k => store.delete(k))
  } catch {}
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
    try { _onWriteFailure() } catch(e) { console.warn('[Vermes] 写入失败回调异常:', e) }
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
