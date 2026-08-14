<script setup>
// 3DStudio：3D 建模工作室面板（P2）。
//
// 左侧：mfgcad 设计会话列表（从 /api/mfgcad/sessions 加载）
// 右侧：选中会话的文件 → ModelViewer 渲染
//
// 对标 ScholarForgePanel 的布局模式：顶部标题 + 左列表 + 右内容区。

import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import ModelViewer from './ModelViewer.vue'

const router = useRouter()

const sessions = ref([])
const loading = ref(false)
const error = ref('')
const selectedSession = ref(null)
const selectedFile = ref(null) // { key: 'stl', url: '/api/mfgcad/files/...' }
const autoRotate = ref(false)
const transparentBg = ref(true)

async function loadSessions() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch('/api/mfgcad/sessions')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    sessions.value = data.sessions || []
    // 自动选最新的成功会话
    const firstOk = sessions.value.find(s => s.ok && Object.keys(s.files || {}).length > 0)
    if (firstOk && !selectedSession.value) {
      selectSession(firstOk)
    }
  } catch (e) {
    error.value = `加载会话列表失败: ${e.message}`
  } finally {
    loading.value = false
  }
}

function selectSession(s) {
  selectedSession.value = s
  selectedFile.value = null
  // 自动选第一个可用文件
  const files = availableFiles.value
  if (files.length > 0) {
    selectedFile.value = files[0]
  }
}

const availableFiles = computed(() => {
  if (!selectedSession.value?.files) return []
  const sid = selectedSession.value.session_id
  return Object.entries(selectedSession.value.files).map(([key, path]) => {
    // 从完整路径提取文件名
    const filename = path.split('/').pop().split('\\').pop()
    return {
      key,
      label: key.toUpperCase(),
      url: `/api/mfgcad/files/${sid}/${filename}`,
      ext: filename.split('.').pop().toLowerCase(),
    }
  }).filter(f => ['stl', 'glb', 'gltf', 'png', 'jpg', 'jpeg'].includes(f.ext))
})

function selectFile(f) {
  selectedFile.value = f
}

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

function fmtVolume(v) {
  if (v == null) return ''
  return `${v.toFixed(1)} mm³`
}

function goChat() { router.push('/') }

onMounted(loadSessions)
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <!-- 顶部标题栏 -->
    <div class="flex items-center justify-between px-4 py-3 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-3">
        <h1 class="text-lg font-semibold text-gray-800 dark:text-gray-200">🏭 3D 建模工作室</h1>
        <button @click="loadSessions" class="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-600 dark:text-gray-300">
          ↻ 刷新
        </button>
      </div>
      <button @click="goChat" class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
        ← 返回对话
      </button>
    </div>

    <div class="flex flex-1 overflow-hidden">
      <!-- 左侧：会话列表 -->
      <div class="w-64 border-r border-gray-200 dark:border-gray-700 overflow-y-auto bg-white dark:bg-gray-800">
        <div v-if="loading" class="p-4 text-center text-sm text-gray-400">加载中…</div>
        <div v-else-if="error" class="p-4 text-sm text-red-500">{{ error }}</div>
        <div v-else-if="sessions.length === 0" class="p-4 text-center text-sm text-gray-400">
          暂无设计会话<br>
          <span class="text-xs">在对话中调用 mfg_text_to_cad 开始建模</span>
        </div>
        <div v-else class="py-2">
          <div
            v-for="s in sessions"
            :key="s.session_id"
            @click="selectSession(s)"
            class="px-3 py-2 cursor-pointer border-l-2 transition"
            :class="selectedSession?.session_id === s.session_id
              ? 'border-green-500 bg-green-50 dark:bg-green-900/20'
              : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/50'"
          >
            <div class="flex items-center gap-2 mb-1">
              <span class="text-xs" :class="s.ok ? 'text-green-600' : 'text-red-500'">
                {{ s.ok ? '✅' : '❌' }}
              </span>
              <span class="text-xs text-gray-400">{{ s.backend }}</span>
              <span class="text-xs text-gray-400 ml-auto">{{ fmtTime(s.ts) }}</span>
            </div>
            <p class="text-xs text-gray-600 dark:text-gray-300 truncate">{{ s.request }}</p>
          </div>
        </div>
      </div>

      <!-- 右侧：模型查看器 -->
      <div class="flex-1 flex flex-col">
        <template v-if="selectedSession">
          <!-- 会话信息 + 文件切换 -->
          <div class="px-4 py-2 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
            <p class="text-sm text-gray-600 dark:text-gray-300 mb-2">{{ selectedSession.request }}</p>
            <div class="flex items-center gap-4 flex-wrap">
              <!-- 文件标签 -->
              <div class="flex items-center gap-1">
                <button
                  v-for="f in availableFiles"
                  :key="f.key"
                  @click="selectFile(f)"
                  class="px-2 py-1 text-xs rounded transition"
                  :class="selectedFile?.key === f.key
                    ? 'bg-green-500 text-white'
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
                >
                  {{ f.label }}
                </button>
              </div>
              <!-- 体积 -->
              <span v-if="selectedSession.volume_mm3" class="text-xs text-gray-400">
                📐 {{ fmtVolume(selectedSession.volume_mm3) }}
              </span>
              <!-- QA -->
              <span v-if="selectedSession.qa?.passed" class="text-xs text-gray-400">
                ✅ 校验 {{ selectedSession.qa.passed }} 项
              </span>
              <!-- 控制按钮 -->
              <div class="ml-auto flex items-center gap-2">
                <button
                  @click="autoRotate = !autoRotate"
                  class="text-xs px-2 py-1 rounded transition"
                  :class="autoRotate ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'"
                >
                  {{ autoRotate ? '⏸ 停止旋转' : '🔄 自动旋转' }}
                </button>
                <button
                  @click="transparentBg = !transparentBg"
                  class="text-xs px-2 py-1 rounded transition"
                  :class="!transparentBg ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300'"
                >
                  {{ transparentBg ? '🌙 暗背景' : '☀️ 透明' }}
                </button>
              </div>
            </div>
          </div>

          <!-- 查看器 -->
          <div class="flex-1 p-4">
            <ModelViewer
              v-if="selectedFile"
              :src="selectedFile.url"
              :auto-rotate="autoRotate"
              :transparent-bg="transparentBg"
            />
            <div v-else class="flex items-center justify-center h-full">
              <p class="text-sm text-gray-400">该会话无可查看的 3D 文件</p>
            </div>
          </div>
        </template>

        <!-- 空状态 -->
        <div v-else class="flex items-center justify-center h-full">
          <div class="text-center">
            <div class="text-6xl mb-4">🏭</div>
            <h2 class="text-lg font-medium text-gray-600 dark:text-gray-300 mb-2">3D 建模工作室</h2>
            <p class="text-sm text-gray-400 max-w-xs">
              在对话中描述你要建模的零件，调用 mfg_text_to_cad 生成 3D 模型后，可在此查看和管理所有设计会话。
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
