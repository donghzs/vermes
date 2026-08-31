<!--
  G13/G4: 我擅长什么 / 我还在学什么
  数据源：GET /api/v1/capabilities/self-check（M5 同源）
  在 G13 人格叙事框架内呈现，禁用仪表盘语言
-->
<template>
  <div class="space-y-4">
    <!-- 我擅长的 -->
    <div>
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">✨</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">我擅长这 {{ skilled.length }} 样</span>
      </div>
      <div v-if="skilled.length" class="flex flex-wrap gap-2">
        <span v-for="cap in skilled" :key="cap.name"
          class="px-2.5 py-1 text-xs rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
          {{ cap.description || cap.name }}
        </span>
      </div>
      <p v-else class="text-xs text-gray-400 dark:text-gray-500">刚开始，还在摸底</p>
    </div>

    <!-- 我还在学的 -->
    <div v-if="learning.length">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">🌱</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">我还在学这 {{ learning.length }} 样</span>
      </div>
      <div class="flex flex-wrap gap-2">
        <span v-for="cap in learning" :key="cap.name"
          class="px-2.5 py-1 text-xs rounded-lg bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
          {{ cap.description || cap.name }}
          <span v-if="cap.emergence_signals > 0" class="ml-1 text-amber-400">· {{ cap.emergence_signals }} 次信号</span>
        </span>
      </div>
    </div>

    <!-- 用户强调的 -->
    <div v-if="emphasized.length">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">📌</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">你让我优先关注的</span>
      </div>
      <div class="flex flex-wrap gap-2">
        <span v-for="eid in emphasized" :key="eid"
          class="px-2.5 py-1 text-xs rounded-lg bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20">
          {{ eid }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { envHeaders } from '../utils/env'

const skilled = ref([])
const learning = ref([])
const emphasized = ref([])

onMounted(async () => {
  try {
    const r = await fetch('/api/v1/capabilities/self-check', { headers: envHeaders() })
    if (r.ok) {
      const data = await r.json()
      skilled.value = data.skilled || []
      learning.value = data.learning || []
      emphasized.value = data.emphasized || []
    }
  } catch (e) { /* fail-open */ }
})
</script>
