<script setup>
import { ref, onMounted, computed } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
const { confirm } = useConfirm()

const documents = ref([])
const loading = ref(false)
const searchQuery = ref('')
const searchResults = ref([])
const searching = ref(false)
const showSearch = ref(false)
const expandedDoc = ref(null)
const chunks = ref({})
const stats = ref([])
const showStats = ref(false)
const uploadRef = ref(null)
const uploading = ref(false)
const uploadMsg = ref('')

const totalCount = computed(() => documents.value.length)

async function loadDocuments() {
  loading.value = true
  try {
    const data = await api.ragListDocuments()
    documents.value = data.documents || []
  } catch (e) {
    console.error('Failed to load documents:', e)
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  try {
    const data = await api.ragStats()
    stats.value = data.documents || []
    showStats.value = true
  } catch (e) {
    console.error('Failed to load stats:', e)
  }
}

async function toggleChunks(docId) {
  if (expandedDoc.value === docId) {
    expandedDoc.value = null
    return
  }
  expandedDoc.value = docId
  if (!chunks.value[docId]) {
    try {
      const data = await api.ragGetChunks(docId)
      chunks.value[docId] = data.chunks || []
    } catch (e) {
      chunks.value[docId] = []
    }
  }
}

async function deleteDoc(docId) {
  if (!await confirm({ title: '删除文档', message: '确定删除这个文档吗？关联的检索 chunks 也会被删除。', confirmText: '删除', danger: true })) return
  try {
    await api.ragDelete(docId)
    documents.value = documents.value.filter(d => d.id !== docId)
    delete chunks.value[docId]
  } catch (e) {
    toast.error('删除失败: ' + e.message)
  }
}

async function search() {
  const q = searchQuery.value.trim()
  if (!q) return
  searching.value = true
  showSearch.value = true
  try {
    const data = await api.ragSearch(q, 5)
    searchResults.value = data.results || []
  } catch (e) {
    searchResults.value = []
  } finally {
    searching.value = false
  }
}

async function onFileUpload(event) {
  const files = event.target.files
  if (!files || files.length === 0) return
  uploading.value = true
  uploadMsg.value = ''
  
  for (const file of files) {
    try {
      const b64 = await fileToBase64(file)
      const fileType = file.name.split('.').pop().toLowerCase()
      const result = await api.ragIngestUpload(file.name, b64, fileType)
      if (result.error) {
        uploadMsg.value += `❌ ${file.name}: ${result.error}\n`
      } else {
        uploadMsg.value += `✅ ${file.name}: ${result.chunks_count || result.total_chunks || '?'} chunks\n`
      }
    } catch (e) {
      uploadMsg.value += `❌ ${file.name}: ${e.message}\n`
    }
  }
  
  uploading.value = false
  await loadDocuments()
  // Clear input so same file can be re-selected
  if (uploadRef.value) uploadRef.value.value = ''
  // Auto-clear message after 5s
  setTimeout(() => { uploadMsg.value = '' }, 5000)
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result
      // Strip data URL prefix: data:...;base64,
      const base64 = result.includes(',') ? result.split(',')[1] : result
      resolve(base64)
    }
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function formatDate(ts) {
  if (!ts) return '-'
  try {
    const d = new Date(ts * 1000)
    return d.toLocaleDateString('zh-CN') + ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch { return '-' }
}

function fileIcon(type) {
  const icons = { pdf: '📄', docx: '📝', xlsx: '📊', pptx: '📊', md: '📋', txt: '📋' }
  return icons[type] || '📄'
}

onMounted(() => {
  loadDocuments()
})
</script>

<template>
  <div class="space-y-4">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">📚</span>
        <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">知识库</h3>
        <span class="text-xs text-gray-400">({{ totalCount }} 个文档)</span>
      </div>
      <div class="flex items-center gap-2">
        <button @click="loadStats" class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">📊 统计</button>
      </div>
    </div>

    <!-- Upload area -->
    <div class="border-2 border-dashed border-gray-200 dark:border-gray-700 rounded-xl p-4 text-center hover:border-green-400 transition-colors cursor-pointer"
         @click="uploadRef?.click()">
      <input ref="uploadRef" type="file" multiple class="hidden"
             accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.py,.js,.ts,.vue,.json,.csv"
             @change="onFileUpload" />
      <div v-if="!uploading" class="text-xs text-gray-400">
        <span class="text-base">📎</span> 点击或拖拽文件上传
        <div class="text-[10px] text-gray-400 mt-1">支持 PDF / DOCX / XLSX / PPTX / TXT / MD / 代码文件</div>
      </div>
      <div v-else class="text-xs text-blue-500">
        <span class="animate-pulse">⏳ 上传中...</span>
      </div>
    </div>
    <!-- Upload message -->
    <div v-if="uploadMsg" class="text-xs whitespace-pre-line bg-gray-50 dark:bg-gray-800 rounded-lg p-2 text-gray-600 dark:text-gray-400">
      {{ uploadMsg }}
    </div>

    <!-- Search bar -->
    <div class="flex gap-2">
      <input v-model="searchQuery" type="text" placeholder="检索知识库..."
             class="flex-1 px-3 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-green-400"
             @keyup.enter="search" />
      <button @click="search" :disabled="searching"
              class="px-3 py-1.5 text-xs rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-50">
        {{ searching ? '⏳' : '🔍' }}
      </button>
    </div>

    <!-- Search results -->
    <div v-if="showSearch" class="space-y-2">
      <div class="text-xs text-gray-400 flex items-center justify-between">
        <span>检索结果 ({{ searchResults.length }})</span>
        <button @click="showSearch = false; searchResults = []" class="text-gray-400 hover:text-gray-600">✕</button>
      </div>
      <div v-for="(result, i) in searchResults" :key="i"
           class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 text-xs">
        <div class="flex items-center gap-2 mb-1">
          <span class="text-gray-400">{{ fileIcon(result.file_type) }}</span>
          <span class="font-medium text-gray-700 dark:text-gray-300 truncate">{{ result.doc_name || result.filename || '未知' }}</span>
          <span v-if="result.score" class="ml-auto text-[10px] text-green-500">score: {{ result.score.toFixed(2) }}</span>
        </div>
        <div class="text-gray-500 dark:text-gray-400 leading-relaxed">{{ result.content?.substring(0, 200) }}...</div>
      </div>
      <div v-if="searchResults.length === 0 && !searching" class="text-xs text-gray-400 text-center py-2">
        未找到匹配内容
      </div>
    </div>

    <!-- Stats panel -->
    <div v-if="showStats" class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-medium text-gray-600 dark:text-gray-400">📊 文档使用统计</span>
        <button @click="showStats = false" class="text-gray-400 hover:text-gray-600 text-xs">✕</button>
      </div>
      <div v-for="s in stats" :key="s.doc_id"
           class="flex items-center justify-between text-xs py-1 border-b border-gray-100 dark:border-gray-700 last:border-0">
        <span class="text-gray-600 dark:text-gray-400 truncate flex-1">{{ s.doc_name }}</span>
        <span class="text-green-500 ml-2">{{ s.referenced_count || 0 }} 次引用</span>
      </div>
      <div v-if="stats.length === 0" class="text-xs text-gray-400 text-center py-1">暂无统计数据</div>
    </div>

    <!-- Document list -->
    <div class="space-y-1.5">
      <div v-for="doc in documents" :key="doc.id"
           class="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-lg overflow-hidden">
        <!-- Doc header -->
        <div class="flex items-center gap-2 px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-750 cursor-pointer"
             @click="toggleChunks(doc.id)">
          <span class="text-sm flex-shrink-0">{{ fileIcon(doc.file_type) }}</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate">{{ doc.name || doc.filename }}</div>
            <div class="text-[10px] text-gray-400">
              {{ doc.chunks_count || doc.chunk_count || '?' }} chunks · {{ formatDate(doc.created_at) }}
            </div>
          </div>
          <button @click.stop="deleteDoc(doc.id)"
                  class="text-gray-300 hover:text-red-500 text-xs flex-shrink-0">🗑</button>
          <span class="text-gray-400 text-xs flex-shrink-0">{{ expandedDoc === doc.id ? '▼' : '▶' }}</span>
        </div>
        <!-- Chunks preview -->
        <div v-if="expandedDoc === doc.id" class="border-t border-gray-100 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 max-h-48 overflow-y-auto">
          <div v-for="(chunk, i) in (chunks[doc.id] || [])" :key="i"
               class="px-3 py-1.5 text-[11px] text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-700 last:border-0">
            <span class="text-gray-400 mr-1">[{{ i + 1 }}]</span>
            {{ chunk.content?.substring(0, 150) }}...
          </div>
          <div v-if="!chunks[doc.id] || chunks[doc.id].length === 0" class="px-3 py-2 text-[11px] text-gray-400">
            加载中...
          </div>
        </div>
      </div>
      <!-- Empty state -->
      <div v-if="!loading && documents.length === 0" class="text-center py-8 text-xs text-gray-400">
        <div class="text-3xl mb-2">📚</div>
        <div>知识库为空，上传文件开始构建</div>
      </div>
      <!-- Loading -->
      <div v-if="loading" class="text-center py-4 text-xs text-gray-400">
        <span class="animate-pulse">加载中...</span>
      </div>
    </div>
  </div>
</template>
