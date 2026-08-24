<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

// 实时“滴答”，让进行中步骤的耗时每秒刷新
const now = ref(Date.now())
let _timer = null
onMounted(() => { _timer = setInterval(() => { now.value = Date.now() }, 1000) })
onUnmounted(() => { if (_timer) clearInterval(_timer) })

// 工具中文标签 + 内部名过滤统一走 chat.toolLabel（避免两处重复定义）

// ── 步骤状态 → 中文 + 样式 ──
const STATUS_TEXT = {
  pending: '待办',
  in_progress: '进行中',
  completed: '已完成',
  cancelled: '已跳过',
  interrupted: '已中断',
}
const STATUS_ICON = {
  pending: '⬜',
  in_progress: '🔄',
  completed: '✅',
  cancelled: '⏭️',
  interrupted: '⚠️',
}
const STATUS_ROW = {
  pending: 'opacity-60',
  in_progress: 'bg-blue-50 dark:bg-blue-900/15 ring-1 ring-blue-200 dark:ring-blue-800',
  completed: '',
  cancelled: 'opacity-50 line-through',
  interrupted: 'opacity-50 text-amber-600 dark:text-amber-400',
}

const stats = computed(() => {
  const items = chat.todoItems
  return {
    total: items.length,
    pending: items.filter(i => i.status === 'pending').length,
    inProgress: items.filter(i => i.status === 'in_progress').length,
    completed: items.filter(i => i.status === 'completed').length,
    cancelled: items.filter(i => i.status === 'cancelled').length,
  }
})
const progressPercent = computed(() => {
  if (stats.value.total === 0) return 0
  return Math.round((stats.value.completed / stats.value.total) * 100)
})

// 步骤耗时（秒）：进行中用实时 now，完成用 finished_at-started_at
function stepElapsed(item) {
  if (item.started_at == null) return null
  const start = item.started_at * 1000
  let end
  if (item.status === 'in_progress') end = now.value
  else if (item.finished_at != null) end = item.finished_at * 1000
  else end = start
  return Math.max(0, Math.round((end - start) / 1000))
}
function fmtDuration(sec) {
  if (sec == null) return ''
  if (sec < 60) return `${sec}秒`
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return s ? `${m}分${s}秒` : `${m}分钟`
}

// 某步骤下的实时子工具列表
function stepActivities(id) {
  return chat.todoStepActivities[id] || []
}

// 空状态引导示例
const EXAMPLES = [
  '帮我调研一下「AI  Agent 框架」并写一份对比报告',
  '读取这个 PDF，总结前 3 章的要点',
  '把这段代码重构一下，并加上单元测试',
]

// ── 未分组工具流：聚合展示（避免逐条裸工具名刷屏）──
const ungroupedActs = computed(() => chat.todoStepActivities['__ungrouped__'] || [])
// 防御性：running 项若超过 30s 仍无 tool_end（流式中断/连接断开），视为已结束，不再显示“进行中”
const RUNNING_STALE_MS = 30000
function _isStaleRunning(a) {
  // 用 now.value（每秒滴答）触发 computed 重算，使过期态能自动显现
  return a.status === 'running' && (now.value - (a.start || 0)) > RUNNING_STALE_MS
}
// 按中文标签聚合计数（过滤内部名 _thinking 等）
const ungroupedSummary = computed(() => {
  const counts = {}
  let running = null
  for (const a of ungroupedActs.value) {
    const label = chat.toolLabel(a.name)
    if (!label) continue
    counts[label] = (counts[label] || 0) + 1
    // 仅当仍在进行中且未过期，才标记为“正在 XX…”
    if (a.status === 'running' && !_isStaleRunning(a)) running = label
  }
  return { counts, running, total: ungroupedActs.value.length }
})
// 仅失败/可恢复项（折叠，点击展开）
const ungroupedErrors = computed(() =>
  ungroupedActs.value.filter(a => a.is_error && a.status === 'done')
)
const showUngroupedErrors = ref(false)
</script>

<template>
  <!-- 遮罩（点击关闭） -->
  <div v-if="chat.showTaskDrawer"
       class="fixed inset-0 bg-black/30 z-40"
       @click="chat.toggleTaskDrawer()"></div>

  <!-- 右侧抽屉 -->
  <aside v-if="chat.showTaskDrawer"
         class="fixed top-0 right-0 h-full w-[340px] max-w-[88vw] bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 shadow-2xl z-50 flex flex-col transition-transform">

    <!-- 头部 -->
    <div class="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <span class="text-lg">📋</span>
        <span class="text-sm font-semibold text-gray-800 dark:text-gray-100">任务清单</span>
        <span v-if="stats.total" class="text-xs text-gray-400">已完成 {{ stats.completed }} / 共 {{ stats.total }}</span>
      </div>
      <!-- verbosity 切换：摘要 / 详细（对标 Claude Code Verbose/Summary） -->
      <div class="flex items-center gap-0.5 mr-1 rounded-lg bg-gray-100 dark:bg-gray-700 p-0.5">
        <button @click="chat.setTaskVerbosity('summary')"
                class="text-[10px] px-1.5 py-0.5 rounded-md transition-colors"
                :class="chat.taskVerbosity === 'summary'
                  ? 'bg-white dark:bg-gray-600 text-gray-800 dark:text-gray-100 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'">摘要</button>
        <button @click="chat.setTaskVerbosity('verbose')"
                class="text-[10px] px-1.5 py-0.5 rounded-md transition-colors"
                :class="chat.taskVerbosity === 'verbose'
                  ? 'bg-white dark:bg-gray-600 text-gray-800 dark:text-gray-100 shadow-sm'
                  : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'">详细</button>
      </div>
      <button @click="chat.toggleTaskDrawer()"
              class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none">✕</button>
    </div>

    <!-- 总进度条 -->
    <div v-if="stats.total" class="px-4 py-2.5 border-b border-gray-100 dark:border-gray-700">
      <div class="flex items-center justify-between text-xs text-gray-400 mb-1">
        <span>总进度</span>
        <span>{{ progressPercent }}%</span>
      </div>
      <div class="w-full h-2 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
        <div class="h-full bg-green-500 transition-all duration-500"
             :style="{ width: progressPercent + '%' }"></div>
      </div>
    </div>

    <!-- 庆祝 / 暂停 提示 -->
    <div v-if="chat.todoAllDone && stats.total"
         class="mx-4 mt-3 px-3 py-2 rounded-lg bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 text-sm flex items-center gap-2">
      🎉 全部完成！共 {{ stats.total }} 步。
    </div>
    <div v-else-if="chat.todoInterrupted && stats.inProgress"
         class="mx-4 mt-3 px-3 py-2 rounded-lg bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 text-sm flex items-center gap-2">
      ⏸ 已暂停，已完成的步骤保留如下。
    </div>

    <!-- 未分组工具调用：聚合摘要（不逐条裸工具名刷屏；内部步骤 _thinking 已过滤） -->
    <div v-if="ungroupedActs.length"
         class="flex-1 overflow-y-auto px-3 py-2">
      <div class="text-[11px] text-gray-400 mb-1.5 px-1">⚙️ 正在工作…</div>

      <!-- 摘要档：聚合计数胶囊 + 进行中提示 -->
      <template v-if="chat.taskVerbosity === 'summary'">
        <div class="flex flex-wrap gap-1.5 px-1">
          <span v-for="(cnt, label) in ungroupedSummary.counts" :key="label"
                class="text-[11px] px-2 py-1 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
            {{ label }} ×{{ cnt }}
          </span>
        </div>
        <div v-if="ungroupedSummary.running" class="mt-2 flex items-center gap-1.5 px-1 text-[11px] text-blue-500">
          <span class="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"></span>
          <span>正在{{ ungroupedSummary.running.replace(/^.*? /, '') }}…</span>
        </div>
      </template>

      <!-- 详细档：逐条裸工具调用时间线（对标 Claude Code Verbose） -->
      <template v-else>
        <div class="space-y-1 px-1">
          <div v-for="act in ungroupedActs" :key="act.id"
               class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
            <span v-if="act.status === 'running' && !_isStaleRunning(act)" class="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"></span>
            <span v-else-if="act.is_error && act.status === 'done'" class="flex-shrink-0">⚠️</span>
            <span v-else-if="act.status === 'done'" class="flex-shrink-0 text-green-500">✓</span>
            <span v-else class="flex-shrink-0 text-gray-400">•</span>
            <span class="truncate">{{ chat.toolLabel(act.name) || act.name }}</span>
            <span v-if="act.status === 'done' && act.duration" class="flex-shrink-0 text-gray-400">· {{ act.duration < 1 ? '<1秒' : Math.round(act.duration) + '秒' }}</span>
          </div>
        </div>
      </template>

      <!-- 失败/重试折叠 -->
      <div v-if="ungroupedErrors.length" class="mt-2 px-1">
        <button @click="showUngroupedErrors = !showUngroupedErrors"
                class="text-[11px] text-amber-500 hover:text-amber-600 flex items-center gap-1">
          <span>⚠️ {{ ungroupedErrors.length }} 次重试/失败</span>
          <span>{{ showUngroupedErrors ? '▲' : '▼' }}</span>
        </button>
        <div v-if="showUngroupedErrors" class="mt-1 space-y-1 pl-2 border-l border-amber-200 dark:border-amber-800">
          <div v-for="act in ungroupedErrors" :key="act.id"
               class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
            <span class="flex-shrink-0">⚠️</span>
            <span class="truncate">{{ chat.toolLabel(act.name) || act.name }}</span>
            <span v-if="act.duration" class="flex-shrink-0 text-gray-400">· {{ act.duration < 1 ? '<1秒' : Math.round(act.duration) + '秒' }}</span>
          </div>
        </div>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto px-3 py-2 space-y-2">
      <div v-if="!stats.total" class="flex flex-col items-center justify-center text-center py-10 px-4">
        <div class="text-4xl mb-3">🧭</div>
        <p class="text-sm text-gray-500 dark:text-gray-400 leading-relaxed">
          当你让我做<strong class="text-gray-700 dark:text-gray-200">多步骤的任务</strong>时，<br/>
          我会自动拆成步骤，并在这里实时显示进度。
        </p>
        <ul class="mt-4 text-xs text-gray-400 space-y-1.5 text-left">
          <li v-for="(ex, i) in EXAMPLES" :key="i" class="flex gap-1.5">
            <span class="text-green-500">💡</span><span>{{ ex }}</span>
          </li>
        </ul>
      </div>

      <div v-for="item in chat.todoItems" :key="item.id"
           class="rounded-xl p-2.5 transition-colors"
           :class="STATUS_ROW[item.status] || ''">
        <div class="flex items-start gap-2">
          <span class="mt-0.5 flex-shrink-0"
                :class="{ 'animate-spin': item.status === 'in_progress' }">{{ STATUS_ICON[item.status] || '❓' }}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-2">
              <span class="text-sm text-gray-800 dark:text-gray-100 break-words"
                    :class="{ 'line-through': item.status === 'completed' || item.status === 'cancelled' }">
                {{ item.content }}
              </span>
              <span class="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded-full"
                    :class="{
                      'bg-blue-100 text-blue-600 dark:bg-blue-900/40 dark:text-blue-300': item.status === 'in_progress',
                      'bg-green-100 text-green-600 dark:bg-green-900/40 dark:text-green-300': item.status === 'completed',
                      'bg-gray-100 text-gray-400': item.status === 'pending',
                      'bg-red-100 text-red-500 dark:bg-red-900/30 dark:text-red-300': item.status === 'cancelled',
                    }">
                {{ STATUS_TEXT[item.status] || item.status }}
                <template v-if="item.status === 'in_progress' && stepElapsed(item) != null"> · {{ fmtDuration(stepElapsed(item)) }}</template>
                <template v-else-if="item.status === 'completed' && stepElapsed(item) != null"> · {{ fmtDuration(stepElapsed(item)) }}</template>
              </span>
            </div>

            <!-- 进行中步骤：实时子工具树（verbose 档展开，summary 档克制只显示步骤本身） -->
            <div v-if="chat.taskVerbosity === 'verbose' && item.status === 'in_progress' && stepActivities(item.id).length" class="mt-2 space-y-1">
              <div v-for="act in stepActivities(item.id)" :key="act.id"
                   class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 pl-1">
                <span v-if="act.status === 'running'" class="inline-block w-3 h-3 border-2 border-blue-400 border-t-transparent rounded-full animate-spin flex-shrink-0"></span>
                <span v-else-if="act.is_error" class="flex-shrink-0">⚠️</span>
                <span v-else class="flex-shrink-0 text-green-500">✓</span>
                <span class="truncate">{{ chat.toolLabel(act.name) || act.name }}</span>
                <span v-if="act.status === 'done' && act.duration" class="flex-shrink-0 text-gray-400">· {{ act.duration < 1 ? '<1秒' : Math.round(act.duration) + '秒' }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>
