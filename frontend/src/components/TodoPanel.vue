<script setup>
import { computed, ref } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

// P1-1（2026-09-22）: 默认折叠为单行摘要，点击才展开完整列表。
// 原实现常驻整块列表（max-h-48 ≈ 192px）且给 in_progress 项加蓝底，
// 长任务期间持续占据聊天区顶部。折叠态高度 ≈ 28px。
// 展开态**不持久化**：每轮新任务都以最小占位开始，避免一次性展开后再也收不回去。
const expanded = ref(false)

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

// 单行摘要：「3/7 · 正在：XXX」——不展开也能知道当前在哪一步
const currentItem = computed(() => chat.todoItems.find(i => i.status === 'in_progress') || null)
const summary = computed(() => {
  const s = stats.value
  if (s.total === 0) return ''
  const parts = [`${s.completed}/${s.total}`]
  if (currentItem.value) {
    parts.push(`正在：${currentItem.value.content}`)
  } else if (s.completed === s.total) {
    parts.push('全部完成')
  } else if (s.pending) {
    parts.push(`${s.pending} 项待办`)
  }
  return parts.join(' · ')
})

const statusIcon = {
  pending: '⬜',
  in_progress: '🔄',
  completed: '✅',
  cancelled: '❌',
}

</script>

<template>
  <div v-if="chat.showTodoPanel && chat.todoItems.length > 0"
       class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm mb-3 overflow-hidden">
    <!-- 单行摘要（常驻）：点击展开/折叠完整列表 -->
    <div class="flex items-center gap-2 px-4 py-1.5 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700 cursor-pointer select-none hover:bg-gray-100 dark:hover:bg-gray-700/50 transition-colors"
         :class="{ 'border-b-0': !expanded }"
         @click="expanded = !expanded">
      <span class="text-xs">📋</span>
      <span class="text-xs text-gray-500 dark:text-gray-400 shrink-0">任务</span>
      <span class="text-xs text-gray-700 dark:text-gray-300 truncate flex-1 min-w-0" :title="summary">{{ summary }}</span>
      <div class="hidden sm:block w-16 h-1 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden shrink-0">
        <div class="h-full bg-green-500 transition-all duration-300" :style="{ width: progressPercent + '%' }"></div>
      </div>
      <span class="text-[11px] text-gray-400 shrink-0">{{ progressPercent }}%</span>
      <span class="text-[10px] text-gray-400 shrink-0">{{ expanded ? '▲' : '▼' }}</span>
      <button @click.stop="chat.showTodoPanel = false"
              class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-xs shrink-0"
              title="关闭任务面板">✕</button>
    </div>
    <!-- 完整列表（仅展开时渲染） -->
    <div v-if="expanded" class="max-h-48 overflow-y-auto py-1">
      <div v-for="item in chat.todoItems" :key="item.id"
           class="flex items-start gap-2 px-4 py-1.5 text-xs transition-colors"
           :class="{
             'bg-blue-50 dark:bg-blue-900/10': item.status === 'in_progress',
             'opacity-50': item.status === 'cancelled',
           }">
        <span class="mt-0.5 flex-shrink-0">{{ statusIcon[item.status] || '❓' }}</span>
        <div class="flex-1 min-w-0">
          <span class="text-gray-700 dark:text-gray-300 break-words"
                :class="{ 'line-through': item.status === 'completed' || item.status === 'cancelled' }">
            {{ item.content }}
          </span>
        </div>
        <span v-if="item.status === 'in_progress'"
              class="flex-shrink-0 text-[10px] px-1.5 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 rounded-full">
          进行中
        </span>
      </div>
    </div>
  </div>
</template>
