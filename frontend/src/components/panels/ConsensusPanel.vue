<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">共识度分析</span>
      <div class="flex items-center gap-2">
        <button @click="$emit('run')" :disabled="loading"
          class="px-2 py-0.5 bg-green-600 hover:bg-green-700 disabled:opacity-40 text-white rounded text-[10px] flex items-center gap-1">
          <span v-if="loading" class="w-3 h-3 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
          <span v-else>🔄</span>
          分析
        </button>
        <button @click="$emit('close')" class="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭共识度面板" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!results.length && !loading" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">📊</div>
        <p class="text-xs text-gray-400">尚未分析共识度</p>
        <p class="text-[10px] text-gray-400 mt-1">点击上方「分析」按钮，评估文献对论文论断的支持度</p>
      </div>
      <div v-for="(r, idx) in results" :key="idx" class="mx-2 mb-2 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600 overflow-hidden">
        <!-- 论断 -->
        <div class="px-2.5 py-2 border-b border-gray-100 dark:border-gray-600">
          <p class="text-xs text-gray-800 dark:text-gray-200 leading-relaxed">{{ r.claim }}</p>
        </div>
        <!-- 共识度柱 -->
        <div class="px-2.5 py-2">
          <div class="flex items-center gap-2 mb-2">
            <div class="flex-1 h-2.5 bg-gray-100 dark:bg-gray-600 rounded-full overflow-hidden flex">
              <div v-if="r.support > 0" :style="{ width: (r.support / r.total * 100) + '%' }"
                class="h-full bg-green-500 transition-all" :title="'支持 ' + r.support"></div>
              <div v-if="r.neutral > 0" :style="{ width: (r.neutral / r.total * 100) + '%' }"
                class="h-full bg-gray-400 transition-all" :title="'中立 ' + r.neutral"></div>
              <div v-if="r.oppose > 0" :style="{ width: (r.oppose / r.total * 100) + '%' }"
                class="h-full bg-red-500 transition-all" :title="'反对 ' + r.oppose"></div>
            </div>
            <span :class="['text-xs font-bold', r.confidence === 'high' ? 'text-green-600' : r.confidence === 'medium' ? 'text-amber-600' : 'text-red-600']">
              {{ r.consensus_pct }}%
            </span>
          </div>
          <!-- 数字徽章 -->
          <div class="flex items-center gap-3 text-[10px]">
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-green-500 inline-block"></span> 👍 {{ r.support }}</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-gray-400 inline-block"></span> 😐 {{ r.neutral }}</span>
            <span class="flex items-center gap-1"><span class="w-3 h-3 rounded-full bg-red-500 inline-block"></span> 👎 {{ r.oppose }}</span>
          </div>
          <!-- 置信度 -->
          <div class="mt-1.5 flex items-center gap-1">
            <span class="text-[9px] text-gray-400">置信度:</span>
            <span :class="['text-[10px] font-medium px-1.5 py-0.5 rounded', r.confidence === 'high' ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400' : r.confidence === 'medium' ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400' : 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400']">
              {{ r.confidence === 'high' ? '高共识' : r.confidence === 'medium' ? '中共识' : '低共识' }}
            </span>
          </div>
          <!-- 逐文献立场（可展开） -->
          <button v-if="r.per_paper?.length" @click="r._expanded = !r._expanded"
            class="mt-1.5 text-[9px] text-gray-400 hover:text-gray-600 flex items-center gap-1">
            <svg :class="['w-3 h-3 transition-transform', r._expanded ? 'rotate-180' : '']" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
            逐文献详情 ({{ r.per_paper.length }}篇)
          </button>
          <div v-if="r._expanded && r.per_paper?.length" class="mt-1.5 space-y-1 max-h-48 overflow-y-auto">
            <div v-for="pp in r.per_paper" :key="pp.ref"
              :class="['px-2 py-1 rounded text-[10px] flex items-start gap-1.5', pp.stance === 'support' ? 'bg-green-50 dark:bg-green-900/20' : pp.stance === 'oppose' ? 'bg-red-50 dark:bg-red-900/20' : 'bg-gray-50 dark:bg-gray-800']">
              <span :class="['shrink-0 font-mono text-[9px]', pp.stance === 'support' ? 'text-green-600' : pp.stance === 'oppose' ? 'text-red-600' : 'text-gray-500']">[{{ pp.ref }}]</span>
              <span :class="pp.stance === 'support' ? 'text-green-700 dark:text-green-400' : pp.stance === 'oppose' ? 'text-red-700 dark:text-red-400' : 'text-gray-600 dark:text-gray-400'">{{ pp.reason }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  results: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['run', 'close'])
</script>
