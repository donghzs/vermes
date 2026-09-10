<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useArtifactPanel } from '../composables/useArtifactPanel'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import ModelViewer from './ModelViewer.vue'

const router = useRouter()
const chat = useChatStore()
const { open, width, autoOpen, activeTabId, fileTabs, openPanel, closePanel, togglePanel, setView, openFileTab, closeFileTab, setWidth } = useArtifactPanel()

// ── 产物 / 变更 数据 ──
const artifacts = ref([])
const changes = ref([])

// ── 产物 / 变更 数据（per-session 隔离：内存只放当前会话，其余会话只落各自 localStorage）──
const _curSession = () => (window.__vermes_current_session_id || 'default')

function addArtifact(item, sid) {
  const cur = sid || _curSession()
  const path = item.path || ''
  // 防御性过滤：路径太短（拆字残片）或无文件扩展名且非图片的，不注册为产物
  if (path.length < 4) return null
  const fname = path.split('/').pop() || ''
  const ext = fname.includes('.') ? fname.split('.').pop().toLowerCase() : ''
  if (!ext) return null  // 无扩展名 = 不是交付物
  const id = item.id || (path + ':' + Date.now())
  const rec = {
    id, title: item.title || fname, path, mime: item.mime || '', ts: Date.now(), source: 'agent',
    sessionId: cur,
    _content: item.content || null,
  }
  if (cur === _curSession()) {
    // 当前会话：更新内存 + 持久化（现有行为）
    if (!artifacts.value.find(a => a.id === id)) artifacts.value.push(rec)
    _persistArtifacts()
  } else {
    // 非当前会话（迟到事件）：只落该会话自己的存储，绝不污染当前视图
    try {
      const key = 'vermes-artifacts-' + cur
      const raw = localStorage.getItem(key)
      const list = raw ? JSON.parse(raw) : []
      if (!list.find(a => a.id === id)) { list.push(rec); localStorage.setItem(key, JSON.stringify(list)) }
    } catch (e) {}
  }
  return id
}
function removeArtifact(id) {
  const idx = artifacts.value.findIndex(a => a.id === id)
  if (idx >= 0) artifacts.value.splice(idx, 1)
  _persistArtifacts()
}
function autoCloseOld() {
  if (artifacts.value.length <= 5) return
  const sorted = [...artifacts.value].sort((a, b) => b.ts - a.ts)
  const keepIds = new Set(sorted.slice(0, 5).map(a => a.id))
  artifacts.value.filter(a => !keepIds.has(a.id)).forEach(a => {
    const i = artifacts.value.findIndex(x => x.id === a.id)
    if (i >= 0) artifacts.value.splice(i, 1)
  })
  _persistArtifacts()
}
setInterval(autoCloseOld, 60 * 1000)
function clearArtifacts() { artifacts.value = []; _persistArtifacts() }

const isDesktop = !!(window.vermes?.saveAs)

function addChange(item, sid) {
  const cur = sid || _curSession()
  const id = item.id || (item.path + ':' + Date.now())
  const rec = { id, path: item.path, action: item.action || 'write', ts: Date.now(), source: 'agent' }
  if (cur === _curSession()) {
    if (changes.value.find(c => c.id === id)) return
    changes.value.push(rec)
    _persistChanges()
  } else {
    try {
      const key = 'vermes-artifacts-' + cur + '-changes'
      const raw = localStorage.getItem(key)
      const list = raw ? JSON.parse(raw) : []
      if (!list.find(c => c.id === id)) { list.push(rec); localStorage.setItem(key, JSON.stringify(list)) }
    } catch (e) {}
  }
}
function removeChange(id) {
  const i = changes.value.findIndex(c => c.id === id)
  if (i >= 0) changes.value.splice(i, 1)
  _persistChanges()
}
function clearChanges() { changes.value = []; _persistChanges() }

// ── 功能菜单 ──
const FUNC_TABS = [
  { id: 'tasks', label: '任务进程', icon: '🧭' },
  { id: 'artifacts', label: '产物', icon: '📄' },
  { id: 'workspace', label: '工作空间', icon: '📁' },
  { id: 'changes', label: '变更', icon: '📝' },
]
const showFuncMenu = ref(false)
// activeTabId: 功能视图 id（'tasks'|'artifacts'|'workspace'|'changes'）或 'file:<id>'
// 当有文件标签时优先显示文件内容；没有文件标签时显示功能列表
const activeFunc = computed(() => FUNC_TABS.find(t => t.id === activeTabId.value) || FUNC_TABS.find(t => t.id === 'tasks'))
const showingFile = computed(() => activeTabId.value?.startsWith('file:'))
function funcBadge(id) {
  if (id === 'tasks') return chat.todoItems?.length || 0
  if (id === 'artifacts') return artifacts.value.length
  if (id === 'workspace') return workspaceFiles.value.length
  if (id === 'changes') return changes.value.length
  return 0
}
function selectFunc(id) {
  setView(id)
  showFuncMenu.value = false
}

const activeFileTab = computed(() => fileTabs.value.find(t => t.id === activeTabId.value) || null)
const activeArtifact = computed(() => {
  if (!activeFileTab.value || activeFileTab.value.kind !== 'artifact') return null
  return artifacts.value.find(a => a.id === activeFileTab.value.id.replace('file:', ''))
    || { id: activeFileTab.value.id.replace('file:', ''), title: activeFileTab.value.title, path: activeFileTab.value.path, mime: '' }
})
const activeChange = computed(() => {
  if (!activeFileTab.value || activeFileTab.value.kind !== 'change') return null
  return changes.value.find(c => c.id === activeFileTab.value.id.replace('file:', ''))
})

// ── 任务进程 ──
const now = ref(Date.now())
let _timer = null
onMounted(() => { _timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { if (_timer) clearInterval(_timer) })

const STATUS_TEXT = { pending: '待办', in_progress: '进行中', completed: '已完成', cancelled: '已跳过', interrupted: '已中断' }
const STATUS_ICON = { pending: '⭕️', in_progress: '🔄', completed: '✅', cancelled: '⏭️', interrupted: '⚠️' }
const STATUS_ROW = {
  pending: 'opacity-60',
  in_progress: 'bg-blue-50 dark:bg-blue-900/15 ring-1 ring-blue-200 dark:ring-blue-800',
  completed: '', cancelled: 'opacity-50 line-through',
  interrupted: 'opacity-50 text-amber-600 dark:text-amber-400',
}
const taskStats = computed(() => {
  const items = chat.todoItems || []
  return {
    total: items.length,
    pending: items.filter(i => i.status === 'pending').length,
    inProgress: items.filter(i => i.status === 'in_progress').length,
    completed: items.filter(i => i.status === 'completed').length,
    cancelled: items.filter(i => i.status === 'cancelled').length,
  }
})
const taskProgress = computed(() => taskStats.value.total ? Math.round(taskStats.value.completed / taskStats.value.total * 100) : 0)
function stepElapsed(item) {
  if (item.started_at == null) return null
  const start = item.started_at * 1000
  let end = item.status === 'in_progress' ? now.value : (item.finished_at != null ? item.finished_at * 1000 : start)
  return Math.max(0, Math.round((end - start) / 1000))
}
function fmtDuration(sec) {
  if (sec == null) return ''
  if (sec < 60) return `${sec}秒`
  const m = Math.floor(sec / 60), s = sec % 60
  return s ? `${m}分${s}秒` : `${m}分钟`
}
function stepActivities(id) { return chat.todoStepActivities[id] || [] }
const ungroupedActs = computed(() => chat.todoStepActivities['__ungrouped__'] || [])
const RUNNING_STALE_MS = 30000
function _isStaleRunning(a) { return a.status === 'running' && (now.value - (a.start || 0)) > RUNNING_STALE_MS }
const ungroupedSummary = computed(() => {
  const counts = {}; let running = null
  for (const a of ungroupedActs.value) {
    const label = chat.toolLabel(a.name)
    if (!label) continue
    counts[label] = (counts[label] || 0) + 1
    if (a.status === 'running' && !_isStaleRunning(a)) running = label
  }
  return { counts, running, total: ungroupedActs.value.length }
})

// ── 工作空间 ──
const workspaceFiles = computed(() => {
  const map = new Map()
  for (const a of artifacts.value) {
    const key = a.path || a.title
    if (key && !map.has(key)) map.set(key, { name: a.title || a.path?.split('/').pop() || key, path: a.path, icon: fileIconFor(a), source: '产物', artifactId: a.id })
  }
  for (const c of changes.value) {
    const key = c.path
    if (key && !map.has(key)) map.set(key, { name: key.split('/').pop(), path: key, icon: c.action === 'write' ? '✍️' : c.action === 'patch' ? '🔧' : '📄', source: '变更', changeId: c.id })
  }
  return Array.from(map.values())
})

function fileIconFor(artifact) {
  const ext = artifact?.path?.split('.').pop()?.toLowerCase() || ''
  const map = {
    md: '📄', txt: '📄', log: '📄', json: '📋', csv: '📊', xlsx: '📊', xls: '📊',
    html: '🌐', htm: '🌐', png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    pdf: '📑', docx: '📝', doc: '📝', pptx: '📽️', ppt: '📽️',
    py: '🐍', js: '📜', ts: '📜', java: '☕', go: '🐹', rs: '⚙️',
    sh: '🔧', yaml: '🔧', yml: '🔧', xml: '🔧', sql: '🗃️', zip: '📦', rar: '📦', '7z': '📦',
  }
  return map[ext] || '📄'
}
function statusIconFor(a) { return a?._error ? '❌' : a?._loading ? '⏳' : '✅' }

function rendererFor(artifact) {
  if (!artifact) return 'empty'
  const mime = artifact.mime || ''
  const ext = artifact.path?.split('.').pop()?.toLowerCase() || ''
  if (['stl','step','stp','glb','gltf'].includes(ext)) return 'model'
  if (mime.startsWith('image/') || ['png','jpg','jpeg','gif','webp','svg'].includes(ext)) return 'image'
  if (mime === 'text/html' || ext === 'html') return 'html'
  if (mime === 'text/markdown' || ext === 'md') return 'markdown'
  if (mime === 'application/json' || ext === 'json') return 'json'
  if (mime === 'text/csv' || ext === 'csv') return 'csv'
  if (mime.startsWith('text/') || ['txt','log','py','js','ts','sh','yaml','yml','toml','ini','cfg','xml','sql','java','go','rs','c','cpp','h','rb','php','vue','css','scss','less'].includes(ext)) return 'code'
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (['xlsx','xls'].includes(ext)) return 'excel'
  if (ext === 'docx') return 'docx'
  if (['pptx','ppt','doc'].includes(ext)) return 'office'
  return 'unsupported'
}

const content = ref('')
const contentLoading = ref(false)
const contentError = ref('')
const rawText = ref('')            // 文本类原始内容（编辑用）
const editing = ref(false)         // 是否处于编辑模式
const editBuffer = ref('')         // 编辑缓冲区
const saveState = ref('')          // '', 'saving', 'saved', 'error'
const previewData = ref(null)      // office 静态预览数据（pptx 分页：{kind:'pptx',pages:[...]}）
const EDITABLE_TYPES = new Set(['markdown', 'code', 'html', 'csv', 'json'])
const isEditableType = computed(() => {
  const a = activeArtifact.value
  return !!a && EDITABLE_TYPES.has(rendererFor(a))
})

async function loadContent(artifact) {
  if (!artifact) return
  const type = rendererFor(artifact)
  if (type === 'pdf') { content.value = ''; contentLoading.value = false; return }
  if (type === 'office') { await loadOfficePreview(artifact); return }
  if (artifact._content) { content.value = artifact._content; if (typeof artifact._content === 'string') rawText.value = artifact._content; contentLoading.value = false; return }
  contentLoading.value = true; contentError.value = ''; content.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}`)
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${resp.status}`) }
    if (type === 'image') { content.value = URL.createObjectURL(await resp.blob()) }
    else if (type === 'excel') {
      const XLSX = await loadXLSX()
      const buf = await resp.blob().then(b => b.arrayBuffer())
      const wb = XLSX.read(buf, { type: 'array' })
      content.value = wb.SheetNames.map(name => `<div class="excel-sheet"><div class="excel-sheet-name">📄 ${name}</div>${XLSX.utils.sheet_to_html(wb.Sheets[name], { editable: false })}</div>`).join('')
    } else if (type === 'docx') {
      const mammoth = await loadMammoth()
      const buf = await resp.blob().then(b => b.arrayBuffer())
      content.value = (await mammoth.convertToHtml({ arrayBuffer: buf })).value || '<p style="color:#999">文档内容为空</p>'
    } else {
      const text = await resp.text()
      rawText.value = text
      content.value = type === 'markdown' ? renderMarkdown(text) : text
    }
  } catch (e) { contentError.value = e.message || String(e) } finally { contentLoading.value = false }
}

async function loadOfficePreview(artifact) {
  contentLoading.value = true; contentError.value = ''; previewData.value = null
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}/preview`)
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.reason || d.detail || `HTTP ${resp.status}`) }
    const data = await resp.json()
    previewData.value = (data && data.kind === 'pptx') ? data : null
  } catch (e) { previewData.value = null; contentError.value = e.message || String(e) } finally { contentLoading.value = false }
}

// ── 轻量编辑（人改 → 回存原文件）── Gap #5 当前阶段形态：零新依赖、markdown/code 可编辑
function startEdit() {
  editBuffer.value = rawText.value || ''
  editing.value = true
  saveState.value = ''
}
function cancelEdit() {
  editing.value = false
  editBuffer.value = ''
  saveState.value = ''
}
async function saveEdit() {
  const a = activeArtifact.value
  if (!a) return
  saveState.value = 'saving'
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/content`, {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
      body: editBuffer.value,
    })
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${resp.status}`)
    }
    rawText.value = editBuffer.value
    const type = rendererFor(a)
    content.value = type === 'markdown' ? renderMarkdown(editBuffer.value) : editBuffer.value
    editing.value = false
    saveState.value = 'saved'
    setTimeout(() => { if (saveState.value === 'saved') saveState.value = '' }, 2000)
  } catch (e) {
    saveState.value = 'error'
    contentError.value = e.message || String(e)
  }
}

watch(() => activeTabId.value, (id) => {
  if (id?.startsWith('file:')) { const a = activeArtifact.value; if (a) loadContent(a) }
}, { immediate: true })

let _XLSX = null
function loadXLSX() { if (_XLSX) return Promise.resolve(_XLSX); return import('xlsx').then(m => { _XLSX = m.default || m; return _XLSX }) }
let _mammoth = null
function loadMammoth() { if (_mammoth) return Promise.resolve(_mammoth); return import('mammoth').then(m => { _mammoth = m.default || m; return _mammoth }) }

const md = new MarkdownIt({ html: true, linkify: true, typographer: true, highlight: (code, lang) => {
  if (lang && hljs.getLanguage(lang)) { try { return '<pre class="hljs"><code>' + hljs.highlight(code, { language: lang, ignoreIllegals: true }).value + '</code></pre>' } catch (e) {} }
  return '<pre class="hljs"><code>' + md.utils.escapeHtml(code) + '</code></pre>'
} })
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]; const href = token.attrGet('href') || ''
  if (/^https?:\/\//i.test(href)) { token.attrSet('target', '_blank'); token.attrSet('rel', 'noopener noreferrer') }
  return self.renderToken(tokens, idx, options)
}
function renderMarkdown(text) { try { return md.render(text || '') } catch (e) { return '<pre>' + (text || '') + '</pre>' } }
function formatJson(text) { try { return JSON.stringify(JSON.parse(text), null, 2) } catch (e) { return text } }
function parseCsv(text) {
  const rows = []; let row = []; let field = ''; let inQ = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQ) { if (ch === '"') { if (text[i+1] === '"') { field += '"'; i++ } else inQ = false } else field += ch }
    else { if (ch === '"') inQ = true; else if (ch === ',') { row.push(field); field = '' } else if (ch === '\n') { row.push(field); rows.push(row); row = []; field = '' } else if (ch === '\r') {} else field += ch }
  }
  if (field.length || row.length) { row.push(field); rows.push(row) }
  return rows.filter(r => r.some(c => c.trim() !== ''))
}

function copyPath(path) {
  if (!path) return
  if (navigator?.clipboard?.writeText) navigator.clipboard.writeText(path).catch(() => fallbackCopy(path))
  else fallbackCopy(path)
}
function fallbackCopy(text) {
  try { const ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta) } catch (e) {}
}

async function downloadArtifact(artifact) {
  if (!artifact || artifact.source === 'test') return
  if (window.vermes?.saveAs) {
    try {
      const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}/resolve`)
      if (resp.ok) { const d = await resp.json(); const r = await window.vermes.saveAs(d.path, artifact.title || d.name); if (!r?.ok && r?.err !== 'cancelled') console.error(r?.err); return }
    } catch (e) { console.error(e) }
  }
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const blob = await resp.blob(); const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = artifact.title || artifact.path.split('/').pop()
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
  } catch (e) { console.error(e) }
}

async function openInFolder(artifact) {
  if (!artifact || !artifact.path || artifact.source === 'test') return
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}/resolve`)
    if (resp.ok) {
      const d = await resp.json()
      if (window.vermes?.showItemInFolder) { const r = await window.vermes.showItemInFolder(d.path); if (!r?.ok) console.error(r?.err) }
      else window.open(`file://${d.path.substring(0, d.path.lastIndexOf('/'))}`)
      return
    }
  } catch (e) { console.error(e) }
  if (window.vermes?.showItemInFolder) { const r = await window.vermes.showItemInFolder(artifact.path); if (!r?.ok) console.error(r?.err) }
  else window.open(`file://${artifact.path.substring(0, artifact.path.lastIndexOf('/'))}`)
}

// 在 3D 工作室打开：把右栏预览的 STEP/STL 导入 3D 工作室做深度编辑
async function openIn3DStudio(artifact) {
  if (!artifact || !artifact.path) return
  const filename = artifact.path.split('/').pop() || 'model'
  // 只支持可导入 3D 工作室的格式（其余不跳，避免 404 空白）
  if (!/\.(step|stp|stl|3mf)$/i.test(filename)) return
  // 从产物 serve 端点拉文件二进制，再走 mfgcad upload 导入新会话
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}`)
    if (!resp.ok) throw new Error(`fetch artifact ${resp.status}`)
    const blob = await resp.blob()
    const formData = new FormData()
    formData.append('file', blob, filename)
    formData.append('name', filename.replace(/\.(step|stp|stl|3mf)$/i, ''))
    // P2-4：cadir 产物携带契约原文路径，供 3D 工作室「编辑契约→重建」预填
    if (artifact.source === 'cadir_build') {
      const cp = artifact.path.replace(/output\.(step|stp)$/i, 'contract.json')
      if (cp && cp !== artifact.path) formData.append('contract_path', cp)
    }
    const up = await fetch('/api/mfgcad/upload', { method: 'POST', body: formData })
    const data = await up.json()
    if (!up.ok || !data.session_id) throw new Error(data.error || `upload ${up.status}`)
    // 跳转 3D 工作室，带 session_id 让工作室直接选中新会话
    router.push({ path: '/3d-studio', query: { session: data.session_id } })
  } catch (e) {
    console.error('[openIn3DStudio]', e)
    // 兜底：至少跳到 3D 工作室，让用户手动导入
    router.push('/3d-studio')
  }
}

// 打开文件标签
function openArtifactTab(a) { openFileTab('artifact', a.id, a.title || a.path?.split('/').pop() || '未知文件', a.path, fileIconFor(a)) }
function openChangeTab(c) { openFileTab('change', c.id, c.path?.split('/').pop() || '变更', c.path, c.action === 'write' ? '✍️' : '🔧') }
function openWorkspaceTab(f) {
  if (f.artifactId) { const a = artifacts.value.find(x => x.id === f.artifactId); if (a) return openArtifactTab(a) }
  if (f.changeId) { const c = changes.value.find(x => x.id === f.changeId); if (c) return openChangeTab(c) }
}

// 拖拽 resize
const panelWidth = computed({ get: () => width.value, set: v => setWidth(Math.min(800, Math.max(360, v))) })
const isFullscreen = ref(false)
let _resizing = false
function startResize(e) {
  _resizing = true; document.body.style.cursor = 'col-resize'
  const startX = e.clientX, startW = width.value
  const onMove = (ev) => { if (_resizing) setWidth(Math.min(800, Math.max(360, startW + (startX - ev.clientX)))) }
  const onUp = () => { _resizing = false; document.body.style.cursor = ''; document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp) }
  document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp)
}

function _storageKey() { return 'vermes-artifacts-' + (window.__vermes_current_session_id || 'default') }
function _persistArtifacts() { try { localStorage.setItem(_storageKey(), JSON.stringify(artifacts.value)) } catch (e) {} }
function _persistChanges() { try { localStorage.setItem(_storageKey() + '-changes', JSON.stringify(changes.value)) } catch (e) {} }
function _loadArtifacts() {
  try {
    const raw = localStorage.getItem(_storageKey()); if (raw) artifacts.value = JSON.parse(raw)
    const rawC = localStorage.getItem(_storageKey() + '-changes'); if (rawC) changes.value = JSON.parse(rawC)
  } catch (e) {}
}

// 文件标签同样按会话隔离，避免切会话后仍堆积旧会话打开的标签页
function _fileTabsKey() { return 'vermes-filetabs-' + (window.__vermes_current_session_id || 'default') }
function _persistFileTabs() { try { localStorage.setItem(_fileTabsKey(), JSON.stringify(fileTabs.value)) } catch (e) {} }
function _loadFileTabs() {
  try {
    const raw = localStorage.getItem(_fileTabsKey())
    const list = raw ? JSON.parse(raw) : []
    fileTabs.value = Array.isArray(list) ? list : []
    // 若当前打开的是文件标签，但新会话里没有这个标签，则切回任务视图，避免展示旧会话残留
    if (activeTabId.value?.startsWith('file:') && !fileTabs.value.find(t => t.id === activeTabId.value)) {
      activeTabId.value = fileTabs.value.length ? fileTabs.value[0].id : 'tasks'
    }
  } catch (e) { fileTabs.value = [] }
}
watch(fileTabs, _persistFileTabs, { deep: true })

_loadArtifacts()
_loadFileTabs()
window.addEventListener('vermes-session-change', () => { _loadArtifacts(); _loadFileTabs() })

window.__vermesArtifacts = { addArtifact, removeArtifact, clearArtifacts, artifacts, addChange, removeChange, clearChanges, changes, openArtifactById }

// 按 id 直接打开产物文件标签并渲染内容（供 chat.js 主动弹出 / MessageList 点击调用）。
// 不再依赖 useArtifactPanel 的 openArtifactFile(id, artifactsRef) —— 那个签名需要
// 外部传入组件内部 artifacts ref，而调用方都拿不到，导致 fileTab.path 被传错。
function openArtifactById(id) {
  const a = artifacts.value.find(x => x.id === id)
  if (!a) return false
  openFileTab('artifact', a.id, a.title || a.path?.split('/').pop() || '未知文件', a.path, fileIconFor(a))
  // Bug #6 结构层：把「已打开产物」回告后端，跨轮次注入 LLM 系统提示，彻底消除反问
  try {
    const sid = chat.currentSessionId || window.__vermes_current_session_id
    if (sid && a.path) {
      fetch('/api/chat/' + encodeURIComponent(sid) + '/artifact_opened', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: a.path, title: a.title || a.path?.split('/').pop() }),
      }).catch(() => {})
    }
  } catch (_) {}
  return true
}

// ── A3 版本历史（产物「选区编辑 + 版本回退」的版本 UI 部分，复用后端 /versions /revert /diff）──
const showVersionHistory = ref(false)
const versions = ref([])
const versionsLoading = ref(false)
const versionsError = ref('')
const selectedVersion = ref(null)
const versionPreview = ref('')
const versionPreviewLoading = ref(false)
const versionDiff = ref('')
const versionDiffLoading = ref(false)
const revertingId = ref(null)
const versionTab = ref('preview')
function setVersionTab(t) { versionTab.value = t }

function fmtVerTime(ts) {
  try { return new Date(ts * 1000).toLocaleString('zh-CN', { hour12: false }) } catch (e) { return String(ts) }
}
function fmtVerSize(n) {
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}
async function openVersionHistory() {
  const a = activeArtifact.value
  if (!a) return
  showVersionHistory.value = true
  await loadVersions(a)
}
async function loadVersions(a) {
  if (!a) return
  versionsLoading.value = true; versionsError.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/versions`)
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${resp.status}`) }
    const data = await resp.json()
    versions.value = data.versions || []
  } catch (e) { versionsError.value = e.message || String(e) } finally { versionsLoading.value = false }
}
async function previewVersion(v) {
  const a = activeArtifact.value
  if (!a || v.kind !== 'text') return
  selectedVersion.value = v
  versionPreviewLoading.value = true; versionPreview.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/versions/${v.id}`)
    if (!resp.ok) throw new Error('加载失败')
    const d = await resp.json()
    versionPreview.value = d.content != null ? d.content : ''
  } catch (e) { versionPreview.value = '加载失败: ' + (e.message || e) } finally { versionPreviewLoading.value = false }
}
async function showVersionDiff(v) {
  const a = activeArtifact.value
  if (!a || v.kind !== 'text') return
  selectedVersion.value = v
  versionDiffLoading.value = true; versionDiff.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/versions/${v.id}/diff?base=current`)
    if (!resp.ok) throw new Error('对比失败')
    const d = await resp.json()
    versionDiff.value = d.diffable ? d.diff : (d.reason || '不可对比')
  } catch (e) { versionDiff.value = '对比失败: ' + (e.message || e) } finally { versionDiffLoading.value = false }
}
async function revertVersion(v) {
  const a = activeArtifact.value
  if (!a || !v.restorable) return
  revertingId.value = v.id
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/versions/${v.id}/revert`, { method: 'POST' })
    if (!resp.ok) { const d = await resp.json().catch(() => ({})); throw new Error(d.detail || `HTTP ${resp.status}`) }
    await loadContent(a)
    await loadVersions(a)
    saveState.value = 'saved'
    setTimeout(() => { if (saveState.value === 'saved') saveState.value = '' }, 2000)
  } catch (e) { versionsError.value = '回退失败: ' + (e.message || e) } finally { revertingId.value = null }
}
function closeVersionHistory() {
  showVersionHistory.value = false
  selectedVersion.value = null
  versionPreview.value = ''
  versionDiff.value = ''
  versionTab.value = 'preview'
}

// ── A2 选区编辑（局部重生成）：前端选区 + 调用后端 /patch ──
const editArea = ref(null)            // 编辑态 textarea 引用（读取鼠标选区）
const showPatchBox = ref(false)       // 文本类「选区重生成」指令输入框
const patchInstruction = ref('')
const patchBusy = ref(false)
const patchError = ref('')
const patchSelected = ref('')         // 当前选中的原文（回显用）

const docxSelectedBlock = ref(null)   // docx 预览点击选中的段落 {index, text}
const docxPatchInstruction = ref('')
const docxPatchBusy = ref(false)
const docxPatchError = ref('')

function _curSid() { return window.__vermes_current_session_id || 'default' }

function getEditSelection() {
  const ta = editArea.value
  if (!ta) return null
  const s = ta.selectionStart, e = ta.selectionEnd
  if (s === e) return null            // 空选区
  const text = editBuffer.value
  const lineStart = text.slice(0, s).split('\n').length
  const lineEnd = lineStart + text.slice(s, e).split('\n').length - 1
  return { lineStart, lineEnd, selected: text.slice(s, e) }
}

async function refreshFromDisk(a) {
  const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}`)
  if (!resp.ok) throw new Error('刷新文件失败')
  const txt = await resp.text()
  rawText.value = txt
  content.value = txt
  if (editing.value) editBuffer.value = txt
}

async function loadVersionsSafe(a) {
  if (showVersionHistory.value) { try { await loadVersions(a) } catch (e) {} }
}

function openPatchBox() {
  const sel = getEditSelection()
  patchError.value = ''
  if (!sel) { patchError.value = '请先在编辑区用鼠标选中要重生成的文字'; return }
  patchSelected.value = sel.selected
  patchInstruction.value = ''
  showPatchBox.value = true
}
function closePatchBox() { showPatchBox.value = false; patchInstruction.value = ''; patchError.value = '' }

async function runPatchText() {
  const a = activeArtifact.value
  const sel = getEditSelection()
  if (!a || !sel) { patchError.value = '选区已失效，请重新选中'; return }
  if (!patchInstruction.value.trim()) { patchError.value = '请填写修改意图'; return }
  patchBusy.value = true; patchError.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/patch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        anchor: { type: 'line_range', start: sel.lineStart, end: sel.lineEnd },
        selection: sel.selected,
        instruction: patchInstruction.value.trim(),
        session_id: _curSid(),
      }),
    })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(d.detail || `HTTP ${resp.status}`)
    await refreshFromDisk(a)
    await loadVersionsSafe(a)
    showPatchBox.value = false
    saveState.value = 'saved'
    setTimeout(() => { if (saveState.value === 'saved') saveState.value = '' }, 2000)
  } catch (e) { patchError.value = e.message || String(e) } finally { patchBusy.value = false }
}

function onDocxClick(e) {
  const p = e.target.closest('p')
  if (!p) { docxSelectedBlock.value = null; return }
  const ps = e.currentTarget.querySelectorAll('p')
  const idx = Array.from(ps).indexOf(p)
  if (idx < 0) return
  docxSelectedBlock.value = { index: idx, text: p.textContent || '' }
  docxPatchError.value = ''
}
function closeDocxPatch() { docxPatchInstruction.value = ''; docxPatchError.value = '' }
async function runPatchDocx() {
  const a = activeArtifact.value
  if (!a || !docxSelectedBlock.value) { docxPatchError.value = '请先在文档中点击选中一个段落'; return }
  if (!docxPatchInstruction.value.trim()) { docxPatchError.value = '请填写修改意图'; return }
  docxPatchBusy.value = true; docxPatchError.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(a.path)}/patch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        anchor: { type: 'block_index', index: docxSelectedBlock.value.index },
        selection: docxSelectedBlock.value.text,
        instruction: docxPatchInstruction.value.trim(),
        session_id: _curSid(),
      }),
    })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(d.detail || `HTTP ${resp.status}`)
    docxSelectedBlock.value = null
    docxPatchInstruction.value = ''
    await loadContent(a)
    await loadVersionsSafe(a)
    saveState.value = 'saved'
    setTimeout(() => { if (saveState.value === 'saved') saveState.value = '' }, 2000)
  } catch (e) { docxPatchError.value = e.message || String(e) } finally { docxPatchBusy.value = false }
}
</script>

<template>
  <transition name="drawer-slide">
    <aside
      v-show="open"
      class="h-full bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 flex flex-col transition-all duration-200 relative z-30"
      :class="isFullscreen ? 'w-full' : ''"
      :style="!isFullscreen ? { width: panelWidth + 'px' } : {}"
    >
      <!-- 拖拽手柄 -->
      <div v-if="!isFullscreen" class="absolute top-0 -left-1 w-2 h-full cursor-col-resize z-10 group" @mousedown="startResize">
        <div class="absolute top-1/2 -translate-y-1/2 left-0 w-1 h-12 bg-gray-300 dark:bg-gray-600 rounded-full opacity-0 group-hover:opacity-100 transition"></div>
      </div>

      <!-- 标签条：功能菜单按钮 + 文件标签（主角） + 工具按钮 -->
      <header class="shrink-0 border-b border-gray-200 dark:border-gray-700 flex items-center gap-0.5 px-1.5 py-1 relative z-20">
        <!-- 功能菜单：小图标按钮，hover 展开下拉 -->
        <div class="relative shrink-0">
          <button
            @click="showFuncMenu = !showFuncMenu"
            @mouseenter="showFuncMenu = true"
            class="flex items-center gap-0.5 px-2 py-1.5 rounded-md text-xs transition border"
            :class="!showingFile && activeFunc
              ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border-green-200 dark:border-green-800'
              : 'text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 border-transparent'"
            title="切换视图"
          >
            <span class="text-sm">{{ activeFunc?.icon || '☰' }}</span>
            <svg class="w-3 h-3 transition-transform" :class="showFuncMenu ? 'rotate-180' : ''" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 9 6 6 6-6"/></svg>
          </button>
          <!-- 下拉 -->
          <div
            v-if="showFuncMenu"
            @mouseleave="showFuncMenu = false"
            class="absolute left-0 top-full z-50 min-w-[160px] rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1"
          >
            <button
              v-for="t in FUNC_TABS" :key="t.id"
              @click="selectFunc(t.id)"
              class="flex items-center justify-between w-full px-3 py-1.5 text-sm hover:bg-gray-50 dark:hover:bg-gray-700 transition"
              :class="activeTabId === t.id ? 'text-green-600 dark:text-green-400 font-medium' : 'text-gray-600 dark:text-gray-300'"
            >
              <span class="flex items-center gap-2"><span>{{ t.icon }}</span><span>{{ t.label }}</span></span>
              <span v-if="funcBadge(t.id)" class="text-[10px] text-gray-400">({{ funcBadge(t.id) }})</span>
            </button>
          </div>
        </div>

        <!-- 文件标签（主角，占据全部剩余空间） -->
        <div class="flex-1 min-w-0 flex items-center gap-0.5 overflow-x-auto">
          <button
            v-for="t in fileTabs" :key="t.id"
            @click="activeTabId = t.id"
            class="shrink-0 group flex items-center gap-1 px-2.5 py-1 rounded-md text-xs transition border max-w-[180px]"
            :class="activeTabId === t.id
              ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-blue-200 dark:border-blue-800'
              : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 border-transparent'"
            :title="t.title"
          >
            <span class="shrink-0">{{ t.icon }}</span>
            <span class="truncate">{{ t.title }}</span>
            <span
              @click.stop="closeFileTab(t.id)"
              class="shrink-0 ml-0.5 w-3.5 h-3.5 flex items-center justify-center rounded-full hover:bg-red-100 dark:hover:bg-red-900/40 text-gray-400 hover:text-red-500 transition text-[10px]"
              title="关闭"
            >×</span>
          </button>

          <!-- 空态提示 -->
          <span v-if="fileTabs.length === 0" class="text-[11px] text-gray-400 dark:text-gray-500 px-1">
            点击列表中的文件可打开标签页
          </span>
        </div>

        <!-- 工具按钮 -->
        <div class="flex items-center gap-0.5 shrink-0 pl-1">
          <button @click="isFullscreen = !isFullscreen" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" :title="isFullscreen ? '退出全屏' : '全屏'">
            <svg v-if="!isFullscreen" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
            <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8V5a2 2 0 0 1 2-2h3m0 18H5a2 2 0 0 1-2-2v-3m18 0v3a2 2 0 0 1-2 2h-3M21 8V5a2 2 0 0 0-2-2h-3"/></svg>
          </button>
          <button @click="closePanel" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" title="关闭">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
      </header>

      <!-- 主体 -->
      <div class="flex-1 overflow-hidden">
        <!-- ════ 文件标签内容（主角） ════ -->
        <div v-if="showingFile" class="h-full flex flex-col overflow-hidden">
          <!-- 标题栏 -->
          <div class="shrink-0 px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center gap-2 bg-gray-50/50 dark:bg-gray-800/30">
            <span class="text-sm">{{ activeFileTab?.icon || '📄' }}</span>
            <span class="flex-1 text-sm font-medium text-gray-700 dark:text-gray-200 truncate">{{ activeFileTab?.title || '预览' }}</span>
            <div v-if="activeArtifact?.path" class="flex items-center gap-0.5">
              <button @click="openInFolder(activeArtifact)" class="group relative p-1.5 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
                <span class="header-tooltip header-tooltip-below group-hover:opacity-100">在文件夹中显示</span>
              </button>
              <button v-if="isEditableType && !editing" @click="startEdit" class="group relative p-1.5 rounded-lg hover:bg-amber-100 dark:hover:bg-amber-900/30 text-gray-400 hover:text-amber-600 dark:hover:text-amber-400 transition" title="编辑此文件">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
                <span class="header-tooltip header-tooltip-below group-hover:opacity-100">编辑</span>
              </button>
              <button @click="downloadArtifact(activeArtifact)" class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                <span class="header-tooltip header-tooltip-below group-hover:opacity-100">{{ isDesktop ? '另存为…' : '下载' }}</span>
              </button>
              <button @click="openVersionHistory" class="group relative p-1.5 rounded-lg hover:bg-purple-100 dark:hover:bg-purple-900/30 text-gray-400 hover:text-purple-600 dark:hover:text-purple-400 transition" title="历史版本">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v5h5M3.05 13A9 9 0 1 0 6 5.3L3 8"/><path d="M12 7v5l4 2"/></svg>
                <span class="header-tooltip header-tooltip-below group-hover:opacity-100">历史版本</span>
              </button>
            </div>
          </div>
          <!-- 渲染区：flex-col 让编辑模式 textarea 能撑满整片高度，而不是被普通 block 布局压成小框 -->
          <div class="flex-1 overflow-y-auto flex flex-col">
            <div v-if="activeChange" class="p-5">
              <div class="text-xs text-gray-400 mb-3 break-all">{{ activeChange.path }}</div>
              <span class="text-xs px-2 py-0.5 rounded-full" :class="activeChange.action === 'write' ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'">{{ activeChange.action === 'write' ? '新建/覆盖' : '修改' }}</span>
              <p class="text-sm text-gray-500 dark:text-gray-400 mt-4">该文件已写入变更记录。如需查看完整内容，可在「工作空间」中找到同名文件并打开。</p>
            </div>
            <template v-else-if="activeArtifact">
              <div v-if="contentLoading" class="flex items-center justify-center text-gray-400 py-20"><span class="animate-pulse text-sm">加载中…</span></div>
              <div v-else-if="contentError" class="flex flex-col items-center justify-center text-gray-400 p-5"><div class="text-2xl mb-1">⚠️</div><div class="text-sm text-red-400">加载失败</div><div class="text-xs mt-0.5 text-gray-400">{{ contentError }}</div></div>
              <div v-else-if="editing && isEditableType" class="flex-1 flex flex-col overflow-hidden">
                <div class="shrink-0 flex items-center gap-2 px-3 py-1.5 bg-amber-50 dark:bg-amber-900/20 border-b border-amber-200 dark:border-amber-800">
                  <span class="text-xs font-medium text-amber-700 dark:text-amber-300">编辑模式</span>
                  <span v-if="saveState==='saving'" class="text-xs text-gray-500">保存中…</span>
                  <span v-else-if="saveState==='saved'" class="text-xs text-green-600">已回存原文件</span>
                  <span v-else-if="saveState==='error'" class="text-xs text-red-500">保存失败，见下方提示</span>
                  <div class="flex-1"></div>
                  <button @click="openPatchBox" class="px-3 py-1 rounded-md bg-blue-500 text-white text-xs font-medium hover:bg-blue-600">选区重生成</button>
                  <button @click="saveEdit" :disabled="saveState==='saving'" class="px-3 py-1 rounded-md bg-green-500 text-white text-xs font-medium hover:bg-green-600 disabled:opacity-50">保存回存</button>
                  <button @click="cancelEdit" class="px-3 py-1 rounded-md bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200 text-xs hover:bg-gray-300 dark:hover:bg-gray-600">取消</button>
                </div>
                <!-- A2 选区重生成：指令输入 + 生成并写回 -->
                <div v-if="showPatchBox" class="shrink-0 border-t border-blue-200 dark:border-blue-800 bg-blue-50 dark:bg-blue-900/20 px-3 py-2 space-y-1.5">
                  <div class="text-xs text-blue-700 dark:text-blue-300">选中 <b>{{ patchSelected.length }}</b> 字：<code class="break-all">{{ patchSelected.slice(0, 90) }}{{ patchSelected.length > 90 ? '…' : '' }}</code></div>
                  <div class="flex items-center gap-2">
                    <input v-model="patchInstruction" placeholder="修改意图，如：改成中文 / 补充示例 / 改为函数式写法" class="flex-1 rounded border border-blue-300 dark:border-blue-700 bg-white dark:bg-gray-800 px-2 py-1 text-xs outline-none" @keyup.enter="runPatchText" />
                    <button @click="runPatchText" :disabled="patchBusy" class="px-3 py-1 rounded-md bg-blue-500 text-white text-xs font-medium hover:bg-blue-600 disabled:opacity-50">{{ patchBusy ? '生成中…' : '生成并写回' }}</button>
                    <button @click="closePatchBox" class="px-3 py-1 rounded-md bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-200 text-xs">取消</button>
                  </div>
                  <div v-if="patchError" class="text-xs text-red-500">{{ patchError }}</div>
                </div>
                <textarea ref="editArea" v-model="editBuffer" spellcheck="false" class="flex-1 w-full resize-none p-4 font-mono text-sm leading-relaxed bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 border-0 outline-none"></textarea>
              </div>
              <div v-else-if="rendererFor(activeArtifact) === 'markdown'" class="artifact-markdown p-5 prose prose-sm dark:prose-invert max-w-none" v-html="renderMarkdown(content)"></div>
              <div v-else-if="rendererFor(activeArtifact) === 'html'" class="w-full h-full"><iframe class="w-full h-full border-0 bg-white" sandbox="allow-scripts allow-same-origin" :srcdoc="content"></iframe></div>
              <div v-else-if="rendererFor(activeArtifact) === 'json'" class="p-5 overflow-auto"><pre class="text-sm text-gray-700 dark:text-gray-200"><code>{{ formatJson(content) }}</code></pre></div>
              <div v-else-if="rendererFor(activeArtifact) === 'csv'" class="p-5 overflow-auto"><table class="text-sm border-collapse w-full"><tbody><tr v-for="(row, i) in parseCsv(content)" :key="i" :class="i === 0 ? 'font-semibold bg-gray-50 dark:bg-gray-800' : ''"><td v-for="(cell, j) in row" :key="j" class="border border-gray-200 dark:border-gray-700 px-3 py-1.5 text-gray-700 dark:text-gray-200">{{ cell }}</td></tr></tbody></table></div>
              <div v-else-if="rendererFor(activeArtifact) === 'code'" class="p-5 overflow-auto"><pre class="text-sm text-gray-700 dark:text-gray-200 bg-gray-50 dark:bg-gray-800 rounded-lg p-4"><code>{{ content }}</code></pre></div>
              <div v-else-if="rendererFor(activeArtifact) === 'image'" class="flex items-center justify-center p-5"><img :src="content" :alt="activeArtifact.title || 'image'" class="max-w-full max-h-full object-contain rounded-lg" /></div>
              <div v-else-if="rendererFor(activeArtifact) === 'pdf'" class="w-full h-full"><iframe :src="`/api/v1/artifacts/${encodeURIComponent(activeArtifact.path)}`" class="w-full h-full border-0 bg-white" referrerpolicy="no-referrer"></iframe></div>
              <div v-else-if="rendererFor(activeArtifact) === 'excel'" class="overflow-auto p-3 bg-gray-50 dark:bg-gray-800/30"><div v-if="content" class="excel-render" v-html="content"></div></div>
              <div v-else-if="rendererFor(activeArtifact) === 'docx'" class="relative overflow-y-auto p-6 bg-white dark:bg-gray-900">
                <div v-if="content" class="docx-render prose prose-sm max-w-none dark:prose-invert" v-html="content" @click="onDocxClick"></div>
                <div v-if="docxSelectedBlock" class="absolute top-3 right-3 z-40 w-72 rounded-lg border border-blue-300 bg-white dark:bg-gray-800 shadow-lg p-3 text-xs space-y-2">
                  <div class="font-medium text-blue-600">已选中第 {{ docxSelectedBlock.index + 1 }} 段</div>
                  <input v-model="docxPatchInstruction" placeholder="修改意图，如：扩写 / 改写正式语气" class="w-full rounded border border-blue-300 dark:border-blue-700 bg-white dark:bg-gray-900 px-2 py-1 text-xs outline-none" @keyup.enter="runPatchDocx" />
                  <div class="flex gap-2">
                    <button @click="runPatchDocx" :disabled="docxPatchBusy" class="px-2 py-1 rounded bg-blue-500 text-white">{{ docxPatchBusy ? '生成中…' : '重生成此段' }}</button>
                    <button @click="docxSelectedBlock = null" class="px-2 py-1 rounded bg-gray-200 dark:bg-gray-600">取消</button>
                  </div>
                  <div v-if="docxPatchError" class="text-red-500">{{ docxPatchError }}</div>
                </div>
              </div>
              <div v-else-if="rendererFor(activeArtifact) === 'office'" class="w-full h-full overflow-auto bg-gray-50 dark:bg-gray-800/30">
                <template v-if="previewData && previewData.kind === 'pptx'">
                  <div class="p-4 space-y-4 max-w-3xl mx-auto">
                    <div v-for="page in previewData.pages" :key="page.i" class="bg-white dark:bg-gray-900 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
                      <div class="text-xs font-semibold text-gray-400 mb-2 flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span>第 {{ page.i }} 页 / 共 {{ previewData.pages.length }} 页</div>
                      <div v-if="page.text" class="text-sm text-gray-700 dark:text-gray-200 whitespace-pre-wrap leading-relaxed mb-2">{{ page.text }}</div>
                      <div v-if="page.images && page.images.length" class="grid grid-cols-2 sm:grid-cols-3 gap-2">
                        <img v-for="(img, idx) in page.images" :key="idx" :src="img" class="rounded-md max-w-full border border-gray-100 dark:border-gray-800" alt="slide image" />
                      </div>
                      <div v-if="!page.text && (!page.images || !page.images.length)" class="text-xs text-gray-400 italic">（空白页）</div>
                    </div>
                  </div>
                </template>
                <template v-else>
                  <div class="flex flex-col items-center justify-center text-gray-400 py-20">
                    <div class="text-5xl mb-3">📘</div>
                    <div class="text-sm font-medium text-gray-600 dark:text-gray-300">{{ activeArtifact.title || activeArtifact.path?.split('/').pop() }}</div>
                    <div class="text-xs mt-1 text-gray-400">{{ (contentError && previewData === null) ? '该 Office 格式暂不支持预览' : 'Office 文档无法在浏览器中直接预览' }}</div>
                    <button @click="downloadArtifact(activeArtifact)" class="mt-4 px-4 py-2 rounded-lg bg-green-500 text-white text-sm font-medium hover:bg-green-600 transition flex items-center gap-2"><svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>{{ isDesktop ? '另存为…' : '下载文件' }}</button>
                  </div>
                </template>
              </div>
              <div v-else-if="rendererFor(activeArtifact) === 'model'" class="relative w-full h-full bg-gray-50 dark:bg-gray-900">
                <ModelViewer :src="`/api/v1/artifacts/${encodeURIComponent(activeArtifact.path)}`" />
                <button
                  @click="openIn3DStudio(activeArtifact)"
                  class="absolute top-3 right-3 z-10 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-medium hover:bg-blue-700 transition flex items-center gap-1.5 shadow"
                  title="在 3D 工作室中打开，进行深度编辑"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
                  在 3D 工作室打开
                </button>
              </div>
              <div v-else class="flex flex-col items-center justify-center text-gray-400 py-20"><div class="text-3xl mb-2">📦</div><div class="text-sm">暂不支持此格式</div><div class="text-xs mt-1 text-gray-400">{{ activeArtifact.path }}</div></div>
            </template>
          </div>
        </div>

        <!-- ════ 功能列表（没有文件标签时显示） ════ -->
        <div v-else class="h-full overflow-y-auto">
          <!-- 任务进程 -->
          <div v-if="activeFunc?.id === 'tasks'" class="px-3 py-2">
            <!-- 进度条（有规划任务时才显示） -->
            <div v-if="taskStats.total" class="mb-3">
              <div class="flex items-center justify-between text-[11px] text-gray-400 mb-1">
                <div class="flex items-center gap-1.5">
                  <span>📋 任务进度</span>
                  <div class="flex items-center gap-0.5 rounded-lg bg-gray-100 dark:bg-gray-700 p-0.5">
                    <button @click="chat.setTaskVerbosity('summary')" class="text-[10px] px-1.5 py-0.5 rounded-md transition-colors" :class="chat.taskVerbosity === 'summary' ? 'bg-white dark:bg-gray-600 text-gray-800 dark:text-gray-100 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'">摘要</button>
                    <button @click="chat.setTaskVerbosity('verbose')" class="text-[10px] px-1.5 py-0.5 rounded-md transition-colors" :class="chat.taskVerbosity === 'verbose' ? 'bg-white dark:bg-gray-600 text-gray-800 dark:text-gray-100 shadow-sm' : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'">详细</button>
                  </div>
                </div>
                <span class="tabular-nums">{{ taskStats.completed }}/{{ taskStats.total }}</span>
              </div>
              <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden"><div class="h-full bg-green-500 transition-all duration-500" :style="{ width: taskProgress + '%' }"></div></div>
            </div>

            <!-- 规划任务步骤列表（主视图） -->
            <div v-if="taskStats.total" class="space-y-2">
              <div v-for="item in chat.todoItems" :key="item.id" class="rounded-xl p-2.5 transition-colors" :class="STATUS_ROW[item.status] || ''">
                <div class="flex items-start gap-2">
                  <span class="mt-0.5 flex-shrink-0" :class="{ 'animate-spin': item.status === 'in_progress' }">{{ STATUS_ICON[item.status] || '❓' }}</span>
                  <div class="flex-1 min-w-0">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-sm text-gray-800 dark:text-gray-100 break-words" :class="{ 'line-through': item.status === 'completed' || item.status === 'cancelled' }">{{ item.content }}</span>
                      <span class="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" :class="{ 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300': item.status === 'in_progress', 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-300': item.status === 'completed', 'bg-gray-100 text-gray-400': item.status === 'pending', 'bg-red-100 text-red-500 dark:bg-red-900/30 dark:text-red-300': item.status === 'cancelled' }">{{ STATUS_TEXT[item.status] || item.status }}<template v-if="item.status === 'in_progress' && stepElapsed(item) != null"> · {{ fmtDuration(stepElapsed(item)) }}</template><template v-else-if="item.status === 'completed' && stepElapsed(item) != null"> · {{ fmtDuration(stepElapsed(item)) }}</template></span>
                    </div>
                    <!-- 每步量化信息：交付物 + 完成标准 -->
                    <div v-if="item.deliverable || item.done_when" class="mt-1.5 pl-1 space-y-0.5 border-l-2 border-gray-200 dark:border-gray-700">
                      <div v-if="item.deliverable" class="flex items-start gap-1 text-[11px] text-gray-500 dark:text-gray-400">
                        <span class="flex-shrink-0 mt-px">📦</span>
                        <span class="break-words">{{ item.deliverable }}</span>
                      </div>
                      <div v-if="item.done_when" class="flex items-start gap-1 text-[11px] text-gray-500 dark:text-gray-400">
                        <span class="flex-shrink-0 mt-px">✅</span>
                        <span class="break-words">{{ item.done_when }}</span>
                      </div>
                    </div>
                    <!-- 详细档：展开步骤下的工具调用 -->
                    <div v-if="chat.taskVerbosity === 'verbose' && stepActivities(item.id).length" class="mt-2 space-y-1">
                      <div v-for="act in stepActivities(item.id)" :key="act.id" class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 pl-1"><span v-if="act.status === 'running'" class="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"></span><span v-else-if="act.is_error" class="flex-shrink-0">⚠️</span><span v-else class="flex-shrink-0 text-green-500">✓</span><span class="truncate">{{ chat.toolLabel(act.name) || act.name }}</span></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 没有规划任务时：诚实显示「执行进度」（操作轨迹），绝不伪装成计划 -->
            <template v-if="ungroupedActs.length">
              <div class="mb-2 flex items-center gap-1.5 text-[11px] text-gray-400">
                <span>⚙️ 执行进度</span>
                <span class="tabular-nums">{{ ungroupedActs.length }} 次操作</span>
              </div>
              <div class="space-y-1">
                <div v-for="act in ungroupedActs" :key="act.id" class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 pl-1">
                  <span v-if="act.status === 'running' && !_isStaleRunning(act)" class="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"></span>
                  <span v-else-if="act.is_error && act.status === 'done'" class="flex-shrink-0">⚠️</span>
                  <span v-else-if="act.status === 'done'" class="flex-shrink-0 text-green-500">✓</span>
                  <span v-else class="flex-shrink-0 text-gray-400">•</span>
                  <span class="truncate">{{ chat.toolLabel(act.name) || act.name }}</span>
                </div>
              </div>
            </template>
            <div v-else class="flex flex-col items-center justify-center text-center py-10 px-4">
              <div class="text-3xl mb-2">🧭</div>
              <p class="text-xs text-gray-500 dark:text-gray-400 leading-relaxed">执行任务时的操作进度会实时显示在这里</p>
            </div>
          </div>

          <!-- 产物列表 -->
          <div v-else-if="activeFunc?.id === 'artifacts'" class="px-3 py-3">
            <div v-if="chat.currentSession" class="mb-2 px-2 py-1 rounded-md bg-gray-50 dark:bg-gray-800/50 border border-gray-100 dark:border-gray-700/50">
              <div class="text-[10px] text-gray-400 dark:text-gray-500 truncate">
                <span class="font-medium text-gray-500 dark:text-gray-400">当前会话</span>
                <span class="mx-1">·</span>
                <span>{{ chat.currentSession.name || '未命名会话' }}</span>
              </div>
            </div>
            <div v-if="artifacts.length === 0" class="flex flex-col items-center justify-center text-gray-400 py-12"><div class="text-3xl mb-2">📄</div><div class="text-xs">暂无产物</div></div>
            <div v-else class="space-y-0.5">
              <div v-for="a in artifacts" :key="a.id" class="group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition" @click="openArtifactTab(a)">
                <span class="shrink-0">{{ fileIconFor(a) }}</span>
                <span class="flex-1 text-sm text-gray-700 dark:text-gray-200 truncate">{{ a.title || a.path?.split('/').pop() || '未知文件' }}</span>
                <span class="shrink-0 text-xs">{{ statusIconFor(a) }}</span>
                <button @click.stop="removeArtifact(a.id)" class="opacity-0 group-hover:opacity-100 ml-0.5 w-4 h-4 flex items-center justify-center rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500 transition text-[10px]" title="移除">×</button>
              </div>
            </div>
          </div>

          <!-- 工作空间 -->
          <div v-else-if="activeFunc?.id === 'workspace'" class="px-3 py-3">
            <div v-if="workspaceFiles.length === 0" class="flex flex-col items-center justify-center text-gray-400 py-12"><div class="text-3xl mb-2">📁</div><div class="text-xs">暂无文件</div></div>
            <div v-else class="space-y-0.5">
              <div v-for="f in workspaceFiles" :key="f.path" class="group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition" @click="openWorkspaceTab(f)">
                <span class="shrink-0">{{ f.icon }}</span>
                <span class="flex-1 text-sm text-gray-700 dark:text-gray-200 truncate">{{ f.name }}</span>
                <span class="shrink-0 text-[10px] px-1.5 py-0.5 rounded-full" :class="f.source === '产物' ? 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-300' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300'">{{ f.source }}</span>
              </div>
            </div>
          </div>

          <!-- 变更 -->
          <div v-else-if="activeFunc?.id === 'changes'" class="px-3 py-3">
            <div v-if="changes.length === 0" class="flex flex-col items-center justify-center text-gray-400 py-12"><div class="text-3xl mb-2">📝</div><div class="text-xs">暂无文件变更</div></div>
            <div v-else class="space-y-0.5">
              <div v-for="c in changes" :key="c.id" class="group flex items-center gap-2 px-2 py-1.5 rounded-md cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800 transition" @click="openChangeTab(c)">
                <span class="shrink-0">{{ c.action === 'write' ? '✍️' : c.action === 'patch' ? '🔧' : '📄' }}</span>
                <span class="flex-1 text-sm text-gray-700 dark:text-gray-200 truncate">{{ c.path }}</span>
                <span class="shrink-0 text-xs px-1.5 py-0.5 rounded-full" :class="c.action === 'write' ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'">{{ c.action === 'write' ? '新建/覆盖' : '修改' }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>
  </transition>

    <!-- A3 版本历史抽屉：产物「选区编辑 + 版本回退」的前端 UI -->
    <div v-if="showVersionHistory" class="fixed inset-0 z-50 flex" @click.self="closeVersionHistory">
      <div class="absolute inset-0 bg-black/40 backdrop-blur-sm"></div>
      <aside class="absolute right-0 top-0 h-full w-[400px] max-w-[92vw] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-2xl flex flex-col">
        <!-- 头部 -->
        <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <div class="min-w-0">
            <div class="text-sm font-semibold text-gray-800 dark:text-gray-100">历史版本</div>
            <div class="text-[11px] text-gray-400 truncate">{{ activeArtifact?.path }}</div>
          </div>
          <button @click="closeVersionHistory" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 transition" title="关闭">
            <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
          </button>
        </div>
        <!-- 版本列表 -->
        <div class="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          <div v-if="versionsLoading" class="text-center text-xs text-gray-400 py-8">加载中…</div>
          <div v-else-if="versionsError" class="text-xs text-red-500 py-2 break-words">{{ versionsError }}</div>
          <div v-else-if="versions.length === 0" class="text-center text-xs text-gray-400 py-8">暂无历史版本（保存后将自动快照）</div>
          <div v-for="v in versions" :key="v.id"
               class="rounded-lg border px-3 py-2 cursor-pointer transition"
               :class="selectedVersion?.id === v.id ? 'border-purple-400 bg-purple-50/60 dark:bg-purple-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-purple-300'">
            <div class="flex items-center justify-between">
              <div class="flex items-center gap-2">
                <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">v{{ v.version }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded-full"
                      :class="v.kind==='text' ? 'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-300' : v.kind==='binary' ? 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300' : 'bg-gray-100 text-gray-500'">
                  {{ v.kind==='text' ? '文本' : v.kind==='binary' ? '二进制' : '超大' }}
                </span>
              </div>
              <span class="text-[10px] text-gray-400">{{ fmtVerSize(v.size) }}</span>
            </div>
            <div class="text-[10px] text-gray-400 mt-1">{{ fmtVerTime(v.created_at) }} · {{ v.note }}</div>
            <div class="flex items-center gap-2 mt-2">
              <button v-if="v.kind==='text'" @click.stop="previewVersion(v); setVersionTab('preview')" class="text-[11px] px-2 py-1 rounded-md bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition">预览</button>
              <button v-if="v.kind==='text'" @click.stop="showVersionDiff(v); setVersionTab('diff')" class="text-[11px] px-2 py-1 rounded-md bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition">对比当前</button>
              <button v-if="v.restorable" @click.stop="revertVersion(v)" :disabled="revertingId===v.id"
                      class="text-[11px] px-2 py-1 rounded-md bg-purple-600 hover:bg-purple-700 text-white disabled:opacity-50 transition">
                {{ revertingId===v.id ? '回退中…' : '回退到此版本' }}
              </button>
              <span v-else class="text-[10px] text-gray-400">不可回退</span>
            </div>
          </div>
        </div>
        <!-- 预览 / 对比面板 -->
        <div v-if="selectedVersion" class="h-[40%] border-t border-gray-200 dark:border-gray-700 flex flex-col">
          <div class="flex items-center gap-3 px-3 py-2 border-b border-gray-100 dark:border-gray-800 text-[11px]">
            <button @click="setVersionTab('preview')" :class="versionTab==='preview' ? 'text-purple-600 font-semibold' : 'text-gray-400 hover:text-gray-600'">预览</button>
            <button v-if="selectedVersion.kind==='text'" @click="setVersionTab('diff')" :class="versionTab==='diff' ? 'text-purple-600 font-semibold' : 'text-gray-400 hover:text-gray-600'">对比当前</button>
            <span class="flex-1"></span>
            <span class="text-gray-400">v{{ selectedVersion.version }}</span>
          </div>
          <div class="flex-1 overflow-auto p-3">
            <pre v-if="versionTab==='preview'" class="text-[11px] leading-relaxed text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-words">{{ versionPreview || '点击上方「预览」查看该版本内容' }}</pre>
            <pre v-else-if="versionTab==='diff'" class="text-[11px] leading-relaxed text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-words">{{ versionDiff || '点击上方「对比当前」生成差异' }}</pre>
          </div>
        </div>
      </aside>
    </div>
</template>

<style scoped>
.drawer-slide-enter-active, .drawer-slide-leave-active { transition: transform 0.25s ease; }
.drawer-slide-enter-from, .drawer-slide-leave-to { transform: translateX(100%); }

.header-tooltip {
  position: absolute; bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%);
  padding: 4px 8px; background: #1f2937; color: #f3f4f6; font-size: 11px; border-radius: 4px;
  white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity 0.15s; z-index: 50;
}
/* 产物区标题栏按钮的 tooltip：向下弹，避免被上方 header / 滚动容器裁剪 */
.header-tooltip-below {
  bottom: auto; top: calc(100% + 6px); z-index: 60;
}
.dark .header-tooltip { background: #374151; color: #e5e7eb; }

.artifact-markdown :deep(h1) { font-size: 1.5em; font-weight: 700; margin: 0.6em 0 0.4em; }
.artifact-markdown :deep(h2) { font-size: 1.25em; font-weight: 600; margin: 0.6em 0 0.4em; }
.artifact-markdown :deep(h3) { font-size: 1.1em; font-weight: 600; margin: 0.5em 0 0.3em; }
.artifact-markdown :deep(p) { margin: 0.5em 0; line-height: 1.7; }
.artifact-markdown :deep(ul), .artifact-markdown :deep(ol) { margin: 0.5em 0; padding-left: 1.5em; }
.artifact-markdown :deep(li) { margin: 0.2em 0; }
.artifact-markdown :deep(blockquote) { border-left: 3px solid #22c55e; padding-left: 1em; color: #6b7280; margin: 0.5em 0; }
.artifact-markdown :deep(table) { width: 100%; border-collapse: collapse; margin: 0.5em 0; }
.artifact-markdown :deep(th), .artifact-markdown :deep(td) { border: 1px solid #e5e7eb; padding: 0.4em 0.6em; }
.artifact-markdown :deep(th) { background: #f9fafb; font-weight: 600; }
.artifact-markdown :deep(code) { background: #f3f4f6; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
.artifact-markdown :deep(pre.hljs) { border-radius: 8px; padding: 1em; overflow-x: auto; margin: 0.5em 0; }
.artifact-markdown :deep(pre code) { background: none; padding: 0; }

.excel-render .excel-sheet { margin-bottom: 1rem; }
.excel-render .excel-sheet-name { font-size: 0.875rem; font-weight: 600; color: #374151; margin-bottom: 0.5rem; padding: 0.25rem 0.5rem; background: #f3f4f6; border-radius: 4px; display: inline-block; }
.dark .excel-render .excel-sheet-name { color: #d1d5db; background: #374151; }
.excel-render table { border-collapse: collapse; width: 100%; font-size: 0.8rem; }
.excel-render td, .excel-render th { border: 1px solid #e5e7eb; padding: 0.25rem 0.5rem; color: #374151; white-space: nowrap; }
.dark .excel-render td, .dark .excel-render th { border-color: #4b5563; color: #d1d5db; }
.excel-render tr:first-child td { font-weight: 600; background: #f9fafb; }
.dark .excel-render tr:first-child td { background: #1f2937; }

.docx-render h1 { font-size: 1.5em; font-weight: 700; margin: 0.8em 0 0.4em; }
.docx-render h2 { font-size: 1.25em; font-weight: 600; margin: 0.6em 0 0.3em; }
.docx-render h3 { font-size: 1.1em; font-weight: 600; margin: 0.5em 0 0.3em; }
.docx-render p { margin: 0.5em 0; line-height: 1.7; }
.docx-render ul, .docx-render ol { margin: 0.5em 0; padding-left: 1.5em; }
.docx-render table { width: 100%; border-collapse: collapse; margin: 0.5em 0; }
.docx-render td, .docx-render th { border: 1px solid #e5e7eb; padding: 0.4em 0.6em; }
.docx-render img { max-width: 100%; height: auto; border-radius: 4px; }
</style>
