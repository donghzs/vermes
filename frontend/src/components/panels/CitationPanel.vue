<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">引用核查</span>
      <div class="flex items-center gap-1.5">
        <span v-if="replacedCount > 0" class="text-[9px] text-green-600 bg-green-50 dark:bg-green-900/30 px-1.5 py-0.5 rounded font-medium">📄 {{ replacedCount }}篇真实文献</span>
        <span v-if="errors > 0" class="w-2 h-2 rounded-full bg-red-500"></span>
        <span v-if="warnings > 0" class="w-2 h-2 rounded-full bg-amber-500"></span>
        <span class="text-[10px] text-gray-400">
          <span v-if="errors > 0" class="text-red-500">{{ errors }}错误</span>
          <span v-if="errors > 0 && warnings > 0"> / </span>
          <span v-if="warnings > 0" class="text-amber-500">{{ warnings }}警告</span>
          <span v-if="errors === 0 && warnings === 0">全部通过</span>
        </span>
        <button @click="$emit('close')" class="ml-1 p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭引用核查面板" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <!-- 真引用替换状态 -->
      <div v-if="replacedList.length > 0" class="mx-2 mb-2 px-2.5 py-2 bg-green-50 dark:bg-green-900/20 rounded-lg border border-green-200 dark:border-green-800">
        <div class="flex items-center gap-1.5 mb-1.5">
          <span class="text-[10px] font-medium text-green-700 dark:text-green-300">📄 已匹配 {{ replacedCount }} 篇真实文献</span>
          <span class="text-[9px] text-green-500">(CrossRef / Semantic Scholar / DBLP)</span>
        </div>
        <div class="space-y-0.5 max-h-32 overflow-y-auto">
          <div v-for="(c, i) in replacedList.slice(0, 5)" :key="i" class="text-[9px] text-gray-500 dark:text-gray-400">
            <span class="font-mono text-green-600">[{{ i+1 }}]</span> {{ c.title }}
            <span class="text-gray-400"> — {{ c.source }} {{ c.year }}</span>
          </div>
        </div>
      </div>
      <div v-if="!results.length && !replacedList.length" class="px-3 py-8 text-center">
        <p class="text-xs text-gray-400">尚未运行引用核查</p>
        <p class="text-[10px] text-gray-400 mt-1">运行「润色」Agent 将自动核查</p>
      </div>
      <div v-for="r in results" :key="r.ref"
        :class="['mx-2 mb-1 px-2.5 py-2 rounded-lg text-xs', r.score >= 7 ? 'bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800' : r.score >= 3 ? 'bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800' : 'bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800']">
        <div class="flex items-start gap-1.5">
          <span :class="['shrink-0 font-mono text-[10px] px-1 py-0.5 rounded', r.score >= 7 ? 'bg-green-200 dark:bg-green-800 text-green-800 dark:text-green-200' : r.score >= 3 ? 'bg-amber-200 dark:bg-amber-800 text-amber-800 dark:text-amber-200' : 'bg-red-200 dark:bg-red-800 text-red-800 dark:text-red-200']">[{{ r.ref }}]</span>
          <div class="flex-1 min-w-0">
            <div :class="r.score >= 7 ? 'text-green-700 dark:text-green-300' : r.score >= 3 ? 'text-amber-700 dark:text-amber-300' : 'text-red-700 dark:text-red-300'">{{ r.reason }}</div>
            <div class="flex items-center gap-2 mt-1">
              <div class="flex-1 h-1 bg-gray-200 dark:bg-gray-700 rounded-full">
                <div :class="['h-1 rounded-full', r.score >= 7 ? 'bg-green-500' : r.score >= 3 ? 'bg-amber-500' : 'bg-red-500']" :style="{ width: (r.score * 10) + '%' }"></div>
              </div>
              <span class="text-[9px] text-gray-400">{{ r.score }}/10</span>
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
  replacedList: { type: Array, default: () => [] },
  replacedCount: { type: Number, default: 0 },
  errors: { type: Number, default: 0 },
  warnings: { type: Number, default: 0 },
})

defineEmits(['close'])
</script>
