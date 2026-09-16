<script setup>
// 工具箱：P0c-2 全量接入 22 个 scholarforge 工具，按功能分组展示。
// 点卡片 → SchemaForm 填参 → invokeTool → ToolResult 列表（最新在上）。
import { ref, computed, onMounted, watch } from 'vue'
import { useScholarStore } from '../../stores/scholar'
import { invokeTool } from '../../utils/invokeTool'
import SchemaForm from './SchemaForm.vue'
import ToolResult from './ToolResult.vue'
import { toolLabel } from '../../utils/toolLabels'
import { BEGINNER_FLOW, BEGINNER_ENTRY, BEGINNER_NAMES, matchTool } from '../../utils/beginnerFlow'

const scholar = useScholarStore()

// 分组定义（组内顺序即展示顺序；未列出的新工具自动落「其他」组，前端免改）
const TOOL_GROUPS = [
  {
    label: '✍️ 写作主链',
    names: [
      // 一条龙放组内第一：它是"不知道从哪下手"时的入口（与「统计与表格」组
      // 把 stats_table 放前面同样的道理 —— 入口在前，深挖在后）。
      'scholarforge_run_pipeline',
      'scholarforge_search',
      'scholarforge_outline',
      'scholarforge_write',
      'scholarforge_polish',
      'scholarforge_score',
      'scholarforge_read_section',
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
      'scholarforge_citation_graph',
    ],
  },
  {
    label: '🛡️ 质量检查',
    names: [
      'scholarforge_plagiarism_check',
      'scholarforge_deaigc',
      'scholarforge_quality_gate',
      'scholarforge_detect_design_flaws',
      'scholarforge_review_claims',
      'scholarforge_review',
    ],
  },
  // 「统计与表格」单独成组（2026-09-16）：写「研究结果」章节时找的是统计/表格，
  // 原来 check_stats 混在 7 项的「质量检查」里，等于让用户在最不该找的地方找。
  // stats_table 放在前面 —— 它是入口（先出表），check_stats 是深挖（已有数字查对错）。
  {
    label: '📊 统计与表格',
    names: [
      'scholarforge_stats_table',
      'scholarforge_check_stats',
    ],
  },
  {
    label: '🗂️ 项目与导出',
    names: [
      'scholarforge_export',
      'scholarforge_manage_snapshots',
      'scholarforge_apply_template',
      'scholarforge_learn_style',
      // 项目类工具与"导出"同属"管理这篇论文"的心智，放一起；
      // 原先它们落在「其他」组 —— 28 个工具里有 5 个进「其他」等于分组形同虚设。
      'scholarforge_list_projects',
      'scholarforge_set_active_project',
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

// 展示模式：新手（按流程走）/ 全部（28 个工具分组平铺）。
// 默认新手 —— 本组件的目标用户不是开发者；选过一次就记住（localStorage）。
const MODE_KEY = 'vermes.toolbox.mode'
function loadMode() {
  try {
    return localStorage.getItem(MODE_KEY) === 'all' ? 'all' : 'beginner'
  } catch {
    return 'beginner' // 隐私模式 / 无 localStorage 时退回新手模式
  }
}
const mode = ref(loadMode())
watch(mode, (m) => {
  try {
    localStorage.setItem(MODE_KEY, m)
  } catch {
    /* 存不了就算了，不影响使用 */
  }
})

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
    // 跨组件（FlowGuide / Uploader）点名要的工具若不在新手流程里，自动切到「全部」——
    // 否则表单弹出来了、卡片却不在视野内，用户不知道自己在跑什么。
    if (!BEGINNER_NAMES.has(name)) mode.value = 'all'
    const t = tools.value.find((x) => x.name === name)
    if (t) selected.value = t
    const pf = scholar.pendingPrefill
    prefillValues.value = pf ? { [pf.field]: pf.value } : {}
    scholar.clearPending()
  },
)

// 新手模式：8 步流程（带白话提示）。搜索时只保留匹配的步骤。
const beginnerSteps = computed(() => {
  const byName = new Map(tools.value.map((t) => [t.name, t]))
  const kw = filter.value.trim().toLowerCase()
  const out = []
  BEGINNER_FLOW.forEach((s, i) => {
    const tool = byName.get(s.name)
    if (tool && matchTool(tool, kw)) out.push({ step: i + 1, hint: s.hint, tool })
  })
  return out
})

const beginnerEntryTool = computed(() => tools.value.find((t) => t.name === BEGINNER_ENTRY) || null)

// 新手模式下搜到了、但不在 8 步里的工具。
// 不加这一组的话，用户搜「三线表」会得到 0 个结果 —— 他会以为没有这个工具。
const beginnerExtras = computed(() => {
  const kw = filter.value.trim().toLowerCase()
  if (!kw) return []
  return tools.value.filter((t) => !BEGINNER_NAMES.has(t.name) && matchTool(t, kw))
})

// 分组 + 过滤后的展示结构；后端新增而未入组的工具落「其他」
const groupedTools = computed(() => {
  const byName = new Map(tools.value.map((t) => [t.name, t]))
  const seen = new Set()
  const kw = filter.value.trim().toLowerCase()
  const match = (t) => matchTool(t, kw)

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
    <!-- 文案刻意避开 project_id / 单阶段 这类术语（2026-09-16 非技术用户上手专项）：
         第一次来的人看到字段名只会更困惑，说清「会自动归到哪个项目」就够。 -->
    <p class="text-sm text-gray-500">
      选一个工具，填好内容直接跑。需要归到某个项目的工具，会自动使用顶部「当前项目」，
      不用你填编号。
    </p>

    <p v-if="loadError" class="text-sm text-red-500">{{ loadError }}</p>

    <!-- 模式切换：新手（按流程走）/ 全部（平铺） -->
    <div class="flex items-center gap-3 flex-wrap">
      <div class="inline-flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
        <button
          @click="mode = 'beginner'"
          :class="[
            'px-3 py-1.5 text-sm transition',
            mode === 'beginner'
              ? 'bg-blue-500 text-white'
              : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700',
          ]"
        >
          🚶 新手模式
        </button>
        <button
          @click="mode = 'all'"
          :class="[
            'px-3 py-1.5 text-sm transition',
            mode === 'all'
              ? 'bg-blue-500 text-white'
              : 'bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700',
          ]"
        >
          📋 全部工具
        </button>
      </div>
      <span class="text-xs text-gray-400">
        {{ mode === 'beginner' ? '按写论文的顺序走' : `共 ${tools.length} 个工具` }}
      </span>
    </div>

    <!-- 搜索过滤 -->
    <input
      v-model="filter"
      type="text"
      placeholder="🔍 搜索工具（名称或描述）"
      class="w-full sm:w-72 rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
    />

    <!-- ── 新手模式 ── -->
    <div v-if="mode === 'beginner'" class="space-y-4">
      <!-- 入口 CTA：覆盖整条流程，给「不知道从哪下手」的人 -->
      <button
        v-if="beginnerEntryTool"
        @click="selected = beginnerEntryTool"
        class="w-full text-left rounded-lg border-2 border-blue-400 dark:border-blue-600 bg-blue-50/60 dark:bg-blue-900/20 p-4 hover:shadow-sm transition"
      >
        <div class="flex items-center gap-2">
          <span class="text-xl">{{ beginnerEntryTool.emoji || '🚀' }}</span>
          <span class="text-sm font-semibold">不知道从哪开始？</span>
        </div>
        <p class="mt-1 text-xs text-gray-500 dark:text-gray-400 leading-snug">
          点「{{ toolLabel(beginnerEntryTool.name) }}」—— 它会按顺序把下面几步一口气做完。
        </p>
      </button>

      <div class="space-y-2">
        <h3 class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
          🚶 写论文的 8 步
        </h3>
        <div class="space-y-2">
          <button
            v-for="s in beginnerSteps"
            :key="s.tool.name"
            data-test="beginner-step"
            @click="selected = s.tool"
            :class="[
              'w-full text-left rounded-lg border p-3 hover:border-blue-400 hover:shadow-sm transition flex items-start gap-3',
              selected && selected.name === s.tool.name
                ? 'border-blue-500 bg-blue-50/60 dark:bg-blue-900/20'
                : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800',
            ]"
          >
            <span
              class="mt-0.5 shrink-0 w-6 h-6 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300 text-xs font-semibold flex items-center justify-center"
              >{{ s.step }}</span
            >
            <span class="min-w-0">
              <span class="flex items-center gap-2">
                <span class="text-base">{{ s.tool.emoji || '🔧' }}</span>
                <span class="text-sm font-medium">{{ toolLabel(s.tool.name) }}</span>
              </span>
              <span class="mt-0.5 block text-xs text-gray-500 dark:text-gray-400 leading-snug">{{
                s.hint
              }}</span>
            </span>
          </button>
        </div>
        <p v-if="!beginnerSteps.length" class="text-sm text-gray-400">
          8 步里没有匹配「{{ filter }}」的，看看下面 👇
        </p>
      </div>

      <!-- 流程外但命中的工具：不给这一组的话，搜「三线表」会是 0 结果 -->
      <div v-if="beginnerExtras.length" class="space-y-2">
        <h3 class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide">
          🔎 其它匹配的工具
        </h3>
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <button
            v-for="t in beginnerExtras"
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
              <span class="text-sm font-medium">{{ toolLabel(t.name) }}</span>
            </div>
            <p class="mt-1 text-xs text-gray-400 leading-snug line-clamp-2">{{ t.description }}</p>
          </button>
        </div>
      </div>
    </div>

    <!-- ── 全部工具（分组平铺）── -->
    <div v-else class="space-y-4">
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
              <span class="text-sm font-medium">{{ toolLabel(t.name) }}</span>
            </div>
            <p class="mt-1 text-xs text-gray-400 leading-snug line-clamp-2">{{ t.description }}</p>
          </button>
        </div>
      </div>
    </div>

    <!-- 选中工具的参数表单 -->
    <div v-if="selected" class="rounded-lg border border-blue-300 dark:border-blue-700 bg-blue-50/40 dark:bg-blue-900/10 p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold flex items-center gap-2">
          <span>{{ selected.emoji }}</span> 运行 {{ toolLabel(selected.name) }}
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
