<template>
  <!-- T4 行内任务卡：真枚举状态 + assignee + task_events 进度 -->
  <div
    class="mt-2 rounded-xl border border-violet-200 dark:border-violet-800 bg-violet-50 dark:bg-violet-900/20 overflow-hidden"
  >
    <button
      class="w-full px-3 py-2 flex items-start gap-2.5 text-left hover:bg-violet-100 dark:hover:bg-violet-900/35 transition"
      @click="expanded = !expanded"
    >
      <span class="text-base shrink-0 mt-0.5">📋</span>
      <span class="min-w-0 flex-1">
        <span class="flex items-center gap-1.5 flex-wrap">
          <span class="text-[13px] font-semibold text-violet-900 dark:text-violet-100 truncate">
            任务卡 · {{ title }}
          </span>
          <span
            class="text-[10px] px-1.5 py-0.5 rounded-full font-medium"
            :class="statusChipClass"
          >{{ statusLabel }}</span>
        </span>
        <span class="block text-[11px] text-violet-700/80 dark:text-violet-200/70 truncate mt-0.5">
          assignee={{ assignee || '未指派' }}
          <template v-if="taskId"> · #{{ taskId }}</template>
          <template v-if="progressText"> · {{ progressText }}</template>
        </span>
      </span>
      <span class="text-[10px] text-violet-400 shrink-0 mt-1">{{ expanded ? '收起' : '看进度' }}</span>
    </button>

    <!-- 进度（task_events） -->
    <div v-if="expanded" class="px-3 pb-2.5 pt-0.5 border-t border-violet-100 dark:border-violet-800/60">
      <div v-if="loading" class="text-[11px] text-violet-400 py-2">加载进度…</div>
      <div v-else-if="error" class="text-[11px] text-rose-500 py-2">{{ error }}</div>
      <div v-else-if="!events.length" class="text-[11px] text-violet-400 py-2">暂无事件，任务刚建。</div>
      <ul v-else class="mt-2 space-y-1">
        <li
          v-for="(e, i) in recentEvents"
          :key="e.id || i"
          class="text-[11px] text-violet-800/90 dark:text-violet-100/80 flex gap-2"
        >
          <span class="text-violet-400 shrink-0">{{ fmtTime(e.created_at) }}</span>
          <span class="min-w-0 truncate">{{ eventLine(e) }}</span>
        </li>
      </ul>
      <p v-if="events.length > 5" class="mt-1.5 text-[10px] text-violet-400">
        共 {{ events.length }} 条事件 · 打开蜂群看板看全量
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { fetchKanbanTask, taskStatusMeta } from '../utils/taskDispatch'

const props = defineProps({
  taskId: { type: [String, Number], default: '' },
  /** 已知标题（消息旁渲染时先给，减轻闪烁） */
  title: { type: String, default: '' },
  assignee: { type: String, default: '' },
  status: { type: String, default: '' },
})

const expanded = ref(false)
const loading = ref(false)
const error = ref('')
const task = ref(null)
const events = ref([])
let timer = null

const title = computed(() =>
  (task.value && (task.value.title || task.value.body)) || props.title || `#${props.taskId}`
)
const assignee = computed(() => (task.value && task.value.assignee) || props.assignee || '')
const status = computed(() => (task.value && task.value.status) || props.status || '')

const statusLabel = computed(() => taskStatusMeta(status.value).label)
const statusChipClass = computed(() => {
  const s = status.value
  if (s === 'done') return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
  if (s === 'running') return 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
  if (s === 'blocked') return 'bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300'
  if (s === 'review') return 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
  return 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-200'
})

const progressText = computed(() => {
  const t = task.value
  if (t && t.progress && t.progress.total) {
    return `▸ ${t.progress.done || 0}/${t.progress.total}`
  }
  if (events.value.length) return `${events.value.length} 事件`
  return ''
})

const recentEvents = computed(() => events.value.slice(-5))

function eventLine(e) {
  const k = e.kind || 'event'
  const p = e.payload
  let extra = ''
  try {
    const obj = typeof p === 'string' ? JSON.parse(p) : p
    if (obj && typeof obj === 'object') {
      extra = obj.note || obj.summary || obj.status || obj.error || ''
      if (typeof extra === 'object') extra = JSON.stringify(extra)
    } else if (typeof p === 'string') {
      extra = p
    }
  } catch {
    extra = String(p || '')
  }
  extra = String(extra || '').slice(0, 80)
  return extra ? `${k} · ${extra}` : k
}

function fmtTime(ts) {
  if (!ts) return ''
  // iso 或 unix 秒
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (Number.isNaN(d.getTime())) return String(ts).slice(11, 19)
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

async function load() {
  if (!props.taskId) return
  loading.value = true
  error.value = ''
  try {
    const data = await fetchKanbanTask(props.taskId)
    task.value = (data && data.task) || data || null
    events.value = (data && data.events) || (task.value && task.value.events) || []
  } catch (e) {
    error.value = e.message || '加载任务失败'
  } finally {
    loading.value = false
  }
}

function startPoll() {
  stopPoll()
  // 进度订阅：卡片展示期间 8s 轻轮询（task_events 落库即可见）
  timer = setInterval(load, 8000)
}
function stopPoll() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(() => props.taskId, (id, old) => {
  if (id && id !== old) {
    load()
    startPoll()
  }
})
onMounted(() => {
  load()
  startPoll()
})
onUnmounted(stopPoll)

defineExpose({ load })
</script>
