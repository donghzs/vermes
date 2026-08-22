<script setup>
import { ref, nextTick, watch, onMounted, onUnmounted, reactive, computed } from 'vue'
import { useChatStore, QUICK_START_SUGGESTIONS, SESSION_TEMPLATES, setScrollTarget } from '../stores/chat'
import { useRightPanel } from '../composables/useRightPanel'
import { toast } from '../utils/toast'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import { DOMPURIFY_BASE_CONFIG, enforceLinkSecurity } from '../utils/security'

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

const { openPanel: openRightPanel } = useRightPanel()

// 工具结果展开状态
const expandedTools = ref(new Set())

// 默认展开的重要工具（用户通常需要查看结果的工具）
const DEFAULT_EXPANDED_TOOLS = new Set(['read_file', 'terminal', 'search_files', 'execute_code', 'get_diagnostics', 'lsp_diagnostics', 'browser_screenshot', 'browser_snapshot', 'browser_navigate'])

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

// 自动展开重要工具的结果
function autoExpandImportantTools(tools) {
  if (!tools) return
  tools.forEach(tool => {
    if (tool.name && DEFAULT_EXPANDED_TOOLS.has(tool.name) && tool.result_preview) {
      expandedTools.value.add(tool.id || tool.name)
    }
  })
}

// 流式中：获取当前正在执行的工具的中文描述
function currentRunningTool(tools) {
  const running = tools.filter(t => t.status === 'running')
  if (running.length === 0) return null
  const t = running[running.length - 1] // 取最后一个running
  const map = {read_file:'读取文件...',write_file:'写入文件...',search_files:'搜索文件...',terminal:'执行命令...',web_search:'搜索网页...',vision_analyze:'分析图片...',list_directory:'列出目录...',edit_file:'编辑文件...',memory:'记忆操作...',execute_command:'执行命令...',google_search:'搜索...',browse_url:'浏览网页...',browser_navigate:'浏览网页...',browser_click:'点击页面...',browser_type:'输入文本...',browser_snapshot:'截取页面...',browser_console:'执行控制台...',lsp_completion:'代码补全...',lsp_diagnose:'诊断代码...',code_execution:'执行代码...'}
  return map[t.name] || t.name + '...'
}

// 工具结果预览模板化
const _toolPreviewCache = new WeakMap()
const _showAllTools = reactive({})

// ── 2.1 推理面板展开态：组件级 reactive map，并从 localStorage 恢复/持久化，整页刷新不重置 ──
const REASONING_EXPANDED_KEY = 'vermes_reasoning_expanded'
function loadReasoningExpanded() {
  try {
    const raw = typeof localStorage !== 'undefined' ? localStorage.getItem(REASONING_EXPANDED_KEY) : null
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}
const reasoningExpanded = reactive(loadReasoningExpanded())
watch(reasoningExpanded, () => {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(REASONING_EXPANDED_KEY, JSON.stringify(reasoningExpanded))
    }
  } catch { /* 忽略持久化异常（隐私模式 / 配额超限） */ }
}, { deep: true })
const reasoningMsgs = computed(() => chat.filteredMessages.filter(m => m && m.reasoning))
const allReasoningExpanded = computed(() =>
  reasoningMsgs.value.length > 0 && reasoningMsgs.value.every(m => reasoningExpanded[m.id])
)
function toggleAllReasoning() {
  const target = !allReasoningExpanded.value
  reasoningMsgs.value.forEach(m => { reasoningExpanded[m.id] = target })
}

// ── 2.2 流式超长内容按边界切片：避免切断未闭合代码块 / 段落 ──
function tailByBoundary(content, max = 8000) {
  if (!content || content.length <= max) return content
  let cut = content.length - max
  // 切片点若落在未闭合的代码围栏（```）内，回退到围栏开头，保证代码块完整渲染
  const before = content.slice(0, cut)
  const fenceCount = (before.match(/```/g) || []).length
  if (fenceCount % 2 === 1) {
    const openIdx = before.lastIndexOf('```')
    if (openIdx !== -1) cut = openIdx
  }
  // 尽量从段落边界（双换行）开始，截断更自然
  const nl = content.indexOf('\n\n', cut)
  if (nl !== -1 && nl - cut < 4000) cut = nl + 2
  return content.slice(cut)
}

function formatToolPreview(tool) {
  if (!tool.result_preview) return null
  // 缓存：同一 tool 对象 + 同一 result_preview 只解析一次
  if (_toolPreviewCache.has(tool)) {
    const cached = _toolPreviewCache.get(tool)
    if (cached.raw === tool.result_preview) return cached
  }
  const preview = tool.result_preview
  let result

  // terminal 工具：直接显示原始预览
  if (tool.name === 'terminal') {
    result = { type: 'terminal', command: null, output: preview, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // search_files 工具：提取文件列表和匹配行
  if (tool.name === 'search_files') {
    const fileMatches = []
    const lines = preview.split('\n')
    let currentFile = null
    for (const line of lines) {
      if (line.match(/^[^:]+\.\w+:/)) {
        currentFile = line.replace(/:$/, '')
        fileMatches.push({ file: currentFile, matches: [] })
      } else if (currentFile && line.trim()) {
        fileMatches[fileMatches.length - 1].matches.push(line.trim())
      }
    }
    result = { type: 'search', files: fileMatches, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // read_file 工具：提取行号范围
  if (tool.name === 'read_file') {
    const lineMatch = preview.match(/\((\d+)-(\d+)\)/)
    result = { type: 'file', lineRange: lineMatch ? `${lineMatch[1]}-${lineMatch[2]}` : null, content: preview, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // LSP 诊断工具：解析诊断结果
  if (tool.name === 'get_diagnostics' || tool.name === 'lsp_diagnostics' || tool.name === 'lsp_diagnose') {
    const diags = []
    const lines = preview.split('\n')
    for (const line of lines) {
      const match = line.match(/^(.+):(\d+):(\d+):\s*(error|warning|info|hint):\s*(.+)$/i)
      if (match) {
        diags.push({ file: match[1], line: match[2], col: match[3], severity: match[4].toLowerCase(), message: match[5] })
      }
    }
    result = { type: 'diagnostics', diags, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // 浏览器截图工具
  if (tool.name === 'browser_screenshot') {
    const imgMatch = preview.match(/data:image\/[^;]+;base64,[A-Za-z0-9+/=]+/)
    result = { type: 'screenshot', imageUrl: imgMatch ? imgMatch[0] : null, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // 浏览器快照/导航工具
  if (tool.name === 'browser_snapshot' || tool.name === 'browser_navigate' || tool.name === 'browse_url') {
    const urlMatch = preview.match(/URL:\s*(https?:\/\/[^\s]+)/) || preview.match(/^(https?:\/\/[^\s]+)/m)
    result = { type: 'browser', url: urlMatch ? urlMatch[1] : null, content: preview, raw: preview }
    _toolPreviewCache.set(tool, result)
    return result
  }

  // ── RecoverableFeedback：@recoverable_tool 返回的结构化错误 ──
  if (tool.is_error || tool.status === 'error') {
    try {
      const parsed = JSON.parse(preview)
      if (parsed && parsed.ok === false && parsed.what_failed) {
        result = {
          type: 'recoverable',
          what_failed: parsed.what_failed || '',
          what_missing: parsed.what_missing || '',
          suggested_next: parsed.suggested_next || '',
          error_type: parsed.error_type || '',
          raw: preview,
        }
        _toolPreviewCache.set(tool, result)
        return result
      }
    } catch (_) {
      // 不是 JSON，走默认逻辑
    }
  }

  // 默认：纯文本
  result = { type: 'text', raw: preview }
  _toolPreviewCache.set(tool, result)
  return result
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
    const lineCount = str.split('\n').length
    const isLong = lineCount > 20
    const codeCls = isLong ? ' hljs-long' : ''
    let codeHtml = ''
    if (lang && hljs.getLanguage(lang)) {
      try {
        const highlighted = hljs.highlight(str, { language: lang }).value
        codeHtml = highlighted
      } catch (__) {
        codeHtml = md.utils.escapeHtml(str)
      }
    } else {
      try {
        const result = hljs.highlightAuto(str, ['javascript', 'python', 'bash', 'json', 'html', 'css'])
        codeHtml = result.value
      } catch (__) {
        codeHtml = md.utils.escapeHtml(str)
      }
    }
    const toggle = isLong ? `<div class="code-toggle" onclick="this.parentElement.classList.toggle('collapsed')"><span class="code-lang">${lang || 'code'}</span><span class="code-toggle-btn">${lineCount} 行 · 点击折叠/展开</span></div>` : (lang ? `<div class="code-lang-bar"><span class="code-lang">${lang}</span></div>` : '')
    return `<pre class="hljs${codeCls}">${toggle}<code>${codeHtml}</code></pre>`
  }
})

// 链接默认在新窗口打开，防止 WebView 导航走导致白屏
const defaultLinkRenderer = md.renderer.rules.link_open || function(tokens, idx, options, env, self) {
  return self.renderToken(tokens, idx, options)
}
md.renderer.rules.link_open = function(tokens, idx, options, env, self) {
  const token = tokens[idx]
  const href = token.attrGet('href') || ''
  // 产物文件链接：加 📄 标记 + class，不改 target（handleContentClick 拦截）
  if (isArtifactLink(href)) {
    token.attrSet('data-artifact', 'true')
    token.attrSet('data-title', href.split('/').pop())
    // 不设 target=_blank，让 handleContentClick 处理
    return defaultLinkRenderer(tokens, idx, options, env, self)
  }
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  return defaultLinkRenderer(tokens, idx, options, env, self)
}

// ── 裸文件路径自动链接化 �─
// 匹配文本中的 /path/to/file.ext 或 path/to/file.ext（至少含一个 /）
const ARTIFACT_PATH_RE = /(?:^|[\s(\[{"'\u3000`])((?:\.?\/)?(?:[\w\u4e00-\u9fff-]+\/)*[\w\u4e00-\u9fff.-]+\.(?:md|html|htm|json|csv|txt|log|py|js|ts|sh|yaml|yml|toml|ini|cfg|png|jpg|jpeg|gif|webp|svg))(?=[\s)\]},"'\u3000`?!，。、；：]|$)/gi

// 后处理：在 sanitize 之后的 HTML 中把裸路径转为产物链接
// 比 markdown-it 内联规则更安全（在 DOMPurify 之后操作）
function linkifyArtifactPaths(html) {
  // 只替换不在 <a> 标签内、不在 <code> 标签内、不在 <pre> 标签内的裸路径
  // 用一个简单的状态机扫描
  let result = ''
  let i = 0
  let inTag = false
  let tagName = ''
  let inCodeOrPre = false
  let inLink = false
  
  while (i < html.length) {
    if (html[i] === '<') {
      // 标签开始
      const tagMatch = html.slice(i).match(/^<\/(\w+)>|^<(\w+)[^>]*>/)
      if (tagMatch) {
        const isClosing = !!html.slice(i).match(/^<\//)
        const name = (isClosing ? tagMatch[1] : tagMatch[2]).toLowerCase()
        if (isClosing) {
          if (name === 'a') inLink = false
          if (name === 'code' || name === 'pre') inCodeOrPre = false
        } else {
          if (name === 'a') inLink = true
          if (name === 'code' || name === 'pre') inCodeOrPre = true
        }
        result += tagMatch[0]
        i += tagMatch[0].length
        continue
      }
    }
    
    // 只在普通文本区域（非标签内、非 code/pre 内、非链接内）做路径替换
    if (!inCodeOrPre && !inLink) {
      // 找到下一个标签开始
      const nextTag = html.indexOf('<', i)
      const textSegment = nextTag === -1 ? html.slice(i) : html.slice(i, nextTag)
      
      // 在文本段中替换裸路径
      const replaced = textSegment.replace(ARTIFACT_PATH_RE, (match, path, offset, str) => {
        // match 包含前导字符（空格/括号等），path 是纯路径
        const prefix = match.slice(0, match.length - path.length)
        const cleanPath = path.replace(/^\.?\//, '')
        const fileName = cleanPath.split('/').pop()
        return `${prefix}<a href="${cleanPath}" data-artifact="true" data-title="${fileName}" class="artifact-link">📄 ${path}</a>`
      })
      result += replaced
      i = nextTag === -1 ? html.length : nextTag
      continue
    }
    
    result += html[i]
    i++
  }
  
  return result
}

const chat = useChatStore()

const props = defineProps({
  inputText: String,
})

const emit = defineEmits(['quickStart', 'editMessage'])

// ── P2-15: 普通消息列表（去掉虚拟滚动，避免高度不一导致重叠） ──
const chatContainer = ref(null)

// DOMPurify 加固配置 — 2.1.2 安全补丁
// 使用集中式安全模块配置，保持与 security.js 同步
DOMPurify.addHook('afterSanitizeAttributes', enforceLinkSecurity)

// ── Markdown 渲染 ──
function renderMd(content) {
  if (!content) return ''
  try {
    const rawHtml = md.render(content)
    const sanitized = DOMPurify.sanitize(rawHtml, DOMPURIFY_BASE_CONFIG)
    // 裸文件路径链接化（在 sanitize 之后，确保不引入未消毒 HTML）
    return linkifyArtifactPaths(sanitized)
  } catch (e) {
    console.error('[DOMPurify] sanitize failed:', e)
    // 降级：纯文本转义
    return content.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}

// ── 文件大小格式化 ──
function formatSize(bytes) {
  if (!bytes) return ''
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

// ── 图片预览：点击放大 ──
const previewImage = ref(null)
function openImagePreview(url) {
  previewImage.value = url
}
function closeImagePreview() {
  previewImage.value = null
}

// ── 用户消息图片提取 ──
function extractImages(content) {
  if (!content) return []
  const re = /!\[.*?\]\((data:image\/[^)]+)\)/g
  const urls = []
  let m
  while ((m = re.exec(content)) !== null) {
    urls.push(m[1])
  }
  return urls
}

// ── 用户消息文本清理：去掉 base64 图片引用避免显示乱码 ──
function cleanUserContent(content) {
  if (!content) return ''
  return content.replace(/!\[.*?\]\(data:image\/[^)]+\)/g, '').replace(/\n{3,}/g, '\n\n').trim()
}

// ── 链接点击拦截（pywebview/浏览器兼容） ──
// ── 产物文件扩展名 ──
const ARTIFACT_EXTS = ['md', 'html', 'htm', 'json', 'csv', 'txt', 'log', 'py', 'js', 'ts', 'sh', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'svg']
const ARTIFACT_EXTS_SET = new Set(ARTIFACT_EXTS)

// 判断链接是否为产物文件（本地路径 + 已知扩展名）
function isArtifactLink(href) {
  if (!href) return false
  // 相对路径或绝对路径（非 http(s)://）
  if (/^https?:\/\//.test(href)) return false
  // 提取路径中的扩展名
  const cleanPath = href.split('?')[0].split('#')[0]
  const ext = cleanPath.split('.').pop()?.toLowerCase()
  return ARTIFACT_EXTS_SET.has(ext)
}

// 从链接 href 提取产物路径（去掉浏览器 base URL 前缀）
function extractArtifactPath(href) {
  // 如果是完整 URL 但实际是本地路径（浏览器自动加了 http://localhost:port/ 前缀）
  try {
    const url = new URL(href, window.location.origin)
    if (url.origin === window.location.origin) {
      return url.pathname.slice(1) // 去掉前导 /
    }
  } catch {}
  // 相对/绝对路径直接用
  return href.replace(/^\.?\//, '')
}

function handleContentClick(e) {
  const a = e.target.closest('a[href]')
  if (a) {
    const href = a.getAttribute('href') || ''
    // 产物文件链接 → 打开产物面板
    if (isArtifactLink(href)) {
      e.preventDefault()
      const path = extractArtifactPath(href)
      const title = a.dataset.title || path.split('/').pop()
      const ext = path.split('.').pop()?.toLowerCase()
      const mimeMap = { md: 'text/markdown', html: 'text/html', htm: 'text/html', json: 'application/json', csv: 'text/csv', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp', svg: 'image/svg+xml' }
      const mime = mimeMap[ext] || 'text/plain'
      if (window.__vermesArtifacts) {
        window.__vermesArtifacts.addArtifact({ path, title, mime, source: 'chat' })
        openRightPanel('artifacts')
      }
      return
    }
    // 外部链接 → 新窗口
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
watch(() => chat.filteredMessages?.length ?? 0, async () => {
  await nextTick()
  if (chatContainer.value) {
    addCopyButtonsToPreElements(chatContainer.value)
  }
  // 自动展开重要工具的结果
  chat.filteredMessages.forEach(msg => {
    if (msg.toolInvocations) {
      autoExpandImportantTools(msg.toolInvocations)
    }
  })
  // 流式中不在此处滚动（由 store 的 _scheduleScroll RAF 调度处理）
})

// ── 推理面板自动滚底 ──
const reasoningContentRefs = {}
watch(() => chat.messages.map(m => m.reasoning?.length).join(','), () => {
  nextTick(() => {
    for (const msg of chat.messages) {
      const el = reasoningContentRefs[msg.id]
      if (el && msg.streaming && msg.reasoning) {
        el.scrollTop = el.scrollHeight
      }
    }
  })
})

// ── 回到底部按钮 ──
const showScrollBtn = ref(false)

function checkScrollPosition() {
  if (!chatContainer.value) return
  const c = chatContainer.value
  // 距离底部超过 300px 时显示按钮
  showScrollBtn.value = c.scrollHeight - c.scrollTop - c.clientHeight > 300
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTo({ top: chatContainer.value.scrollHeight, behavior: 'smooth' })
  }
}

// 滚到最后一条用户消息位置
function scrollToLastUserMsg() {
  if (!chatContainer.value) return
  const container = chatContainer.value
  const userMsgs = container.querySelectorAll('[data-role="user"]')
  if (userMsgs.length > 0) {
    const lastUser = userMsgs[userMsgs.length - 1]
    lastUser.scrollIntoView({ block: 'start', behavior: 'instant' })
  } else {
    container.scrollTop = container.scrollHeight
  }
}

// 初始挂载时也添加 + 滚到最新用户消息
onMounted(() => {
  if (chatContainer.value) {
    addCopyButtonsToPreElements(chatContainer.value)
    chatContainer.value.addEventListener('scroll', checkScrollPosition)
    // ── 长任务优化: 注入滚动容器给 store 的滚动调度器 ──
    setScrollTarget(chatContainer.value)
    nextTick(() => {
      scrollToLastUserMsg()
    })
  }
})

onUnmounted(() => {
  if (chatContainer.value) {
    chatContainer.value.removeEventListener('scroll', checkScrollPosition)
  }
  setScrollTarget(null)
})

// 切换会话时滚到最新用户消息（多次尝试，确保长列表渲染完成）
watch(() => chat.currentSessionId, async () => {
  for (let i = 0; i < 5; i++) {
    await nextTick()
  }
  scrollToLastUserMsg()
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

// ── 长任务优化 #3: 流式耗时显示 ──
function streamElapsed(startTime) {
  if (!startTime) return ''
  const sec = Math.round((Date.now() - startTime) / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const s = sec % 60
  return `${min}m${s}s`
}


</script>

<template>
  <div ref="chatContainer" class="flex-1 min-h-0 overflow-y-auto px-4 py-6 bg-gray-50 dark:bg-gray-900 relative">
    <!-- 骨架屏：loading 且无消息（切换会话/loading 中） -->
    <div v-if="(chat.filteredMessages?.length ?? 0) === 0 && chat.loading" class="space-y-4 px-4 py-6">
      <div v-for="i in 3" :key="'skeleton-'+i" class="flex gap-3" :class="i % 2 === 0 ? 'flex-row-reverse' : ''">
        <div class="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-700 animate-pulse flex-shrink-0"></div>
        <div class="max-w-[75%] space-y-2">
          <div class="h-4 rounded bg-gray-200 dark:bg-gray-700 animate-pulse" :style="{width: (120 + i * 60) + 'px'}"></div>
          <div class="h-4 rounded bg-gray-200 dark:bg-gray-700 animate-pulse" :style="{width: (80 + i * 40) + 'px'}"></div>
          <div v-if="i < 3" class="h-4 rounded bg-gray-200 dark:bg-gray-700 animate-pulse w-24"></div>
        </div>
      </div>
    </div>

    <!-- 消息列表 -->
    <div v-else>
      <!-- 2.1 推理面板：展开全部 / 收起全部 -->
      <div v-if="reasoningMsgs.length > 0" class="flex justify-end px-4 pb-1">
        <button @click="toggleAllReasoning"
                class="text-[11px] text-purple-500 dark:text-purple-400 hover:underline select-none">
          {{ allReasoningExpanded ? '▾ 收起全部推理' : '▸ 展开全部推理' }} ({{ reasoningMsgs.length }})
        </button>
      </div>
      <div v-for="msg in chat.filteredMessages" :key="msg.id"
           :data-role="msg.role"
           class="flex gap-3 group px-4 py-2"
           :class="msg.role === 'user' ? 'flex-row-reverse' : msg._isModelChange ? 'flex-row justify-center' : ''">
        <!-- 模型切换提示：居中轻量样式 -->
        <div v-if="msg._isModelChange" class="text-xs text-gray-400 dark:text-gray-500 bg-gray-100 dark:bg-gray-800/50 rounded-full px-3 py-1 inline-flex items-center gap-1">
          {{ msg.content }}
        </div>
        <template v-else>
        <div class="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0" :class="msg.role === 'user' ? 'bg-indigo-500' : 'bg-green-500'">
          {{ msg.role === 'user' ? '我' : 'V' }}
        </div>
        <div class="max-w-[75%] min-w-0">
          <!-- P3-8: 对比模式标签 -->
          <div v-if="msg._compareModel" class="text-[10px] text-purple-500 dark:text-purple-400 mb-1 font-medium px-1">
            🔬 {{ msg._compareModel }}
          </div>
          <div class="px-4 py-3 rounded-2xl text-sm leading-relaxed" :class="msg._isBriefing ? 'evo-briefing' : msg.role === 'user' ? 'bg-indigo-500 text-white rounded-br-md' : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-bl-md shadow-sm border-l-[3px] border-green-400 dark:border-green-500'">
            <template v-if="msg.role === 'user'">
              <!-- attachments 中的图片 -->
              <template v-if="msg.attachments && msg.attachments.length">
                <div v-for="(att, idx) in msg.attachments" :key="'att-' + idx" class="mb-2">
                  <img v-if="att.type === 'image' && att.data" :src="'data:' + (att.mime || 'image/png') + ';base64,' + att.data" class="rounded-lg cursor-pointer hover:opacity-90 transition" style="max-width: 100%; max-height: 300px; object-fit: contain;" @click="openImagePreview('data:' + (att.mime || 'image/png') + ';base64,' + att.data)" />
                  <video v-else-if="att.type === 'video' && att.data" :src="'data:' + (att.mime || 'video/mp4') + ';base64,' + att.data" controls class="rounded-lg" style="max-width: 100%; max-height: 300px;"></video>
                  <div v-else class="flex items-center gap-2 text-xs opacity-80 bg-black/10 rounded-lg px-3 py-2">
                    <span>{{ att.name?.match(/\.(mp4|mov|avi|webm)$/i) ? '🎬' : '📄' }}</span>
                    <span class="truncate max-w-[150px]">{{ att.name }}</span>
                    <span class="opacity-60">{{ att.size ? formatSize(att.size) : '' }}</span>
                  </div>
                </div>
              </template>
              <!-- 兼容旧消息：从 markdown ![](data:image/...) 中提取显示 -->
              <img v-for="(imgUrl, idx) in extractImages(msg.content)" :key="'uimg-' + idx" :src="imgUrl" class="rounded-lg mb-2 cursor-pointer hover:opacity-90 transition" style="max-width: 100%; max-height: 300px; object-fit: contain;" @click="openImagePreview(imgUrl)" />
              <!-- 文本内容：去掉 base64 避免显示乱码 -->
              <div v-if="cleanUserContent(msg.content)" style="white-space:pre-wrap;word-break:break-word;">{{ cleanUserContent(msg.content) }}</div>
            </template>
            <template v-else>
              <!-- ── 长任务优化 #5: 内容分片 ── -->
              <!-- 流式中只渲染最新 8K 字符，避免超长内容卡顿 -->
              <div v-if="msg.content" class="vermes-md" v-html="renderMd(msg._expanded || !msg.streaming || msg.content.length <= 8000 ? msg.content : tailByBoundary(msg.content, 8000))" @click="handleContentClick($event)"></div>
              <!-- 展开按钮：流式中内容超过 8K 时显示 -->
              <button v-if="msg.streaming && msg.content && msg.content.length > 8000 && !msg._expanded"
                      @click="msg._expanded = true"
                      class="mt-2 text-xs text-green-600 dark:text-green-400 hover:underline">
                📄 内容较长，点击展开全部 ({{ msg.content.length.toLocaleString() }} 字符)
              </button>
              <span v-else-if="msg.streaming" class="thinking-inline">正在思考<span class="thinking-dots"><span>●</span><span>●</span><span>●</span></span></span>
              <span v-if="msg.streaming && msg.content" class="typing-cursor"></span>
            </template>
          </div>
          <!-- 推理链可视化：折叠/展开面板 -->
          <div v-if="msg.reasoning" class="mt-2 reasoning-block">
            <details :open="reasoningExpanded[msg.id] || false" @toggle="reasoningExpanded[msg.id] = $event.target.open" class="reasoning-details">
              <summary class="cursor-pointer text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 select-none flex items-center gap-1.5 reasoning-summary">
                <span class="reasoning-chevron">▸</span>
                <span>💡 推理过程</span>
                <span class="opacity-50">({{ msg.reasoning.length.toLocaleString() }} 字)</span>
                <span v-if="msg.streaming" class="text-purple-400 animate-pulse">●</span>
              </summary>
              <div :ref="el => reasoningContentRefs[msg.id] = el" class="mt-1.5 pl-3 border-l-2 border-purple-200 dark:border-purple-800 text-sm text-gray-500 dark:text-gray-400 italic whitespace-pre-wrap max-h-96 overflow-y-auto reasoning-content" style="white-space:pre-wrap;word-break:break-word;">{{ msg.reasoning }}</div>
            </details>
          </div>
          <!-- 工具调用展示：流式中=单条状态，完成后=紧凑时间线 -->
          <div v-if="msg.toolInvocations && msg.toolInvocations.length > 0" class="mt-2">
            <!-- 流式中：进度条 + 当前工具 -->
            <template v-if="msg.streaming">
              <!-- ── 长任务优化 #3: 进度指示器 ── -->
              <div v-if="msg._streamStartTime"
                   class="inline-flex items-center gap-2 px-2.5 py-1 rounded-full text-[11px] bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400">
                <span class="animate-pulse">●</span>
                <span>第 {{ msg._currentStep || 1 }} 步</span>
                <span class="opacity-60">·</span>
                <span class="opacity-60">{{ streamElapsed(msg._streamStartTime) }}</span>
                <span v-if="msg._toolCount" class="opacity-60">· {{ msg._toolCount }} 工具</span>
              </div>
              <div v-if="currentRunningTool(msg.toolInvocations)"
                   class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 tool-running">
                <span class="animate-spin text-xs">⏳</span>
                <span>{{ currentRunningTool(msg.toolInvocations) }}</span>
              </div>
              <!-- "正在思考"动画（无工具执行时显示） -->
              <div v-else-if="msg.streaming && msg._streamStartTime"
                   class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] bg-gray-50 dark:bg-gray-800 text-gray-500 dark:text-gray-400">
                <span class="thinking-indicator">正在思考</span>
              </div>
            </template>
            <!-- 完成后：紧凑时间线 -->
            <template v-else>
              <div v-if="msg.toolInvocations.length > 0"
                   class="flex flex-wrap gap-1.5 text-[11px] text-gray-400 dark:text-gray-500">
                <template v-for="(tool, idx) in msg.toolInvocations" :key="'done-' + (tool.id || tool.name)">
                  <span v-if="idx < 5 || _showAllTools[msg.id]"
                        class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition"
                        :class="tool.status === 'error' ? 'text-red-500' : ''"
                        @click="tool.result_preview && toggleToolExpand(tool.id || tool.name)">
                    <span>{{ tool.status === 'error' ? '❌' : '✅' }}</span>
                    <span>{{ ({read_file:'读取文件',write_file:'写入文件',search_files:'搜索文件',terminal:'终端',web_search:'网页搜索',vision_analyze:'图片分析',list_directory:'列出目录',edit_file:'编辑文件',memory:'记忆',execute_command:'执行命令',google_search:'搜索',browse_url:'浏览网页',browser_navigate:'浏览网页',browser_click:'点击页面',browser_type:'输入文本',browser_snapshot:'截取页面',browser_console:'控制台',lsp_completion:'代码补全',lsp_diagnose:'诊断代码',code_execution:'执行代码'})[tool.name] || tool.name }}</span>
                    <span v-if="tool.duration" class="opacity-60">{{ tool.duration }}s</span>
                    <span v-if="tool.result_preview">{{ isToolExpanded(tool.id || tool.name) ? '▼' : '▶' }}</span>
                  </span>
                </template>
                <span v-if="msg.toolInvocations.length > 5 && !_showAllTools[msg.id]"
                      @click="_showAllTools[msg.id] = true"
                      class="px-2 py-0.5 rounded-full text-green-500 cursor-pointer hover:bg-green-50 dark:hover:bg-green-900/20">
                  +{{ msg.toolInvocations.length - 5 }} 个工具
                </span>
              </div>
              <!-- 可折叠的结果摘要 -->
              <template v-for="tool in msg.toolInvocations" :key="'preview-' + (tool.id || tool.name)">
                <div v-if="tool.result_preview && isToolExpanded(tool.id || tool.name)"
                     class="mt-1 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700">
                  <!-- terminal 工具模板 -->
                  <template v-if="formatToolPreview(tool)?.type === 'terminal'">
                    <div v-if="formatToolPreview(tool).command" class="text-[11px] text-green-600 dark:text-green-400 font-mono mb-1">
                      $ {{ formatToolPreview(tool).command }}
                    </div>
                    <pre v-if="formatToolPreview(tool).output" class="text-[11px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ formatToolPreview(tool).output }}</pre>
                  </template>
                  <!-- search_files 工具模板 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'search'">
                    <div v-for="(file, idx) in formatToolPreview(tool).files" :key="'file-' + idx" class="mb-2">
                      <div class="text-[11px] text-blue-600 dark:text-blue-400 font-mono">📄 {{ file.file }}</div>
                      <div v-for="(match, mIdx) in file.matches.slice(0, 3)" :key="'match-' + mIdx"
                           class="text-[11px] text-gray-600 dark:text-gray-300 font-mono pl-3">
                        {{ match }}
                      </div>
                      <div v-if="file.matches.length > 3" class="text-[10px] text-gray-400 pl-3">
                        ... 还有 {{ file.matches.length - 3 }} 个匹配
                      </div>
                    </div>
                  </template>
                  <!-- read_file 工具模板 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'file'">
                    <div v-if="formatToolPreview(tool).lineRange" class="text-[11px] text-gray-500 dark:text-gray-400 font-mono mb-1">
                      📄 行 {{ formatToolPreview(tool).lineRange }}
                    </div>
                    <pre class="text-[11px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ formatToolPreview(tool).content }}</pre>
                  </template>
                  <!-- LSP 诊断模板 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'diagnostics'">
                    <div class="space-y-1">
                      <div v-for="(d, idx) in formatToolPreview(tool).diags" :key="'diag-' + idx"
                           class="flex items-start gap-2 text-[11px] font-mono">
                        <span :class="{
                          'text-red-500': d.severity === 'error',
                          'text-yellow-500': d.severity === 'warning',
                          'text-blue-400': d.severity === 'info'
                        }">{{ d.severity === 'error' ? '❌' : d.severity === 'warning' ? '⚠️' : 'ℹ️' }}</span>
                        <span class="text-gray-500 dark:text-gray-400">{{ d.file }}:{{ d.line }}:{{ d.col }}</span>
                        <span class="text-gray-700 dark:text-gray-300">{{ d.message }}</span>
                      </div>
                      <div v-if="formatToolPreview(tool).diags.length === 0" class="text-[11px] text-green-500">✅ 无诊断问题</div>
                    </div>
                  </template>
                  <!-- 浏览器截图模板 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'screenshot'">
                    <div class="browser-screenshot">
                      <img v-if="formatToolPreview(tool).imageUrl"
                           :src="formatToolPreview(tool).imageUrl"
                           class="max-w-full rounded-lg border border-gray-200 dark:border-gray-700 cursor-pointer"
                           style="max-height: 300px; object-fit: contain;"
                           @click="window.open(formatToolPreview(tool).imageUrl, '_blank')" />
                      <span v-else class="text-[11px] text-gray-500">截图生成中...</span>
                    </div>
                  </template>
                  <!-- 浏览器快照模板 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'browser'">
                    <div class="browser-result">
                      <div v-if="formatToolPreview(tool).url" class="text-[11px] text-blue-500 dark:text-blue-400 font-mono mb-1">
                        🌐 {{ formatToolPreview(tool).url }}
                      </div>
                      <pre class="text-[11px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ formatToolPreview(tool).content }}</pre>
                    </div>
                  </template>
                  <!-- RecoverableFeedback：结构化错误提示 -->
                  <template v-else-if="formatToolPreview(tool)?.type === 'recoverable'">
                    <div class="space-y-1.5">
                      <div class="flex items-center gap-1.5 text-[11px] text-red-600 dark:text-red-400">
                        <span>⚠️</span>
                        <span class="font-medium">{{ formatToolPreview(tool).what_failed }}</span>
                      </div>
                      <div v-if="formatToolPreview(tool).what_missing" class="text-[11px] text-orange-500 dark:text-orange-400">
                        💡 {{ formatToolPreview(tool).what_missing }}
                      </div>
                      <div v-if="formatToolPreview(tool).suggested_next" class="text-[11px] text-blue-500 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 rounded px-2 py-1">
                        🔧 {{ formatToolPreview(tool).suggested_next }}
                      </div>
                    </div>
                  </template>
                  <!-- 默认模板 -->
                  <template v-else>
                    <pre class="text-[11px] text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">{{ tool.result_preview }}</pre>
                  </template>
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
        </template>
      </div>
    </div><!-- end msg -->
  </div><!-- end message loop -->

  <!-- 流式状态消息（压缩警告、lifecycle 通知等） -->
  <div v-if="(chat.currentStatusMessages?.length ?? 0) > 0" class="px-4 py-1 flex flex-col gap-1">
    <div v-for="s in chat.currentStatusMessages" :key="s.id"
         class="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800/50 animate-fade-in">
      <span>{{ s.type === 'warn' ? '⚠️' : '📦' }}</span>
      <span>{{ s.message }}</span>
    </div>
  </div>

  <!-- 进化事件（工具调用后的成长反馈，仅显示最新1条） -->
  <div v-if="chat.evolutionEvents?.length > 0" class="px-4 py-0.5">
    <div v-for="e in chat.evolutionEvents.slice(-1)" :key="e.id"
         class="text-[11px] flex items-center gap-1.5 px-2.5 py-1 rounded-lg animate-fade-in opacity-70 hover:opacity-100 transition-opacity"
         :class="e.is_error
           ? 'bg-amber-50 dark:bg-amber-900/20 text-amber-600 dark:text-amber-400'
           : 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-400'">
          <span>{{ e.is_error ? '💡' : '✨' }}</span>
          <span class="truncate max-w-[280px]">{{ e.message }}</span>
          <span v-if="e.tool_name" class="opacity-50 text-[10px] font-mono">{{ e.tool_name }}</span>
    </div>
  </div>

  <!-- token 用量 -->
  <div v-if="chat.lastTokenUsage && !chat.loading" class="px-4 py-2 flex justify-end">
    <span class="text-[10px] text-gray-400 dark:text-gray-500 font-mono">
      {{ chat.lastTokenUsage.prompt_tokens || 0 }} / {{ chat.lastTokenUsage.completion_tokens || 0 }} → {{ chat.lastTokenUsage.total_tokens }} tokens
    </span>
  </div>

  <!-- 回到底部浮动按钮 -->
  <button v-if="showScrollBtn" @click="scrollToBottom"
    class="fixed bottom-24 right-6 z-50 w-10 h-10 rounded-full bg-green-500 hover:bg-green-600 text-white shadow-lg flex items-center justify-center transition-all duration-200 hover:scale-110"
    title="回到底部">
    ↓
  </button>

  <!-- 图片预览遮罩 -->
  <Teleport to="body">
    <!-- 成就解锁通知 -->
    <Transition name="achievement-pop">
      <div v-if="chat.showAchievement" class="fixed top-6 left-1/2 -translate-x-1/2 z-[90] cursor-pointer" @click="chat.showAchievement = false">
        <div class="bg-gradient-to-r from-amber-400 to-yellow-500 text-white px-6 py-3 rounded-2xl shadow-2xl flex items-center gap-3">
          <span class="text-2xl">🏆</span>
          <div>
            <div class="font-bold text-sm">成就解锁</div>
            <div class="text-xs opacity-90">{{ chat.achievementData?.message }}</div>
          </div>
          <span class="text-xs opacity-70 ml-2">点击关闭</span>
        </div>
      </div>
    </Transition>

    <div v-if="previewImage" @click="closeImagePreview" class="fixed inset-0 z-[100] bg-black/80 flex items-center justify-center cursor-zoom-out">
      <img :src="previewImage" class="max-w-[90vw] max-h-[90vh] object-contain rounded-lg shadow-2xl" />
    </div>
  </Teleport>
</template>

<style scoped>
/* ── 进化简报卡片 ── */
.evo-briefing {
  border-left: 3px solid transparent;
  border-image: linear-gradient(to bottom, #22c55e, #3b82f6) 1;
  background: linear-gradient(135deg, rgba(34, 197, 94, 0.03) 0%, rgba(59, 130, 246, 0.03) 100%);
  animation: briefing-in 0.5s ease-out;
}
.dark .evo-briefing {
  background: linear-gradient(135deg, rgba(34, 197, 94, 0.06) 0%, rgba(59, 130, 246, 0.06) 100%);
}
@keyframes briefing-in {
  from { opacity: 0; transform: translateY(12px); }
  to { opacity: 1; transform: translateY(0); }
}

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
.vermes-md :deep(img) { max-width: 100%; max-height: 300px; object-fit: contain; border-radius: 8px; cursor: pointer; margin: 0.3em 0; }

/* P3-4: highlight.js 完整 token 样式（Catppuccin Mocha 配色） */
.vermes-md :deep(pre.hljs) { background: #1e1e2e; color: #cdd6f4; border-radius: 8px; padding: 12px 16px; overflow-x: auto; margin: 0.6em 0; font-size: 0.85em; position: relative; }
.vermes-md :deep(pre.hljs code) { background: none; padding: 0; color: inherit; font-size: 1em; }
.dark .vermes-md :deep(pre.hljs) { background: #1e1e2e; }
/* 代码块语言标签 */
.vermes-md :deep(.code-lang-bar) { padding-bottom: 6px; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); }
.vermes-md :deep(.code-lang) { font-size: 0.75em; color: #888; text-transform: uppercase; letter-spacing: 0.5px; }
/* 长代码块折叠 */
.vermes-md :deep(pre.hljs-long) { padding-top: 0; }
.vermes-md :deep(.code-toggle) { display: flex; justify-content: space-between; align-items: center; padding: 8px 0 6px; margin-bottom: 4px; border-bottom: 1px solid rgba(255,255,255,0.1); cursor: pointer; user-select: none; }
.vermes-md :deep(.code-toggle-btn) { font-size: 0.75em; color: #6cb6ff; }
.vermes-md :deep(pre.hljs-long.collapsed code) { display: none; }
.vermes-md :deep(pre.hljs-long.collapsed) { padding-bottom: 8px; }
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
/* 产物链接：绿色加粗 + 📄 图标 */
.vermes-md :deep(a.artifact-link) { color: #16a34a; font-weight: 500; border-bottom: 1px dashed #22c55e; padding-bottom: 1px; }
.vermes-md :deep(a.artifact-link:hover) { background: rgba(34,197,94,0.08); border-bottom-style: solid; cursor: pointer; }

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

/* 工具卡片脉冲动效 */
@keyframes tool-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.4); }
  50% { box-shadow: 0 0 0 6px rgba(59, 130, 246, 0); }
}
.tool-running {
  animation: tool-pulse 2s infinite;
  border-color: #3b82f6;
}
.dark .tool-running {
  border-color: #60a5fa;
}

/* "正在思考"进度区域动画 */
@keyframes thinking-dots-progress {
  0%, 20% { content: '.'; }
  40% { content: '..'; }
  60%, 100% { content: '...'; }
}
.thinking-indicator::after {
  content: '';
  animation: thinking-dots-progress 1.5s infinite;
}

/* "正在思考"内联动画（跟随打字机光标） */
.thinking-inline {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  color: #9ca3af;
  font-size: 0.75rem;
}
.dark .thinking-inline {
  color: #6b7280;
}
.thinking-dots {
  display: inline-flex;
  gap: 2px;
}
.thinking-dots span {
  animation: thinking-inline-pulse 1.4s infinite;
  opacity: 0.4;
}
.thinking-dots span:nth-child(1) { animation-delay: 0s; }
.thinking-dots span:nth-child(2) { animation-delay: 0.2s; }
.thinking-dots span:nth-child(3) { animation-delay: 0.4s; }
@keyframes thinking-inline-pulse {
  0%, 80%, 100% { opacity: 0.4; }
  40% { opacity: 1; }
}

/* 状态消息淡入动画 */
@keyframes fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to { opacity: 1; transform: translateY(0); }
}
.animate-fade-in {
  animation: fade-in 0.3s ease-out;
}

/* 成就解锁动画 */
.achievement-pop-enter-active {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.achievement-pop-leave-active {
  transition: all 0.3s ease-in;
}
.achievement-pop-enter-from {
  opacity: 0;
  transform: translate(-50%, -20px) scale(0.8);
}
.achievement-pop-leave-to {
  opacity: 0;
  transform: translate(-50%, -10px) scale(0.95);
}
/* ── 推理链展开/收起动画 ── */
.reasoning-details > summary {
  list-style: none;
}
.reasoning-details > summary::-webkit-details-marker {
  display: none;
}
.reasoning-chevron {
  display: inline-block;
  transition: transform 0.2s ease;
  font-size: 10px;
  color: #a78bfa;
}
.reasoning-details[open] .reasoning-chevron {
  transform: rotate(90deg);
}
.reasoning-content {
  animation: reasoning-expand 0.25s ease-out;
  max-height: 24rem;
  overflow-y: auto;
}
@keyframes reasoning-expand {
  from {
    opacity: 0;
    transform: translateY(-4px);
    max-height: 0;
  }
  to {
    opacity: 1;
    transform: translateY(0);
    max-height: 24rem;
  }
}
.reasoning-details:not([open]) .reasoning-content {
  display: none;
}
/* 推理文本滚动条美化 */
.reasoning-content::-webkit-scrollbar {
  width: 4px;
}
.reasoning-content::-webkit-scrollbar-thumb {
  background: rgba(167, 139, 250, 0.3);
  border-radius: 2px;
}
</style>
