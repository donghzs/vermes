<script setup>
// 工具结果卡：标题 / 状态 / 耗时 / 参数摘要 / 正文（Markdown）或错误。
import { ref, computed } from 'vue'
import MarkdownPreview from './MarkdownPreview.vue'

const props = defineProps({
  item: { type: Object, required: true },
})

const showArgs = ref(false)
const argSummary = computed(() => {
  const a = props.item.args || {}
  const keys = Object.keys(a)
  if (!keys.length) return '（无参数）'
  return keys.map((k) => `${k}=${typeof a[k] === 'string' && a[k].length > 40 ? a[k].slice(0, 40) + '…' : a[k]}`).join('，')
})
</script>

<template>
  <div
    class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 overflow-hidden"
  >
    <div class="flex items-center gap-2 px-3 py-2 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <span class="text-base">{{ item.emoji || '🔧' }}</span>
      <span class="text-sm font-medium">{{ item.name }}</span>
      <span
        class="ml-auto text-xs px-2 py-0.5 rounded-full"
        :class="item.ok ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300' : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'"
      >
        {{ item.ok ? '成功' : '失败' }}
      </span>
      <span class="text-xs text-gray-400">{{ item.ms }}ms</span>
    </div>

    <div class="px-3 py-1.5 text-xs text-gray-500 flex items-center gap-2">
      <button class="hover:underline" @click="showArgs = !showArgs">
        {{ showArgs ? '隐藏参数' : '查看参数' }}
      </button>
      <span v-if="showArgs" class="font-mono break-all">{{ argSummary }}</span>
      <span v-else class="truncate font-mono">{{ argSummary }}</span>
    </div>

    <div class="px-3 py-2">
      <div v-if="item.ok" class="text-gray-800 dark:text-gray-100">
        <MarkdownPreview :content="item.result" />
      </div>
      <p v-else class="text-sm text-red-600 dark:text-red-400 whitespace-pre-wrap">{{ item.error }}</p>
    </div>
  </div>
</template>
