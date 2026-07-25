<script setup>
// ScholarForge 论文写作面板（A 入口）：容器 + 顶部项目选择器 + Tab。
//
// Tab 由 useScholarStore.activeTab 驱动：FlowGuide「打开工具」/ Uploader「填入工具箱」
// 会经 store 切回「工具箱」并预选工具，实现跨子组件联动。
//
// 与对话式(C) 共享同一后端引擎：工具箱经 invokeTool → POST /api/tools/invoke →
// handler 内部含 run_quality_gate，故质量护栏对两种入口行为一致（P0b 已封堵缺口）。
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useScholarStore } from '../stores/scholar'
import ToolBox from './scholar/ToolBox.vue'
import ProjectSpace from './scholar/ProjectSpace.vue'
import QualityView from './scholar/QualityView.vue'
import FlowGuide from './scholar/FlowGuide.vue'
import Uploader from './scholar/Uploader.vue'

const router = useRouter()
const scholar = useScholarStore()

function goChat() { router.push('/') }

const TABS = [
  { key: 'tools', label: '🧰 工具箱' },
  { key: 'projects', label: '🗂️ 项目空间' },
  { key: 'quality', label: '🛡️ 质量视图' },
  { key: 'guide', label: '🧭 写作引导' },
  { key: 'upload', label: '📥 上传' },
]

onMounted(() => {
  scholar.loadProjects()
})
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <header class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0 flex-wrap">
      <button
        @click="goChat"
        class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500"
        title="返回会话"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
      </button>
      <h1 class="text-base font-semibold flex items-center gap-2">
        <span>📝</span> 论文写作
      </h1>

      <!-- Tab 切换 -->
      <nav class="flex items-center gap-1 ml-2 flex-wrap">
        <button
          v-for="t in TABS"
          :key="t.key"
          :class="[
            'px-3 py-1.5 rounded-lg text-sm transition',
            scholar.activeTab === t.key
              ? 'bg-blue-600 text-white font-medium'
              : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
          ]"
          @click="scholar.activeTab = t.key"
        >
          {{ t.label }}
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
      <ToolBox v-if="scholar.activeTab === 'tools'" />
      <ProjectSpace v-else-if="scholar.activeTab === 'projects'" />
      <QualityView v-else-if="scholar.activeTab === 'quality'" />
      <FlowGuide v-else-if="scholar.activeTab === 'guide'" />
      <Uploader v-else-if="scholar.activeTab === 'upload'" />
    </main>
  </div>
</template>
