<!--
  G13/G10: 我眼里的你
  数据源：GET /api/memory/profile（聚合 preference + decision + expertise）
  在 G13 人格叙事框架内呈现，用户可看不可改（只读视图）
-->
<template>
  <div class="space-y-4">
    <!-- 你偏好的 -->
    <div v-if="profile.preferences.length">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">💚</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">你偏好</span>
      </div>
      <div class="space-y-1.5">
        <div v-for="(pref, i) in profile.preferences.slice(0, 8)" :key="i"
          class="text-xs text-gray-600 dark:text-gray-300 px-3 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800/50">
          {{ pref.content }}
        </div>
      </div>
    </div>

    <!-- 你常做的决定 -->
    <div v-if="profile.decisions.length">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">🧭</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">你定过的事</span>
      </div>
      <div class="space-y-1.5">
        <div v-for="(dec, i) in profile.decisions.slice(0, 6)" :key="i"
          class="text-xs text-gray-600 dark:text-gray-300 px-3 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800/50">
          {{ dec.decision }}
          <span v-if="dec.context" class="text-gray-400 dark:text-gray-500"> · {{ dec.context }}</span>
        </div>
      </div>
    </div>

    <!-- 你会的技能 -->
    <div v-if="profile.expertise.length">
      <div class="flex items-center gap-2 mb-2">
        <span class="text-base">🔧</span>
        <span class="text-sm font-medium text-gray-700 dark:text-gray-200">你的手艺</span>
      </div>
      <div class="flex flex-wrap gap-2">
        <span v-for="(exp, i) in profile.expertise.slice(0, 8)" :key="i"
          class="px-2.5 py-1 text-xs rounded-lg bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20">
          {{ exp.content }}
        </span>
      </div>
    </div>

    <!-- 空态 -->
    <div v-if="!profile.preferences.length && !profile.decisions.length && !profile.expertise.length"
      class="text-center py-6">
      <span class="text-3xl">🤝</span>
      <p class="text-sm text-gray-400 dark:text-gray-500 mt-2">我们刚开始相处，我还在了解你</p>
      <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">聊得越多，我越懂你</p>
    </div>

    <!-- 记忆总量 -->
    <div v-if="profile.total_memories > 0" class="pt-2 border-t border-gray-100 dark:border-gray-700">
      <span class="text-xs text-gray-400 dark:text-gray-500">我记了你 {{ profile.total_memories }} 件事</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { envHeaders } from '../utils/env'

const profile = ref({ preferences: [], decisions: [], expertise: [], total_memories: 0 })

onMounted(async () => {
  try {
    const r = await fetch('/api/memory/profile', { headers: envHeaders() })
    if (r.ok) {
      profile.value = await r.json()
    }
  } catch (e) { /* fail-open */ }
})
</script>
