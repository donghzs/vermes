<script setup>
/**
 * ArtifactPanel — 聊天右侧产物面板
 * 范式参照 ToolSkillDrawer（Teleport / 600px / drawer-slide）
 * 阶段 1：骨架 + Markdown/HTML/Code/Image/JSON/CSV 渲染器 + 全屏 toggle
 */
import { ref, computed, watch } from 'vue'
import { useRightPanel } from '../composables/useRightPanel'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import markdown from 'highlight.js/lib/languages/markdown'
import typescript from 'highlight.js/lib/languages/typescript'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'

// 注册常用语言
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)

const { open, tab, artifactTab, autoOpenOnArtifact, panelWidth, openPanel, closePanel, setArtifactTab } = useRightPanel()

// ── 拖拽 resize ──
const isResizing = ref(false)
function startResize(e) {
  isResizing.value = true
  const startX = e.clientX
  const startW = panelWidth.value
  const onMove = (ev) => {
    const newW = startW + (startX - ev.clientX)
    if (newW >= 360 && newW <= 800) panelWidth.value = newW
  }
  const onUp = () => {
    isResizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

// ── Markdown 渲染器（复用 MessageList 范式）──
const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try { return `<pre class="hljs"><code>${hljs.highlight(str, { language: lang }).value}</code></pre>` } catch {}
    }
    try { return `<pre class="hljs"><code>${hljs.highlightAuto(str, ['javascript', 'python', 'bash', 'json', 'html', 'css']).value}</code></pre>` } catch {}
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  }
})
md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return self.renderToken(tokens, idx, options)
}

function renderMarkdown(text) {
  if (!text) return ''
  return md.render(text)
}

function formatJson(text) {
  try { return JSON.stringify(JSON.parse(text), null, 2) } catch { return text }
}

function parseCsv(text) {
  if (!text) return []
  const rows = []
  let row = [], field = '', inQuote = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuote) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ }
        else inQuote = false
      } else field += c
    } else {
      if (c === '"') inQuote = true
      else if (c === ',') { row.push(field); field = '' }
      else if (c === '\n') { row.push(field); rows.push(row); row = []; field = '' }
      else if (c === '\r') { /* skip */ }
      else field += c
    }
  }
  if (field || row.length) { row.push(field); rows.push(row) }
  return rows
}

// ── 产物列表（localStorage 持久化，per-session）──
const ARTIFACTS_STORAGE_KEY = 'vermes-artifacts'
const artifacts = ref([])  // [{ id, path, title, mime, source, ts }]
const activeId = ref(null)
// 变更列表
const changes = ref([])  // [{ id, path, action, diff, ts }]
const activeChangeId = ref(null)
const activeChange = computed(() => changes.value.find(c => c.id === activeChangeId.value))

function addChange(item) {
  const id = `chg-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
  changes.value.unshift({ id, ts: Date.now(), ...item })
  if (!activeChangeId.value) activeChangeId.value = id
  // 自动切换到变更 tab
  if (autoOpenOnArtifact.value) {
    openPanel('artifacts')
    setArtifactTab('changes')
  }
}
function clearChanges() {
  changes.value = []
  activeChangeId.value = null
}
window.__vermesChanges = { addChange, clearChanges, changes }

// 预览 tab
const previewUrl = ref('')
const previewSrc = ref('')
const previewLoaded = ref(false)
function loadPreview() {
  const url = previewUrl.value.trim()
  if (!url) return
  // 支持 file:// 和 http(s)://
  if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('file://')) {
    previewSrc.value = url
  } else {
    // 尝试作为本地路径加载
    previewSrc.value = `/api/v1/artifacts/${encodeURIComponent(url)}`
  }
  previewLoaded.value = true
}
function refreshPreview() {
  if (previewSrc.value) {
    const src = previewSrc.value
    previewSrc.value = ''
    setTimeout(() => { previewSrc.value = src }, 50)
  }
}
function openPreviewExternal() {
  if (previewSrc.value) {
    if (window.vermes?.openExternal) {
      window.vermes.openExternal(previewSrc.value)
    } else {
      window.open(previewSrc.value, '_blank')
    }
  }
}
// 暴露给产物 tab 点击 HTML 文件时切换到预览
window.__vermesPreview = {
  previewUrl,
  loadPreview,
  setUrl: (url) => { previewUrl.value = url; loadPreview() },
}

// 文件 tab
const fileItems = ref([])
const fileLoading = ref(false)
const fileCrumbs = ref('')
const currentDir = ref('')

async function loadFiles(dir = '') {
  fileLoading.value = true
  try {
    const res = await fetch(`/api/v1/workspace/tree?path=${encodeURIComponent(dir)}`)
    if (!res.ok) return
    const data = await res.json()
    fileItems.value = data.items || []
    fileCrumbs.value = data.current || '工作目录'
    currentDir.value = dir
  } catch (e) {
    console.error('loadFiles:', e)
  } finally {
    fileLoading.value = false
  }
}
function onFileClick(item) {
  if (item.is_dir) {
    loadFiles(item.path)
  } else {
    previewFile(item)
  }
}
function goUp() {
  if (!currentDir.value) return
  const parts = currentDir.value.split('/')
  parts.pop()
  loadFiles(parts.join('/'))
}
function fileIcon(ext) {
  const map = { '.md': '📝', '.html': '🌐', '.json': '📋', '.csv': '📊', '.py': '🐍', '.js': '📜', '.ts': '📜', '.txt': '📄', '.pdf': '📕', '.docx': '📘', '.xlsx': '📗', '.pptx': '📙', '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.gif': '🖼️', '.svg': '🖼️', '.step': '⚙️', '.stp': '⚙️', '.stl': '🖨️', '.gcode': '⚙️' }
  return map[ext] || '📄'
}
function formatSize(bytes) {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1048576).toFixed(1) + 'MB'
}
function previewFile(item) {
  // 切到预览 tab 加载文件
  setArtifactTab('preview')
  const url = `/api/v1/artifacts/${item.path}`
  previewUrl.value = item.path
  previewSrc.value = url
  previewLoaded.value = true
}
// 当切到文件 tab 时自动加载
watch(artifactTab, (v) => {
  if (v === 'files' && fileItems.value.length === 0 && !fileLoading.value) {
    loadFiles('')
  }
})
const isFullscreen = ref(false)

// 从 localStorage 恢复
function _loadArtifacts() {
  try {
    const raw = localStorage.getItem(ARTIFACTS_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        artifacts.value = parsed
        if (parsed.length > 0) activeId.value = parsed[0].id
      }
    }
  } catch {}
}

// 持久化到 localStorage
function _persistArtifacts() {
  try {
    // 最多保留 20 条，避免无限增长
    const toSave = artifacts.value.slice(0, 20)
    localStorage.setItem(ARTIFACTS_STORAGE_KEY, JSON.stringify(toSave))
  } catch {}
}

_loadArtifacts()

function addArtifact(item) {
  const existing = artifacts.value.findIndex(a => a.path === item.path)
  if (existing >= 0) {
    artifacts.value[existing] = { ...artifacts.value[existing], ...item, ts: Date.now() }
    activeId.value = artifacts.value[existing].id
    _persistArtifacts()
    return
  }
  const id = 'art-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6)
  artifacts.value.unshift({ id, ts: Date.now(), ...item })
  activeId.value = id
  _persistArtifacts()
}

function removeArtifact(id) {
  const idx = artifacts.value.findIndex(a => a.id === id)
  if (idx >= 0) artifacts.value.splice(idx, 1)
  if (activeId.value === id) activeId.value = artifacts.value[0]?.id || null
  _persistArtifacts()
}

function clearArtifacts() {
  artifacts.value = []
  activeId.value = null
  _persistArtifacts()
}

const activeArtifact = computed(() => artifacts.value.find(a => a.id === activeId.value))

// ── MIME → 渲染器映射 ──
function rendererFor(artifact) {
  if (!artifact) return 'empty'
  const mime = artifact.mime || ''
  const ext = artifact.path?.split('.').pop()?.toLowerCase() || ''
  if (mime.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image'
  if (mime === 'text/html' || ext === 'html') return 'html'
  if (mime === 'text/markdown' || ext === 'md') return 'markdown'
  if (mime === 'application/json' || ext === 'json') return 'json'
  if (mime === 'text/csv' || ext === 'csv') return 'csv'
  if (mime.startsWith('text/') || ['txt', 'log', 'py', 'js', 'ts', 'sh', 'yaml', 'yml', 'toml', 'ini', 'cfg'].includes(ext)) return 'code'
  return 'unsupported'
}

// ── 文件内容加载 ──
const content = ref('')
const contentLoading = ref(false)
const contentError = ref('')

async function loadContent(artifact) {
  if (!artifact) return
  // 如果产物自带内容（tool_end SSE 推送或测试注入），直接使用
  if (artifact._content) {
    content.value = artifact._content
    contentLoading.value = false
    return
  }
  contentLoading.value = true
  contentError.value = ''
  content.value = ''
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}`)
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}))
      throw new Error(d.detail || `HTTP ${resp.status}`)
    }
    const type = rendererFor(artifact)
    if (type === 'image') {
      const blob = await resp.blob()
      content.value = URL.createObjectURL(blob)
    } else {
      content.value = await resp.text()
    }
  } catch (e) {
    contentError.value = e.message || String(e)
  } finally {
    contentLoading.value = false
  }
}

watch(activeId, () => {
  if (activeArtifact.value) loadContent(activeArtifact.value)
})

watch([open, tab], ([o, t]) => {
  if (o && t === 'artifacts' && !activeId.value && artifacts.value.length > 0) {
    activeId.value = artifacts.value[0].id
  }
})


// ── 下载产物（阶段 5）──
async function downloadArtifact(artifact) {
  if (!artifact || artifact.source === 'test') return
  try {
    const resp = await fetch(`/api/v1/artifacts/${encodeURIComponent(artifact.path)}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = artifact.title || artifact.path.split('/').pop()
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error('[ArtifactPanel] download failed:', e)
  }
}

// ── 在文件夹中显示（WorkBuddy 核心体验）──
async function openInFolder(artifact) {
  if (!artifact || !artifact.path || artifact.source === 'test') return
  if (window.vermes?.showItemInFolder) {
    const result = await window.vermes.showItemInFolder(artifact.path)
    if (!result?.ok) console.error('[ArtifactPanel] showItemInFolder failed:', result?.err)
  } else {
    // Web fallback: 尝试打开所在目录
    const dir = artifact.path.substring(0, artifact.path.lastIndexOf('/'))
    window.open(`file://${dir}`)
  }
}

// 暴露给全局供 MessageList / chat store 调用
window.__vermesArtifacts = { addArtifact, removeArtifact, clearArtifacts, artifacts }
</script>

<template>
  <Teleport to="body">
    <!-- 背景遮罩 -->
    <div v-if="open && tab === 'artifacts'" class="fixed inset-0 z-[90] bg-black/40" @click="closePanel"></div>

    <!-- 右侧产物面板 -->
    <transition name="drawer-slide">
      <aside
        v-if="open && tab === 'artifacts'"
        class="fixed top-0 right-0 z-[91] h-full bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-2xl flex flex-col transition-all duration-200"
        :class="isFullscreen ? 'w-full' : 'max-w-[94vw]'"
        :style="!isFullscreen ? { width: panelWidth + 'px' } : {}"
      >
        <!-- 拖拽手柄（左边缘） -->
        <div
          v-if="!isFullscreen"
          class="absolute top-0 -left-1 w-2 h-full cursor-col-resize z-10 group"
          @mousedown="startResize"
        >
          <div class="absolute top-1/2 -translate-y-1/2 left-0 w-1 h-12 bg-gray-300 dark:bg-gray-600 rounded-full opacity-0 group-hover:opacity-100 transition"></div>
        </div>

        <!-- 头部 + 4-tab 导航 -->
        <header class="shrink-0 border-b border-gray-200 dark:border-gray-700">
          <div class="px-5 py-3 flex items-center justify-between">
            <div class="flex items-center gap-2">
              <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100">产物工作台</h2>
            </div>
            <div class="flex items-center gap-1">
              <button
                @click="isFullscreen = !isFullscreen"
                class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
                :title="isFullscreen ? '退出全屏' : '全屏'"
              >
                <svg v-if="!isFullscreen" class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
                <svg v-else class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8V5a2 2 0 0 1 2-2h3m0 18H5a2 2 0 0 1-2-2v-3m18 0v3a2 2 0 0 1-2 2h-3M21 8V5a2 2 0 0 0-2-2h-3"/></svg>
              </button>
              <button @click="closePanel" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" title="关闭 (Esc)">
                <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
              </button>
            </div>
          </div>
          <!-- 4-tab 导航 -->
          <div class="flex items-center gap-1 px-3 pb-2">
            <button
              v-for="t in [
                { id: 'artifacts', label: '产物', icon: '📄', count: artifacts.length },
                { id: 'files', label: '文件', icon: '📁', count: 0 },
                { id: 'changes', label: '变更', icon: '📝', count: changes.length },
                { id: 'preview', label: '预览', icon: '🌐', count: 0 }
              ]"
              :key="t.id"
              @click="setArtifactTab(t.id)"
              :class="artifactTab === t.id
                ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border-b-2 border-green-500'
                : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 border-b-2 border-transparent'"
              class="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium transition rounded-t-lg"
            >
              <span>{{ t.icon }}</span>
              <span>{{ t.label }}</span>
              <span v-if="t.count > 0" class="text-xs opacity-60">({{ t.count }})</span>
            </button>
          </div>
        </header>

        <!-- 主体：根据 artifactTab 切换 -->
        <div class="flex-1 flex overflow-hidden">
          <!-- ════ 产物 tab ════ -->
          <div v-show="artifactTab === 'artifacts'" class="flex-1 flex overflow-hidden">
          <!-- 左侧产物列表（多产物时显示） -->
          <div
            v-if="artifacts.length > 1"
            class="w-44 shrink-0 border-r border-gray-200 dark:border-gray-700 overflow-y-auto py-2"
          >
            <button
              v-for="a in artifacts" :key="a.id"
              @click="activeId = a.id"
              :class="activeId === a.id
                ? 'bg-green-50 dark:bg-green-900/20 border-l-2 border-green-500'
                : 'hover:bg-gray-50 dark:hover:bg-gray-800 border-l-2 border-transparent'"
              class="group w-full text-left px-3 py-2 transition relative"
            >
              <div class="text-sm font-medium text-gray-700 dark:text-gray-200 truncate pr-12">{{ a.title || a.path?.split('/').pop() }}</div>
              <div class="text-[10px] text-gray-400 mt-0.5">{{ a.source }} · {{ new Date(a.ts).toLocaleTimeString() }}</div>
              <!-- hover 操作按钮（WorkBuddy 风格：打开文件夹 + 下载 + 删除） -->
              <div class="absolute right-2 top-1/2 -translate-y-1/2 flex gap-0.5 opacity-0 group-hover:opacity-100 transition" @click.stop>
                <button
                  v-if="a.path"
                  @click="openInFolder(a)"
                  class="p-1 rounded hover:bg-blue-100 dark:hover:bg-blue-900/30 text-gray-400 hover:text-blue-500"
                  title="在文件夹中显示"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
                </button>
                <button @click="downloadArtifact(a)" class="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200" title="下载">
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                </button>
                <button @click="removeArtifact(a.id)" class="p-1 rounded hover:bg-red-100 dark:hover:bg-red-900/30 text-gray-400 hover:text-red-500" title="删除">
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </div>
            </button>
          </div>

          <!-- 右侧预览区（产物 tab 内） -->
          <div class="flex-1 flex flex-col overflow-y-auto">
            <!-- 产物标题操作栏（WorkBuddy 风格） -->
            <div
              v-if="activeArtifact && !contentLoading && !contentError"
              class="shrink-0 px-4 py-2.5 border-b border-gray-100 dark:border-gray-700 flex items-center gap-2 bg-gray-50/50 dark:bg-gray-800/30"
            >
              <!-- 文件图标 -->
              <span class="text-base">📄</span>
              <!-- 文件名 -->
              <span class="flex-1 text-sm font-medium text-gray-700 dark:text-gray-200 truncate">
                {{ activeArtifact.title || activeArtifact.path?.split('/').pop() || '未知文件' }}
              </span>
              <!-- 操作按钮组 -->
              <div class="flex items-center gap-0.5">
                <!-- 在文件夹中显示（WorkBuddy 核心体验） -->
                <button
                  v-if="activeArtifact.path"
                  @click="openInFolder(activeArtifact)"
                  class="p-1.5 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition"
                  title="在文件夹中显示"
                >
                  <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
                </button>
                <!-- 复制路径 -->
                <button
                  v-if="activeArtifact.path"
                  @click="() => { navigator.clipboard.writeText(activeArtifact.path).catch(() => {}); }"
                  class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                  title="复制路径"
                >
                  <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                </button>
                <!-- 下载 -->
                <button
                  v-if="activeArtifact.path"
                  @click="downloadArtifact(activeArtifact)"
                  class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition"
                  title="下载"
                >
                  <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                </button>
              </div>
            </div>

            <!-- 空状态 -->
            <div v-if="!activeArtifact && artifacts.length === 0" class="flex-1 flex flex-col items-center justify-center text-gray-400">
              <div class="text-5xl mb-3">📄</div>
              <div class="text-sm">暂无产物</div>
              <div class="text-xs mt-1 text-gray-400 dark:text-gray-500">聊天中的文件链接或工具产物将显示在这里</div>
            </div>

            <!-- 加载中 -->
            <div v-else-if="contentLoading" class="flex-1 flex items-center justify-center text-gray-400">
              <div class="animate-pulse text-sm">加载中…</div>
            </div>

            <!-- 加载错误 -->
            <div v-else-if="contentError" class="flex-1 flex flex-col items-center justify-center text-red-400">
              <div class="text-3xl mb-2">⚠️</div>
              <div class="text-sm">加载失败</div>
              <div class="text-xs mt-1 text-gray-400">{{ contentError }}</div>
            </div>

            <!-- Markdown -->
            <div v-else-if="rendererFor(activeArtifact) === 'markdown'" class="artifact-markdown flex-1 p-5 prose prose-sm dark:prose-invert max-w-none overflow-y-auto" v-html="renderMarkdown(content)">
            </div>

            <!-- HTML（iframe sandbox srcdoc） -->
            <div v-else-if="rendererFor(activeArtifact) === 'html'" class="flex-1">
              <iframe
                class="w-full h-full border-0 bg-white"
                sandbox="allow-same-origin"
                :srcdoc="content"
              ></iframe>
            </div>

            <!-- JSON -->
            <div v-else-if="rendererFor(activeArtifact) === 'json'" class="flex-1 p-5 overflow-auto">
              <pre class="text-sm text-gray-700 dark:text-gray-200"><code>{{ formatJson(content) }}</code></pre>
            </div>

            <!-- CSV -->
            <div v-else-if="rendererFor(activeArtifact) === 'csv'" class="flex-1 p-5 overflow-auto">
              <table class="text-sm border-collapse w-full">
                <tbody>
                  <tr v-for="(row, i) in parseCsv(content)" :key="i" :class="i === 0 ? 'font-semibold bg-gray-50 dark:bg-gray-800' : ''">
                    <td v-for="(cell, j) in row" :key="j" class="border border-gray-200 dark:border-gray-700 px-3 py-1.5 text-gray-700 dark:text-gray-200">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Code -->
            <div v-else-if="rendererFor(activeArtifact) === 'code'" class="flex-1 p-5 overflow-auto">
              <pre class="text-sm text-gray-700 dark:text-gray-200 bg-gray-50 dark:bg-gray-800 rounded-lg p-4"><code>{{ content }}</code></pre>
            </div>

            <!-- Image -->
            <div v-else-if="rendererFor(activeArtifact) === 'image'" class="flex-1 flex items-center justify-center p-5">
              <img :src="content" :alt="activeArtifact?.title || 'image'" class="max-w-full max-h-full object-contain rounded-lg" />
            </div>

            <!-- Unsupported -->
            <div v-else class="flex-1 flex flex-col items-center justify-center text-gray-400">
              <div class="text-3xl mb-2">📦</div>
              <div class="text-sm">暂不支持此格式</div>
              <div class="text-xs mt-1 text-gray-400">{{ activeArtifact?.path }}</div>
            </div>
          </div>
        </div><!-- end 产物 tab -->

        <!-- ════ 文件 tab ════ -->
        <div v-show="artifactTab === 'files'" class="flex-1 flex flex-col overflow-hidden">
          <!-- 路径面包屑 -->
          <div class="flex items-center gap-1 px-3 py-2 border-b border-gray-200 dark:border-gray-700 shrink-0 text-xs">
            <button @click="goUp" class="p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400" title="上级">
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
            </button>
            <span class="text-gray-500 dark:text-gray-400 truncate">{{ fileCrumbs || '工作目录' }}</span>
          </div>
          <!-- 文件列表 -->
          <div class="flex-1 overflow-y-auto">
            <div v-if="fileLoading" class="flex items-center justify-center py-8 text-gray-400 text-sm">加载中…</div>
            <div v-else-if="fileItems.length === 0" class="flex items-center justify-center py-8 text-gray-400 text-sm">空目录</div>
            <div v-else>
              <button
                v-for="item in fileItems"
                :key="item.path"
                @click="onFileClick(item)"
                class="w-full flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800 transition text-left"
              >
                <span class="text-base shrink-0">{{ item.is_dir ? '📁' : fileIcon(item.ext) }}</span>
                <span class="flex-1 truncate text-sm text-gray-700 dark:text-gray-200">{{ item.name }}</span>
                <span v-if="!item.is_dir" class="text-[10px] text-gray-400 shrink-0">{{ formatSize(item.size) }}</span>
                <button
                  v-if="!item.is_dir"
                  @click.stop="previewFile(item)"
                  class="p-0.5 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 shrink-0"
                  title="预览"
                >
                  <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                </button>
              </button>
            </div>
          </div>
        </div>

        <!-- ════ 变更 tab ════ -->
        <div v-show="artifactTab === 'changes'" class="flex-1 flex overflow-hidden">
          <!-- 左侧变更列表 -->
          <div class="w-56 shrink-0 border-r border-gray-200 dark:border-gray-700 overflow-y-auto py-2">
            <div v-if="changes.length === 0" class="px-3 py-8 text-center text-gray-400 text-xs">
              暂无文件变更
            </div>
            <button
              v-for="c in changes" :key="c.id"
              @click="activeChangeId = c.id"
              :class="activeChangeId === c.id
                ? 'bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 border-l-2 border-blue-500'
                : 'text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 border-l-2 border-transparent'"
              class="w-full text-left px-3 py-2 text-sm transition flex items-center gap-2"
            >
              <span class="shrink-0">{{ c.action === 'write' ? '✍️' : c.action === 'patch' ? '🔧' : '📄' }}</span>
              <span class="truncate flex-1">{{ c.path.split('/').pop() }}</span>
            </button>
          </div>
          <!-- 右侧 diff 预览 -->
          <div class="flex-1 flex flex-col overflow-hidden">
            <template v-if="activeChange">
              <!-- 标题栏 -->
              <div class="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 shrink-0">
                <span class="text-sm font-medium text-gray-700 dark:text-gray-200 truncate flex-1">{{ activeChange.path }}</span>
                <span class="text-xs px-2 py-0.5 rounded-full" :class="activeChange.action === 'write' ? 'bg-orange-100 text-orange-600 dark:bg-orange-900/30 dark:text-orange-400' : 'bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400'">{{ activeChange.action === 'write' ? '新建/覆盖' : '修改' }}</span>
              </div>
              <!-- diff 内容 -->
              <div class="flex-1 overflow-auto p-3 text-sm">
                <pre v-if="activeChange.diff" class="font-mono text-xs leading-relaxed whitespace-pre-wrap"><template v-for="(line, i) in activeChange.diff.split('\n')" :key="i"><span :class="line.startsWith('+') ? 'text-green-600 dark:text-green-400' : line.startsWith('-') ? 'text-red-600 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'">{{ line }}
</span></template></pre>
                <div v-else class="text-gray-400 text-center py-8 text-xs">无 diff 内容</div>
              </div>
            </template>
            <div v-else class="flex-1 flex items-center justify-center text-gray-400">
              <div class="text-center">
                <div class="text-5xl mb-3">📝</div>
                <div class="text-sm">文件变更审计</div>
                <div class="text-xs mt-1 text-gray-400 dark:text-gray-500">Agent 修改文件后将在此显示 diff</div>
              </div>
            </div>
          </div>
        </div>

        <!-- ════ 预览 tab ════ -->
        <div v-show="artifactTab === 'preview'" class="flex-1 flex flex-col overflow-hidden">
          <!-- URL 工具栏 -->
          <div class="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700 shrink-0">
            <input
              v-model="previewUrl"
              type="text"
              placeholder="输入 URL 或选择 HTML 产物预览"
              class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500/30"
              @keydown.enter="loadPreview"
            />
            <button
              @click="loadPreview"
              class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              title="加载"
            >
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </button>
            <button
              @click="refreshPreview"
              class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              title="刷新"
            >
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
            </button>
            <button
              @click="openPreviewExternal"
              class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
              title="在外部浏览器打开"
            >
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><path d="M15 3h6v6"/><path d="M10 14 21 3"/></svg>
            </button>
          </div>
          <!-- 预览内容 -->
          <div class="flex-1 overflow-hidden relative">
            <iframe
              v-if="previewLoaded && previewSrc"
              :src="previewSrc"
              class="w-full h-full border-0"
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
              referrerpolicy="no-referrer"
            ></iframe>
            <div v-else class="flex-1 flex items-center justify-center text-gray-400 h-full">
              <div class="text-center">
                <div class="text-5xl mb-3">🌐</div>
                <div class="text-sm">网页预览</div>
                <div class="text-xs mt-1 text-gray-400 dark:text-gray-500">输入 URL 或点击 HTML 产物在此预览</div>
              </div>
            </div>
          </div>
        </div>
        </div>
      </aside>
    </transition>
  </Teleport>
</template>

<style scoped>
.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 0.25s ease;
}
.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}

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
</style>
