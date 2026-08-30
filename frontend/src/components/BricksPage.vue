<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🧱</span>
        <h1 class="text-xl font-bold">积木市场</h1>
        <span class="text-sm text-gray-400">
          {{ activeList.length > 0 ? `${activeList.length} 个积木` : (anyLoading ? '加载中…' : '无结果') }}
        </span>
      </div>
      <button
        @click="loadAll(true)"
        :disabled="anyLoading"
        class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition"
      >
        {{ anyLoading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </div>

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

      <!-- 技能市场渠道筛选（仅技能类型时显示） -->
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

      <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

      <input
        v-model="query"
        @keyup.enter="searchSkills"
        :placeholder="typeFilter === 'skill' ? '搜索技能名称…' : '搜索名称 / 描述…'"
        class="px-3 py-1 text-sm rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:border-emerald-500 w-64"
      />
    </div>

    <!-- 内容 -->
    <div class="flex-1 overflow-y-auto p-6">
      <!-- 全部空白 + 全部在加载 -->
      <div v-if="activeList.length === 0 && allSourcesLoading" class="text-center text-gray-400 py-12">
        <p class="text-lg animate-pulse">⏳ 加载中…</p>
        <p class="text-sm mt-2 text-gray-400">正在从开源社区拉取积木，可能需要几秒</p>
      </div>

      <div v-else-if="activeList.length === 0 && !anyLoading" class="text-center text-gray-400 py-12">
        <p class="text-lg">🧱 暂无可安装的积木</p>
        <p class="text-sm mt-2">换个筛选或搜索关键词试试</p>
      </div>

      <div v-else>
        <!-- 技能搜索中提示（不阻塞其他源展示） -->
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
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'
import { toast } from '../utils/toast'
import MarketCard from './MarketCard.vue'

// ── 类型筛选 ──
const TYPE_FILTERS = [
  { key: '', label: '全部' },
  { key: 'skill', label: '🧩 技能' },
  { key: 'module', label: '📦 模块' },
  { key: 'software', label: '🖥 软件' },
]

// ── 技能市场渠道来源 ──
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

// ── 各源独立加载态（不互相阻塞）──
const skillsLoading = ref(false)
const modulesLoading = ref(false)
const softwareLoading = ref(false)

const anyLoading = computed(() => skillsLoading.value || modulesLoading.value || softwareLoading.value)
const allSourcesLoading = computed(() => skillsLoading.value && modulesLoading.value && softwareLoading.value)

// ── 三个市场的数据 ──
const skillMarketItems = ref([])
const moduleItems = ref([])
const softwareItems = ref([])

// 合并展示列表
const activeList = computed(() => {
  const q = query.value.trim().toLowerCase()
  const filterFn = (item) => {
    if (q && !`${item.name || ''} ${item.description || ''} ${item._key || ''}`.toLowerCase().includes(q)) return false
    return true
  }

  if (typeFilter.value === 'skill') return skillMarketItems.value.filter(filterFn)
  if (typeFilter.value === 'module') return moduleItems.value.filter(filterFn)
  if (typeFilter.value === 'software') return softwareItems.value.filter(filterFn)

  return [
    ...skillMarketItems.value,
    ...moduleItems.value,
    ...softwareItems.value,
  ].filter(filterFn)
})

// ── 各源独立加载 ──
async function loadAll(refresh = false) {
  // 并行发起，但各自独立管理 loading 态
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

function searchSkills() {
  loadSkills()
}

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

// ── 安装 ──
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

onMounted(() => loadAll())
</script>
