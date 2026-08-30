<template>
  <div class="benchmark-dashboard p-6 max-w-6xl mx-auto bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <h2 class="text-xl font-bold mb-4">📊 Benchmark 大盘</h2>

    <!-- 触发栏 -->
    <div class="flex items-center gap-3 mb-6">
      <button
        @click="triggerDryRun"
        :disabled="running"
        class="px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition"
      >
        {{ running ? '运行中…' : '▶ 触发 Dry-Run' }}
      </button>
      <select v-model="llmTier" class="px-3 py-2 border rounded-lg bg-gray-50 dark:bg-gray-800 text-gray-900 dark:text-gray-100 border-gray-200 dark:border-gray-600">
        <option value="strong">Strong LLM</option>
        <option value="mid">Mid LLM</option>
        <option value="weak">Weak LLM</option>
      </select>
      <span class="text-sm text-gray-400">共 {{ totalRuns }} 次运行</span>
    </div>

    <!-- 趋势图（简易柱状） -->
    <div v-if="runs.length > 0" class="mb-8">
      <h3 class="text-sm font-semibold text-gray-400 dark:text-gray-400 mb-2">通过率趋势</h3>
      <div class="flex items-end gap-1 h-32">
        <div
          v-for="(run, i) in runs.slice(-20)"
          :key="i"
          class="flex-1 flex flex-col items-center justify-end"
          :title="`${run.timestamp || ''} · ${run.summary?.pass_count || 0}/${run.summary?.total || 0}`"
        >
          <div
            class="w-full rounded-t transition-all"
            :style="{
              height: barHeight(run) + '%',
              backgroundColor: passRate(run) >= 0.8 ? '#10b981' : passRate(run) >= 0.5 ? '#f59e0b' : '#ef4444'
            }"
          />
          <span class="text-xs text-gray-500 mt-1">{{ passRate(run) ? Math.round(passRate(run) * 100) + '%' : '-' }}</span>
        </div>
      </div>
    </div>

    <!-- 任务覆盖面 -->
    <div class="mb-8">
      <h3 class="text-sm font-semibold text-gray-400 dark:text-gray-400 mb-2">任务覆盖面（{{ tasks.length }} 个任务）</h3>
      <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
        <div
          v-for="t in tasks"
          :key="t.id"
          class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50"
        >
          <div class="flex items-center justify-between">
            <span class="text-sm font-medium">{{ t.title }}</span>
            <span
              class="text-xs px-1.5 py-0.5 rounded"
              :class="t.kind === 'pipeline' ? 'bg-purple-100 dark:bg-purple-900/50 text-purple-700 dark:text-purple-300' : 'bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300'"
            >
              {{ t.kind }}
            </span>
          </div>
          <div class="text-xs text-gray-500 mt-1">
            {{ t.tools.join(', ') }}
            <span v-if="t.llm_required" class="ml-1 text-amber-400">⚡需LLM</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 历史 runs 表 -->
    <div v-if="runs.length > 0">
      <h3 class="text-sm font-semibold text-gray-400 dark:text-gray-400 mb-2">历史运行</h3>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 border-b border-gray-200 dark:border-gray-700">
              <th class="text-left py-2 px-3">时间</th>
              <th class="text-left py-2 px-3">模式</th>
              <th class="text-left py-2 px-3">LLM 档</th>
              <th class="text-left py-2 px-3">通过</th>
              <th class="text-left py-2 px-3">接线率</th>
              <th class="text-left py-2 px-3">耗时</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(run, i) in runs.slice(-10).reverse()"
              :key="i"
              class="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/30"
            >
              <td class="py-2 px-3 text-gray-600 dark:text-gray-300">{{ formatTime(run.timestamp) }}</td>
              <td class="py-2 px-3">
                <span :class="run.mode === 'dry' ? 'text-gray-400' : 'text-emerald-500'">{{ run.mode }}</span>
              </td>
              <td class="py-2 px-3 text-gray-400 dark:text-gray-400">{{ run.llm_tier }}</td>
              <td class="py-2 px-3">
                <span :class="passRate(run) >= 0.8 ? 'text-emerald-400' : passRate(run) >= 0.5 ? 'text-amber-400' : 'text-red-400'">
                  {{ run.summary?.pass_count || 0 }}/{{ run.summary?.total || 0 }}
                </span>
              </td>
              <td class="py-2 px-3 text-gray-400 dark:text-gray-400">{{ run.summary?.wiring_rate ? Math.round(run.summary.wiring_rate * 100) + '%' : '-' }}</td>
              <td class="py-2 px-3 text-gray-400 dark:text-gray-400">{{ run.summary?.total_wall_time_s ? run.summary.total_wall_time_s.toFixed(1) + 's' : '-' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 空态 -->
    <div v-if="!running && runs.length === 0" class="text-center py-12 text-gray-400 dark:text-gray-500">
      暂无 benchmark 记录。点击「触发 Dry-Run」开始第一次运行。
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { envHeaders } from '../utils/env'

const runs = ref([])
const tasks = ref([])
const totalRuns = ref(0)
const running = ref(false)
const llmTier = ref('strong')

function passRate(run) {
  const s = run.summary
  if (!s || !s.total) return 0
  return (s.pass_count || 0) / s.total
}

function barHeight(run) {
  return Math.max(passRate(run) * 100, 2)
}

function formatTime(ts) {
  if (!ts) return '-'
  try {
    return new Date(ts).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

async function loadRuns() {
  try {
    const r = await fetch('/api/v1/benchmark/runs?limit=50', { headers: envHeaders() })
    if (r.ok) {
      const body = await r.json()
      runs.value = body.runs || []
      totalRuns.value = body.total || 0
    }
  } catch (e) {
    console.error('loadRuns failed', e)
  }
}

async function loadTasks() {
  try {
    const r = await fetch('/api/v1/benchmark/tasks', { headers: envHeaders() })
    if (r.ok) {
      const body = await r.json()
      tasks.value = body.tasks || []
    }
  } catch (e) {
    console.error('loadTasks failed', e)
  }
}

async function triggerDryRun() {
  running.value = true
  try {
    const r = await fetch(`/api/v1/benchmark/run?mode=dry&llm_tier=${llmTier.value}`, {
      method: 'POST',
      headers: envHeaders(),
    })
    if (r.ok) {
      await loadRuns()  // 刷新历史
    } else {
      console.error('triggerDryRun failed', r.status, await r.text())
    }
  } catch (e) {
    console.error('triggerDryRun error', e)
  } finally {
    running.value = false
  }
}

onMounted(() => {
  loadRuns()
  loadTasks()
})
</script>
