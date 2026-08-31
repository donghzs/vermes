<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🧱</span>
        <h1 class="text-xl font-bold">积木市场</h1>
        <span class="text-sm text-gray-400">
          {{ topTab === 'trending' ? '热门开源积木' : (activeList.length > 0 ? `${activeList.length} 个积木` : (anyLoading ? '加载中…' : '无结果')) }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <!-- 顶层 tab 切换 -->
        <div class="flex items-center gap-1 bg-gray-100 dark:bg-gray-800 rounded-lg p-1">
          <button
            @click="topTab = 'market'"
            class="px-3 py-1 text-sm rounded-md transition"
            :class="topTab === 'market' ? 'bg-white dark:bg-gray-700 text-emerald-600 dark:text-emerald-400 font-medium shadow-sm' : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'"
          >🛒 市场</button>
          <button
            @click="topTab = 'trending'; loadTrending()"
            class="px-3 py-1 text-sm rounded-md transition"
            :class="topTab === 'trending' ? 'bg-white dark:bg-gray-700 text-emerald-600 dark:text-emerald-400 font-medium shadow-sm' : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'"
          >🔥 热门榜</button>
        </div>
        <button
          v-if="topTab === 'market'"
          @click="loadAll(true)"
          :disabled="anyLoading"
          class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition"
        >
          {{ anyLoading ? '刷新中…' : '🔄 刷新' }}
        </button>
      </div>
    </div>

    <!-- ════════ 市场 tab ════════ -->
    <template v-if="topTab === 'market'">
      <!-- 类型筛选 + 搜索 -->
      <div class="px-6 py-3 border-b border-gray-200 dark:border-gray-800 flex flex-wrap items-center gap-2">
        <button
          v-for="t in TYPE_FILTERS"
          :key="t.key"
          @click="typeFilter = t.key"
          class="px-3 py-1 text-xs rounded-full transition"
          :class="typeFilter === t.key
            ? 'bg-emerald-600 text-white'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
        >{{ t.label }}</button>

        <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

        <!-- 技能市场渠道筛选 -->
        <template v-if="typeFilter === 'skill'">
          <button
            v-for="s in SOURCE_FILTERS"
            :key="s.id"
            @click="skillSource = s.id; searchSkills()"
            class="px-2.5 py-1 text-xs rounded-full transition"
            :class="skillSource === s.id
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'"
          >{{ s.label }}</button>
        </template>

        <span class="w-px h-5 bg-gray-200 dark:border-gray-700 mx-1"></span>

        <input
          v-model="query"
          @keyup.enter="searchSkills"
          :placeholder="typeFilter === 'skill' ? '搜索技能名称…' : '搜索名称 / 描述…'"
          class="px-3 py-1 text-sm rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-emerald-500 w-64"
        />
      </div>

      <!-- 内容 -->
      <div class="flex-1 overflow-y-auto p-6">
        <div v-if="activeList.length === 0 && allSourcesLoading" class="text-center text-gray-400 py-12">
          <p class="text-lg animate-pulse">⏳ 加载中…</p>
          <p class="text-sm mt-2 text-gray-400">正在从开源社区拉取积木，可能需要几秒</p>
        </div>

        <div v-else-if="activeList.length === 0 && !anyLoading" class="text-center text-gray-400 py-12">
          <p class="text-lg">🧱 暂无可安装的积木</p>
          <p class="text-sm mt-2">换个筛选或搜索关键词试试</p>
        </div>

        <div v-else>
          <div v-if="typeFilter === '' || typeFilter === 'skill'" class="mb-4">
            <div v-if="skillsLoading && skillMarketItems.length === 0" class="text-center text-sm text-gray-400 py-3 animate-pulse">
              🧩 技能市场搜索中…（从 GitHub/官方/Skillhub 拉取，约 10-30 秒）
            </div>
            <div v-else-if="skillsLoading && skillMarketItems.length > 0" class="text-center text-xs text-gray-400 py-1">
              🧩 技能市场刷新中…
            </div>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <MarketCard
              v-for="item in activeList"
              :key="item._key"
              :item="item"
              :busy="busyId === item._key"
              @install="onInstall"
            />
          </div>
        </div>
      </div>
    </template>

    <!-- ════════ 热门榜 tab ════════ -->
    <template v-if="topTab === 'trending'">
      <!-- 榜单切换 + 筛选 -->
      <div class="px-6 py-3 border-b border-gray-200 dark:border-gray-800 flex flex-wrap items-center gap-2">
        <button
          v-for="b in BOARDS"
          :key="b.id"
          @click="board = b.id; loadTrending()"
          class="px-3 py-1 text-xs rounded-full transition"
          :class="board === b.id
            ? 'bg-emerald-600 text-white'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
        >{{ b.label }}</button>

        <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

        <!-- GitHub 时间范围 -->
        <template v-if="board !== 'tencent'">
          <button
            v-for="s in SINCE_FILTERS"
            :key="s.id"
            @click="since = s.id; loadTrending()"
            class="px-2.5 py-1 text-xs rounded-full transition"
            :class="since === s.id
              ? 'bg-blue-600 text-white'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'"
          >{{ s.label }}</button>
        </template>

        <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

        <!-- 语言筛选（仅 GitHub） -->
        <template v-if="board !== 'tencent'">
          <button
            v-for="l in LANG_FILTERS"
            :key="l.id"
            @click="lang = l.id; loadTrending()"
            class="px-2.5 py-1 text-xs rounded-full transition"
            :class="lang === l.id
              ? 'bg-purple-600 text-white'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'"
          >{{ l.label }}</button>
        </template>
      </div>

      <!-- 内容 -->
      <div class="flex-1 overflow-y-auto p-6">
        <div v-if="trendingLoading" class="text-center text-gray-400 py-12">
          <p class="text-lg animate-pulse">⏳ 加载中…</p>
          <p class="text-sm mt-2">从 {{ board === 'tencent' ? '腾讯开源' : 'GitHub' }} 拉取热门项目</p>
        </div>

        <div v-else-if="trendingItems.length === 0" class="text-center text-gray-400 py-12">
          <p class="text-lg">🔥 暂无数据</p>
          <p class="text-sm mt-2">换个榜单或筛选条件试试</p>
        </div>

        <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <div
            v-for="(item, idx) in trendingItems"
            :key="item.full_name || item.name"
            class="border border-gray-200 dark:border-gray-700 rounded-xl p-4 hover:border-emerald-400 dark:hover:border-emerald-500 hover:shadow-md transition flex flex-col gap-2"
          >
            <div class="flex items-start gap-3">
              <span class="text-sm font-mono text-gray-400 w-6 shrink-0">#{{ idx + 1 }}</span>
              <img v-if="item.owner_avatar" :src="item.owner_avatar" class="w-8 h-8 rounded-full shrink-0" />
              <div class="min-w-0 flex-1">
                <a :href="item.url" target="_blank" rel="noopener" class="text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline truncate block">{{ item.full_name || item.name }}</a>
                <p class="text-xs text-gray-500 dark:text-gray-400 line-clamp-2 mt-0.5">{{ item.description || '（无描述）' }}</p>
              </div>
            </div>
            <div class="flex items-center gap-3 text-xs text-gray-500 dark:text-gray-400 flex-wrap">
              <span class="text-yellow-600 dark:text-yellow-400">⭐ {{ formatStars(item.stars) }}</span>
              <span v-if="item.language" class="text-blue-500">{{ item.language }}</span>
              <span v-if="item.forks">⑂ {{ formatStars(item.forks) }}</span>
              <span v-if="item.topics && item.topics.length" class="text-gray-400 truncate">{{ item.topics.slice(0, 3).join(', ') }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'
import { toast } from '../utils/toast'
import { envHeaders } from '../utils/env'
import MarketCard from './MarketCard.vue'

// ── 顶层 tab ──
const topTab = ref('market')

// ── 类型筛选 ──
const TYPE_FILTERS = [
  { key: '', label: '全部' },
  { key: 'skill', label: '🧩 技能' },
  { key: 'module', label: '📦 模块' },
  { key: 'software', label: '🖥 软件' },
]

// ── 技能市场渠道 ──
const SOURCE_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'official', label: '官方' },
  { id: 'clawhub', label: 'QClaw' },
  { id: 'github', label: 'GitHub' },
  { id: 'skillhub', label: 'Skillhub' },
  { id: 'lobehub', label: 'LobeHub' },
]

const typeFilter = ref('')
const skillSource = ref('all')
const query = ref('')
const busyId = ref('')

// ── 各源独立加载态 ──
const skillsLoading = ref(false)
const modulesLoading = ref(false)
const softwareLoading = ref(false)

const anyLoading = computed(() => skillsLoading.value || modulesLoading.value || softwareLoading.value)
const allSourcesLoading = computed(() => skillsLoading.value && modulesLoading.value && softwareLoading.value)

// ── 市场数据 ──
const skillMarketItems = ref([])
const moduleItems = ref([])
const softwareItems = ref([])

const activeList = computed(() => {
  const q = query.value.trim().toLowerCase()
  const filterFn = (item) => {
    if (q && !`${item.name || ''} ${item.description || ''} ${item._key || ''}`.toLowerCase().includes(q)) return false
    return true
  }
  if (typeFilter.value === 'skill') return skillMarketItems.value.filter(filterFn)
  if (typeFilter.value === 'module') return moduleItems.value.filter(filterFn)
  if (typeFilter.value === 'software') return softwareItems.value.filter(filterFn)
  return [...skillMarketItems.value, ...moduleItems.value, ...softwareItems.value].filter(filterFn)
})

async function loadAll(refresh = false) {
  loadSkills(refresh)
  loadModules()
  loadSoftware()
}

async function loadSkills(refresh = false) {
  skillsLoading.value = true
  try {
    const params = new URLSearchParams()
    if (query.value) params.set('q', query.value)
    if (skillSource.value !== 'all') params.set('source', skillSource.value)
    params.set('limit', '50')
    const data = await api.get(`/skills/market?${params}`)
    const items = (data.items || data.results || []).map(s => ({
      _key: `skill:${s.name}`,
      _type: 'skill',
      name: s.display_name || s.name,
      id: s.name,
      description: s.description || s.summary || '',
      version: s.version || '',
      source: s.source || '',
      trust: s.trust_level || s.trust || '',
      trust_level: s.trust_level || '',
      tags: s.tags || [],
      repo: s.repo || '',
      install_state: s.installed ? 'installed' : 'available',
      size_label: s.size_label || '',
      security_audits: s.extra?.security_audits || s.security_audits || null,
      raw: s,
    }))
    skillMarketItems.value = items
  } catch (e) {
    console.error('技能市场加载失败', e)
  } finally {
    skillsLoading.value = false
  }
}

function searchSkills() { loadSkills() }

async function loadModules() {
  modulesLoading.value = true
  try {
    const data = await api.get('/v1/modules/market')
    const items = (data.modules || data.catalog || data.items || []).map(m => ({
      _key: `module:${m.name}`,
      _type: 'module',
      name: m.display_name || m.name,
      id: m.name,
      description: m.description || '',
      version: m.version || '',
      source: 'catalog',
      install_state: m.installed ? 'installed' : 'available',
      recommended: m.recommended || false,
      tools_count: m.tools_count || (m.provides_tools || []).length,
      size_label: m.size_code ? Math.round(m.size_code / 1024) + 'KB' : '',
      raw: m,
    }))
    moduleItems.value = items
  } catch (e) {
    console.error('模块商店加载失败', e)
  } finally {
    modulesLoading.value = false
  }
}

async function loadSoftware() {
  softwareLoading.value = true
  try {
    const data = await api.get('/adapters/installed')
    const installed = (data.adapters || data || []).map(a => ({
      _key: `software:${a.name}`,
      _type: 'software',
      name: a.display_name || a.name,
      id: a.name,
      description: a.description || '',
      version: a.version || '',
      source: 'adapter',
      install_state: 'installed',
      tools_count: a.tools_count || 0,
      raw: a,
    }))
    let recommended = []
    try {
      const rec = await api.get('/adapters/recommend')
      recommended = (rec.recommendations || rec.items || []).map(r => ({
        _key: `software-rec:${r.name || r.id}`,
        _type: 'software',
        name: r.display_name || r.name,
        id: r.name || r.id,
        description: r.description || r.reason || '',
        version: '',
        source: 'recommended',
        install_state: 'available',
        raw: r,
      }))
    } catch {}
    const installedIds = new Set(installed.map(i => i.id))
    softwareItems.value = [...installed, ...recommended.filter(r => !installedIds.has(r.id))]
  } catch (e) {
    console.error('软件发现加载失败', e)
  } finally {
    softwareLoading.value = false
  }
}

async function onInstall(item) {
  busyId.value = item._key
  try {
    let r
    if (item._type === 'skill') {
      r = await api.post('/skills/install', { name: item.id })
    } else if (item._type === 'module') {
      r = await api.post('/v1/modules/market/install', { name: item.id })
    } else if (item._type === 'software') {
      toast.info('软件适配器请通过 Agent 管理 → 软件 tab 安装')
      busyId.value = ''
      return
    }
    if (r?.ok !== false) {
      toast.success(r?.message || `已安装 ${item.name}`)
      await loadAll(true)
    } else {
      toast.error(r?.message || '安装失败')
    }
  } catch (e) {
    toast.error('安装失败：' + (e?.message || e))
  } finally {
    busyId.value = ''
  }
}

// ── 热门榜 ──
const BOARDS = [
  { id: 'github', label: '🐙 GitHub 热门', api: '/api/github/trending' },
  { id: 'tencent', label: '🐧 腾讯开源', api: '/api/trending/tencent' },
]
const SINCE_FILTERS = [
  { id: 'daily', label: '日榜' },
  { id: 'weekly', label: '周榜' },
  { id: 'monthly', label: '月榜' },
]
const LANG_FILTERS = [
  { id: '', label: '全部' },
  { id: 'python', label: 'Python' },
  { id: 'javascript', label: 'JS' },
  { id: 'typescript', label: 'TS' },
  { id: 'go', label: 'Go' },
  { id: 'rust', label: 'Rust' },
  { id: 'java', label: 'Java' },
  { id: 'cpp', label: 'C++' },
  { id: 'shell', label: 'Shell' },
]

const board = ref('github')
const since = ref('daily')
const lang = ref('')
const trendingLoading = ref(false)
const trendingItems = ref([])

async function loadTrending() {
  trendingLoading.value = true
  try {
    const b = BOARDS.find(b => b.id === board.value)
    if (!b) return
    const params = new URLSearchParams()
    params.set('limit', '30')
    if (board.value === 'github') {
      params.set('since', since.value)
      if (lang.value) params.set('language', lang.value)
    }
    const resp = await fetch(`${b.api}?${params}`, { headers: envHeaders() })
    const data = await resp.json()
    trendingItems.value = data.items || []
  } catch (e) {
    console.error('热门榜加载失败', e)
    trendingItems.value = []
  } finally {
    trendingLoading.value = false
  }
}

function formatStars(n) {
  if (!n) return '0'
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

onMounted(() => loadAll())
</script>
