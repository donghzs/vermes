<script setup>
// 工具箱：P0c-1 接入 5 个高频工具，构成「文献→大纲→写作→评分→查重」链路。
//   search → outline → write → score → plagiarism_check
// 点卡片 → SchemaForm 填参 → invokeTool → ToolResult 列表（最新在上）。
import { ref, onMounted } from 'vue'
import { invokeTool } from '../../utils/invokeTool'
import SchemaForm from './SchemaForm.vue'
import ToolResult from './ToolResult.vue'

// P0c-1 锁定的 5 个工具（决策 #2：export 依赖 pandoc，MVP 易卡，故换为 outline）
const P0C1_TOOLS = [
  'scholarforge_search',
  'scholarforge_outline',
  'scholarforge_write',
  'scholarforge_score',
  'scholarforge_plagiarism_check',
]

const tools = ref([])
const selected = ref(null)
const results = ref([])
const loading = ref(false)
const loadError = ref('')

onMounted(async () => {
  try {
    const resp = await fetch('/api/scholar/tools')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    const all = data.tools || []
    tools.value = P0C1_TOOLS
      .map((name) => all.find((t) => t.name === name))
      .filter(Boolean)
  } catch (e) {
    loadError.value = `工具清单加载失败：${e.message}`
  }
})

async function runTool(args) {
  if (!selected.value) return
  loading.value = true
  const t0 = Date.now()
  const tool = selected.value
  try {
    const result = await invokeTool(tool.name, args)
    results.value.unshift({
      name: tool.name,
      emoji: tool.emoji,
      args,
      result,
      ms: Date.now() - t0,
      ok: true,
    })
  } catch (e) {
    results.value.unshift({
      name: tool.name,
      emoji: tool.emoji,
      args,
      error: e.message,
      ms: Date.now() - t0,
      ok: false,
    })
  } finally {
    loading.value = false
    selected.value = null
  }
}
</script>

<template>
  <div class="p-4 space-y-4">
    <p class="text-sm text-gray-500">
      选择工具直接运行（单阶段独立用）。带项目上下文的工具会从顶部「当前项目」自动注入
      <code class="px-1 bg-gray-100 dark:bg-gray-700 rounded">project_id</code>，无需手填。
    </p>

    <p v-if="loadError" class="text-sm text-red-500">{{ loadError }}</p>

    <!-- 工具卡片网格 -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      <button
        v-for="t in tools"
        :key="t.name"
        @click="selected = t"
        class="text-left rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-3 hover:border-blue-400 hover:shadow-sm transition"
      >
        <div class="flex items-center gap-2">
          <span class="text-lg">{{ t.emoji || '🔧' }}</span>
          <span class="text-sm font-medium">{{ t.name.replace('scholarforge_', '') }}</span>
        </div>
        <p class="mt-1 text-xs text-gray-400 leading-snug line-clamp-2">{{ t.description }}</p>
      </button>
    </div>

    <!-- 选中工具的参数表单 -->
    <div v-if="selected" class="rounded-lg border border-blue-300 dark:border-blue-700 bg-blue-50/40 dark:bg-blue-900/10 p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold flex items-center gap-2">
          <span>{{ selected.emoji }}</span> 运行 {{ selected.name.replace('scholarforge_', '') }}
        </h3>
        <button
          class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
          @click="selected = null"
        >
          取消
        </button>
      </div>
      <SchemaForm :schema="selected.schema" @submit="runTool" />
    </div>

    <!-- 结果列表 -->
    <div v-if="results.length" class="space-y-3">
      <h3 class="text-sm font-semibold text-gray-600 dark:text-gray-300">运行结果</h3>
      <ToolResult v-for="(r, i) in results" :key="i" :item="r" />
      <p v-if="loading" class="text-sm text-gray-400">运行中…</p>
    </div>
  </div>
</template>
