<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-yellow-500 uppercase tracking-wider">★ 论文评分</span>
      <div class="flex items-center gap-2">
        <button @click="$emit('run')" :disabled="loading"
          class="px-2 py-0.5 bg-yellow-600 hover:bg-yellow-700 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1">
          <span v-if="loading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span v-else>🔄</span>
          评分
        </button>
        <button @click="$emit('close')" class="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!result" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">⭐</div>
        <p class="text-xs text-gray-400">尚未评分</p>
        <p class="text-[10px] text-gray-400 mt-1">点击上方「评分」按钮进行六维评审</p>
      </div>
      <template v-if="result">
        <!-- Halt 警告 -->
        <div v-if="result.halt" class="mx-2 mb-2 p-2 bg-red-50 dark:bg-red-900/20 border border-red-300 dark:border-red-700 rounded-lg">
          <p class="text-[11px] font-bold text-red-700 dark:text-red-300">⛔ Halt: 综合评分 &lt; 40</p>
          <p class="text-[10px] text-red-600 dark:text-red-400 mt-0.5">论文质量不足，建议大幅重写后重新提交</p>
        </div>

        <!-- 综合评分卡片 -->
        <div class="mx-2 mb-3 p-3 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
          <div class="flex items-center gap-3">
            <div class="relative w-16 h-16 flex-shrink-0">
              <svg class="w-16 h-16 -rotate-90">
                <circle cx="32" cy="32" r="28" fill="none" stroke="currentColor" stroke-width="5" class="text-gray-200 dark:text-gray-600" />
                <circle cx="32" cy="32" r="28" fill="none"
                  :stroke="overallColor"
                  stroke-width="5" stroke-linecap="round"
                  :stroke-dasharray="Math.round((result.overall || result.score * 10 || 0) / 100 * 176) + ' 176'" />
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center">
                <span class="text-lg font-bold" :class="overallTextColor">{{ result.overall ?? (result.score ? Math.round(result.score * 10) : '—') }}</span>
                <span class="text-[9px] text-gray-400">/ 100</span>
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-0.5">综合评分</div>
              <div class="text-[10px] text-gray-500 leading-relaxed line-clamp-3">{{ result.fatal && result.fatal !== '无' ? '⚠️ ' + result.fatal : '基于六维 LLM 深度评审' }}</div>
            </div>
          </div>
        </div>

        <!-- 六维评分 -->
        <div class="mx-2 mb-3">
          <div class="text-[10px] font-medium text-gray-500 mb-1.5">📊 六维结构化评分</div>
          <div v-for="dim in dimensionList" :key="dim.key" class="mb-1.5 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600 overflow-hidden">
            <div class="px-2.5 py-2 flex items-center gap-2">
              <span class="text-base">{{ dim.icon }}</span>
              <div class="flex-1 min-w-0">
                <div class="flex items-center justify-between mb-1">
                  <span class="text-[11px] font-medium text-gray-700 dark:text-gray-200">{{ dim.label }}</span>
                  <span :class="['text-xs font-bold', scoreColor(dim.score)]">
                    {{ dim.score ?? '—' }}
                    <span class="text-[9px] text-gray-400">/10</span>
                  </span>
                </div>
                <div class="w-full h-1.5 bg-gray-100 dark:bg-gray-600 rounded-full overflow-hidden">
                  <div :class="['h-full rounded-full transition-all', scoreBarColor(dim.score)]"
                    :style="{ width: ((dim.score || 0) / 10 * 100) + '%' }"></div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 修改建议 -->
        <div v-if="suggestionsList.length" class="mx-2 mb-2">
          <div class="text-[10px] font-medium text-green-600 mb-1.5">✏️ 修改建议 ({{ suggestionsList.length }})</div>
          <div v-for="(s, i) in suggestionsList" :key="'sug'+i"
            class="mb-1 p-2 bg-green-50 dark:bg-green-900/15 rounded text-[10px] text-gray-600 dark:text-gray-300 leading-relaxed">
            {{ i + 1 }}. {{ s }}
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

defineEmits(['run', 'close'])

const DIMENSION_META = [
  { key: '创新性', icon: '💡', label: '创新性' },
  { key: '方法论', icon: '🔬', label: '方法论' },
  { key: '论证逻辑', icon: '🔗', label: '论证逻辑' },
  { key: '语言表达', icon: '✍️', label: '语言表达' },
  { key: '引用完整性', icon: '📚', label: '引用完整性' },
  { key: '数据真实性', icon: '📊', label: '数据真实性' },
]

const dimensionList = computed(() => {
  if (!props.result) return DIMENSION_META
  // 优先从 six_dim 取值
  const six = props.result.six_dim || {}
  // 兼容旧格式 dimensions 数组
  const oldDims = {}
  if (props.result.dimensions) {
    for (const d of props.result.dimensions) {
      oldDims[d.name] = d.score
    }
  }
  return DIMENSION_META.map(dim => ({
    ...dim,
    score: six[dim.key] ?? oldDims[dim.key] ?? null,
  }))
})

const suggestionsList = computed(() => {
  if (!props.result) return []
  // 修改建议可能在 report 里或独立字段
  if (props.result.suggestions) return props.result.suggestions
  return []
})

function scoreColor(v) {
  if (v == null) return 'text-gray-400'
  if (v >= 7) return 'text-green-600'
  if (v >= 5) return 'text-amber-600'
  return 'text-red-600'
}

function scoreBarColor(v) {
  if (v == null) return 'bg-gray-300'
  if (v >= 7) return 'bg-green-500'
  if (v >= 5) return 'bg-amber-500'
  return 'bg-red-500'
}

const overallColor = computed(() => {
  const v = props.result?.overall ?? (props.result?.score ? Math.round(props.result.score * 10) : null)
  if (v == null) return '#9ca3af'
  if (v >= 70) return '#22c55e'
  if (v >= 40) return '#f59e0b'
  return '#ef4444'
})

const overallTextColor = computed(() => {
  const v = props.result?.overall ?? (props.result?.score ? Math.round(props.result.score * 10) : null)
  if (v == null) return 'text-gray-400'
  if (v >= 70) return 'text-green-600'
  if (v >= 40) return 'text-amber-600'
  return 'text-red-600'
})
</script>
