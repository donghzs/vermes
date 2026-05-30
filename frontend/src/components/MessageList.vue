<script setup>
import { ref, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useChatStore, QUICK_START_SUGGESTIONS, SESSION_TEMPLATES } from '../stores/chat'
import { toast } from '../utils/toast'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'

// P3-4: 按需导入highlight.js核心和常用语言
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import html from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import java from 'highlight.js/lib/languages/java'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import yaml from 'highlight.js/lib/languages/yaml'

// 工具结果展开状态
const expandedTools = ref(new Set())

function toggleToolExpand(toolId) {
  if (expandedTools.value.has(toolId)) {
    expandedTools.value.delete(toolId)
  } else {
    expandedTools.value.add(toolId)
  }
}

function isToolExpanded(toolId) {
  return expandedTools.value.has(toolId)
}

// 流式中：获取当前正在执行的工具/思考的中文描述
function currentRunningTool(tools) {
  const running = tools.filter(t => t.status === 'running')
  if (running.length === 0) return null
  const t = running[running.length - 1] // 取最后一个running
  if (t.name === 'thinking') return '思考中...'
  const map = {read_file:'读取文件...',write_file:'写入文件...',search_files:'搜索文件...',terminal:'执行命令...',web_search:'搜索网页...',vision_analyze:'分析图片...',list_directory:'列出目录...',edit_file:'编辑文件...',memory:'记忆操作...',execute_command:'执行命令...',google_search:'搜索...',browse_url:'浏览网页...'}
  return map[t.name] || t.name + '...'
}
import markdown from 'highlight.js/lib/languages/markdown'
import typescript from 'highlight.js/lib/languages/typescript'
import jsx from 'highlight.js/lib/languages/javascript'

// 注册常用语言
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('jsx', jsx)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('html', html)
hljs.registerLanguage('xml', html)
hljs.registerLanguage('css', css)
hljs.registerLanguage('java', java)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)

// P3-4: 配置markdown-it使用highlight.js
const md = new MarkdownIt({
  html: false,
  breaks: true,
  linkify: true,
  highlight: function (str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const highlighted = hljs.highlight(str, { language: lang }).value
        return `<pre class="hljs"><code>${highlighted}</code></pre>`
      } catch (__) {}
    }
    // 无语言标记时尝试自动检测
    try {
      const result = hljs.highlightAuto(str, ['javascript', 'python', 'bash', 'json', 'html', 'css'])
      return `<pre class="hljs"><code>${result.value}</code></pre>`
    } catch (__) {}
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  }
})

// 链接默认在新窗口打开，防止 WebView 导航走导致白屏
const defaultLinkRenderer = md.renderer.rules.link_open || function(tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  const token = tokens[idx]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return defaultLinkRenderer(tokens, idx, options, env, self)
}

const chat = useChatStore()

const props = defineProps({
  inputText: String,
})

const emit = defineEmits(['quickStart', 'editMessage'])

// ── P2-15: 普通消息列表（去掉虚拟滚动，避免高度不一导致重叠） ──
const chatContainer = ref(null)

// ── Markdown 渲染 ──
function renderMd(content) {
  if (!content) return ''
  try { return DOMPurify.sanitize(md.render(content), { ADD_ATTR: ['target', 'rel'] }) } catch(e) { return content }
}

// ── 链接点击拦截（pywebview/浏览器兼容） ──
function handleContentClick(e) {
  const a = e.target.closest('a[href]')
  if (a) {
    e.preventDefault()
    const url = a.href
    if (window.pywebview?.api?.open_external_browser) {
      window.pywebview.api.open_external_browser(url)
    } else {
      window.open(url, '_blank')
    }
  }
}

// ── 代码块复制按钮（DOM 操作方式，比正则替换更可靠） ──
function addCopyButtonsToPreElements(container) {
  if (!container) return
  const pres = container.querySelectorAll('pre:not([data-copy-btn-added])')
  pres.forEach(pre => {
    pre.setAttribute('data-copy-btn-added', 'true')
    pre.classList.add('relative', 'group')

    const btn = document.createElement('button')
    btn.className = 'absolute top-2 right-2 px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 rounded opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer'
    btn.textContent = '复制'
    btn.addEventListener('click', () => {
      const code = pre.querySelector('code')
      const text = code ? code.textContent : pre.textContent
      navigator.clipboard.writeText(text).then(() => {
        btn.textContent = '✅ 已复制'
        setTimeout(() => { btn.textContent = '复制' }, 2000)
      }).catch(() => {
        const ta = document.createElement('textarea')
        ta.value = text
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        btn.textContent = '✅ 已复制'
        setTimeout(() => { btn.textContent = '复制' }, 2000)
      })
    })
    pre.appendChild(btn)
  })
}

// 监听消息变化，为新渲染的代码块添加复制按钮
watch(() => chat.filteredMessages.length, async () => {
  await nextTick()
  if (chatContainer.value) {
    addCopyButtonsToPreElements(chatContainer.value)
  }
})

// 初始挂载时也添加 + 滚到底部
onMounted(() => {
  if (chatContainer.value) {
    addCopyButtonsToPreElements(chatContainer.value)
    // 打开会话时默认滚到最新消息（底部）
    nextTick(() => {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    })
  }
})

// 切换会话时滚到底部（多次尝试，确保长列表渲染完成）
watch(() => chat.currentSessionId, async () => {
  // 等待 messages 更新 + DOM 渲染 + 图片懒加载
  for (let i = 0; i < 5; i++) {
    await nextTick()
    if (chatContainer.value) {
      chatContainer.value.scrollTop = chatContainer.value.scrollHeight
    }
  }
})

// ── 消息复制 ──
function copyMessage(msg) {
  if (!msg || !msg.content) return
  let text = msg.content
  text = text.replace(/!\[.*?\]\(data:image[^)]*\)/g, '[图片]')
  navigator.clipboard.writeText(text).then(() => {
    toast.success('✅ 已复制到剪贴板')
  }).catch(() => {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    toast.success('✅ 已复制到剪贴板')
  })
}

// ── 重新生成 ──
async function regenerate(msg) {
  if (chat.loading) {
    toast.warning('正在生成中，请等待完成')
    return
  }
  const msgs = chat.filteredMessages
  const msgIndex = msgs.findIndex(m => m.id === msg.id)
  if (msgIndex <= 0) return
  let userMsg = null
  for (let i = msgIndex - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') { userMsg = msgs[i]; break }
  }
  if (!userMsg) { toast.warning('未找到对应的用户消息'); return }
  // 删除旧的 assistant 消息
  const globalIndex = chat.messages.findIndex(m => m.id === msg.id)
  if (globalIndex >= 0) chat.messages.splice(globalIndex, 1)
  // 重新发送，传 _isRegenerate=true 让 sendMessage 跳过添加 user 消息
  await chat.sendMessage(userMsg.content, userMsg.attachments || [], null, null, true)
}

function isLastAssistant(msg) {
  const msgs = chat.filteredMessages
  const lastAssistant = [...msgs].reverse().find(m => m.role === 'assistant')
  return lastAssistant && lastAssistant.id === msg.id
}

function quickStart(text) {
  emit('quickStart', text)
}

function startFromTemplate(tpl) {
  chat.createSession(tpl.name, tpl)
}

// ── P3-5: 消息时间显示 ──
function formatTime(timestamp) {
  if (!timestamp) return ''
  const now = Date.now()
  const diff = now - timestamp
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  
  if (diff < minute) return '刚刚'
  if (diff < hour) return Math.floor(diff / minute) + '分钟前'
  if (diff < day) return Math.floor(diff / hour) + '小时前'
  if (diff < 2 * day) return '昨天'
  if (diff < 7 * day) return Math.floor(diff / day) + '天前'
  
  const date = new Date(timestamp)
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

// 自动滚动到底部
watch(() => chat.filteredMessages.length, async () => {
  await nextTick()
  if (chatContainer.value) {
    // 只在用户已经在底部时自动滚动
    const container = chatContainer.value
    const isAtBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100
    if (isAtBottom || chat.loading) {
      container.scrollTop = container.scrollHeight
    }
  }
})
</script>

<template>
  <div ref="chatContainer" class="flex-1 overflow-y-auto px-4 py-6 bg-gray-50 dark:bg-gray-900 relative">
    <!-- 欢迎页 -->
    <div v-if="chat.filteredMessages.length === 0" class="flex-1 flex flex-col items-center justify-center px-8 py-16">
      <div class="text-center mb-8">
        <div class="w-16 h-16 bg-green-500 rounded-2xl flex items-center justify-center text-white text-3xl font-bold mx-auto mb-4 shadow-lg">V</div>
        <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100">欢迎使用 Vermes</h1>
        <p class="text-gray-400 text-sm mt-2">选择一个快速开始，或在下方输入你的需求</p>
      </div>
      <div class="flex flex-wrap justify-center gap-2 mb-8 max-w-lg">
        <button v-for="sug in QUICK_START_SUGGESTIONS" :key="sug.text"
          @click="quickStart(sug.text)"
          class="px-4 py-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-full text-sm text-gray-600 dark:text-gray-300 hover:border-green-400 hover:text-green-600 dark:hover:text-green-400 transition shadow-sm">
          {{ sug.icon }} {{ sug.text }}
        </button>
      </div>
      <div class="text-center">
        <p class="text-xs text-gray-400 mb-3">或选择一个模板开始</p>
        <div class="flex flex-wrap justify-center gap-3">
          <button v-for="tpl in SESSION_TEMPLATES.filter(t => t.id !== 'blank')" :key="tpl.id"
            @click="startFromTemplate(tpl)"
            class="px-5 py-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl text-sm text-gray-700 dark:text-gray-300 hover:border-green-400 hover:shadow-md transition flex items-center gap-2">
            <span class="text-lg">{{ tpl.icon }}</span>
            <span>{{ tpl.name }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- 消息列表 -->
    <div v-else>
      <div v-for="msg in chat.filteredMessages" :key="msg.id"
           class="flex gap-3 group px-4 py-2"
           :class="msg.role === 'user' ? 'flex-row-reverse' : ''">
        <div class="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0" :class="msg.role === 'user' ? 'bg-indigo-500' : 'bg-green-500'">
          {{ msg.role === 'user' ? '我' : 'V' }}
        </div>
        <div class="max-w-[75%] min-w-0">
          <!-- P3-8: 对比模式标签 -->
          <div v-if="msg._compareModel" class="text-[10px] text-purple-500 dark:text-purple-400 mb-1 font-medium px-1">
            🔬 {{ msg._compareModel }}
          </div>
          <div class="px-4 py-3 rounded-2xl text-sm leading-relaxed" :class="msg.role === 'user' ? 'bg-indigo-500 text-white rounded-br-md' : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-bl-md shadow-sm'">
            <template v-if="msg.role === 'user'">
              <img v-if="msg.content && msg.content.includes('data:image')" :src="msg.content.match(/data:image[^)]+/)?.[0]" class="max-w-full rounded-lg mb-2" />
              <template v-if="!msg.content?.match(/^!\[.*\]\(data:image/)">
                <div style="white-space:pre-wrap;word-break:break-word;">{{ msg.content }}</div>
              </template>
            </template>
            <template v-else>
              <div v-if="msg.content" class="vermes-md" v-html="renderMd(msg.content)" @click="handleContentClick($event)"></div>
              <span v-else class="text-gray-400 text-xs">等待中...</span>
              <span v-if="msg.streaming" class="typing-cursor"></span>
            </template>
          </div>
          <!-- 工具调用展示：流式中=单条状态，完成后=紧凑时间线 -->
          <div v-if="msg.toolInvocations && msg.toolInvocations.length > 0" class="mt-2">
            <!-- 流式中：只显示当前正在执行的（thinking或tool） -->
            <template v-if="msg.streaming">
              <div v-if="currentRunningTool(msg.toolInvocations)"
                   class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400">
                <span class="animate-spin text-xs">⏳</span>
                <span>{{ currentRunningTool(msg.toolInvocations) }}</span>
              </div>
            </template>
            <!-- 完成后：紧凑时间线（只显示真实工具，不含thinking） -->
            <template v-else>
              <div v-if="msg.toolInvocations.filter(t => t.name !== 'thinking').length > 0"
                   class="flex flex-wrap gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
                <span v-for="tool in msg.toolInvocations.filter(t => t.name !== 'thinking')"
                      :key="'done-' + (tool.id || tool.name)"
                      class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition"
                      :class="tool.status === 'error' ? 'text-red-500' : ''"
                      @click="tool.result_preview && toggleToolExpand(tool.id || tool.name)">
                  <span>{{ tool.status === 'error' ? '❌' : '✅' }}</span>
                  <span>{{ ({read_file:'读取文件',write_file:'写入文件',search_files:'搜索文件',terminal:'终端',web_search:'网页搜索',vision_analyze:'图片分析',list_directory:'列出目录',edit_file:'编辑文件',memory:'记忆',execute_command:'执行命令',google_search:'搜索',browse_url:'浏览网页'})[tool.name] || tool.name }}</span>
                  <span v-if="tool.duration" class="opacity-60">{{ tool.duration }}s</span>
                  <span v-if="tool.result_preview">{{ isToolExpanded(tool.id || tool.name) ? '▼' : '▶' }}</span>
                </span>
              </div>
              <!-- 可折叠的结果摘要 -->
              <template v-for="tool in msg.toolInvocations.filter(t => t.name !== 'thinking')" :key="'preview-' + (tool.id || tool.name)">
                <div v-if="tool.result_preview && isToolExpanded(tool.id || tool.name)"
                     class="mt-1 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                  <pre class="text-[11px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ tool.result_preview }}</pre>
                </div>
              </template>
            </template>
          </div>
          <div class="flex items-center gap-2 mt-1"
               :class="msg.role === 'user' ? 'justify-end' : ''">
            <span class="text-[10px] text-gray-400">{{ formatTime(msg.timestamp) }}</span>
            <div v-if="msg.content && !msg.streaming"
                 class="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
              <button @click="copyMessage(msg)"
                      class="text-xs text-gray-400 hover:text-green-500 transition flex items-center gap-1 px-1 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                📋 复制
              </button>
              <button v-if="msg.role === 'user'" @click="emit('editMessage', msg)"
                      class="text-xs text-gray-400 hover:text-green-500 transition flex items-center gap-1 px-1 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                ✏️ 编辑
              </button>
              <button v-if="msg.role === 'assistant' && isLastAssistant(msg)" @click="regenerate(msg)"
                      class="text-xs text-gray-400 hover:text-green-500 transition flex items-center gap-1 px-1 py-0.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                🔄 重新生成
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.vermes-md :deep(a) { color: #61afef; text-decoration: underline; cursor: pointer; }
.dark .vermes-md :deep(a) { color: #82b1ff; }
.vermes-md :deep(a:hover) { color: #82b1ff; opacity: 0.85; }
.vermes-md :deep(p) { margin: 0.4em 0; line-height: 1.7; }
.vermes-md :deep(h1), .vermes-md :deep(h2), .vermes-md :deep(h3) { font-weight: 600; margin: 0.6em 0 0.3em; }
.vermes-md :deep(h1) { font-size: 1.2em; }
.vermes-md :deep(h2) { font-size: 1.1em; }
.vermes-md :deep(h3) { font-size: 1.05em; }
.vermes-md :deep(ul), .vermes-md :deep(ol) { padding-left: 1.5em; margin: 0.3em 0; }
.vermes-md :deep(li) { margin: 0.15em 0; line-height: 1.6; }
.vermes-md :deep(code) { background: rgba(0,0,0,0.06); padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.85em; font-family: 'SF Mono', Monaco, Consolas, monospace; }
.dark .vermes-md :deep(code) { background: rgba(255,255,255,0.1); }
.vermes-md :deep(pre) { background: #1e1e2e; color: #cdd6f4; border-radius: 8px; padding: 12px 16px; overflow-x: auto; margin: 0.6em 0; font-size: 0.85em; }
.vermes-md :deep(pre code) { background: none; padding: 0; color: inherit; font-size: 1em; }

/* P3-4: highlight.js 完整 token 样式（Catppuccin Mocha 配色） */
.vermes-md :deep(pre.hljs) { background: #1e1e2e; color: #cdd6f4; border-radius: 8px; padding: 12px 16px; overflow-x: auto; margin: 0.6em 0; font-size: 0.85em; }
.vermes-md :deep(pre.hljs code) { background: none; padding: 0; color: inherit; font-size: 1em; }
.dark .vermes-md :deep(pre.hljs) { background: #1e1e2e; }
/* 关键字/控制流 */
.vermes-md :deep(.hljs-keyword) { color: #c678dd; }
.vermes-md :deep(.hljs-selector-tag) { color: #c678dd; }
.vermes-md :deep(.hljs-type) { color: #e6c07b; }
.vermes-md :deep(.hljs-section) { color: #c678dd; }
.vermes-md :deep(.hljs-name) { color: #c678dd; }
.vermes-md :deep(.hljs-tag) { color: #c678dd; }
/* 字符串/模板 */
.vermes-md :deep(.hljs-string) { color: #98c379; }
.vermes-md :deep(.hljs-template-variable) { color: #98c379; }
.vermes-md :deep(.hljs-template-tag) { color: #98c379; }
.vermes-md :deep(.hljs-regexp) { color: #98c379; }
.vermes-md :deep(.hljs-addition) { color: #98c379; }
.vermes-md :deep(.hljs-attribute) { color: #98c379; }
/* 注释 */
.vermes-md :deep(.hljs-comment) { color: #5c6370; font-style: italic; }
.vermes-md :deep(.hljs-quote) { color: #5c6370; font-style: italic; }
.vermes-md :deep(.hljs-doctag) { color: #98c379; }
/* 函数/方法 */
.vermes-md :deep(.hljs-function) { color: #61afef; }
.vermes-md :deep(.hljs-title) { color: #61afef; }
.vermes-md :deep(.hljs-title.function_) { color: #61afef; }
.vermes-md :deep(.hljs-title.class_) { color: #e6c07b; }
.vermes-md :deep(.hljs-title.class_.inherited__) { color: #e6c07b; }
.vermes-md :deep(.hljs-built_in) { color: #e6c07b; }
/* 字面量 */
.vermes-md :deep(.hljs-number) { color: #d19a66; }
.vermes-md :deep(.hljs-literal) { color: #56b6c2; }
.vermes-md :deep(.hljs-boolean) { color: #d19a66; }
.vermes-md :deep(.hljs-variable) { color: #e06c75; }
.vermes-md :deep(.hljs-variable.constant_) { color: #d19a66; }
/* 参数/属性 */
.vermes-md :deep(.hljs-params) { color: #abb2bf; }
.vermes-md :deep(.hljs-attr) { color: #d19a66; }
.vermes-md :deep(.hljs-property) { color: #abb2bf; }
.vermes-md :deep(.hljs-symbol) { color: #98c379; }
/* 元信息 */
.vermes-md :deep(.hljs-meta) { color: #56b6c2; }
.vermes-md :deep(.hljs-meta .hljs-keyword) { color: #c678dd; }
.vermes-md :deep(.hljs-meta .hljs-string) { color: #98c379; }
/* 操作符/标点 */
.vermes-md :deep(.hljs-operator) { color: #abb2bf; }
.vermes-md :deep(.hljs-punctuation) { color: #abb2bf; }
.vermes-md :deep(.hljs-subst) { color: #abb2bf; }
.vermes-md :deep(.hljs-link) { color: #61afef; text-decoration: underline; }
.vermes-md :deep(.hljs-emphasis) { font-style: italic; }
.vermes-md :deep(.hljs-strong) { font-weight: 700; }
/* 删除/差异 */
.vermes-md :deep(.hljs-deletion) { color: #e06c75; }
/* 选择器 */
.vermes-md :deep(.hljs-selector-class) { color: #e6c07b; }
.vermes-md :deep(.hljs-selector-id) { color: #e6c07b; }
.vermes-md :deep(.hljs-selector-attr) { color: #d19a66; }
.vermes-md :deep(.hljs-selector-pseudo) { color: #c678dd; }
.vermes-md :deep(blockquote) { border-left: 3px solid #22c55e; padding-left: 12px; margin: 0.5em 0; color: #666; }
.dark .vermes-md :deep(blockquote) { color: #aaa; }
.vermes-md :deep(table) { width: 100%; border-collapse: collapse; margin: 0.6em 0; font-size: 0.9em; }
.vermes-md :deep(th), .vermes-md :deep(td) { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
.dark .vermes-md :deep(th), .dark .vermes-md :deep(td) { border-color: #374151; }
.vermes-md :deep(th) { background: rgba(0,0,0,0.04); font-weight: 600; }
.dark .vermes-md :deep(th) { background: rgba(255,255,255,0.06); }
.vermes-md :deep(strong) { font-weight: 700; }
.vermes-md :deep(hr) { border: none; border-top: 1px solid #e5e7eb; margin: 1em 0; }
.dark .vermes-md :deep(hr) { border-top-color: #374151; }
.vermes-md :deep(a) { color: #16a34a; text-decoration: none; }
.vermes-md :deep(a:hover) { text-decoration: underline; }

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}
.typing-cursor {
  display: inline-block;
  width: 2px;
  height: 1.1em;
  background: #22c55e;
  animation: blink 1s infinite;
  margin-left: 2px;
  vertical-align: text-bottom;
}
.dark .typing-cursor {
  background: #4ade80;
}
</style>
