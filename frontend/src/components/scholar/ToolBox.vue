<script setup>
// 工具箱：P0c-2 全量接入 22 个 scholarforge 工具，按功能分组展示。
// 点卡片 → SchemaForm 填参 → invokeTool → ToolResult 列表（最新在上）。
import { ref, computed, onMounted, watch } from 'vue'
import { useScholarStore } from '../../stores/scholar'
import { invokeTool } from '../../utils/invokeTool'
import SchemaForm from './SchemaForm.vue'
import ToolResult from './ToolResult.vue'

const scholar = useScholarStore()

// 分组定义（组内顺序即展示顺序；未列出的新工具自动落「其他」组，前端免改）
const TOOL_GROUPS = [
  {
    label: '✍️ 写作主链',
    names: [
      'scholarforge_search',
      'scholarforge_outline',
      'scholarforge_write',
      'scholarforge_polish',
      'scholarforge_score',
    ],
  },
  {
    label: '📚 引用与文献',
    names: [
      'scholarforge_replace_citations',
      'scholarforge_format_refs',
      'scholarforge_verify_citations',
      'scholarforge_save_literature_cards',
      'scholarforge_literature_matrix',
      'scholarforge_research_map',
    ],
  },
  {
    label: '🛡️ 质量检查',
    names: [
      'scholarforge_plagiarism_check',
      'scholarforge_deaigc',
      'scholarforge_quality_gate',
      'scholarforge_check_stats',
      'scholarforge_detect_design_flaws',
      'scholarforge_review_claims',
      'scholarforge_review',
    ],
  },
  {
    label: '🗂️ 项目与导出',
    names: [
      'scholarforge_export',
      'scholarforge_manage_snapshots',
      'scholarforge_apply_template',
      'scholarforge_learn_style',
    ],
  },
]

const tools = ref([])
const selected = ref(null)
const results = ref([])
const loading = ref(false)
const loadError = ref('')
const filter = ref('')
const prefillValues = ref({})

onMounted(async () => {
  try {
    const resp = await fetch('/api/scholar/tools')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    tools.value = data.tools || []
  } catch (e) {
    loadError.value = `工具清单加载失败：${e.message}`
  }
})

// FlowGuide / Uploader 跨组件协调：选中指定工具并（可选）预填字段
watch(
  () => scholar.pendingTool,
  (name) => {
    if (!name) return
    const t = tools.value.find((x) => x.name === name)
    if (t) selected.value = t
    const pf = scholar.pendingPrefill
    prefillValues.value = pf ? { [pf.field]: pf.value } : {}
    scholar.clearPending()
  },
)

// 分组 + 过滤后的展示结构；后端新增而未入组的工具落「其他」
const groupedTools = computed(() => {
  const byName = new Map(tools.value.map((t) => [t.name, t]))
  const seen = new Set()
  const kw = filter.value.trim().toLowerCase()
  const match = (t) =>
    !kw ||
    t.name.toLowerCase().includes(kw) ||
    (t.description || '').toLowerCase().includes(kw)

  const groups = TOOL_GROUPS.map((g) => {
    const items = g.names
      .map((n) => {
        seen.add(n)
        return byName.get(n)
      })
      .filter((t) => t && match(t))
    return { label: g.label, items }
  })
  const rest = tools.value.filter((t) => !seen.has(t.name) && match(t))
  if (rest.length) groups.push({ label: '🧩 其他', items: rest })
  return groups.filter((g) => g.items.length)
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

    <!-- 搜索过滤 -->
    <input
      v-model="filter"
      type="text"
      placeholder="🔍 搜索工具（名称或描述）"
      class="w-full sm:w-72 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
    />

    <!-- 分组工具卡片 -->
    <div v-for="g in groupedTools" :key="g.label" class="space-y-2">
      <h3 class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
        {{ g.label }}
      </h3>
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <button
          v-for="t in g.items"
          :key="t.name"
          @click="selected = t"
          :class="[
            'text-left rounded-lg border p-3 hover:border-blue-400 hover:shadow-sm transition',
            selected && selected.name === t.name
              ? 'border-blue-500 bg-blue-50/60 dark:bg-blue-900/20'
              : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800',
          ]"
        >
          <div class="flex items-center gap-2">
            <span class="text-lg">{{ t.emoji || '🔧' }}</span>
            <span class="text-sm font-medium">{{ t.name.replace('scholarforge_', '') }}</span>
          </div>
          <p class="mt-1 text-xs text-gray-400 leading-snug line-clamp-2">{{ t.description }}</p>
        </button>
      </div>
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
      <SchemaForm :schema="selected.schema" :initial-values="prefillValues" @submit="runTool" />
    </div>

    <!-- 结果列表 -->
    <div v-if="results.length" class="space-y-3">
      <h3 class="text-sm font-semibold text-gray-600 dark:text-gray-300">运行结果</h3>
      <ToolResult v-for="(r, i) in results" :key="i" :item="r" />
      <p v-if="loading" class="text-sm text-gray-400">运行中…</p>
    </div>
  </div>
</template>
