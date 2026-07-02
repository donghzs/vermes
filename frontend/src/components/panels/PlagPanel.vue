<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">查重 + AIGC</span>
      <div class="flex items-center gap-2">
        <button @click="$emit('run')" :disabled="loading"
          class="px-2 py-0.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1">
          <span v-if="loading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span v-else>🔄</span>
          检测
        </button>
        <button @click="$emit('close')" class="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭查重面板" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!result" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">🔍</div>
        <p class="text-xs text-gray-400">尚未检测</p>
        <p class="text-[10px] text-gray-400 mt-1">点击上方「检测」按钮进行查重和 AIGC 分析</p>
      </div>
      <template v-if="result">
        <!-- 综合评分卡片 -->
        <div class="mx-2 mb-3 p-3 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
          <div class="flex items-center gap-3 mb-2">
            <!-- 重复率环 -->
            <div class="relative w-14 h-14 flex-shrink-0">
              <svg class="w-14 h-14 -rotate-90">
                <circle cx="28" cy="28" r="24" fill="none" stroke="currentColor" stroke-width="4" class="text-gray-200 dark:text-gray-600" />
                <circle cx="28" cy="28" r="24" fill="none" stroke="currentColor" stroke-width="4" stroke-linecap="round"
                  :stroke="result.overall_similarity > 0.3 ? '#ef4444' : '#22c55e'"
                  :stroke-dasharray="Math.round(result.overall_similarity * 151) + ' 151'" />
              </svg>
              <span class="absolute inset-0 flex items-center justify-center text-xs font-bold text-gray-700 dark:text-gray-200">{{ Math.round(result.overall_similarity * 100) }}%</span>
            </div>
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-1">
                <span class="text-sm font-semibold text-gray-800 dark:text-gray-100">查重率</span>
                <span :class="['text-xs px-1.5 py-0.5 rounded', result.overall_similarity > 0.3 ? 'bg-red-100 text-red-700' : result.overall_similarity > 0.15 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700']">
                  {{ result.overall_similarity > 0.3 ? '偏高' : result.overall_similarity > 0.15 ? '中等' : '良好' }}
                </span>
              </div>
              <div class="text-[10px] text-gray-500">{{ result.total_chars.toLocaleString() }} 字 · {{ result.total_paragraphs }} 段</div>
            </div>
          </div>
          <!-- AIGC 痕迹 -->
          <div class="mt-2 pt-2 border-t border-gray-100 dark:border-gray-600">
            <div class="flex items-center justify-between mb-1">
              <span class="text-[11px] text-gray-600 dark:text-gray-300">🤖 AIGC 痕迹</span>
              <span :class="['text-xs font-bold', result.aigc_overall_ratio > 0.4 ? 'text-red-600' : result.aigc_overall_ratio > 0.2 ? 'text-amber-600' : 'text-green-600']">
                {{ Math.round(result.aigc_overall_ratio * 100) }}%
              </span>
            </div>
            <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
              <div :class="['h-full rounded-full transition-all', result.aigc_overall_ratio > 0.4 ? 'bg-red-500' : result.aigc_overall_ratio > 0.2 ? 'bg-amber-500' : 'bg-green-500']"
                :style="{ width: Math.round(result.aigc_overall_ratio * 100) + '%' }"></div>
            </div>
            <div class="flex justify-between text-[9px] text-gray-400 mt-0.5">
              <span>人类写作</span><span>AI 辅助</span><span>AI 生成</span>
            </div>
          </div>
        </div>

        <!-- 重复片段 -->
        <div v-if="result.plag_results?.length" class="mx-2 mb-2">
          <div class="flex items-center gap-1.5 mb-1.5">
            <span class="text-[10px] font-medium text-red-600">🔴 重复段落 ({{ result.plag_results.length }})</span>
          </div>
          <div v-for="(p, i) in result.plag_results" :key="'plag'+i"
            class="mb-1.5 p-2 bg-red-50 dark:bg-red-900/15 rounded border border-red-200 dark:border-red-800">
            <p class="text-xs text-red-800 dark:text-red-300 line-clamp-3">{{ p.text }}</p>
            <div class="mt-1 text-[9px] text-red-500">相似度 {{ Math.round(p.score * 100) }}% · {{ p.length }}字</div>
          </div>
        </div>

        <!-- AIGC 段落 -->
        <div v-if="result.aigc_results?.length" class="mx-2 mb-2">
          <div class="flex items-center gap-1.5 mb-1.5">
            <span class="text-[10px] font-medium text-amber-600">🟡 AI 痕迹段落 ({{ result.aigc_results.length }})</span>
          </div>
          <div v-for="(a, i) in result.aigc_results" :key="'aigc'+i"
            class="mb-1.5 p-2 bg-amber-50 dark:bg-amber-900/15 rounded border border-amber-200 dark:border-amber-800">
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-1">
                <span v-for="f in a.features" :key="f" class="text-[8px] px-1 py-0.5 bg-amber-200 dark:bg-amber-800 text-amber-700 dark:text-amber-300 rounded">{{ f }}</span>
              </div>
              <span class="text-[9px] font-bold text-amber-600">{{ Math.round(a.aigc_probability * 100) }}%</span>
            </div>
            <p class="text-xs text-amber-800 dark:text-amber-300 line-clamp-3">{{ a.text }}</p>
          </div>
        </div>

        <!-- 建议 -->
        <div v-if="result.suggestions?.length" class="mx-2 mb-2">
          <div class="text-[10px] font-medium text-gray-500 mb-1.5">💡 改进建议</div>
          <div v-for="(s, i) in result.suggestions" :key="'sug'+i"
            class="mb-1 p-2 bg-blue-50 dark:bg-blue-900/15 rounded text-[11px] text-blue-700 dark:text-blue-300 leading-relaxed">
            {{ s }}
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
defineProps({
  result: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

defineEmits(['run', 'close'])
</script>
