<script setup>
import { computed, reactive, ref, watch, nextTick, onUnmounted } from 'vue'

const props = defineProps({
  items: { type: Array, required: true }, // 树根节点(含 children / depth)
})

// 2026-09-27：手动收起。任务流此前是「常驻展示」，只有 onTaskComplete 才会
// 置 allDone —— 一旦 agent 建了 todo 但没走完生命周期（连续多轮排查、中途
// 中断、流式断连），onTaskComplete 永不到达，卡片就永久挂在 0/2、0%。
// 这不是用户的操作问题，是产品缺退出路径；这里补一个显式收起。
const emit = defineEmits(['dismiss'])

// 折叠状态：存已折叠节点的 id
const collapsed = reactive(new Set())

function toggle(id) {
  if (collapsed.has(id)) collapsed.delete(id)
  else collapsed.add(id)
}

// 可见(考虑折叠)的深度优先扁平列表
const visible = computed(() => {
  const out = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      out.push({ ...n, depth })
      if (n.children && n.children.length && !collapsed.has(n.id)) walk(n.children, depth + 1)
    }
  }
  walk(props.items || [], 0)
  return out
})

// ── P1-2（2026-09-22）：进度只按【叶子节点】计数 ──
// 原实现 countNodes 把父节点与子节点一并计入 total，而父节点状态通常不随子节点
// 同步推进 → 百分比系统性失真（父子双计）。这里只统计叶子，父节点状态不参与计数。
function countLeaves(nodes) {
  return (nodes || []).reduce((s, n) => {
    const kids = n.children || []
    return s + (kids.length ? countLeaves(kids) : 1)
  }, 0)
}
function countLeavesByStatus(nodes, st) {
  return (nodes || []).reduce((s, n) => {
    const kids = n.children || []
    return s + (kids.length ? countLeavesByStatus(kids, st) : (n.status === st ? 1 : 0))
  }, 0)
}

const total = computed(() => countLeaves(props.items || []))
const completed = computed(() => countLeavesByStatus(props.items || [], 'completed'))
const inProgress = computed(() => countLeavesByStatus(props.items || [], 'in_progress'))
const failed = computed(() => countLeavesByStatus(props.items || [], 'failed'))
const percent = computed(() => (total.value ? Math.round((completed.value / total.value) * 100) : 0))

// ── 2026-09-27：未收尾态（原「僵尸任务流」的可视化）──
// 没有任何 in_progress、但也没全部完成 → agent 建了 todo 却没走完生命周期，
// 且 onTaskComplete 永远等不到。此前这种状态在界面上与「正在执行」无法区分，
// 卡片就一直挂着假装还在跑。这里显式标注，让用户知道它是卡住了而不是在干活。
const stalled = computed(
  () => total.value > 0 && inProgress.value === 0 && completed.value < total.value
)
const unfinished = computed(() => Math.max(0, total.value - completed.value - failed.value))

// ── P1-3：当前进行中的叶子节点 → 高亮 + 自动滚动聚焦 ──
function firstInProgressLeaf(nodes) {
  for (const n of nodes || []) {
    const kids = n.children || []
    if (kids.length) {
      const hit = firstInProgressLeaf(kids)
      if (hit) return hit
    } else if (n.status === 'in_progress') {
      return n
    }
  }
  return null
}
const currentNodeId = computed(() => firstInProgressLeaf(props.items || [])?.id || null)

const scrollRef = ref(null)
let scrollTimer = null
function scrollToCurrent() {
  const id = currentNodeId.value
  if (!id) return
  const el = scrollRef.value && scrollRef.value.querySelector(`[data-node-id="${id}"]`)
  // jsdom / 非浏览器环境下 scrollIntoView 不存在，静默跳过（测试守卫）
  if (el && typeof el.scrollIntoView === 'function') {
    try { el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) } catch (_) {}
  }
}
watch(currentNodeId, () => {
  clearTimeout(scrollTimer)
  // 合并高频状态变更：30ms 内的多次推进只滚一次，避免滚动抖动
  scrollTimer = setTimeout(async () => {
    await nextTick()
    scrollToCurrent()
  }, 30)
})
onUnmounted(() => clearTimeout(scrollTimer))

// 每步耗时：已完成步骤用 finished_at - started_at（秒级时间戳，见 chat.js:1181）
// 进行中步骤不显示秒数 —— 否则需每秒 tick，会把整棵任务树每秒重渲染一次（噪音 + 性能）。
function formatDur(sec) {
  if (!sec || sec < 0) return ''
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m}m${sec % 60}s`
  const h = Math.floor(m / 60)
  return `${h}h${m % 60}m`
}
function durationOf(node) {
  if (!node.started_at) return ''
  const end = node.finished_at || 0
  if (!end) return '' // 进行中/未结束：不显示，避免每秒刷新
  return formatDur(Math.max(0, Math.round(end - node.started_at)))
}

function statusIcon(st) {
  return ({ pending: '○', in_progress: '▶', completed: '✅', failed: '❌', cancelled: '⏭' })[st] || '○'
}
function statusClass(st) {
  return {
    pending: 'text-gray-400',
    in_progress: 'text-blue-500',
    completed: 'text-green-500',
    failed: 'text-red-500',
    cancelled: 'text-gray-400',
  }[st] || 'text-gray-400'
}
</script>

<template>
  <div class="mx-3 mt-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 overflow-hidden">
    <!-- 头部：标题 + 进度 + 收起 -->
    <div class="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-sm">🌳</span>
        <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">任务流</span>
        <span class="text-[11px] text-gray-400">已完成 {{ completed }} / 共 {{ total }}</span>
        <!-- 未收尾标记：与「正在执行」区分开，避免僵尸任务流假装还在跑 -->
        <span
          v-if="stalled"
          class="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 shrink-0"
          title="任务已无进行中条目，但也未全部完成 —— agent 可能中途建了 todo 却没走完，本轮不会有完成事件"
        >未收尾</span>
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <span class="text-[11px] text-gray-400">{{ percent }}%</span>
        <!-- 手动收起：给常驻任务流一个确定的退出路径 -->
        <button
          class="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-gray-700 dark:hover:text-gray-100 rounded hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
          title="收起任务流（本轮不再显示，下次有新任务推进时自动恢复）"
          aria-label="收起任务流"
          @click="emit('dismiss')"
        >✕</button>
      </div>
    </div>
    <!-- 总进度条 -->
    <div class="px-3 pt-2">
      <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
        <div class="h-full bg-green-500 transition-all duration-500" :style="{ width: percent + '%' }"></div>
      </div>
    </div>
    <!-- 树形列表 -->
    <div ref="scrollRef" class="px-2 py-2 max-h-64 overflow-y-auto">
      <div
        v-for="node in visible"
        :key="node.id"
        :data-node-id="node.id"
        class="flex items-center gap-1.5 py-1 text-xs rounded hover:bg-gray-100 dark:hover:bg-gray-700/50"
        :style="{ paddingLeft: (node.depth * 14 + 4) + 'px' }"
        :class="node.id === currentNodeId ? 'bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-200 dark:ring-blue-800' : ''"
      >
        <!-- 折叠按钮(仅父节点) -->
        <button
          v-if="node.children && node.children.length"
          @click="toggle(node.id)"
          class="w-4 h-4 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 shrink-0"
        >
          <span class="text-[10px] transition-transform" :class="collapsed.has(node.id) ? '' : 'rotate-90'">▶</span>
        </button>
        <span v-else class="w-4 shrink-0"></span>

        <span :class="statusClass(node.status)" class="shrink-0">{{ statusIcon(node.status) }}</span>
        <!-- P1-2: 去掉 truncate（长任务名被截断是「任务流不准确」的主因）→ 换行 + title 浮层 -->
        <span
          class="flex-1 min-w-0 break-words"
          :title="node.content || node.title"
          :class="node.status === 'completed'
            ? 'text-gray-400 dark:text-gray-500 line-through'
            : 'text-gray-700 dark:text-gray-200'"
        >{{ node.content || node.title }}</span>
        <span v-if="durationOf(node)" class="text-[10px] text-gray-400 shrink-0 tabular-nums">{{ durationOf(node) }}</span>
        <span v-if="node.agent_role && node.agent_role !== 'default'" class="text-[10px] text-gray-400 shrink-0">{{ node.agent_role }}</span>
      </div>
    </div>
    <!-- 底部状态行 -->
    <div v-if="inProgress || failed || stalled" class="px-3 py-1.5 border-t border-gray-200 dark:border-gray-700 flex items-center gap-3 text-[11px]">
      <span v-if="inProgress" class="text-blue-500">▶ {{ inProgress }} 进行中</span>
      <span v-if="failed" class="text-red-500">❌ {{ failed }} 失败</span>
      <span v-if="stalled && !inProgress" class="text-amber-600 dark:text-amber-400">⚠️ {{ unfinished }} 项未收尾（本轮已无活动）</span>
    </div>
  </div>
</template>
