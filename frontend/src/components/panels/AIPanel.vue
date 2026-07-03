<template>
  <div class="h-full flex flex-col">
    <!-- 固定头部 -->
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between shrink-0">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">AI 写作助手</span>
      <div class="flex items-center gap-1">
        <span class="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
        <span class="text-[10px] text-green-600">在线</span>
      </div>
    </div>
    <!-- 消息区：受限高度，可滚动 -->
    <div class="flex-1 overflow-y-auto p-3 space-y-3 max-h-[calc(100vh-20rem)]">
      <!-- AI 对话历史 -->
      <div v-for="(msg, idx) in messages" :key="idx" 
        :class="['text-xs', msg.role === 'user' ? 'ml-4' : 'mr-4']">
        <div :class="['p-2.5 rounded-lg', msg.role === 'user' ? 'bg-green-100 dark:bg-green-900/30 text-gray-800 dark:text-gray-200' : 'bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-300']">
          <div class="flex items-center gap-1 mb-1 text-[10px] text-gray-400">
            <span>{{ msg.role === 'user' ? '👤 你' : '🤖 AI' }}</span>
            <span>{{ formatTime(msg.time) }}</span>
          </div>
          <div class="whitespace-pre-wrap">{{ msg.content }}</div>
        </div>
      </div>
    </div>
    <!-- AI 快捷操作（始终可见，固定在底部） -->
    <div class="p-3 border-t border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-700/50">
      <!-- 研究深度选择器 -->
      <div class="mb-2 flex items-center gap-1.5">
        <span class="text-[10px] text-gray-500">研究深度:</span>
        <button v-for="d in researchDepths" :key="d.value" @click="$emit('update:researchDepth', d.value)"
          :class="['px-2 py-1 rounded text-[10px] transition-colors', researchDepth === d.value ? 'bg-purple-600 text-white' : 'bg-white dark:bg-gray-600 text-gray-500 hover:text-gray-700']">
          {{ d.label }}
        </button>
      </div>
      <!-- 全链路写作按钮 -->
      <button @click="$emit('run-pipeline')" :disabled="streaming"
        class="w-full mb-2 px-3 py-2 bg-gradient-to-r from-purple-600 to-indigo-500 hover:from-purple-500 hover:to-indigo-400 text-white rounded-lg text-xs font-semibold transition-all disabled:opacity-40 flex items-center justify-center gap-1.5"
        title="6阶段全链路：选题→文献综述→大纲→逐章写作→润色→审稿">
        <span>⚡</span> 全链路写作
      </button>
      <div class="grid grid-cols-2 gap-2">
        <button v-for="action in quickActions" :key="action.id" @click="$emit('run-action', action)"
          class="px-2 py-1.5 bg-white dark:bg-gray-600 border border-gray-200 dark:border-gray-500 rounded text-[10px] text-gray-600 dark:text-gray-300 hover:border-purple-500 hover:text-purple-600 transition-colors text-left">
          {{ action.icon }} {{ action.name }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  messages: { type: Array, default: () => [] },
  streaming: { type: Boolean, default: false },
  researchDepth: { type: Number, default: 2 },
  researchDepths: { type: Array, default: () => [] },
  quickActions: { type: Array, default: () => [] },
})

defineEmits(['run-pipeline', 'run-action', 'update:researchDepth', 'close'])

const formatTime = (ts) => {
  const d = new Date(ts)
  return `${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`
}
</script>
