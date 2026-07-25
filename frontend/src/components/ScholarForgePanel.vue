<script setup>
// ScholarForge 论文写作面板（A 入口）：容器 + 顶部项目选择器 + Tab（工具箱 / 项目空间）。
//
// 与对话式(C) 共享同一后端引擎：工具箱经 invokeTool → POST /api/tools/invoke →
// handler 内部含 run_quality_gate，故质量护栏对两种入口行为一致（P0b 已封堵缺口）。
import { ref, onMounted } from 'vue'
import { useScholarStore } from '../stores/scholar'
import ToolBox from './scholar/ToolBox.vue'
import ProjectSpace from './scholar/ProjectSpace.vue'

const scholar = useScholarStore()
const tab = ref('tools') // 'tools' | 'projects'

onMounted(() => {
  scholar.loadProjects()
})
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <header class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0">
      <h1 class="text-base font-semibold flex items-center gap-2">
        <span>📝</span> 论文写作
      </h1>

      <!-- Tab 切换 -->
      <nav class="flex items-center gap-1 ml-4">
        <button
          :class="[
            'px-3 py-1.5 rounded-lg text-sm transition',
            tab === 'tools'
              ? 'bg-blue-600 text-white font-medium'
              : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
          ]"
          @click="tab = 'tools'"
        >
          🧰 工具箱
        </button>
        <button
          :class="[
            'px-3 py-1.5 rounded-lg text-sm transition',
            tab === 'projects'
              ? 'bg-blue-600 text-white font-medium'
              : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
          ]"
          @click="tab = 'projects'"
        >
          🗂️ 项目空间
        </button>
      </nav>

      <div class="ml-auto flex items-center gap-2">
        <label class="text-xs text-gray-500">当前项目</label>
        <select
          v-model="scholar.currentProjectId"
          class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1.5 text-sm"
        >
          <option :value="null">未选择（工具不带项目上下文）</option>
          <option v-for="p in scholar.projects" :key="p.id" :value="p.id">
            #{{ p.id }} {{ p.title }}
          </option>
        </select>
      </div>
    </header>

    <main class="flex-1 overflow-y-auto">
      <ToolBox v-if="tab === 'tools'" />
      <ProjectSpace v-else />
    </main>
  </div>
</template>
