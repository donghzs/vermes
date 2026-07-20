<script setup>
import { ref, onUnmounted, computed, nextTick } from 'vue'
import { useChatStore } from '../stores/chat'
import { toast } from '../utils/toast'

const chat = useChatStore()

const inputText = ref('')
const fileInput = ref(null)
const uploadedFiles = ref([])

const inputRef = ref(null)
const isDragging = ref(false)

const emit = defineEmits(['send'])

// ── 推理强度 ──
const reasoningLevels = [
  { value: '', label: '自动', icon: '⚡' },
  { value: 'low', label: '快速', icon: '🚀' },
  { value: 'medium', label: '标准', icon: '🧠' },
  { value: 'high', label: '深度', icon: '🔬' },
]
const showReasoningPicker = ref(false)
function selectReasoning(level) {
  chat.reasoningEffort = level
  localStorage.setItem('vermes-reasoning-effort', level)
  showReasoningPicker.value = false
}
const currentReasoningLabel = computed(() => {
  const found = reasoningLevels.find(r => r.value === chat.reasoningEffort)
  return found || reasoningLevels[0]
})

// ── 联网搜索开关 ──
function toggleSearch() {
  chat.searchEnabled = !chat.searchEnabled
  localStorage.setItem('vermes-search-enabled', String(chat.searchEnabled))
}

// ── 常量 ──
const MAX_SINGLE_FILE = 20 * 1024 * 1024   // 20MB
const MAX_TOTAL_SIZE = 50 * 1024 * 1024     // 50MB

// 空会话检测
function isEmptySession() {
  return (chat.filteredMessages?.length ?? 0) === 0
}

// ── 文件上传 ──
function triggerFileUpload() { fileInput.value?.click() }

async function urlToFile(url, name) {
  try {
    const resp = await fetch(url)
    const blob = await resp.blob()
    if (blob.size > MAX_SINGLE_FILE) {
      toast.warning(`图片 ${name || url.slice(-20)} 超过 20MB`)
      return null
    }
    return new File([blob], name || 'image.png', { type: blob.type || 'image/png' })
  } catch {
    return null
  }
}

function addFile(f) {
  let totalSize = uploadedFiles.value.reduce((s, f) => s + f.size, 0)
  if (f.size > MAX_SINGLE_FILE) {
    toast.warning(`文件 ${f.name} 超过 20MB`)
    return
  }
  if (totalSize + f.size > MAX_TOTAL_SIZE) {
    toast.warning(`附件总大小超过 50MB`)
    return
  }
  uploadedFiles.value.push({
    name: f.name,
    size: f.size,
    file: f,
    preview: f.type.startsWith('image/') ? URL.createObjectURL(f) : null
  })
}

function addFiles(fileList) {
  for (const f of Array.from(fileList)) addFile(f)
}

function handleFileSelect(e) {
  addFiles(e.target.files)
  e.target.value = ''
}

// ── #1 拖拽上传 ──
function onDragEnter(e) { e.preventDefault(); isDragging.value = true }
function onDragOver(e) { e.preventDefault(); isDragging.value = true }
function onDragLeave(e) {
  e.preventDefault()
  if (!e.currentTarget.contains(e.relatedTarget)) isDragging.value = false
}
function onDrop(e) {
  e.preventDefault()
  isDragging.value = false
  // 文件拖放
  if (e.dataTransfer?.files?.length > 0) {
    addFiles(e.dataTransfer.files)
  }
  // 网页图片拖放（URL）
  const html = e.dataTransfer?.getData('text/html')
  if (html) {
    const imgMatch = html.match(/<img[^>]+src=["']([^"']+)["']/i)
    if (imgMatch?.[1]) {
      urlToFile(imgMatch[1], 'dropped-image.png').then(f => { if (f) addFile(f) })
    }
  }
}

function removeFile(idx) {
  const f = uploadedFiles.value[idx]
  if (f?.preview) URL.revokeObjectURL(f.preview)
  uploadedFiles.value.splice(idx, 1)
}

// ── 多行输入 ──
function insertNewline(e) { e.preventDefault(); inputText.value += '\n' }
function autoResize(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

// ── 粘贴图片 ──
async function onPaste(e) {
  const items = e.clipboardData?.items
  if (!items) return
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault()
      const blob = item.getAsFile()
      if (blob) {
        addFile(new File([blob], 'pasted-image.png', { type: blob.type }))
      }
    }
  }
}

// ── @file 引用检测 ──
const showFileHint = ref(false)
const fileHintItems = ref([])

function onInputCheckFileRef(e) {
  const el = e.target
  const pos = el.selectionStart
  const before = el.value.substring(0, pos)
  // 检查光标前是否有 @ 符号（且前面是空格或行首）
  const atMatch = before.match(/(?:^|\s)@([\w./\-+]*)$/)
  if (atMatch) {
    const partial = atMatch[1]
    // 简单提示：显示当前目录下的文件
    showFileHint.value = true
    fileHintItems.value = [
      { label: '输入文件路径，如 @src/main.py', hint: '支持代码文件、配置文件、文档等' },
      { label: '@AGENTS.md', hint: '工作区配置' },
      { label: '@package.json', hint: '项目配置' },
    ]
  } else {
    showFileHint.value = false
  }
  autoResize(e)
}

function insertFileRef(path) {
  const el = inputRef.value
  const pos = el.selectionStart
  const before = el.value.substring(0, pos)
  const after = el.value.substring(pos)
  // 替换 @ 后的部分
  const atIdx = before.lastIndexOf('@')
  if (atIdx >= 0) {
    const prefix = before.substring(0, atIdx + 1)
    inputText.value = prefix + path + ' ' + after
    nextTick(() => {
      el.focus()
      const newPos = (prefix + path + ' ').length
      el.setSelectionRange(newPos, newPos)
    })
  }
  showFileHint.value = false
}

// ── 发送 ──
async function send() {
  const input = inputText.value.trim()
  const files = [...uploadedFiles.value]
  if (!input && files.length === 0) return
  inputText.value = ''
  uploadedFiles.value.forEach(f => { if (f.preview) URL.revokeObjectURL(f.preview) })
  uploadedFiles.value = []
  if (inputRef.value) inputRef.value.style.height = 'auto'
  emit('send', input, files)
}

onUnmounted(() => {
  uploadedFiles.value.forEach(f => { if (f.preview) URL.revokeObjectURL(f.preview) })
})

// 点击外部关闭推理强度选择器
function onDocClick(e) {
  if (showReasoningPicker.value && !e.target.closest('.relative')) showReasoningPicker.value = false
}
document.addEventListener('click', onDocClick)
onUnmounted(() => document.removeEventListener('click', onDocClick))

defineExpose({ inputText, uploadedFiles, inputRef })
</script>

<template>
  <div class="px-4 py-3 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 relative"
       @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
    <!-- 拖拽高亮遮罩 -->
    <div v-if="isDragging" class="absolute inset-0 bg-green-500/10 border-2 border-dashed border-green-500 rounded-xl z-50 flex items-center justify-center pointer-events-none">
      <span class="text-green-600 dark:text-green-400 text-lg font-medium">📎 拖拽文件/图片到这里</span>
    </div>
    <!-- 对比模式标签 -->
    <div v-if="chat.compareModels && chat.compareModels.length >= 2" class="flex items-center gap-2 mb-2">
      <span class="text-xs px-2 py-1 bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400 rounded-full font-medium">
        🔬 对比模式 ({{ chat.compareModels.length }}个模型)
      </span>
      <span v-for="m in chat.compareModels" :key="m.id" class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded-full">{{ m.name }}</span>
      <button @click="chat.compareModels = []" class="text-[10px] text-red-400 hover:text-red-600">✕ 取消</button>
    </div>
    <div v-if="chat.uploading" class="mb-2 text-xs text-blue-500 flex items-center gap-1"><span class="animate-spin">⏳</span> 正在处理附件...</div>
    <div v-if="uploadedFiles.length > 0" class="flex flex-wrap gap-2 mb-2">
      <div v-for="(f, idx) in uploadedFiles" :key="idx" class="flex items-center gap-2 bg-gray-100 dark:bg-gray-700 rounded-lg px-3 py-1.5 text-xs">
        <img v-if="f.preview" :src="f.preview" class="w-6 h-6 object-cover rounded" />
        <span v-else class="text-base">{{ f.name.match(/\.(mp4|mov|avi|webm)$/i) ? '🎬' : '📄' }}</span>
        <span class="truncate max-w-[120px]">{{ f.name }}</span>
        <span class="text-gray-400">{{ chat.formatSize(f.size) }}</span>
        <button @click="removeFile(idx)" class="text-red-400 hover:text-red-600 font-bold">×</button>
      </div>
    </div>
    <div class="flex gap-3 items-end relative">
      <input ref="fileInput" type="file" multiple
        accept="image/*,video/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.html,.css,.yaml,.yml,.toml,.sh,.bash,.java,.go,.rs,.c,.cpp,.h,.rb,.php,.swift,.kt,.docx,.xlsx,.pptx,.zip,.tar,.gz"
        class="hidden" @change="handleFileSelect" />
      <button @click="triggerFileUpload()" class="p-3 rounded-xl border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition text-base" title="上传文件/图片/视频">📎</button>
      <!-- 推理强度选择器 -->
      <div class="relative">
        <button @click="showReasoningPicker = !showReasoningPicker" class="p-3 rounded-xl border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition text-base" :title="'推理强度: ' + currentReasoningLabel.label">{{ currentReasoningLabel.icon }}</button>
        <div v-if="showReasoningPicker" class="absolute bottom-full mb-2 left-0 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl shadow-lg py-1 min-w-[120px] z-50">
          <button v-for="r in reasoningLevels" :key="r.value" @click="selectReasoning(r.value)" 
            class="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 transition"
            :class="chat.reasoningEffort === r.value ? 'text-green-600 dark:text-green-400 font-medium' : 'text-gray-600 dark:text-gray-300'">
            <span>{{ r.icon }}</span><span>{{ r.label }}</span>
            <span v-if="chat.reasoningEffort === r.value" class="ml-auto">✓</span>
          </button>
        </div>
      </div>
      <!-- 联网搜索开关 -->
      <button @click="toggleSearch()" class="p-3 rounded-xl border transition text-base"
        :class="chat.searchEnabled ? 'border-green-400 bg-green-50 dark:bg-green-900/20 text-green-600 dark:text-green-400' : 'border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700'"
        :title="chat.searchEnabled ? '联网搜索已开启' : '联网搜索已关闭'">🌐</button>
      <textarea ref="inputRef" v-model="inputText" @keydown.enter.exact.prevent="send" @keydown.shift.enter="insertNewline"
        :placeholder="isEmptySession() ? '输入你的第一个问题…' : '问我任何问题…'" rows="1"
        @input="onInputCheckFileRef" @paste="onPaste"
        class="flex-1 rounded-xl px-4 py-3 text-sm bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-green-400 dark:focus:border-green-500 resize-none overflow-y-auto placeholder-gray-400 dark:placeholder-gray-500"
        :class="isEmptySession() ? 'border-2 border-green-300 dark:border-green-600' : 'border border-gray-300 dark:border-gray-500'">
      </textarea>
      <!-- @file 引用提示 -->
      <div v-if="showFileHint" class="absolute bottom-full mb-2 left-16 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-600 rounded-xl shadow-lg py-1 min-w-[240px] z-50">
        <div class="px-3 py-1.5 text-[10px] text-gray-400 border-b border-gray-100 dark:border-gray-700">📁 输入文件路径引用文件内容</div>
        <button v-for="item in fileHintItems" :key="item.label" @click="insertFileRef(item.label.replace('@', ''))"
          class="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 transition text-left">
          <span class="text-green-500">📄</span>
          <div>
            <div class="text-gray-700 dark:text-gray-300">{{ item.label }}</div>
            <div class="text-[10px] text-gray-400">{{ item.hint }}</div>
          </div>
        </button>
      </div>
      <button v-if="chat.loading" @click="chat.stopGeneration()" class="px-3 py-3 bg-red-500 hover:bg-red-600 text-white rounded-xl text-sm transition" title="停止生成">⏹</button>
      <button @click="send()" :disabled="!inputText.trim() && uploadedFiles.length===0" class="px-5 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm transition disabled:opacity-40">发送</button>
    </div>
  </div>
</template>
