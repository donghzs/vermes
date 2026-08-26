<template>
  <div class="delivery-card my-3 mx-2 rounded-xl border border-green-200 dark:border-green-800 bg-gradient-to-br from-green-50 to-white dark:from-green-900/20 dark:to-gray-800 overflow-hidden shadow-sm">
    <!-- 头部 -->
    <div class="flex items-center gap-2 px-4 py-2.5 bg-green-50 dark:bg-green-900/30 border-b border-green-100 dark:border-green-800/50">
      <span class="text-lg">✅</span>
      <span class="font-semibold text-sm text-green-700 dark:text-green-300">任务完成</span>
      <span class="text-xs text-gray-500 dark:text-gray-400 ml-auto">
        完成 {{ summary.completed }}/{{ summary.total }} 步
        <span v-if="duration" class="ml-1">· 耗时 {{ duration }}</span>
      </span>
    </div>

    <!-- 产物列表 -->
    <div v-if="artifacts.length > 0" class="px-4 py-2.5 border-b border-gray-100 dark:border-gray-700/50">
      <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">📦 交付产物 ({{ artifacts.length }})</div>
      <div class="space-y-1">
        <button
          v-for="a in artifacts.slice(0, 5)"
          :key="a.id"
          @click="openArtifact(a)"
          class="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition group"
        >
          <span class="text-sm">{{ iconFor(a) }}</span>
          <span class="text-xs text-gray-700 dark:text-gray-300 truncate flex-1 group-hover:text-green-600 dark:group-hover:text-green-400 transition">
            {{ a.title || a.path?.split('/').pop() || '未知文件' }}
          </span>
          <span class="text-[10px] text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition">点击查看 ›</span>
        </button>
        <button
          v-if="artifacts.length > 5"
          @click="showAllArtifacts"
          class="text-xs text-gray-500 dark:text-gray-400 hover:text-green-600 dark:hover:text-green-400 px-2 py-1"
        >
          查看全部 {{ artifacts.length }} 个产物 ›
        </button>
      </div>
    </div>

    <!-- 变更统计 -->
    <div v-if="changes.length > 0" class="px-4 py-2 border-b border-gray-100 dark:border-gray-700/50">
      <button @click="showAllChanges" class="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition">
        <span>🔧</span>
        <span>{{ changes.length }} 项文件变更</span>
        <span class="ml-auto">查看 ›</span>
      </button>
    </div>

    <!-- 结论摘要 -->
    <div v-if="summaryText" class="px-4 py-2.5">
      <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">📝 结论摘要</div>
      <div class="text-xs text-gray-600 dark:text-gray-300 leading-relaxed">{{ summaryText }}</div>
    </div>

    <!-- 空态 -->
    <div v-if="artifacts.length === 0 && changes.length === 0 && !summaryText" class="px-4 py-3 text-xs text-gray-400 dark:text-gray-500 text-center">
      任务已完成（无产物产出）
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  summary: { type: Object, default: () => ({ total: 0, completed: 0, in_progress: 0 }) },
  artifacts: { type: Array, default: () => [] },
  changes: { type: Array, default: () => [] },
  summaryText: { type: String, default: '' },
  startTime: { type: Number, default: 0 },
  endTime: { type: Number, default: 0 },
})

const emit = defineEmits(['openArtifact', 'showAllArtifacts', 'showAllChanges'])

const duration = computed(() => {
  if (!props.startTime || !props.endTime) return ''
  const secs = Math.round((props.endTime - props.startTime) / 1000)
  if (secs < 60) return `${secs}秒`
  const mins = Math.floor(secs / 60)
  const rem = secs % 60
  return rem ? `${mins}分${rem}秒` : `${mins}分钟`
})

function iconFor(a) {
  const ext = (a.path || '').split('.').pop()?.toLowerCase()
  if (['md', 'markdown'].includes(ext)) return '📄'
  if (['html', 'htm'].includes(ext)) return '🌐'
  if (['pdf'].includes(ext)) return '📕'
  if (['docx', 'doc'].includes(ext)) return '📘'
  if (['xlsx', 'xls', 'csv'].includes(ext)) return '📊'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️'
  if (['py', 'js', 'ts', 'sh'].includes(ext)) return '🐍'
  return '📄'
}

function openArtifact(a) {
  emit('openArtifact', a)
}
function showAllArtifacts() {
  emit('showAllArtifacts')
}
function showAllChanges() {
  emit('showAllChanges')
}
</script>

<style scoped>
.delivery-card {
  max-width: 420px;
}
</style>
