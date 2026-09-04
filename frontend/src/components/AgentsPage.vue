<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🤖</span>
        <h1 class="text-xl font-bold">Bot 实验室 · 智能体市场</h1>
        <span class="text-sm text-gray-400">
          {{ loading ? '加载中…' : `共 ${agents.length} 个（本机 ${counts.local} · 社区 ${counts.remote}）` }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <input
          v-model="query"
          placeholder="搜索名称 / 描述…"
          class="px-3 py-1 text-sm rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-emerald-500 w-56"
        />
        <button
          @click="loadAgents(true)"
          :disabled="loading"
          class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition"
        >{{ loading ? '刷新中…' : '🔄 刷新' }}</button>
      </div>
    </div>

    <!-- 内容 -->
    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="loading && agents.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg animate-pulse">⏳ 正在扫描本机 agent…</p>
      </div>
      <div v-else-if="error" class="text-center text-red-400 py-12">
        <p class="text-lg">⚠️ 加载失败</p>
        <p class="text-sm mt-2">{{ error }}</p>
      </div>
      <div v-else-if="filtered.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">🤖 暂未发现智能体</p>
        <p class="text-sm mt-2">本机未安装常见 CLI / App，或封神榜暂无数据</p>
      </div>
      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div
          v-for="a in filtered"
          :key="a._key"
          class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 flex flex-col gap-3 hover:shadow-md transition"
        >
          <div class="flex items-start justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-2xl">{{ kindIcon(a.agent_kind) }}</span>
              <div class="min-w-0">
                <h3 class="font-semibold truncate">{{ a.name || a.id }}</h3>
                <p class="text-xs text-gray-400 truncate">{{ a.id }}</p>
              </div>
            </div>
            <span
              class="shrink-0 px-2 py-0.5 text-xs rounded-full"
              :class="a.source === 'remote' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-600 dark:text-emerald-300'"
            >{{ sourceLabel(a.source) }}</span>
          </div>

          <p
            v-if="a.description"
            class="text-sm text-gray-500 dark:text-gray-400"
            style="display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden"
          >{{ a.description }}</p>

          <div class="flex flex-wrap gap-1.5">
            <span class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{{ kindLabel(a.agent_kind) }}</span>
            <span class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">鉴权：{{ authLabel(a.auth_scheme) }}</span>
            <span v-if="a.version" class="px-2 py-0.5 text-[11px] rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">v{{ a.version }}</span>
          </div>

          <!--
            ⑭ 字段契约（2026-09-04 董董审计发现 T4 字段错位 bug 后定档）：
            - 顶层  有  agent_kind / auth_scheme / source / install_state / name / version
            - 顶层  无  popularity / homepage / repository（这三个走 extra 或 entry_point）
            - 远端 a.entry_point = homepage（_discover_agents:457 误用，但已用守卫避免重复显示）
            - 远端 a.extra     = {popularity, repository}（homepage 不在 extra）
            - 本地 a.entry_point = 真实 CLI 命令/路径（不当链接用，守卫隐藏）
            - 守护测试：tests/test_agent_discovery.py::test_remote_agent_field_contract
          -->
          <div v-if="a.source !== 'remote' && a.entry_point" class="text-xs text-gray-400 truncate font-mono">↳ {{ a.entry_point }}</div>
          <div v-if="a.source === 'remote' && a.extra && a.extra.popularity" class="text-xs text-amber-500">🔥 热度 {{ a.extra.popularity }}</div>

          <div class="mt-auto pt-1 flex items-center gap-2 text-xs">
            <span class="px-2 py-0.5 rounded-full bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-300">已接入（只读发现）</span>
            <a v-if="a.source === 'remote' && (a.extra && a.extra.repository || a.entry_point)" :href="(a.extra && a.extra.repository) || a.entry_point" target="_blank" rel="noopener" class="text-blue-500 hover:underline">{{ (a.extra && a.extra.repository) ? '仓库' : '主页' }}</a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'

const agents = ref([])
const loading = ref(false)
const error = ref('')
const query = ref('')

async function loadAgents(refresh = false) {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams({ type: 'agent' })
    if (refresh) params.set('refresh', 'true')
    const data = await api.get(`/v1/bricks?${params.toString()}`)
    agents.value = (data.bricks || []).map(a => ({ ...a, _key: a.id }))
  } catch (e) {
    console.error('Agent 发现加载失败', e)
    error.value = e && e.message ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return agents.value
  return agents.value.filter(a =>
    `${a.name || ''} ${a.description || ''} ${a.id || ''}`.toLowerCase().includes(q)
  )
})

const counts = computed(() => {
  let local = 0, remote = 0
  for (const a of agents.value) {
    if (a.source === 'remote') remote++
    else local++
  }
  return { local, remote }
})

function kindIcon(kind) {
  return ({ cli: '🖥️', app: '📦', mcp: '🔌', remote: '☁️', subprocess: '⚙️' })[kind] || '🤖'
}
function kindLabel(kind) {
  return ({ cli: 'CLI', app: 'App', mcp: 'MCP', remote: '远端', subprocess: '子进程' })[kind] || (kind || '未知')
}
function authLabel(scheme) {
  return ({ none: '无需鉴权', apikey: 'API Key', oauth: 'OAuth', local: '本地', bearer: 'Bearer' })[scheme] || (scheme || '—')
}
function sourceLabel(src) {
  // ⑭ 神魔堂产品语义（2026-09-04 董董定调）：远端=封神榜（市场投票/热度），本机=本机发现
  return src === 'remote' ? '🔥 封神榜' : '本机发现'
}

onMounted(() => loadAgents())
</script>
