<script setup>
import { onMounted } from 'vue'
import { useMemoryFlagsStore } from '../stores/memoryFlags'

const store = useMemoryFlagsStore()

const flagTypeNames = {
  contradiction: '矛盾',
  outdated: '过时',
  scope_creep: '范围漂移',
  duplicate: '重复',
  hallucination: '幻觉',
}

const typeColor = {
  contradiction: 'text-red-400',
  outdated: 'text-amber-400',
  scope_creep: 'text-purple-400',
  duplicate: 'text-blue-400',
  hallucination: 'text-pink-400',
}

const resolutions = [
  { key: 'false_positive', label: '误报' },
  { key: 'merge', label: '合并' },
  { key: 'demote', label: '降级' },
]

onMounted(() => store.fetchFlags())
</script>

<template>
  <div v-if="store.flags.length > 0"
       class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm mb-3 overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <span class="text-base">🚩</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-300">记忆问题</span>
        <span class="text-xs text-gray-400">{{ store.flags.length }}</span>
      </div>
      <button @click="store.flags = []"
              class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-sm">✕</button>
    </div>
    <!-- Flag 列表 -->
    <div class="max-h-56 overflow-y-auto py-1">
      <div v-for="f in store.flags" :key="f.id"
           class="px-4 py-2 text-xs border-b border-gray-100 dark:border-gray-700 last:border-0">
        <div class="flex items-start gap-2">
          <span class="mt-0.5 flex-shrink-0">🚩</span>
          <div class="flex-1 min-w-0">
            <span class="font-medium" :class="typeColor[f.flag_type] || 'text-gray-400'">
              {{ flagTypeNames[f.flag_type] || f.flag_type }}
            </span>
            <span class="text-gray-500 dark:text-gray-400 ml-1">#{{ f.memory_id }}</span>
            <p class="text-gray-600 dark:text-gray-300 mt-0.5 break-words">{{ f.evidence }}</p>
          </div>
        </div>
        <div class="flex gap-1.5 mt-1.5 flex-wrap">
          <button v-for="r in resolutions" :key="r.key"
                  @click="store.resolveFlag(f.id, r.key)"
                  class="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors">
            {{ r.label }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
