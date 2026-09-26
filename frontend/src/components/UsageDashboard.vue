<template>
  <div class="usage-dashboard h-full overflow-y-auto p-6 max-w-6xl mx-auto bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <div class="flex items-center justify-between mb-6">
      <div>
        <h2 class="text-xl font-bold">📈 用量与成本</h2>
        <p class="text-xs text-gray-400 mt-1">
          token / 成本 / 趋势 · 数据来自本地会话账本（{{ periodDays }} 天）
        </p>
      </div>
      <div class="flex items-center gap-2">
        <select
          v-model="periodDays"
          @change="load"
          class="px-3 py-1.5 text-sm border rounded-lg bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-600"
        >
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
          <option :value="90">近 90 天</option>
        </select>
        <button
          @click="load"
          :disabled="loading"
          class="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
        >{{ loading ? '…' : '刷新' }}</button>
      </div>
    </div>

    <!-- 汇总卡 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
      <div v-for="c in summaryCards" :key="c.label" class="p-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <div class="text-xs text-gray-400">{{ c.label }}</div>
        <div class="text-2xl font-semibold mt-1 tabular-nums">{{ c.value }}</div>
        <div v-if="c.sub" class="text-xs text-gray-400 mt-1">{{ c.sub }}</div>
      </div>
    </div>

    <!-- 日趋势 -->
    <div class="mb-8">
      <h3 class="text-sm font-semibold text-gray-400 mb-2">日 token 用量（输入 + 输出）</h3>
      <div v-if="daily.length" class="flex items-end gap-1 h-36">
        <div
          v-for="(d, i) in daily"
          :key="i"
          class="flex-1 flex flex-col items-center justify-end"
          :title="`${d.day} · in ${fmt(d.input_tokens)} · out ${fmt(d.output_tokens)}`"
        >
          <div class="w-full flex flex-col justify-end h-28">
            <div
              class="w-full rounded-t-sm bg-emerald-400/80"
              :style="{ height: barPct(d.output_tokens, maxDaily) + '%' }"
            />
            <div
              class="w-full bg-emerald-700/80"
              :style="{ height: barPct(d.input_tokens, maxDaily) + '%' }"
            />
          </div>
          <span class="text-[10px] text-gray-500 mt-1 truncate w-full text-center">
            {{ dayLabel(d.day) }}
          </span>
        </div>
      </div>
      <p v-else class="text-sm text-gray-400 py-8 text-center">暂无用量记录 — 开几轮对话后这里会出现趋势</p>
    </div>

    <!-- 按模型 -->
    <div v-if="byModel.length" class="mb-8">
      <h3 class="text-sm font-semibold text-gray-400 mb-2">按模型（{{ byModel.length }}）</h3>
      <div class="overflow-x-auto rounded-xl border border-gray-200 dark:border-gray-700">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-gray-400 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40">
              <th class="text-left py-2 px-3">模型</th>
              <th class="text-right py-2 px-3">输入</th>
              <th class="text-right py-2 px-3">输出</th>
              <th class="text-right py-2 px-3">会话</th>
              <th class="text-right py-2 px-3">API 调用</th>
              <th class="text-right py-2 px-3">预估成本</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in byModel"
              :key="m.model"
              class="border-b border-gray-100 dark:border-gray-800 last:border-0"
            >
              <td class="py-2 px-3 font-mono text-xs">{{ m.model }}</td>
              <td class="py-2 px-3 text-right tabular-nums">{{ fmt(m.input_tokens) }}</td>
              <td class="py-2 px-3 text-right tabular-nums">{{ fmt(m.output_tokens) }}</td>
              <td class="py-2 px-3 text-right tabular-nums">{{ m.sessions }}</td>
              <td class="py-2 px-3 text-right tabular-nums">{{ fmt(m.api_calls) }}</td>
              <td class="py-2 px-3 text-right tabular-nums">${{ cost(m.estimated_cost) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 技能使用 -->
    <div v-if="skills && (skills.top_skills || []).length">
      <h3 class="text-sm font-semibold text-gray-400 mb-2">技能使用 Top</h3>
      <div class="grid grid-cols-2 md:grid-cols-3 gap-2">
        <div
          v-for="s in skills.top_skills.slice(0, 9)"
          :key="s.id || s.name"
          class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40"
        >
          <div class="text-sm font-medium truncate">{{ s.title || s.name || s.id }}</div>
          <div class="text-xs text-gray-400 mt-1">×{{ s.uses ?? s.usage_count ?? s.count ?? 0 }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const loading = ref(false)
const periodDays = ref(30)
const daily = ref([])
const byModel = ref([])
const totals = ref({})
const skills = ref(null)

const maxDaily = computed(() =>
  Math.max(1, ...daily.value.map(d => (Number(d.input_tokens) || 0) + (Number(d.output_tokens) || 0)))
)

const summaryCards = computed(() => {
  const t = totals.value || {}
  return [
    {
      label: '输入 token',
      value: fmt(t.total_input || 0),
      sub: t.total_cache_read ? `含缓存读 ${fmt(t.total_cache_read)}` : '',
    },
    {
      label: '输出 token',
      value: fmt(t.total_output || 0),
      sub: t.total_reasoning ? `含推理 ${fmt(t.total_reasoning)}` : '',
    },
    {
      label: '预估成本',
      value: '$' + cost(t.total_estimated_cost || 0),
      sub: t.total_actual_cost ? `实际 $${cost(t.total_actual_cost)}` : '按标价估算',
    },
    {
      label: '会话 / API 调用',
      value: `${t.total_sessions || 0} / ${fmt(t.total_api_calls || 0)}`,
      sub: `近 ${periodDays.value} 天`,
    },
  ]
})

function fmt(n) {
  const v = Number(n) || 0
  if (v >= 1e9) return (v / 1e9).toFixed(1) + 'B'
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k'
  return String(v)
}
function cost(n) {
  const v = Number(n) || 0
  return v >= 100 ? v.toFixed(0) : v.toFixed(2)
}
function barPct(v, max) {
  return Math.max(2, Math.min(100, ((Number(v) || 0) / max) * 100))
}
function dayLabel(day) {
  // "2026-09-26" → "09/26"
  const s = String(day || '')
  return s.length >= 10 ? s.slice(5, 10) : s
}

async function load() {
  loading.value = true
  try {
    const r = await fetch(`/api/analytics/usage?days=${periodDays.value}`)
    if (!r.ok) throw new Error('HTTP ' + r.status)
    const data = await r.json()
    daily.value = data.daily || []
    byModel.value = data.by_model || []
    totals.value = data.totals || {}
    skills.value = data.skills || null
  } catch (e) {
    console.error('[UsageDashboard] load failed', e)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
