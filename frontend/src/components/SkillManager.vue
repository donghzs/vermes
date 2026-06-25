<script setup>
import { ref, onMounted, computed } from 'vue'
import api from '../services/api.js'

const skills = ref([])
const toolsets = ref([])
const loading = ref(false)
const showToolsets = ref(false)

const enabledCount = computed(() => skills.value.filter(s => s.enabled).length)

async function loadSkills() {
  loading.value = true
  try {
    const data = await api.getSkills()
    skills.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('Failed to load skills:', e)
  } finally {
    loading.value = false
  }
}

async function loadToolsets() {
  try {
    const data = await api.getToolsets()
    toolsets.value = Array.isArray(data) ? data : []
    showToolsets.value = true
  } catch (e) {
    console.error('Failed to load toolsets:', e)
  }
}

async function toggleSkill(name, enabled) {
  try {
    await api.toggleSkill(name, enabled)
    const skill = skills.value.find(s => s.name === name)
    if (skill) skill.enabled = enabled
  } catch (e) {
    // Revert on failure
    const skill = skills.value.find(s => s.name === name)
    if (skill) skill.enabled = !enabled
    alert('切换失败: ' + e.message)
  }
}

function skillIcon(source) {
  const icons = { builtin: '🔷', trusted: '🟢', community: '🟡', hub: '🟣' }
  return icons[source] || '📄'
}

onMounted(() => {
  loadSkills()
})
</script>

<template>
  <div class="space-y-3">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">🧩</span>
        <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">技能管理</h3>
        <span class="text-xs text-gray-400">({{ enabledCount }}/{{ skills.length }} 启用)</span>
      </div>
      <button @click="loadToolsets" class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
        📦 工具集
      </button>
    </div>

    <!-- Toolsets panel -->
    <div v-if="showToolsets" class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1.5">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-medium text-gray-600 dark:text-gray-400">📦 工具集</span>
        <button @click="showToolsets = false" class="text-gray-400 hover:text-gray-600 text-xs">✕</button>
      </div>
      <div v-for="ts in toolsets" :key="ts.name"
           class="flex items-center gap-2 text-xs py-1">
        <span class="text-gray-400">{{ ts.enabled ? '✅' : '⬜' }}</span>
        <div class="flex-1 min-w-0">
          <span class="text-gray-700 dark:text-gray-300">{{ ts.label || ts.name }}</span>
          <span v-if="ts.configured === false" class="text-[10px] text-orange-400 ml-1">未配置</span>
        </div>
        <div class="flex flex-wrap gap-0.5 max-w-[50%]">
          <span v-for="tool in (ts.tools || []).slice(0, 4)" :key="tool"
                class="text-[9px] px-1 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-400 rounded truncate max-w-[80px]">
            {{ tool }}
          </span>
          <span v-if="(ts.tools || []).length > 4" class="text-[9px] text-gray-400">+{{ ts.tools.length - 4 }}</span>
        </div>
      </div>
    </div>

    <!-- Skill list -->
    <div class="space-y-1 max-h-64 overflow-y-auto">
      <div v-for="skill in skills" :key="skill.name"
           class="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-750">
        <span class="text-sm flex-shrink-0">{{ skillIcon(skill.source) }}</span>
        <div class="flex-1 min-w-0">
          <div class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate">{{ skill.name }}</div>
          <div class="text-[10px] text-gray-400 truncate">{{ skill.description || skill.source || '' }}</div>
        </div>
        <!-- Toggle switch -->
        <button @click="toggleSkill(skill.name, !skill.enabled)"
                class="relative inline-flex h-4 w-7 items-center rounded-full transition-colors flex-shrink-0"
                :class="skill.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'">
          <span class="inline-block h-3 w-3 transform rounded-full bg-white transition-transform"
                :class="skill.enabled ? 'translate-x-3.5' : 'translate-x-0.5'"></span>
        </button>
      </div>
      <!-- Empty -->
      <div v-if="!loading && skills.length === 0" class="text-center py-6 text-xs text-gray-400">
        <div class="text-2xl mb-1">🧩</div>
        <div>暂无已安装技能</div>
      </div>
      <div v-if="loading" class="text-center py-3 text-xs text-gray-400 animate-pulse">加载中...</div>
    </div>
  </div>
</template>
