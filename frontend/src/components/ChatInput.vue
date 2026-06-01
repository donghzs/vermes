<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'
import { toast } from '../utils/toast'

const chat = useChatStore()

const inputText = ref('')
const fileInput = ref(null)
const uploadedFiles = ref([])
const inputRef = ref(null)
const isDragging = ref(false)

const emit = defineEmits(['send'])

// ── 常量 ──
const MAX_SINGLE_FILE = 20 * 1024 * 1024   // 20MB
const MAX_TOTAL_SIZE = 50 * 1024 * 1024     // 50MB

// ── 文件上传 ──
function triggerFileUpload() { fileInput.value?.click() }

function addFiles(fileList) {
  const files = Array.from(fileList)
  let totalSize = uploadedFiles.value.reduce((s, f) => s + f.size, 0)
  for (const f of files) {
    if (f.size > MAX_SINGLE_FILE) {
      toast.warning(`文件 ${f.name} 超过 20MB`)
      continue
    }
    if (totalSize + f.size > MAX_TOTAL_SIZE) {
      toast.warning(`附件总大小超过 50MB`)
      break
    }
    totalSize += f.size
    uploadedFiles.value.push({
      name: f.name,
      size: f.size,
      file: f,
      preview: f.type.startsWith('image/') ? URL.createObjectURL(f) : null
    })
  }
}

function handleFileSelect(e) {
  addFiles(e.target.files)
  e.target.value = ''
}

// ── #1 拖拽上传 ──
function onDragEnter(e) {
  e.preventDefault()
  isDragging.value = true
}
function onDragOver(e) {
  e.preventDefault()
  isDragging.value = true
}
function onDragLeave(e) {
  e.preventDefault()
  // 只在离开容器时取消高亮
  if (!e.currentTarget.contains(e.relatedTarget)) {
    isDragging.value = false
  }
}
function onDrop(e) {
  e.preventDefault()
  isDragging.value = false
  if (e.dataTransfer?.files?.length > 0) {
    addFiles(e.dataTransfer.files)
  }
}

function removeFile(idx) {
  const f = uploadedFiles.value[idx]
  if (f?.preview) URL.revokeObjectURL(f.preview)
  uploadedFiles.value.splice(idx, 1)
}

// ── 多行输入 ──
function insertNewline(e) {
  e.preventDefault()
  inputText.value += '\n'
}

function autoResize(e) {
  const el = e.target
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 200) + 'px'
}

// ── 发送 ──
async function send() {
  const input = inputText.value.trim()
  const files = [...uploadedFiles.value]
  if ((!input && files.length === 0) || chat.loading) return
  inputText.value = ''
  uploadedFiles.value.forEach(f => { if (f.preview) URL.revokeObjectURL(f.preview) })
  uploadedFiles.value = []
  // 重置 textarea 高度
  if (inputRef.value) {
    inputRef.value.style.height = 'auto'
  }
  emit('send', input, files)
}

// 暴露给父组件
defineExpose({ inputText, uploadedFiles, inputRef })
</script>

<template>
  <div class="px-4 py-3 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 relative"
       @dragenter="onDragEnter" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
    <!-- 拖拽高亮遮罩 -->
    <div v-if="isDragging" class="absolute inset-0 bg-green-500/10 border-2 border-dashed border-green-500 rounded-xl z-50 flex items-center justify-center pointer-events-none">
      <span class="text-green-600 dark:text-green-400 text-lg font-medium">📎 拖拽文件到这里</span>
    </div>
    <!-- P3-8: 对比模式标签 -->
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
      <!-- #8 accept 补全 -->
      <input ref="fileInput" type="file" multiple
        accept="image/*,video/*,.pdf,.txt,.md,.csv,.json,.py,.js,.ts,.html,.css,.yaml,.yml,.toml,.sh,.bash,.java,.go,.rs,.c,.cpp,.h,.rb,.php,.swift,.kt,.docx,.xlsx,.pptx,.zip,.tar,.gz"
        class="hidden" @change="handleFileSelect" />
      <button @click="triggerFileUpload()" class="p-3 rounded-xl border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition text-base" title="上传文件/图片/视频">📎</button>
      <textarea ref="inputRef" v-model="inputText" @keydown.enter.exact.prevent="send" @keydown.shift.enter="insertNewline"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行..." rows="1"
        @input="autoResize"
        class="flex-1 border border-gray-300 dark:border-gray-600 rounded-xl px-4 py-3 text-sm bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500 resize-none overflow-y-auto"></textarea>
      <button v-if="chat.loading" @click="chat.stopGeneration()" class="px-5 py-3 bg-red-500 hover:bg-red-600 text-white rounded-xl text-sm transition">停止</button>
      <button v-else @click="send()" :disabled="!inputText.trim() && uploadedFiles.length===0" class="px-5 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm transition disabled:opacity-40">发送</button>
    </div>
  </div>
</template>
