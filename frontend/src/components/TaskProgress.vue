<template>
  <!-- 通用长任务进度组件 — 抽象自神魔堂 org 看板设计 -->
  <div class="task-progress">
    <!-- 状态徽标 -->
    <div class="flex items-center gap-2">
      <!-- P1-4（2026-09-22）：脉冲只在状态切换瞬间闪 ~3s，随后转静态点。
           原实现长任务期间持续 animate-pulse，是常驻视觉噪音。 -->
      <span v-if="active" class="w-2 h-2 rounded-full bg-blue-500" :class="{ 'animate-pulse': pulsing }" />
      <span class="text-xs font-medium" :class="statusClass">{{ statusLabel }}</span>
      <span v-if="subLabel" class="text-[11px] text-gray-400">{{ subLabel }}</span>
    </div>

    <!-- 进度条 -->
    <div v-if="progress" class="mt-1.5 h-1 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
      <div
        class="h-full rounded-full transition-all duration-500"
        :class="active ? 'bg-blue-500' : 'bg-emerald-500'"
        :style="{ width: progress.pct + '%' }"
      />
    </div>

    <!-- 耗时 -->
    <div v-if="elapsed" class="mt-1 text-[11px] text-gray-400">
      {{ active ? '已运行' : '耗时' }}：{{ elapsed }}
    </div>

    <!-- 失败诊断 -->
    <div v-if="failures && failures.length" class="mt-1.5 space-y-0.5">
      <div
        v-for="(f, i) in failures"
        :key="i"
        class="text-[11px] text-red-500 flex items-start gap-1"
      >
        <span class="flex-shrink-0">⛔</span>
        <span>{{ f.message || f }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted, watch } from 'vue'

const props = defineProps({
  status: { type: String, default: '' },
  statusLabels: { type: Object, default: () => ({}) },
  activeStatuses: { type: Array, default: () => ['dispatched', 'planning', 'executing', 'auditing', 'reworking', 'aggregating', 'running', 'processing'] },
  createdAt: { type: Number, default: 0 },
  updatedAt: { type: Number, default: 0 },
  done: { type: Number, default: 0 },
  total: { type: Number, default: 0 },
  failures: { type: Array, default: () => [] },
  subLabel: { type: String, default: '' },
})

// 实时秒针
const nowSec = ref(Math.floor(Date.now() / 1000))
let timer = null

const active = computed(() => props.activeStatuses.indexOf(props.status) >= 0)
const progress = computed(() => {
  if (!props.total) return null
  return { done: props.done, total: props.total, pct: Math.min(100, Math.round(props.done / props.total * 100)) }
})
const statusLabel = computed(() => (props.statusLabels && props.statusLabels[props.status]) || props.status || '')
const elapsed = computed(() => {
  if (!props.createdAt) return ''
  const end = active.value ? nowSec.value : (props.updatedAt || nowSec.value)
  const s = Math.max(0, Math.floor(end - props.createdAt))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  return `${m} 分 ${s % 60} 秒`
})

const statusClassMap = {
  done: 'text-green-600 dark:text-green-400',
  delivered: 'text-amber-600 dark:text-amber-400',
  rejected: 'text-red-600 dark:text-red-400',
  executing: 'text-blue-600 dark:text-blue-400',
  auditing: 'text-indigo-600 dark:text-indigo-400',
  aggregating: 'text-purple-600 dark:text-purple-400',
  planning: 'text-cyan-600 dark:text-cyan-400',
  reworking: 'text-orange-600 dark:text-orange-400',
  running: 'text-blue-600 dark:text-blue-400',
  processing: 'text-blue-600 dark:text-blue-400',
  completed: 'text-green-600 dark:text-green-400',
  failed: 'text-red-600 dark:text-red-400',
}
const statusClass = computed(() => statusClassMap[props.status] || 'text-gray-500 dark:text-gray-400')

function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

function startTimer() {
  stopTimer()
  timer = setInterval(() => { nowSec.value = Math.floor(Date.now() / 1000) }, 1000)
}

// P1-4：脉冲窗口 —— 进入 active 时闪 3 秒后转静态，避免长时间闪烁干扰阅读。
const pulsing = ref(false)
let pulseTimer = null
watch(active, (v) => {
  clearTimeout(pulseTimer)
  if (v) {
    pulsing.value = true
    pulseTimer = setTimeout(() => { pulsing.value = false }, 3000)
  } else {
    pulsing.value = false
  }
}, { immediate: true })

// 秒针须随 active 动态启停，不能在 onMounted 里只判一次：
// ① 挂载时非 active、随后才转 active 的任务，计时器永不启动（耗时恒为 0）；
// ② 任务结束（active→false）后计时器仍每秒空转，直到组件销毁。
watch(active, (v) => { v ? startTimer() : stopTimer() }, { immediate: true })
onUnmounted(() => { stopTimer(); clearTimeout(pulseTimer) })
</script>
