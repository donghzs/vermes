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
        <button @click="$emit('close')" class="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭评分面板" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!result" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">⭐</div>
        <p class="text-xs text-gray-400">尚未评分</p>
        <p class="text-[10px] text-gray-400 mt-1">点击上方「评分」按钮进行三维度评估</p>
      </div>
      <template v-if="result">
        <div v-if="result._is_fallback" class="mx-2 mb-2 px-3 py-2 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg">
          <p class="text-[10px] text-amber-700 dark:text-amber-400">⚠️ 以下为启发式估算（非 LLM 评估）。请为论文 Agent 配置 API Key 以获得基于 LLM 的准确评分。</p>
        </div>
        <div class="mx-2 mb-3 p-3 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
          <div class="flex items-center gap-3">
            <div class="relative w-16 h-16 flex-shrink-0">
              <svg class="w-16 h-16 -rotate-90">
                <circle cx="32" cy="32" r="28" fill="none" stroke="currentColor" stroke-width="5" class="text-gray-200 dark:text-gray-600" />
                <circle cx="32" cy="32" r="28" fill="none"
                  :stroke="result.overall >= 7 ? '#22c55e' : result.overall >= 5 ? '#f59e0b' : '#ef4444'"
                  stroke-width="5" stroke-linecap="round"
                  :stroke-dasharray="Math.round(result.overall / 10 * 176) + ' 176'" />
              </svg>
              <div class="absolute inset-0 flex flex-col items-center justify-center">
                <span class="text-lg font-bold" :class="result.overall >= 7 ? 'text-green-600' : result.overall >= 5 ? 'text-amber-600' : 'text-red-600'">{{ result.overall }}</span>
                <span class="text-[9px] text-gray-400">/ 10</span>
              </div>
            </div>
            <div class="flex-1 min-w-0">
              <div class="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-0.5">综合评分</div>
              <div class="text-[10px] text-gray-500 leading-relaxed line-clamp-3">{{ result.overall_reasoning }}</div>
            </div>
          </div>
        </div>
        <div v-for="dim in dimensions" :key="dim.key" class="mx-2 mb-2 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600 overflow-hidden">
          <div class="px-2.5 py-2 flex items-center gap-2">
            <span class="text-base">{{ dim.icon }}</span>
            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between mb-1">
                <span class="text-[11px] font-medium text-gray-700 dark:text-gray-200">{{ dim.label }}</span>
                <span :class="['text-xs font-bold', (result[dim.key]?.score || 0) >= 7 ? 'text-green-600' : (result[dim.key]?.score || 0) >= 5 ? 'text-amber-600' : 'text-red-600']">
                  {{ result[dim.key]?.score || '—' }}
                  <span class="text-[9px] text-gray-400">/10</span>
                </span>
              </div>
              <div class="w-full h-1.5 bg-gray-100 dark:bg-gray-600 rounded-full overflow-hidden">
                <div :class="['h-full rounded-full transition-all', (result[dim.key]?.score || 0) >= 7 ? 'bg-green-500' : (result[dim.key]?.score || 0) >= 5 ? 'bg-amber-500' : 'bg-red-500']"
                  :style="{ width: ((result[dim.key]?.score || 0) / 10 * 100) + '%' }"></div>
              </div>
            </div>
          </div>
          <div v-if="result[dim.key]?.reasoning" class="px-2.5 pb-2">
            <p class="text-[10px] text-gray-500 leading-relaxed">{{ result[dim.key]?.reasoning }}</p>
          </div>
        </div>
        <div class="mx-2 mb-2 p-2 bg-gray-50 dark:bg-gray-700/50 rounded text-[9px] text-gray-400 text-center">
          综合评分 = 原创性×30% + 逻辑性×35% + 引用完整性×35%
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  dimensions: { type: Array, default: () => [] },
})

defineEmits(['run', 'close'])
</script>
