<script setup>
// 蜂群协作看板（Kanban）：消费后端 /api/plugins/kanban/board，
// 渲染 Vermes 任务图（triage → todo → scheduled → ready → running → blocked → review → done）。
// 这是文章承诺的"可读蜂群任务图"在桌面端（frontend/Vue）的真正落地入口。
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const board = ref(null)          // { columns:[{name,tasks:[]}], tenants, assignees, latest_event_id, now }
const loading = ref(false)
const error = ref('')
let pollTimer = null

// 列的中文标签与配色（保持与任务图语义一致）
const COLUMN_META = {
  triage:    { label: '分诊',   dot: 'bg-gray-400' },
  todo:      { label: '待办',   dot: 'bg-slate-400' },
  scheduled: { label: '已排期', dot: 'bg-indigo-400' },
  ready:     { label: '就绪',   dot: 'bg-blue-400' },
  running:   { label: '执行中', dot: 'bg-green-500' },
  blocked:   { label: '阻塞',   dot: 'bg-red-500' },
  review:    { label: '复核',   dot: 'bg-amber-400' },
  done:      { label: '完成',   dot: 'bg-emerald-600' },
}

function colLabel(name) {
  return COLUMN_META[name]?.label || name
}
function colDot(name) {
  return COLUMN_META[name]?.dot || 'bg-gray-400'
}

// 任务卡显示标题：优先 title，否则取 body 首行
function taskTitle(t) {
  if (t.title) return t.title
  if (t.body) return t.body.split('\n')[0].slice(0, 80)
  return `(#${t.id})`
}

async function loadBoard() {
  loading.value = true
  error.value = ''
  try {
    // 与 chat-storage.js 一致：从 window 全局取 session token 注入请求头
    const headers = { 'Content-Type': 'application/json' }
    const t = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
    if (t) headers['X-Vermes-Session-Token'] = t
    const resp = await fetch('/api/plugins/kanban/board', { headers })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    board.value = data
  } catch (e) {
    error.value = e?.message || '看板加载失败'
    board.value = null
  } finally {
    loading.value = false
  }
}

function goChat() { router.push('/') }

function startPoll() {
  stopPoll()
  pollTimer = setInterval(loadBoard, 5000) // 5s 轮询，任务图变化时自动刷新
}
function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

onMounted(() => { loadBoard(); startPoll() })
onUnmounted(stopPoll)
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <!-- 顶部栏 -->
    <header class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0">
      <button
        @click="goChat"
        class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500"
        title="返回会话"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
      </button>
      <h1 class="text-base font-semibold flex items-center gap-2">
        <span>🐝</span> 蜂群协作看板
      </h1>
      <span class="text-xs text-gray-400">Vermes 任务图 · 多 Agent 并行执行可视化</span>

      <div class="ml-auto flex items-center gap-2">
        <button
          @click="loadBoard"
          :disabled="loading"
          class="px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white transition"
          title="刷新看板"
        >
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </header>

    <!-- 错误态 -->
    <div v-if="error" class="p-4 text-sm text-red-600 bg-red-50 dark:bg-red-950/30 m-4 rounded-lg">
      {{ error }}
    </div>

    <!-- 加载态 -->
    <div v-else-if="loading && !board" class="flex-1 flex items-center justify-center text-sm text-gray-400">
      正在加载蜂群任务图…
    </div>

    <!-- 空态 -->
    <div v-else-if="board && board.columns.every(c => c.tasks.length === 0)" class="flex-1 flex flex-col items-center justify-center text-center gap-3 px-6">
      <div class="text-5xl">🐝</div>
      <p class="text-sm text-gray-500 max-w-md">
        还没有蜂群任务。在对话里让 Vermes 用 <code class="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">kanban swarm</code>
        拆解复杂任务，任务图会自动出现在这里——每个 worker 的执行状态、依赖关系一目了然。
      </p>
      <button @click="goChat" class="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition">
        去对话创建蜂群任务
      </button>
    </div>

    <!-- 看板：8 列横向滚动 -->
    <div v-else-if="board" class="flex-1 overflow-x-auto overflow-y-hidden">
      <div class="flex gap-3 p-4 h-full min-w-max">
        <div
          v-for="col in board.columns"
          :key="col.name"
          class="flex flex-col w-64 shrink-0 bg-gray-100 dark:bg-gray-800 rounded-xl"
        >
          <!-- 列头 -->
          <div class="flex items-center gap-2 px-3 py-2.5 border-b border-gray-200 dark:border-gray-700">
            <span class="w-2.5 h-2.5 rounded-full" :class="colDot(col.name)"></span>
            <span class="text-sm font-medium">{{ colLabel(col.name) }}</span>
            <span class="ml-auto text-xs text-gray-400">{{ col.tasks.length }}</span>
          </div>
          <!-- 任务卡 -->
          <div class="flex-1 overflow-y-auto p-2 space-y-2">
            <div
              v-for="t in col.tasks"
              :key="t.id"
              class="p-2.5 rounded-lg bg-white dark:bg-gray-700 shadow-sm border border-gray-200 dark:border-gray-600 text-sm"
            >
              <div class="font-medium leading-snug">{{ taskTitle(t) }}</div>
              <div class="mt-1.5 flex items-center gap-2 text-xs text-gray-400">
                <span class="font-mono">#{{ t.id }}</span>
                <span v-if="t.assignee" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-600">@{{ t.assignee }}</span>
                <span v-if="t.status && t.status !== col.name" class="px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300">{{ t.status }}</span>
              </div>
            </div>
            <div v-if="col.tasks.length === 0" class="text-xs text-gray-400 text-center py-4">
              空
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
