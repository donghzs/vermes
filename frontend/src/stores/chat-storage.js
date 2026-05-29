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

export function saveToStorage(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)) } catch(e) {}
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
        type: file.type.startsWith('image/') ? 'image' : 'file',
      })
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}
