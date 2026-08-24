<script setup>
// 🐝 蜂群协作看板 — Vermes 任务图可视化
// 后端: plugins/kanban/dashboard/plugin_api.py (36 路由)
// 实时: WebSocket /events 替代轮询，仅在 WS 断开时回退到 5s 轮询
// 交互: 点击任务卡展开详情抽屉、拖拽移列、新建任务、手动 dispatch
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

// ── 状态 ──
const board = ref(null)
const loading = ref(false)
const error = ref('')
const selectedTask = ref(null)     // 详情抽屉
const taskDetail = ref(null)       // GET /tasks/{id} 完整数据
const detailLoading = ref(false)
const showNewTask = ref(false)     // 新建任务弹窗
const newTask = ref({ title: '', body: '', assignee: '', priority: 0 })
const creating = ref(false)
const dragTask = ref(null)         // 当前拖拽的 task
const dragOverCol = ref(null)      // 拖拽悬停的列
const ws = ref(null)               // WebSocket 连接
const wsConnected = ref(false)
const workers = ref([])            // 活跃 worker 列表
const showStats = ref(false)       // 统计面板
const statsData = ref(null)
let pollTimer = null
let workerTimer = null

// ── 列定义 ──
const COLUMN_META = {
  triage:    { label: '分诊',   dot: 'bg-gray-400',    empty: '等待分诊的任务' },
  todo:      { label: '待办',   dot: 'bg-slate-400',   empty: '没有待办任务' },
  scheduled: { label: '已排期', dot: 'bg-indigo-400',  empty: '没有排期任务' },
  ready:     { label: '就绪',   dot: 'bg-blue-400',    empty: '没有就绪任务' },
  running:   { label: '执行中', dot: 'bg-green-500',   empty: '没有执行中的任务' },
  blocked:   { label: '阻塞',   dot: 'bg-red-500',     empty: '没有阻塞任务' },
  review:    { label: '复核',   dot: 'bg-amber-400',   empty: '没有待复核任务' },
  done:      { label: '完成',   dot: 'bg-emerald-600', empty: '还没有完成的任务' },
}
function colLabel(n) { return COLUMN_META[n]?.label || n }
function colDot(n)   { return COLUMN_META[n]?.dot || 'bg-gray-400' }
function colEmpty(n) { return COLUMN_META[n]?.empty || '空' }

// ── 统计 ──
const stats = computed(() => {
  if (!board.value) return { total: 0, running: 0, blocked: 0, done: 0, ready: 0, review: 0 }
  let total = 0, running = 0, blocked = 0, done = 0, ready = 0, review = 0
  for (const col of board.value.columns) {
    for (const _t of col.tasks) {
      total++
      if (col.name === 'running') running++
      else if (col.name === 'blocked') blocked++
      else if (col.name === 'done') done++
      else if (col.name === 'ready') ready++
      else if (col.name === 'review') review++
    }
  }
  return { total, running, blocked, done, ready, review }
})

// ── 工具函数 ──
function taskTitle(t) {
  if (t.title) return t.title
  if (t.body) return t.body.split('\n')[0].slice(0, 80)
  return `#${t.id}`
}
function warnBadge(w) {
  if (!w) return null
  const sev = w.highest_severity
  if (sev === 'critical') return { text: `⛔ ${w.count}`, cls: 'bg-red-100 text-red-600 dark:bg-red-900/40 dark:text-red-300' }
  if (sev === 'warning')  return { text: `⚠️ ${w.count}`, cls: 'bg-amber-100 text-amber-600 dark:bg-amber-900/40 dark:text-amber-300' }
  return { text: `ℹ️ ${w.count}`, cls: 'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300' }
}
function depText(t) {
  const parts = []
  const lc = t.link_counts || {}
  if (lc.parents) parts.push(`⛓ ${lc.parents}`)
  if (t.progress) parts.push(`▸ ${t.progress.done}/${t.progress.total}`)
  return parts
}
function skillTags(t) {
  if (!t.skills) return []
  return String(t.skills).split(',').map(s => s.trim()).filter(Boolean).slice(0, 2)
}
function failText(t) {
  const c = t.consecutive_failures || 0
  return c > 0 ? `↻ ${c}` : null
}
function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const now = new Date()
  const diff = (now - d) / 1000
  if (diff < 60) return `${Math.floor(diff)}秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)}分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)}小时前`
  return d.toLocaleDateString('zh-CN')
}
function fmtDuration(secs) {
  if (!secs) return ''
  if (secs < 60) return `${Math.floor(secs)}s`
  if (secs < 3600) return `${Math.floor(secs / 60)}m${Math.floor(secs % 60)}s`
  return `${Math.floor(secs / 3600)}h${Math.floor((secs % 3600) / 60)}m`
}

// ── 鉴权头 ──
function authHeaders() {
  const h = { 'Content-Type': 'application/json' }
  const t = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
  if (t) h['X-Vermes-Session-Token'] = t
  return h
}

// ── 加载看板 ──
async function loadBoard() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch('/api/plugins/kanban/board', { headers: authHeaders() })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    board.value = await resp.json()
  } catch (e) {
    error.value = e?.message || '看板加载失败'
    board.value = null
  } finally {
    loading.value = false
  }
}

// ── 任务详情 ──
async function openTaskDetail(t) {
  selectedTask.value = t
  taskDetail.value = null
  detailLoading.value = true
  try {
    const resp = await fetch(`/api/plugins/kanban/tasks/${encodeURIComponent(t.id)}`, { headers: authHeaders() })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    taskDetail.value = await resp.json()
  } catch (e) {
    console.error('[Kanban] load task detail:', e)
  } finally {
    detailLoading.value = false
  }
}
function closeDetail() {
  selectedTask.value = null
  taskDetail.value = null
}

// ── 新建任务 ──
async function createTask() {
  if (!newTask.value.title.trim()) return
  creating.value = true
  try {
    const resp = await fetch('/api/plugins/kanban/tasks', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        title: newTask.value.title,
        body: newTask.value.body || null,
        assignee: newTask.value.assignee || null,
        priority: newTask.value.priority || 0,
      }),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    showNewTask.value = false
    newTask.value = { title: '', body: '', assignee: '', priority: 0 }
    await loadBoard()
  } catch (e) {
    console.error('[Kanban] create task:', e)
    error.value = `创建失败: ${e.message}`
  } finally {
    creating.value = false
  }
}

// ── 拖拽移列 ──
function onDragStart(e, t) {
  dragTask.value = t
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', t.id)
}
function onDragOver(e, colName) {
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  dragOverCol.value = colName
}
function onDragLeave(colName) {
  if (dragOverCol.value === colName) dragOverCol.value = null
}
async function onDrop(e, colName) {
  e.preventDefault()
  dragOverCol.value = null
  const t = dragTask.value
  dragTask.value = null
  if (!t || t.status === colName) return
  // running 不允许直接拖入（需走 dispatcher claim 路径）
  if (colName === 'running') return
  try {
    const resp = await fetch(`/api/plugins/kanban/tasks/${encodeURIComponent(t.id)}`, {
      method: 'PATCH',
      headers: authHeaders(),
      body: JSON.stringify({ status: colName }),
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.detail || `HTTP ${resp.status}`)
    }
    // 乐观更新：先移前端，等 WS 或下次 loadBoard 纠正
    for (const col of board.value.columns) {
      const idx = col.tasks.findIndex(x => x.id === t.id)
      if (idx >= 0) {
        col.tasks.splice(idx, 1)
        break
      }
    }
    const targetCol = board.value.columns.find(c => c.name === colName)
    if (targetCol) {
      t.status = colName
      targetCol.tasks.push(t)
    }
  } catch (e) {
    console.error('[Kanban] move task:', e)
    error.value = `移动失败: ${e.message}`
    await loadBoard()  // 回退：重新加载
  }
}

// ── 手动 dispatch ──
async function manualDispatch() {
  try {
    const resp = await fetch('/api/plugins/kanban/dispatch?max=4', {
      method: 'POST',
      headers: authHeaders(),
    })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const result = await resp.json()
    await loadBoard()
    if (result.spawned || result.claimed) {
      console.log('[Kanban] dispatch:', result)
    }
  } catch (e) {
    console.error('[Kanban] dispatch:', e)
  }
}

// ── 活跃 Worker ──
async function loadWorkers() {
  try {
    const resp = await fetch('/api/plugins/kanban/workers/active', { headers: authHeaders() })
    if (!resp.ok) return
    const data = await resp.json()
    workers.value = data.workers || []
  } catch { /* silent */ }
}

// ── 统计 ──
async function loadStats() {
  try {
    const resp = await fetch('/api/plugins/kanban/stats', { headers: authHeaders() })
    if (!resp.ok) return
    statsData.value = await resp.json()
  } catch { /* silent */ }
}

// ── WebSocket 实时事件 ──
function connectWS() {
  if (ws.value) return
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
  const since = board.value?.latest_event_id || 0
  const url = `${proto}//${location.host}/api/plugins/kanban/events?token=${encodeURIComponent(token)}&since=${since}`
  try {
    ws.value = new WebSocket(url)
    ws.value.onopen = () => { wsConnected.value = true; console.log('[Kanban] WS connected') }
    ws.value.onclose = () => {
      wsConnected.value = false
      ws.value = null
      console.log('[Kanban] WS closed, fallback to poll')
      // 3s 后重连
      setTimeout(() => { if (!ws.value) connectWS() }, 3000)
    }
    ws.value.onerror = () => { wsConnected.value = false }
    ws.value.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data)
        if (msg.events && msg.events.length > 0) {
          // 收到事件就刷新看板（增量更新太复杂，全量刷新足够快）
          loadBoard()
        }
      } catch { /* ignore parse errors */ }
    }
  } catch (e) {
    console.error('[Kanban] WS connect failed:', e)
  }
}
function disconnectWS() {
  if (ws.value) {
    ws.value.onclose = null  // 阻止重连
    ws.value.close()
    ws.value = null
  }
  wsConnected.value = false
}

// ── 轮询回退 ──
function startPoll() {
  stopPoll()
  pollTimer = setInterval(() => {
    if (!wsConnected.value) loadBoard()
  }, 5000)
}
function stopPoll() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

// ── Worker 轮询（独立频率，10s）──
function startWorkerPoll() {
  stopWorkerPoll()
  workerTimer = setInterval(() => { loadWorkers() }, 10000)
}
function stopWorkerPoll() {
  if (workerTimer) { clearInterval(workerTimer); workerTimer = null }
}

// ── 页面可见性 ──
let wasVisible = true
function onVisibility() {
  const visible = !document.hidden
  if (visible && !wasVisible) {
    // 恢复可见：立即刷新 + 重连 WS
    loadBoard()
    if (!ws.value) connectWS()
  } else if (!visible && wasVisible) {
    // 不可见：断开 WS（节省资源）
    disconnectWS()
  }
  wasVisible = visible
}

// ── 生命周期 ──
onMounted(async () => {
  await loadBoard()
  connectWS()
  startPoll()       // WS 断开时的回退
  startWorkerPoll()
  loadWorkers()
  document.addEventListener('visibilitychange', onVisibility)
})
onUnmounted(() => {
  stopPoll()
  stopWorkerPoll()
  disconnectWS()
  document.removeEventListener('visibilitychange', onVisibility)
})

function goChat() { router.push('/') }
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <!-- 顶部栏 -->
    <header class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0">
      <button
        @click="goChat"
        class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
        <span class="header-tooltip group-hover:opacity-100">返回会话</span>
      </button>
      <h1 class="text-base font-semibold flex items-center gap-2">
        <span>🐝</span> 蜂群协作看板
      </h1>
      <span class="text-xs text-gray-400">Vermes 任务图 · 多 Agent 并行执行可视化</span>

      <!-- WS 状态指示 -->
      <span class="flex items-center gap-1 text-xs" :class="wsConnected ? 'text-green-500' : 'text-gray-400'">
        <span class="w-1.5 h-1.5 rounded-full" :class="wsConnected ? 'bg-green-500 animate-pulse' : 'bg-gray-400'"></span>
        {{ wsConnected ? '实时' : '轮询' }}
      </span>

      <div class="ml-auto flex items-center gap-2">
        <!-- 统计按钮 -->
        <button
          @click="showStats = !showStats; if (showStats) loadStats()"
          class="group relative px-3 py-1.5 text-sm rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition"
        >
          📊
          <span class="header-tooltip group-hover:opacity-100">统计</span>
        </button>
        <!-- 手动 dispatch -->
        <button
          @click="manualDispatch"
          class="group relative px-3 py-1.5 text-sm rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 transition"
        >
          ⚡
          <span class="header-tooltip group-hover:opacity-100">手动调度</span>
        </button>
        <!-- 新建任务 -->
        <button
          @click="showNewTask = true"
          class="group relative px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition"
        >
          + 新建
          <span class="header-tooltip group-hover:opacity-100">创建蜂群任务</span>
        </button>
        <!-- 刷新 -->
        <button
          @click="loadBoard"
          :disabled="loading"
          class="group relative px-3 py-1.5 text-sm rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-50 transition"
        >
          {{ loading ? '⏳' : '🔄' }}
          <span class="header-tooltip group-hover:opacity-100">刷新看板</span>
        </button>
      </div>
    </header>

    <!-- 错误提示 -->
    <div v-if="error" class="mx-4 mt-4 p-3 text-sm text-red-600 bg-red-50 dark:bg-red-950/30 rounded-lg flex items-center justify-between">
      <span>{{ error }}</span>
      <button @click="error = ''" class="text-red-400 hover:text-red-600">✕</button>
    </div>

    <!-- 加载态 -->
    <div v-if="loading && !board" class="flex-1 flex items-center justify-center text-sm text-gray-400">
      正在加载蜂群任务图…
    </div>

    <!-- 空态 -->
    <div v-else-if="board && stats.total === 0" class="flex-1 flex flex-col items-center justify-center text-center gap-3 px-6">
      <div class="text-5xl">🐝</div>
      <p class="text-sm text-gray-500 max-w-md">
        还没有蜂群任务。在对话里让 Vermes 用 <code class="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">kanban swarm</code>
        拆解复杂任务，或点击「新建」手动创建。
      </p>
      <div class="flex gap-2">
        <button @click="showNewTask = true" class="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition">
          新建任务
        </button>
        <button @click="goChat" class="px-4 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
          去对话
        </button>
      </div>
    </div>

    <!-- 统计面板（展开式） -->
    <div v-if="showStats && statsData" class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0">
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
        <div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
          <div class="text-xs text-gray-400">总任务</div>
          <div class="text-lg font-semibold">{{ statsData.total || stats.total }}</div>
        </div>
        <div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
          <div class="text-xs text-gray-400">活跃 Worker</div>
          <div class="text-lg font-semibold text-green-600">{{ workers.length }}</div>
        </div>
        <div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
          <div class="text-xs text-gray-400">完成率</div>
          <div class="text-lg font-semibold text-emerald-600">{{ stats.total > 0 ? Math.round(stats.done / stats.total * 100) : 0 }}%</div>
        </div>
        <div class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700">
          <div class="text-xs text-gray-400">阻塞/重试</div>
          <div class="text-lg font-semibold" :class="stats.blocked > 0 ? 'text-red-600' : 'text-gray-400'">{{ stats.blocked }}</div>
        </div>
      </div>
    </div>

    <!-- Worker 监控条（有活跃 worker 时显示） -->
    <div v-if="workers.length > 0" class="px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-green-50 dark:bg-green-950/20 shrink-0">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-xs font-medium text-green-700 dark:text-green-300">🔧 活跃 Worker:</span>
        <span v-for="w in workers" :key="w.run_id"
              class="text-xs px-2 py-0.5 rounded-full bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300">
          #{{ w.task_id }} · {{ w.profile || 'default' }} · PID {{ w.worker_pid }}
          <span v-if="w.started_at" class="text-green-500">· {{ fmtDuration(Date.now() / 1000 - w.started_at) }}</span>
        </span>
      </div>
    </div>

    <!-- 态势统计条 -->
    <div v-if="board && stats.total > 0" class="flex items-center gap-2 px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0 flex-wrap">
      <span class="text-xs px-2 py-1 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">共 {{ stats.total }}</span>
      <span class="text-xs px-2 py-1 rounded-full bg-green-50 dark:bg-green-900/30 text-green-600 dark:text-green-300">🟢 执行中 {{ stats.running }}</span>
      <span class="text-xs px-2 py-1 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300">🔵 就绪 {{ stats.ready }}</span>
      <span class="text-xs px-2 py-1 rounded-full bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-300">🟡 复核 {{ stats.review }}</span>
      <span class="text-xs px-2 py-1 rounded-full bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-300">🔴 阻塞 {{ stats.blocked }}</span>
      <span class="text-xs px-2 py-1 rounded-full bg-emerald-50 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-300">✅ 完成 {{ stats.done }}</span>
    </div>

    <!-- 看板：8 列横向滚动 -->
    <div v-if="board" class="flex-1 overflow-x-auto overflow-y-hidden">
      <div class="flex gap-3 p-4 h-full min-w-max">
        <div
          v-for="col in board.columns"
          :key="col.name"
          class="flex flex-col w-64 shrink-0 rounded-xl transition-all"
          :class="[
            dragOverCol === col.name ? 'ring-2 ring-blue-400 bg-blue-50 dark:bg-blue-900/20' : 'bg-gray-100 dark:bg-gray-800',
            col.name === 'running' ? 'cursor-not-allowed' : 'cursor-pointer'
          ]"
          @dragover="onDragOver($event, col.name)"
          @dragleave="onDragLeave(col.name)"
          @drop="onDrop($event, col.name)"
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
              draggable="true"
              @dragstart="onDragStart($event, t)"
              @click="openTaskDetail(t)"
              class="p-2.5 rounded-lg bg-white dark:bg-gray-700 shadow-sm border border-gray-200 dark:border-gray-600 text-sm cursor-pointer hover:shadow-md hover:border-blue-300 dark:hover:border-blue-700 transition"
              :class="{ 'ring-1 ring-red-300 dark:ring-red-800': t.warnings && t.warnings.highest_severity === 'critical' }"
            >
              <div class="font-medium leading-snug">{{ taskTitle(t) }}</div>

              <!-- Worker 类型标签 -->
              <div v-if="skillTags(t).length" class="mt-1 flex flex-wrap gap-1">
                <span v-for="s in skillTags(t)" :key="s"
                      class="text-[10px] px-1.5 py-0.5 rounded bg-violet-50 dark:bg-violet-900/30 text-violet-600 dark:text-violet-300">
                  🛠 {{ s }}
                </span>
              </div>

              <!-- 依赖 / 子任务进度 -->
              <div v-if="depText(t).length" class="mt-1.5 flex flex-wrap gap-1.5 text-[10px] text-gray-500 dark:text-gray-400">
                <span v-for="d in depText(t)" :key="d" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-600">{{ d }}</span>
              </div>

              <!-- Worker 交接摘要预览 -->
              <div v-if="t.latest_summary" class="mt-1.5 text-[10px] text-gray-400 dark:text-gray-500 line-clamp-2 leading-relaxed border-l-2 border-gray-200 dark:border-gray-600 pl-1.5">
                {{ t.latest_summary }}
              </div>

              <!-- 底部元数据行 -->
              <div class="mt-1.5 flex items-center gap-1.5 text-xs text-gray-400 flex-wrap">
                <span class="font-mono">#{{ t.id }}</span>
                <span v-if="t.assignee" class="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-600">@{{ t.assignee }}</span>
                <span v-if="failText(t)" class="px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-300">{{ failText(t) }}</span>
                <span v-if="warnBadge(t.warnings)" class="px-1.5 py-0.5 rounded" :class="warnBadge(t.warnings).cls">{{ warnBadge(t.warnings).text }}</span>
                <span class="ml-auto text-[10px]">{{ fmtTime(t.created_at) }}</span>
              </div>
            </div>
            <!-- 空列空态 -->
            <div v-if="col.tasks.length === 0" class="text-xs text-gray-300 dark:text-gray-600 text-center py-6">
              {{ colEmpty(col.name) }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 任务详情抽屉 -->
    <Teleport to="body">
      <transition name="drawer-slide">
        <div v-if="selectedTask" class="fixed inset-0 flex justify-end z-50">
          <div class="absolute inset-0 bg-black/20" @click="closeDetail"></div>
          <div class="relative w-96 max-w-[90vw] h-full bg-white dark:bg-gray-800 shadow-xl flex flex-col">
            <!-- 抽屉头 -->
            <div class="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700 shrink-0">
              <button @click="closeDetail" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition">
                <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
              <span class="text-sm font-semibold">任务 #{{ selectedTask.id }}</span>
              <span class="ml-auto text-xs px-2 py-1 rounded-full" :class="colDot(selectedTask.status) + ' text-white'">{{ colLabel(selectedTask.status) }}</span>
            </div>
            <!-- 抽屉内容 -->
            <div class="flex-1 overflow-y-auto p-4 space-y-4">
              <div v-if="detailLoading" class="text-center text-sm text-gray-400 py-8">加载中…</div>
              <template v-else-if="taskDetail">
                <!-- 标题 -->
                <div>
                  <div class="text-xs text-gray-400 mb-1">标题</div>
                  <div class="text-sm font-medium">{{ taskDetail.task?.title || '(无标题)' }}</div>
                </div>
                <!-- 描述 -->
                <div v-if="taskDetail.task?.body">
                  <div class="text-xs text-gray-400 mb-1">描述</div>
                  <div class="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap">{{ taskDetail.task.body }}</div>
                </div>
                <!-- 元数据 -->
                <div class="grid grid-cols-2 gap-2 text-sm">
                  <div><span class="text-gray-400">负责人:</span> {{ taskDetail.task?.assignee || '—' }}</div>
                  <div><span class="text-gray-400">优先级:</span> {{ taskDetail.task?.priority ?? 0 }}</div>
                  <div><span class="text-gray-400">创建:</span> {{ fmtTime(taskDetail.task?.created_at) }}</div>
                  <div><span class="text-gray-400">更新:</span> {{ fmtTime(taskDetail.task?.updated_at) }}</div>
                </div>
                <!-- 交接摘要 -->
                <div v-if="taskDetail.task?.latest_summary">
                  <div class="text-xs text-gray-400 mb-1">Worker 交接摘要</div>
                  <div class="text-sm text-gray-600 dark:text-gray-300 p-2 rounded-lg bg-gray-50 dark:bg-gray-700 border-l-2 border-blue-400 whitespace-pre-wrap">{{ taskDetail.task.latest_summary }}</div>
                </div>
                <!-- 运行历史 -->
                <div v-if="taskDetail.runs?.length">
                  <div class="text-xs text-gray-400 mb-1">运行历史 ({{ taskDetail.runs.length }})</div>
                  <div class="space-y-1.5">
                    <div v-for="r in taskDetail.runs.slice(0, 5)" :key="r.id"
                         class="text-xs p-2 rounded-lg bg-gray-50 dark:bg-gray-700 flex items-center gap-2">
                      <span class="font-mono text-gray-400">run#{{ r.id }}</span>
                      <span class="px-1.5 py-0.5 rounded text-[10px]"
                            :class="r.status === 'completed' ? 'bg-green-100 text-green-600' : r.status === 'failed' ? 'bg-red-100 text-red-600' : 'bg-gray-100 text-gray-500'">
                        {{ r.status }}
                      </span>
                      <span v-if="r.profile" class="text-gray-400">@{{ r.profile }}</span>
                      <span v-if="r.started_at" class="ml-auto text-gray-400">{{ fmtTime(r.started_at) }}</span>
                    </div>
                  </div>
                </div>
                <!-- 事件流 -->
                <div v-if="taskDetail.events?.length">
                  <div class="text-xs text-gray-400 mb-1">事件流 ({{ taskDetail.events.length }})</div>
                  <div class="space-y-1">
                    <div v-for="e in taskDetail.events.slice(-10).reverse()" :key="e.id"
                         class="text-xs flex items-center gap-2 text-gray-500 dark:text-gray-400">
                      <span class="w-1.5 h-1.5 rounded-full bg-blue-400"></span>
                      <span class="font-mono">{{ e.kind }}</span>
                      <span class="ml-auto">{{ fmtTime(e.created_at) }}</span>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>
        </div>
      </transition>
    </Teleport>

    <!-- 新建任务弹窗 -->
    <Teleport to="body">
      <transition name="fade">
        <div v-if="showNewTask" class="fixed inset-0 z-50 flex items-center justify-center">
          <div class="absolute inset-0 bg-black/30" @click="showNewTask = false"></div>
          <div class="relative w-96 max-w-[90vw] bg-white dark:bg-gray-800 rounded-xl shadow-xl p-6 space-y-4">
            <h2 class="text-base font-semibold">新建蜂群任务</h2>
            <div>
              <label class="text-xs text-gray-400">标题 *</label>
              <input v-model="newTask.title" placeholder="任务标题…"
                     class="w-full mt-1 px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none transition" />
            </div>
            <div>
              <label class="text-xs text-gray-400">描述</label>
              <textarea v-model="newTask.body" rows="3" placeholder="任务描述…"
                        class="w-full mt-1 px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none transition resize-none" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="text-xs text-gray-400">负责人</label>
                <input v-model="newTask.assignee" placeholder="profile 名…"
                       class="w-full mt-1 px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none transition" />
              </div>
              <div>
                <label class="text-xs text-gray-400">优先级</label>
                <input v-model.number="newTask.priority" type="number" placeholder="0"
                       class="w-full mt-1 px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:border-blue-400 focus:ring-1 focus:ring-blue-400 outline-none transition" />
              </div>
            </div>
            <div class="flex justify-end gap-2 pt-2">
              <button @click="showNewTask = false" class="px-4 py-2 text-sm rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition">取消</button>
              <button @click="createTask" :disabled="creating || !newTask.title.trim()"
                      class="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white transition">
                {{ creating ? '创建中…' : '创建' }}
              </button>
            </div>
          </div>
        </div>
      </transition>
    </Teleport>
  </div>
</template>

<style scoped>
.drawer-slide-enter-active, .drawer-slide-leave-active { transition: transform 0.2s ease; }
.drawer-slide-enter-from, .drawer-slide-leave-to { transform: translateX(100%); }
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s ease; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
