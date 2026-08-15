<script setup>
// 3DStudio：3D 建模工作室 — 小白+AI 协同建模工作站
//
// 核心能力链：
// 1. 会话列表 — 所有建模会话（AI 生成 + 用户上传）
// 2. 3D 预览 — STL/STEP 渲染查看
// 3. 参数面板 — 显示尺寸参数，拖滑块实时重建
// 4. AI 协助 — 对话式修改优化（"壁厚加到5mm"→AI改参重建）
// 5. 文件上传 — 用户自有 STEP/STL 导入
// 6. 导出下载 — STEP/STL/3MF 文件下载

import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import ModelViewer from './ModelViewer.vue'

const router = useRouter()

// ── 状态 ──
const sessions = ref([])
const loading = ref(false)
const error = ref('')
const selectedSession = ref(null)
const selectedFile = ref(null)
const autoRotate = ref(false)
const transparentBg = ref(true)

// ── 参数面板 ──
const paramsForSession = ref([])
const sliderValues = ref({})
const rebuilding = ref(false)
const rebuildMsg = ref('')
let rebuildTimer = null

// ── AI 协助 ──
const aiPrompt = ref('')
const aiThinking = ref(false)
const aiHistory = ref([])  // [{role: 'user'|'assistant', text: '...'}]

// ── 上传 ──
const uploadRef = ref(null)
const uploading = ref(false)
const uploadMsg = ref('')

// ── 会话列表 ──
async function loadSessions() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch('/api/mfgcad/sessions')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    sessions.value = data.sessions || []
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
  const files = availableFiles.value
  if (files.length > 0) {
    selectedFile.value = files.find(f => f.ext === 'stl') || files[0]
  }
  loadParameters(s)
  // 加载 AI 历史
  aiHistory.value = s.ai_history || []
}

const availableFiles = computed(() => {
  if (!selectedSession.value?.files) return []
  return Object.entries(selectedSession.value.files).map(([key, url]) => {
    const filename = url.split('/').pop()
    const ext = filename.split('.').pop().toLowerCase()
    return { key, label: key.toUpperCase(), url, ext, filename }
  })
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

// ── 参数化重建 ──
async function loadParameters(s) {
  paramsForSession.value = []
  sliderValues.value = {}
  rebuildMsg.value = ''
  if (!s?.has_parameters) return
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${s.session_id}/parameters`)
    if (!resp.ok) return
    const data = await resp.json()
    const ps = data.parameters || {}
    const arr = Object.entries(ps).map(([name, p]) => ({
      name,
      value: p.value,
      min: p.min,
      max: p.max,
      step: p.step,
      unit: p.unit || '',
    }))
    paramsForSession.value = arr
    const sv = {}
    for (const p of arr) sv[p.name] = p.value
    sliderValues.value = sv
  } catch (e) {
    // 无参数则无滑块
  }
}

function onSliderInput() {
  rebuildMsg.value = ''
  if (rebuildTimer) clearTimeout(rebuildTimer)
  rebuildTimer = setTimeout(() => rebuild(), 600)
}

async function rebuild() {
  const sid = selectedSession.value?.session_id
  if (!sid || !selectedSession.value?.has_parameters || rebuilding.value) return
  rebuilding.value = true
  rebuildMsg.value = '重建中…'
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${sid}/rebuild`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters: sliderValues.value }),
    })
    const data = await resp.json()
    rebuildMsg.value = data.message || '重建完成'
    if (data.child_session && data.child_session.ok) {
      await loadSessions()
      const child = sessions.value.find(x => x.session_id === data.child_session.session_id)
      if (child) selectSession(child)
    }
  } catch (e) {
    rebuildMsg.value = `重建失败: ${e.message}`
  } finally {
    rebuilding.value = false
  }
}

// ── AI 协助修改 ──
async function aiAssist() {
  const prompt = aiPrompt.value.trim()
  if (!prompt || aiThinking.value) return
  const sid = selectedSession.value?.session_id
  if (!sid) return

  aiThinking.value = true
  aiHistory.value.push({ role: 'user', text: prompt })
  aiPrompt.value = ''

  try {
    // 调后端 AI 协助端点
    const resp = await fetch(`/api/mfgcad/sessions/${sid}/ai-assist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    })
    const data = await resp.json()
    aiHistory.value.push({ role: 'assistant', text: data.message || '处理完成' })

    // 如果 AI 重建了模型，刷新会话
    if (data.child_session || data.rebuilt) {
      await loadSessions()
      const child = sessions.value.find(x => x.session_id === (data.child_session?.session_id || sid))
      if (child) selectSession(child)
    }
  } catch (e) {
    aiHistory.value.push({ role: 'assistant', text: `❌ ${e.message}` })
  } finally {
    aiThinking.value = false
  }
}

// ── 文件上传 ──
async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  uploading.value = true
  uploadMsg.value = `上传 ${file.name} 中…`

  try {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('name', file.name.replace(/\.(step|stp|stl|3mf)$/i, ''))

    const resp = await fetch('/api/mfgcad/upload', {
      method: 'POST',
      body: formData,
    })
    const data = await resp.json()
    if (data.session_id) {
      uploadMsg.value = `✅ ${file.name} 已导入`
      await loadSessions()
      const newSession = sessions.value.find(x => x.session_id === data.session_id)
      if (newSession) selectSession(newSession)
    } else {
      uploadMsg.value = `❌ ${data.detail || '上传失败'}`
    }
  } catch (e) {
    uploadMsg.value = `❌ ${e.message}`
  } finally {
    uploading.value = false
    if (uploadRef.value) uploadRef.value.value = ''
  }
}

// ── 下载文件 ──
function downloadFile(file) {
  if (!file?.url) return
  const a = document.createElement('a')
  a.href = file.url
  a.download = file.filename || 'model'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

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
      <div class="flex items-center gap-3">
        <!-- 上传自有文件 -->
        <button @click="uploadRef?.click()" class="text-xs px-3 py-1.5 rounded-lg bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 transition">
          📂 打开文件
        </button>
        <input ref="uploadRef" type="file" accept=".step,.stp,.stl,.3mf" @change="onUpload" class="hidden" />
        <button @click="goChat" class="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200">
          ← 返回对话
        </button>
      </div>
    </div>

    <!-- 上传提示 -->
    <div v-if="uploadMsg" class="px-4 py-1.5 text-xs text-center" :class="uploadMsg.startsWith('✅') ? 'bg-green-50 text-green-600' : uploadMsg.startsWith('❌') ? 'bg-red-50 text-red-600' : 'bg-blue-50 text-blue-600'">
      {{ uploadMsg }}
    </div>

    <div class="flex flex-1 overflow-hidden">
      <!-- 左侧：会话列表 -->
      <div class="w-64 border-r border-gray-200 dark:border-gray-700 overflow-y-auto bg-white dark:bg-gray-800">
        <div v-if="loading" class="p-4 text-center text-sm text-gray-400">加载中…</div>
        <div v-else-if="error" class="p-4 text-sm text-red-500">{{ error }}</div>
        <div v-else-if="sessions.length === 0" class="p-4 text-center text-sm text-gray-400">
          暂无设计会话<br>
          <span class="text-xs">在对话中描述零件，或点击「打开文件」上传</span>
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
              <span v-if="s.has_parameters" class="text-xs text-blue-500">🎚️</span>
              <span class="text-xs text-gray-400 ml-auto">{{ fmtTime(s.ts) }}</span>
            </div>
            <p class="text-xs text-gray-600 dark:text-gray-300 truncate">{{ s.request }}</p>
          </div>
        </div>
      </div>

      <!-- 右侧：建模工作站 -->
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
              <!-- 下载按钮 -->
              <button
                v-if="selectedFile"
                @click="downloadFile(selectedFile)"
                class="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition"
              >
                ⬇️ 下载
              </button>
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

          <!-- 参数面板 + AI 协助（横向分栏） -->
          <div class="flex flex-1 overflow-hidden">
            <!-- 中间：3D 查看器 -->
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

            <!-- 右侧：参数面板 + AI 协助 -->
            <div class="w-80 border-l border-gray-200 dark:border-gray-700 flex flex-col bg-white dark:bg-gray-800">
              <!-- 参数面板 -->
              <div
                v-if="paramsForSession.length"
                class="px-4 py-3 border-b border-gray-200 dark:border-gray-700"
              >
                <div class="flex items-center gap-2 mb-3">
                  <span class="text-xs font-medium text-gray-500 dark:text-gray-400">🎚️ 参数微调</span>
                  <span v-if="rebuilding" class="text-xs text-blue-500">🔄 重建中…</span>
                  <span v-else-if="rebuildMsg" class="text-xs text-green-600 truncate">{{ rebuildMsg }}</span>
                </div>
                <div class="space-y-3 max-h-48 overflow-y-auto">
                  <div v-for="p in paramsForSession" :key="p.name" class="flex flex-col">
                    <div class="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                      <span class="font-mono">{{ p.name }}</span>
                      <span class="tabular-nums">{{ sliderValues[p.name] }} {{ p.unit }}</span>
                    </div>
                    <input
                      type="range"
                      :min="p.min"
                      :max="p.max"
                      :step="p.step"
                      v-model.number="sliderValues[p.name]"
                      @input="onSliderInput"
                      class="w-full accent-green-500"
                    />
                  </div>
                </div>
              </div>

              <!-- 无参数提示 -->
              <div
                v-else-if="selectedSession && !selectedSession.has_parameters"
                class="px-4 py-3 border-b border-gray-200 dark:border-gray-700"
              >
                <p class="text-xs text-gray-400">
                  ⚠️ 此会话无可调参数。AI 生成的模型需要引擎落盘 build123d 源码才能参数化。
                  可用 AI 协助重新描述修改需求。
                </p>
              </div>

              <!-- AI 协助面板 -->
              <div class="flex-1 flex flex-col">
                <div class="px-4 py-2 border-b border-gray-200 dark:border-gray-700">
                  <span class="text-xs font-medium text-gray-500 dark:text-gray-400">🤖 AI 协助修改</span>
                </div>

                <!-- 对话历史 -->
                <div class="flex-1 overflow-y-auto px-4 py-2 space-y-2">
                  <div v-if="aiHistory.length === 0" class="text-xs text-gray-400 text-center py-4">
                    描述你要修改的内容，AI 自动调参重建<br>
                    <span class="text-gray-300">如："壁厚改成5mm"、"高度增加到120"</span>
                  </div>
                  <div
                    v-for="(msg, i) in aiHistory"
                    :key="i"
                    class="text-xs rounded-lg px-3 py-2"
                    :class="msg.role === 'user'
                      ? 'bg-green-50 dark:bg-green-900/20 text-gray-700 dark:text-gray-200 ml-4'
                      : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 mr-4'"
                  >
                    {{ msg.text }}
                  </div>
                  <div v-if="aiThinking" class="text-xs text-blue-500 px-3">🤔 AI 思考中…</div>
                </div>

                <!-- 输入框 -->
                <div class="px-3 py-2 border-t border-gray-200 dark:border-gray-700">
                  <div class="flex items-center gap-2">
                    <input
                      v-model="aiPrompt"
                      @keydown.enter="aiAssist"
                      :disabled="aiThinking"
                      placeholder="描述修改需求…"
                      class="flex-1 text-xs px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-green-500"
                    />
                    <button
                      @click="aiAssist"
                      :disabled="aiThinking || !aiPrompt.trim()"
                      class="text-xs px-3 py-2 rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-50 disabled:cursor-not-allowed transition"
                    >
                      发送
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 空状态 -->
        <div v-else class="flex items-center justify-center h-full">
          <div class="text-center">
            <div class="text-6xl mb-4">🏭</div>
            <h2 class="text-lg font-medium text-gray-600 dark:text-gray-300 mb-2">3D 建模工作室</h2>
            <p class="text-sm text-gray-400 max-w-xs mb-4">
              AI 建模 + 手动改参 + AI 协助优化，小白也能出专业级 3D 图
            </p>
            <div class="flex items-center justify-center gap-3">
              <button @click="goChat" class="px-4 py-2 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 transition">
                💬 对话建模
              </button>
              <button @click="uploadRef?.click()" class="px-4 py-2 text-sm rounded-lg bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100 transition">
                📂 打开文件
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
