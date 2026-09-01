<script setup>
import { onMounted, ref, computed } from 'vue'
import { useMemoryFlagsStore } from '../stores/memoryFlags'
import { showToast } from '../utils/toast'

const store = useMemoryFlagsStore()
const showResolved = ref(false)

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

const resolutionLabels = {
  demote: '降级',
  merge: '合并标记',
  false_positive: '误报',
}

const resolutions = [
  { key: 'false_positive', label: '误报' },
  { key: 'merge', label: '合并' },
  { key: 'demote', label: '降级' },
]

// 批量降级：所有 skill source 的 duplicate flags (conf>=0.9)
async function batchDemoteSkillDuplicates() {
  const skillDuplicates = store.flags.filter(
    f => f.flag_type === 'duplicate' && f.confidence >= 0.9 && f.source === 'skill'
  )
  let count = 0
  for (const f of skillDuplicates) {
    const ok = await store.resolveFlag(f.id, 'demote')
    if (ok) count++
  }
  if (count > 0) showToast(`已批量降级 ${count} 条重复记忆`)
}

const skillDuplicateCount = computed(() =>
  store.flags.filter(f => f.flag_type === 'duplicate' && f.confidence >= 0.9 && f.source === 'skill').length
)

onMounted(() => {
  store.fetchFlags()
  store.fetchResolved()
})
</script>

<template>
  <!-- open flag > 0：完整面板（需要用户拍板，正当打扰） -->
  <div v-if="store.flags.length > 0"
       class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm mb-3 overflow-hidden">
    <!-- Header -->
    <div class="flex items-center justify-between px-4 py-2.5 bg-gray-50 dark:bg-gray-750 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <span class="text-base">🚩</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-300">记忆问题</span>
        <span class="text-xs text-gray-400">{{ store.flags.length }} 条待处理</span>
        <button v-if="skillDuplicateCount > 3"
                @click="batchDemoteSkillDuplicates"
                class="text-xs px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900 text-blue-600 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-800 transition-colors ml-1">
          批量降级重复 ({{ skillDuplicateCount }})
        </button>
      </div>
      <div class="flex items-center gap-2">
        <button v-if="store.resolvedTotal > 0"
                @click="showResolved = !showResolved"
                class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition-colors">
          已解决 {{ store.resolvedTotal }} ▾
        </button>
        <button @click="store.flags = []"
                class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-sm">✕</button>
      </div>
    </div>

    <!-- Open flags -->
    <div class="max-h-72 overflow-y-auto py-1">
      <div v-for="f in store.flags" :key="f.id"
           class="px-4 py-2 text-xs border-b border-gray-100 dark:border-gray-700 last:border-0">
        <div class="flex items-start gap-2">
          <span class="mt-0.5 flex-shrink-0">🚩</span>
          <div class="flex-1 min-w-0">
            <span class="font-medium" :class="typeColor[f.flag_type] || 'text-gray-400'">
              {{ flagTypeNames[f.flag_type] || f.flag_type }}
            </span>
            <span class="text-gray-400 ml-1">{{ (f.confidence * 100).toFixed(0) }}%</span>
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

    <!-- Resolved flags (collapsible) -->
    <div v-if="showResolved && store.resolvedFlags.length > 0"
         class="border-t border-gray-200 dark:border-gray-700">
      <div class="px-4 py-2 text-xs bg-gray-50 dark:bg-gray-750 text-gray-500 dark:text-gray-400">
        已解决的记忆问题（可恢复）
      </div>
      <div class="max-h-56 overflow-y-auto py-1">
        <div v-for="f in store.resolvedFlags" :key="'r-'+f.id"
             class="px-4 py-2 text-xs border-b border-gray-100 dark:border-gray-700 last:border-0">
          <div class="flex items-start gap-2">
            <span class="mt-0.5 flex-shrink-0 opacity-50">🚩</span>
            <div class="flex-1 min-w-0">
              <span class="font-medium opacity-60" :class="typeColor[f.flag_type] || 'text-gray-400'">
                {{ flagTypeNames[f.flag_type] || f.flag_type }}
              </span>
              <span class="text-gray-400 ml-1">#{{ f.memory_id }}</span>
              <span class="text-xs text-gray-400 ml-1">→ {{ resolutionLabels[f.resolution] || f.resolution }}</span>
              <p class="text-gray-500 dark:text-gray-400 mt-0.5 break-words opacity-70">{{ f.evidence }}</p>
            </div>
          </div>
          <div class="flex gap-1.5 mt-1.5">
            <button @click="store.restoreFlag(f.id)"
                    class="text-[10px] px-2 py-0.5 rounded-full bg-green-50 dark:bg-green-900 text-green-600 dark:text-green-300 hover:bg-green-100 dark:hover:bg-green-800 transition-colors">
              ↩ 恢复
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- open flag = 0：轻量只读角标（不打扰，点开才看已解决详情） -->
  <div v-else-if="store.resolvedTotal > 0"
       class="mb-3">
    <button @click="showResolved = !showResolved"
            class="w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs text-gray-400 dark:text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
      <span>✅ 已自动处理 {{ store.resolvedTotal }} 条记忆问题</span>
      <span class="transition-transform" :class="showResolved ? 'rotate-180' : ''">▾</span>
    </button>
    <div v-if="showResolved && store.resolvedFlags.length > 0"
         class="mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-sm overflow-hidden">
      <div class="px-4 py-2 text-xs bg-gray-50 dark:bg-gray-750 text-gray-500 dark:text-gray-400">
        已解决的记忆问题（可恢复）
      </div>
      <div class="max-h-56 overflow-y-auto py-1">
        <div v-for="f in store.resolvedFlags" :key="'r-'+f.id"
             class="px-4 py-2 text-xs border-b border-gray-100 dark:border-gray-700 last:border-0">
          <div class="flex items-start gap-2">
            <span class="mt-0.5 flex-shrink-0 opacity-50">🚩</span>
            <div class="flex-1 min-w-0">
              <span class="font-medium opacity-60" :class="typeColor[f.flag_type] || 'text-gray-400'">
                {{ flagTypeNames[f.flag_type] || f.flag_type }}
              </span>
              <span class="text-gray-400 ml-1">#{{ f.memory_id }}</span>
              <span class="text-xs text-gray-400 ml-1">→ {{ resolutionLabels[f.resolution] || f.resolution }}</span>
              <p class="text-gray-500 dark:text-gray-400 mt-0.5 break-words opacity-70">{{ f.evidence }}</p>
            </div>
          </div>
          <div class="flex gap-1.5 mt-1.5">
            <button @click="store.restoreFlag(f.id)"
                    class="text-[10px] px-2 py-0.5 rounded-full bg-green-50 dark:bg-green-900 text-green-600 dark:text-green-300 hover:bg-green-100 dark:hover:bg-green-800 transition-colors">
              ↩ 恢复
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
