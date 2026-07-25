<script setup>
// QualityView：质量护栏结果视图（P0c-3）。
// 读取 section_quality 表（写回 flag 闸门落库 + 手动全量检查落库），
// 并可一键触发 scholarforge_quality_gate 全量检查并落库。
import { ref, computed, watch, onMounted } from 'vue'
import { useScholarStore } from '../../stores/scholar'
import { invokeTool } from '../../utils/invokeTool'
import MarkdownPreview from './MarkdownPreview.vue'

const scholar = useScholarStore()
const reports = ref([])
const loading = ref(false)
const running = ref(false)
const error = ref('')
const lastRun = ref('') // 最近一次手动检查的报告（未落库前预览）

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

async function load() {
  if (!scholar.currentProjectId) {
    reports.value = []
    return
  }
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch(`/api/scholar/quality?project_id=${scholar.currentProjectId}`)
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    reports.value = data.reports || []
  } catch (e) {
    error.value = `质量报告加载失败：${e.message}`
  } finally {
    loading.value = false
  }
}

async function runFull() {
  if (!scholar.currentProjectId) {
    error.value = '请先在顶部选择一个项目。'
    return
  }
  running.value = true
  error.value = ''
  lastRun.value = ''
  try {
    const report = await invokeTool('scholarforge_quality_gate', {
      project_id: scholar.currentProjectId,
    })
    lastRun.value = typeof report === 'string' ? report : JSON.stringify(report, null, 2)
    // 落库，便于后续在报告中回溯
    await fetch('/api/scholar/quality', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: scholar.currentProjectId,
        section_key: '',
        report: lastRun.value,
      }),
    })
    await load()
  } catch (e) {
    error.value = `质量检查失败：${e.message}`
  } finally {
    running.value = false
  }
}

onMounted(load)
watch(() => scholar.currentProjectId, load)

const hasProject = computed(() => !!scholar.currentProjectId)
</script>

<template>
  <div class="p-4 space-y-4">
    <div class="flex items-center justify-between gap-3 flex-wrap">
      <p class="text-sm text-gray-500">
        质量护栏结果（AIGC / 查重 / 引用真实性 / 统计一致性 / 设计缺陷）。
        <br />
        写回阶段 <code class="px-1 bg-gray-100 dark:bg-gray-700 rounded">flag</code> 模式会自动落库，也可手动触发全量检查。
      </p>
      <button
        class="shrink-0 px-3 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium transition disabled:opacity-50"
        :disabled="!hasProject || running"
        @click="runFull"
      >
        {{ running ? '检查中…' : '🛡️ 运行全量质量检查' }}
      </button>
    </div>

    <p v-if="!hasProject" class="text-sm text-amber-600">
      ⚠️ 未选择项目，无法读取质量报告。请在顶部「当前项目」下拉选择。
    </p>
    <p v-else-if="error" class="text-sm text-red-500">{{ error }}</p>
    <p v-else-if="loading" class="text-sm text-gray-400">加载中…</p>
    <p v-else-if="!reports.length && !lastRun" class="text-sm text-gray-400">
      暂无质量报告。写回章节或点上方「运行全量质量检查」即可生成。
    </p>

    <!-- 最近一次手动检查结果（即时预览） -->
    <div
      v-if="lastRun"
      class="rounded-lg border border-emerald-300 dark:border-emerald-700 bg-emerald-50/40 dark:bg-emerald-900/10 p-4"
    >
      <h3 class="text-sm font-semibold text-emerald-700 dark:text-emerald-300 mb-2">
        ✅ 本次全量检查结果
      </h3>
      <MarkdownPreview :source="lastRun" />
    </div>

    <!-- 历史质量报告列表 -->
    <div v-for="(r, i) in reports" :key="i" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
      <div class="flex items-center justify-between mb-2">
        <h3 class="text-sm font-semibold flex items-center gap-2">
          <span>📋</span>
          <span class="font-mono">{{ r.section_key }}</span>
        </h3>
        <span class="text-xs text-gray-400">{{ fmtTime(r.checked_at) }}</span>
      </div>
      <MarkdownPreview :source="r.report" />
    </div>
  </div>
</template>
