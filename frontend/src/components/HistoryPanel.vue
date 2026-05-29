<script setup>
import { ref, computed } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

const show = ref(false)
const historyKeyword = ref('')
const historyDateFilter = ref('all')

const historyResults = computed(() => {
  return chat.searchAllMessages(historyKeyword.value, historyDateFilter.value)
})

function jumpToHistoryItem(item) {
  chat.switchSession(item.sessionId)
  show.value = false
}

function toggle() {
  show.value = !show.value
}

defineExpose({ toggle })
</script>

<template>
  <div v-if="show" class="fixed inset-0 z-50 flex justify-end" @click.self="show = false">
    <div class="absolute inset-0 bg-black/30" @click="show = false"></div>
    <div class="relative w-96 max-w-full bg-white dark:bg-gray-800 shadow-2xl flex flex-col h-full animate-slide-in-right">
      <!-- 头部 -->
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <h3 class="font-semibold text-gray-800 dark:text-gray-200">📋 历史记录</h3>
        <button @click="show = false" class="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 transition">✕</button>
      </div>
      <!-- 搜索和过滤 -->
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 space-y-2">
        <input v-model="historyKeyword" type="text" placeholder="搜索消息内容..."
          class="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-200 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-green-400" />
        <div class="flex gap-1">
          <button v-for="opt in [{v:'all',l:'全部'},{v:'today',l:'今天'},{v:'week',l:'本周'},{v:'month',l:'本月'}]" :key="opt.v"
            @click="historyDateFilter = opt.v"
            class="px-2.5 py-1 text-xs rounded-full transition"
            :class="historyDateFilter === opt.v ? 'bg-green-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'">
            {{ opt.l }}
          </button>
        </div>
      </div>
      <!-- 结果列表 -->
      <div class="flex-1 overflow-y-auto">
        <div v-if="historyResults.length === 0" class="text-center text-gray-400 text-sm py-12">
          {{ historyKeyword ? '没有找到匹配的消息' : '暂无历史消息' }}
        </div>
        <div v-for="item in historyResults.slice(0, 100)" :key="item.id + item.sessionId"
          @click="jumpToHistoryItem(item)"
          class="px-4 py-3 border-b border-gray-100 dark:border-gray-700 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700/50 transition">
          <div class="flex items-center justify-between mb-1">
            <span class="text-xs font-medium text-green-600 dark:text-green-400 truncate">{{ item.sessionName }}</span>
            <span class="text-[10px] text-gray-400 shrink-0 ml-2">{{ item.timestamp ? new Date(item.timestamp).toLocaleString('zh-CN', {month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}) : '' }}</span>
          </div>
          <div class="text-sm text-gray-600 dark:text-gray-300 truncate">
            <span class="text-[10px] px-1 py-0.5 rounded mr-1" :class="item.role === 'user' ? 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400' : 'bg-green-100 dark:bg-green-900/30 text-green-600 dark:text-green-400'">{{ item.role === 'user' ? '我' : 'AI' }}</span>
            {{ item.snippet }}
          </div>
        </div>
      </div>
      <!-- 底部统计 -->
      <div class="px-4 py-2 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-400 text-center">
        共 {{ historyResults.length }} 条记录
      </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes slide-in-right {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}
.animate-slide-in-right {
  animation: slide-in-right 0.25s ease-out;
}
</style>
