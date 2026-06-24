<script setup>
import { computed } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

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

const statusIcon = {
  pending: '⬜',
  in_progress: '🔄',
  completed: '✅',
  cancelled: '❌',
}

const statusColor = {
  pending: 'text-gray-400',
  in_progress: 'text-blue-500',
  completed: 'text-green-500',
  cancelled: 'text-red-400',
}
</script>

<template>
  <div v-if="chat.showTodoPanel && chat.todoItems.length > 0"
       class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm mb-3 overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <span class="text-base">📋</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-300">任务进度</span>
        <span class="text-xs text-gray-400">{{ stats.completed }}/{{ stats.total }}</span>
      </div>
      <div class="flex items-center gap-2">
        <div class="w-24 h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
          <div class="h-full bg-green-500 transition-all duration-300" :style="{ width: progressPercent + '%' }"></div>
        </div>
        <span class="text-xs text-gray-400">{{ progressPercent }}%</span>
        <button @click="chat.showTodoPanel = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-sm">✕</button>
      </div>
    </div>
    <!-- Todo Items -->
    <div class="max-h-48 overflow-y-auto py-1">
      <div v-for="item in chat.todoItems" :key="item.id"
           class="flex items-start gap-2 px-4 py-1.5 text-xs transition-colors"
           :class="{
             'bg-blue-50 dark:bg-blue-900/10': item.status === 'in_progress',
             'opacity-50': item.status === 'cancelled',
           }">
        <span class="mt-0.5 flex-shrink-0">{{ statusIcon[item.status] || '❓' }}</span>
        <div class="flex-1 min-w-0">
          <span class="text-gray-700 dark:text-gray-300"
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
