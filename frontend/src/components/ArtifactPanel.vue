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

// ── 产物列表（内存级持久，会话生命周期内不丢）──
const artifacts = ref([])  // [{ id, path, title, mime, source, ts }]
const activeId = ref(null)
const isFullscreen = ref(false)

function addArtifact(item) {
  const existing = artifacts.value.findIndex(a => a.path === item.path)
  if (existing >= 0) {
    artifacts.value[existing] = { ...artifacts.value[existing], ...item }
    activeId.value = artifacts.value[existing].id
    return
  }
  const id = 'art-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6)
  artifacts.value.unshift({ id, ts: Date.now(), ...item })
  activeId.value = id
}

function removeArtifact(id) {
  const idx = artifacts.value.findIndex(a => a.id === id)
  if (idx >= 0) artifacts.value.splice(idx, 1)
  if (activeId.value === id) activeId.value = artifacts.value[0]?.id || null
}

function clearArtifacts() {
  artifacts.value = []
  activeId.value = null
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

// ── 测试入口（阶段 1 验收用，阶段 2 后移除）──
function addTestArtifact() {
  const testContent = `# 欢迎使用产物面板 📄

这是 Vermes **产物面板**（Artifact Panel）的测试产物。

## 功能
- ✅ 右侧滑入面板，600px 默认宽度
- ✅ 全屏切换（撑满右侧）
- ✅ Markdown 渲染 + 代码高亮
- ✅ 多产物列表切换

## 代码高亮测试
\`\`\`python
def hello():
    print("Hello from Vermes!")
\`\`\`

## 表格测试
| 类型 | 格式 | 状态 |
|------|------|------|
| Markdown | .md | ✅ |
| HTML | .html | ✅ |
| Code | .py/.js | ✅ |
| Image | .png | ✅ |

> 提示：点击右上角「⤢」可切换全屏模式
`
  addArtifact({
    path: 'test-welcome.md',
    title: '欢迎使用产物面板',
    mime: 'text/markdown',
    source: 'test',
    _content: testContent,
  })
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
              class="w-full text-left px-3 py-2 transition"
            >
              <div class="text-sm font-medium text-gray-700 dark:text-gray-200 truncate">{{ a.title || a.path?.split('/').pop() }}</div>
              <div class="text-[10px] text-gray-400 mt-0.5">{{ a.source }} · {{ new Date(a.ts).toLocaleTimeString() }}</div>
            </button>
          </div>

          <!-- 右侧预览区 -->
          <div class="flex-1 overflow-y-auto">
            <!-- 空状态 -->
            <div v-if="!activeArtifact && artifacts.length === 0" class="h-full flex flex-col items-center justify-center text-gray-400">
              <div class="text-5xl mb-3">📄</div>
              <div class="text-sm">暂无产物</div>
              <div class="text-xs mt-1 text-gray-400 dark:text-gray-500">聊天中的文件链接或工具产物将显示在这里</div>
              <button @click="addTestArtifact" class="mt-4 px-3 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 transition">
                🧪 添加测试产物
              </button>
            </div>

            <!-- 加载中 -->
            <div v-else-if="contentLoading" class="h-full flex items-center justify-center text-gray-400">
              <div class="animate-pulse text-sm">加载中…</div>
            </div>

            <!-- 加载错误 -->
            <div v-else-if="contentError" class="h-full flex flex-col items-center justify-center text-red-400">
              <div class="text-3xl mb-2">⚠️</div>
              <div class="text-sm">加载失败</div>
              <div class="text-xs mt-1 text-gray-400">{{ contentError }}</div>
            </div>

            <!-- Markdown -->
            <div v-else-if="rendererFor(activeArtifact) === 'markdown'" class="artifact-markdown p-5 prose prose-sm dark:prose-invert max-w-none" v-html="renderMarkdown(content)">
            </div>

            <!-- HTML（iframe sandbox srcdoc） -->
            <div v-else-if="rendererFor(activeArtifact) === 'html'" class="h-full">
              <iframe
                class="w-full h-full border-0 bg-white"
                sandbox="allow-same-origin"
                :srcdoc="content"
              ></iframe>
            </div>

            <!-- JSON -->
            <div v-else-if="rendererFor(activeArtifact) === 'json'" class="p-5">
              <pre class="text-sm text-gray-700 dark:text-gray-200 overflow-auto"><code>{{ formatJson(content) }}</code></pre>
            </div>

            <!-- CSV -->
            <div v-else-if="rendererFor(activeArtifact) === 'csv'" class="p-5 overflow-auto">
              <table class="text-sm border-collapse">
                <tbody>
                  <tr v-for="(row, i) in parseCsv(content)" :key="i" :class="i === 0 ? 'font-semibold bg-gray-50 dark:bg-gray-800' : ''">
                    <td v-for="(cell, j) in row" :key="j" class="border border-gray-200 dark:border-gray-700 px-3 py-1.5 text-gray-700 dark:text-gray-200">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- Code -->
            <div v-else-if="rendererFor(activeArtifact) === 'code'" class="p-5">
              <pre class="text-sm text-gray-700 dark:text-gray-200 overflow-auto bg-gray-50 dark:bg-gray-800 rounded-lg p-4"><code>{{ content }}</code></pre>
            </div>

            <!-- Image -->
            <div v-else-if="rendererFor(activeArtifact) === 'image'" class="h-full flex items-center justify-center p-5">
              <img :src="content" :alt="activeArtifact?.title || 'image'" class="max-w-full max-h-full object-contain rounded-lg" />
            </div>

            <!-- Unsupported -->
            <div v-else class="h-full flex flex-col items-center justify-center text-gray-400">
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
