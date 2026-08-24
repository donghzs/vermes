<script setup>
/**
 * ArtifactPanel — 聊天右侧产物面板（WorkBuddy 风格）
 * 核心体验：有最终交付物时右侧直接渲染内容，不弹复杂文件列表。
 */
import { ref, computed, watch } from 'vue'
import { useArtifactPanel } from '../composables/useArtifactPanel'
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

// Office 文档预览库（动态加载，不阻塞首屏）
let _xlsx = null
let _mammoth = null
async function loadXLSX() {
  if (!_xlsx) _xlsx = await import('xlsx')
  return _xlsx
}
async function loadMammoth() {
  if (!_mammoth) _mammoth = await import('mammoth/mammoth.browser.js')
  return _mammoth
}

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

const { open, tab: artifactTab, width: panelWidth, autoOpen, openPanel, closePanel, setTab: setArtifactTab } = useArtifactPanel()

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
  // 文件变更不再自动弹右侧面板，避免 write_file/patch 这类高频操作打扰用户。
  // 用户可手动通过顶部「详情面板」→「变更」查看 diff。
}
function clearChanges() {
  changes.value = []
  activeChangeId.value = null
}
window.__vermesChanges = { addChange, clearChanges, changes }

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
  // PDF：iframe 内嵌预览
  if (mime === 'application/pdf' || ext === 'pdf') return 'pdf'
  // Excel：SheetJS 前端解析渲染表格
  if (['xlsx', 'xls'].includes(ext)) return 'excel'
  // Word：mammoth.js 前端渲染为 HTML
  if (ext === 'docx') return 'docx'
  // PPT 等：无法前端渲染，提供下载入口
  if (['pptx', 'ppt', 'doc'].includes(ext)) return 'office'
  return 'unsupported'
}

// ── 文件内容加载 ──
const content = ref('')
const contentLoading = ref(false)
const contentError = ref('')

async function loadContent(artifact) {
  if (!artifact) return
  // PDF / PPT：不需要预加载 content，模板直接用 iframe src 或下载按钮
  const type = rendererFor(artifact)
  if (type === 'pdf' || type === 'office') {
    content.value = ''
    contentLoading.value = false
    return
  }
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
    if (type === 'image') {
      const blob = await resp.blob()
      content.value = URL.createObjectURL(blob)
    } else if (type === 'excel') {
      // SheetJS 解析 Excel → HTML 表格
      const XLSX = await loadXLSX()
      const blob = await resp.blob()
      const arrayBuffer = await blob.arrayBuffer()
      const wb = XLSX.read(arrayBuffer, { type: 'array' })
      const sheetsHtml = wb.SheetNames.map(name => {
        const ws = wb.Sheets[name]
        const html = XLSX.utils.sheet_to_html(ws, { editable: false })
        return `<div class="excel-sheet"><div class="excel-sheet-name">📄 ${name}</div>${html}</div>`
      }).join('')
      content.value = sheetsHtml
    } else if (type === 'docx') {
      // mammoth.js 解析 docx → HTML
      const mammoth = await loadMammoth()
      const blob = await resp.blob()
      const arrayBuffer = await blob.arrayBuffer()
      const result = await mammoth.convertToHtml({ arrayBuffer })
      content.value = result.value || '<p style="color:#999">文档内容为空</p>'
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

watch([open, artifactTab], ([o, t]) => {
  if (o && t === 'artifacts' && !activeId.value && artifacts.value.length > 0) {
    activeId.value = artifacts.value[0].id
  }
})


// ── 复制路径（安全兼容 Electron 无 navigator.clipboard 环境）──
function copyPath(path) {
  if (!path) return
  // 优先 navigator.clipboard（HTTPS / Electron 安全上下文）
  if (navigator?.clipboard?.writeText) {
    navigator.clipboard.writeText(path).catch(() => fallbackCopy(path))
  } else {
    fallbackCopy(path)
  }
}
function fallbackCopy(text) {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  } catch (e) {
    console.warn('[ArtifactPanel] copy failed:', e)
  }
}

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
  <!-- 不再用 Teleport + fixed + 黑色遮罩：避免遮挡主聊天区。
       改为内联在 App.vue 的 router-view 兄弟节点，与聊天流并排显示，
       类似 WorkBuddy：左侧聊天，右侧产物实时渲染。 -->
  <transition name="drawer-slide">
    <aside
      v-show="open"
      class="h-full bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 flex flex-col transition-all duration-200"
      :class="isFullscreen ? 'w-full' : ''"
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

        <!-- 头部工具栏：WorkBuddy 风格，简洁直接 -->
        <header class="shrink-0 border-b border-gray-200 dark:border-gray-700">
          <div class="px-3 py-2 flex items-center gap-2">
            <!-- 标题 -->
            <div class="flex items-center gap-1.5 shrink-0">
              <span class="text-sm">📄</span>
              <span class="text-sm font-semibold text-gray-700 dark:text-gray-200">详情面板</span>
            </div>

            <!-- 产物切换条：仅在多产物时显示，单个产物时完全隐藏保持简洁 -->
            <div
              v-if="artifactTab === 'artifacts' && artifacts.length > 1"
              class="flex-1 min-w-0 flex items-center gap-1 overflow-x-auto"
            >
              <button
                v-for="a in artifacts"
                :key="a.id"
                @click="activeId = a.id"
                :class="activeId === a.id
                  ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400 border border-green-200 dark:border-green-800'
                  : 'text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800 border border-transparent'"
                class="shrink-0 px-2 py-1 rounded text-xs transition truncate max-w-[120px]"
                :title="a.title || a.path"
              >
                {{ a.title || a.path?.split('/').pop() }}
              </button>
            </div>
            <div v-else class="flex-1"></div>

            <!-- 右侧：产物/变更切换 + 全屏 + 关闭 -->
            <div class="flex items-center gap-0.5 shrink-0">
              <button
                @click="setArtifactTab('artifacts')"
                :class="artifactTab === 'artifacts'
                  ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800'"
                class="p-1.5 rounded-lg text-xs transition"
                title="产物"
              >
                <span>📄</span>
              </button>
              <button
                @click="setArtifactTab('changes')"
                :class="artifactTab === 'changes'
                  ? 'bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400'
                  : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800'"
                class="p-1.5 rounded-lg text-xs transition flex items-center gap-1"
                title="文件变更"
              >
                <span>📝</span>
                <span v-if="changes.length > 0" class="text-[10px]">({{ changes.length }})</span>
              </button>
              <button
                @click="isFullscreen = !isFullscreen"
                class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition"
                :title="isFullscreen ? '退出全屏' : '全屏'"
              >
                <svg v-if="!isFullscreen" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
                <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 8V5a2 2 0 0 1 2-2h3m0 18H5a2 2 0 0 1-2-2v-3m18 0v3a2 2 0 0 1-2 2h-3M21 8V5a2 2 0 0 0-2-2h-3"/></svg>
              </button>
              <button @click="closePanel" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" title="关闭">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
              </button>
            </div>
          </div>
        </header>

        <!-- 主体：根据 artifactTab 切换 -->
        <div class="flex-1 flex overflow-hidden">
        <!-- ════ 产物 tab ════ -->
        <div v-show="artifactTab === 'artifacts'" class="flex-1 flex flex-col overflow-hidden">
          <!-- 当前产物标题栏（WorkBuddy 风格：只保留文件名+核心操作） -->
          <div
            v-if="activeArtifact"
            class="shrink-0 px-3 py-2 border-b border-gray-100 dark:border-gray-700 flex items-center gap-2 bg-gray-50/50 dark:bg-gray-800/30"
          >
            <span class="text-sm">📄</span>
            <span class="flex-1 text-sm font-medium text-gray-700 dark:text-gray-200 truncate">
              {{ activeArtifact.title || activeArtifact.path?.split('/').pop() || '未知文件' }}
            </span>
            <div class="flex items-center gap-0.5">
              <button
                v-if="activeArtifact.path"
                @click="openInFolder(activeArtifact)"
                class="p-1.5 rounded-lg hover:bg-blue-100 dark:hover:bg-blue-900/30 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition"
                title="在文件夹中显示"
              >
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-6l-2-2H5a2 2 0 0 0-2 2z"/></svg>
              </button>
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

          <!-- 主体内容区：直接渲染交付物 -->
          <div class="flex-1 flex flex-col overflow-y-auto relative">
            <!-- 空状态 -->
            <div v-if="!activeArtifact && artifacts.length === 0" class="flex-1 flex flex-col items-center justify-center text-gray-400">
              <div class="text-4xl mb-2">📄</div>
              <div class="text-sm">暂无产物</div>
              <div class="text-xs mt-1 text-gray-400 dark:text-gray-500">最终交付物将自动显示在这里</div>
            </div>

            <!-- 加载中 -->
            <div v-else-if="contentLoading" class="flex-1 flex items-center justify-center text-gray-400">
              <div class="animate-pulse text-sm">加载中…</div>
            </div>

            <!-- 加载错误：轻提示，不抢占主区域 -->
            <div v-else-if="contentError" class="absolute inset-0 flex flex-col items-center justify-center text-gray-400 bg-white/80 dark:bg-gray-900/80 z-10">
              <div class="text-2xl mb-1">⚠️</div>
              <div class="text-sm text-red-400">加载失败</div>
              <div class="text-xs mt-0.5 text-gray-400">{{ contentError }}</div>
            </div>

            <!-- Markdown -->
            <div v-else-if="rendererFor(activeArtifact) === 'markdown'" class="artifact-markdown flex-1 p-5 prose prose-sm dark:prose-invert max-w-none overflow-y-auto" v-html="renderMarkdown(content)">
            </div>

            <!-- HTML（iframe sandbox srcdoc，禁脚本防 XSS） -->
            <div v-else-if="rendererFor(activeArtifact) === 'html'" class="flex-1">
              <iframe
                class="w-full h-full border-0 bg-white"
                sandbox=""
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

            <!-- PDF -->
            <div v-else-if="rendererFor(activeArtifact) === 'pdf'" class="flex-1 flex flex-col">
              <iframe
                :src="`/api/v1/artifacts/${encodeURIComponent(activeArtifact.path)}`"
                class="w-full flex-1 border-0 bg-white"
                referrerpolicy="no-referrer"
              ></iframe>
            </div>

            <!-- Excel (SheetJS 渲染) -->
            <div v-else-if="rendererFor(activeArtifact) === 'excel'" class="flex-1 overflow-auto p-3 bg-gray-50 dark:bg-gray-800/30">
              <div v-if="content" class="excel-render" v-html="content"></div>
            </div>

            <!-- Word (mammoth.js 渲染) -->
            <div v-else-if="rendererFor(activeArtifact) === 'docx'" class="flex-1 overflow-y-auto p-6 bg-white dark:bg-gray-900">
              <div v-if="content" class="docx-render prose prose-sm max-w-none dark:prose-invert" v-html="content"></div>
            </div>

            <!-- Office 文档（无法前端渲染，提供下载） -->
            <div v-else-if="rendererFor(activeArtifact) === 'office'" class="flex-1 flex flex-col items-center justify-center text-gray-400">
              <div class="text-5xl mb-3">📘</div>
              <div class="text-sm font-medium text-gray-600 dark:text-gray-300">{{ activeArtifact.title || activeArtifact.path?.split('/').pop() }}</div>
              <div class="text-xs mt-1 text-gray-400">Office 文档无法在浏览器中直接预览</div>
              <button @click="downloadArtifact(activeArtifact)" class="mt-4 px-4 py-2 rounded-lg bg-green-500 text-white text-sm font-medium hover:bg-green-600 transition flex items-center gap-2">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                下载文件
              </button>
            </div>

            <!-- Unsupported -->
            <div v-else class="flex-1 flex flex-col items-center justify-center text-gray-400">
              <div class="text-3xl mb-2">📦</div>
              <div class="text-sm">暂不支持此格式</div>
              <div class="text-xs mt-1 text-gray-400">{{ activeArtifact?.path }}</div>
            </div>
          </div>
        </div><!-- end 产物 tab -->

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

        </div>
      </aside>
    </transition>
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

/* Excel 渲染样式 */
.excel-render .excel-sheet { margin-bottom: 1rem; }
.excel-render .excel-sheet-name {
  font-size: 0.875rem; font-weight: 600; color: #374151;
  margin-bottom: 0.5rem; padding: 0.25rem 0.5rem;
  background: #f3f4f6; border-radius: 4px; display: inline-block;
}
.dark .excel-render .excel-sheet-name { color: #d1d5db; background: #374151; }
.excel-render table {
  border-collapse: collapse; width: 100%; font-size: 0.8rem;
}
.excel-render td, .excel-render th {
  border: 1px solid #e5e7eb; padding: 0.25rem 0.5rem;
  color: #374151; white-space: nowrap;
}
.dark .excel-render td, .dark .excel-render th {
  border-color: #4b5563; color: #d1d5db;
}
.excel-render tr:first-child td { font-weight: 600; background: #f9fafb; }
.dark .excel-render tr:first-child td { background: #1f2937; }

/* Word/docx 渲染样式 */
.docx-render h1 { font-size: 1.5em; font-weight: 700; margin: 0.8em 0 0.4em; }
.docx-render h2 { font-size: 1.25em; font-weight: 600; margin: 0.6em 0 0.3em; }
.docx-render h3 { font-size: 1.1em; font-weight: 600; margin: 0.5em 0 0.3em; }
.docx-render p { margin: 0.5em 0; line-height: 1.7; }
.docx-render ul, .docx-render ol { margin: 0.5em 0; padding-left: 1.5em; }
.docx-render table { width: 100%; border-collapse: collapse; margin: 0.5em 0; }
.docx-render td, .docx-render th { border: 1px solid #e5e7eb; padding: 0.4em 0.6em; }
.docx-render img { max-width: 100%; height: auto; border-radius: 4px; }
</style>
