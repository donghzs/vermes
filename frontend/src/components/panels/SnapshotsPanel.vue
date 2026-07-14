<template>
  <div>
    <div class="px-3 py-2 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
      <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">版本历史</span>
      <div class="flex items-center gap-2">
        <button @click="$emit('create')" class="px-2 py-0.5 bg-violet-600 hover:bg-violet-700 text-white rounded text-[10px] flex items-center gap-1">
          💾 存快照
        </button>
        <button @click="$emit('close')" class="p-0.5 hover:bg-gray-200 dark:hover:bg-gray-600 rounded text-gray-400 hover:text-gray-600" aria-label="关闭版本历史面板" title="关闭">✕</button>
      </div>
    </div>
    <div class="flex-1 overflow-y-auto py-2">
      <div v-if="!snapshots.length && !loading" class="px-3 py-8 text-center">
        <div class="text-3xl mb-2">⏱️</div>
        <p class="text-xs text-gray-400">暂无版本快照</p>
        <p class="text-[10px] text-gray-400 mt-1">点击上方「💾 存快照」保存当前全文状态</p>
      </div>
      <div v-for="snap in snapshots" :key="snap.id"
        class="mx-2 mb-2 p-2.5 bg-white dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600 hover:border-violet-400 transition-colors">
        <div class="flex items-center justify-between mb-1">
          <span class="text-xs font-medium text-gray-800 dark:text-gray-200 truncate max-w-[160px]">{{ snap.label || '未命名快照' }}</span>
          <span class="text-[9px] text-gray-400">{{ formatTime(snap.created_at) }}</span>
        </div>
        <div v-if="snap.note" class="text-[10px] text-gray-500 mb-1.5">{{ snap.note }}</div>
        <div class="flex items-center gap-2">
          <span class="text-[9px] text-gray-400">{{ (snap.size / 1024).toFixed(1) }} KB</span>
          <div class="flex-1"></div>
          <button @click="$emit('restore', snap)" class="text-[10px] text-violet-600 hover:text-violet-700">恢复</button>
          <button @click="$emit('delete', snap.id)" class="text-[10px] text-red-400 hover:text-red-600">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  snapshots: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['create', 'restore', 'delete', 'close'])

const formatTime = (ts) => {
  const d = new Date(ts)
  return `${d.getHours()}:${d.getMinutes().toString().padStart(2, '0')}`
}
</script>
