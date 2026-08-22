<script setup>
/**
 * ArtifactPanel — 聊天右侧产物面板
 * 范式参照 ToolSkillDrawer（Teleport / 600px / drawer-slide）
 * 阶段 1：骨架 + Markdown/HTML/Code/Image/JSON/CSV 渲染器 + 全屏 toggle
 */
import { ref, computed, watch } from 'vue'
import { useRightPanel } from '../composables/useRightPanel'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

const { open, tab, closePanel } = useRightPanel()

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
  // 测试产物（无真实路径）直接用内嵌内容
  if (artifact.source === 'test') {
    content.value = artifact._content || ''
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
        :class="isFullscreen ? 'w-full' : 'w-[600px] max-w-[94vw]'"
      >
        <!-- 头部 -->
        <header class="shrink-0 px-5 py-3.5 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div class="flex items-center gap-2">
            <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100">📄 产物</h2>
            <span v-if="artifacts.length" class="text-xs text-gray-400">({{ artifacts.length }})</span>
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
            <button
              v-if="artifacts.length"
              @click="clearArtifacts"
              class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-red-500 transition"
              title="清空产物列表"
            >
              <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
            </button>
            <button @click="closePanel" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" title="关闭 (Esc)">
              <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
        </header>

        <!-- 主体：左侧列表 + 右侧预览 -->
        <div class="flex-1 flex overflow-hidden">
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
                  v-if="a.source !== 'test' && a.path"
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

          <!-- 右侧预览区 -->
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
                  v-if="activeArtifact.source !== 'test' && activeArtifact.path"
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
                  v-if="activeArtifact.source !== 'test'"
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
