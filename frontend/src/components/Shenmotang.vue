<script setup>
import { ref } from 'vue'
import BotRooms from './BotRooms.vue'
import AgentsPage from './AgentsPage.vue'

// ⛩️ 神魔堂：单一入口，融合「诸神会晤（群聊）」与「神魔架（请神/造神）」。
// 用户定调（2026-09-05 董董）：群聊与智能体市场本就是神魔堂一体两面，
// 不必拆两个前端入口——融合混淆，一个门进去。
const tab = ref('hall') // 'hall' = 诸神会晤 · 'roster' = 神魔架
</script>

<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部：神魔堂标题 + tab 切换 -->
    <div class="px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-4">
      <div class="flex items-center gap-2">
        <span class="text-2xl">⛩️</span>
        <h1 class="text-lg font-bold">神魔堂</h1>
        <span class="text-xs text-gray-400">诸神会晤 · 请神登堂 · 造神协作</span>
      </div>
      <div class="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-800 p-1">
        <button
          class="px-3 py-1.5 text-sm rounded-md transition"
          :class="tab === 'hall' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
          @click="tab = 'hall'"
        >💬 诸神会晤</button>
        <button
          class="px-3 py-1.5 text-sm rounded-md transition"
          :class="tab === 'roster' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
          @click="tab = 'roster'"
        >🔥 神魔架</button>
      </div>
    </div>

    <!-- 内容：两个 tab 复用现有组件 -->
    <div class="flex-1 min-h-0">
      <BotRooms v-show="tab === 'hall'" class="h-full" />
      <AgentsPage v-show="tab === 'roster'" class="h-full" />
    </div>
  </div>
</template>
