<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🔥</span>
        <h1 class="text-xl font-bold">热门榜</h1>
        <span class="text-sm text-gray-400">
          {{ loading ? '加载中…' : `${activeList.length} 个项目` }}
        </span>
      </div>
      <button
        @click="loadAll"
        :disabled="loading"
        class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:opacity-50 transition"
      >
        {{ loading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </div>

    <!-- 榜单切换 -->
    <div class="px-6 py-3 border-b border-gray-200 dark:border-gray-800 flex flex-wrap items-center gap-2">
      <!-- 榜单类型 -->
      <button
        v-for="t in BOARD_TYPES"
        :key="t.id"
        @click="boardType = t.id; loadAll()"
        class="px-3 py-1 text-xs rounded-full transition"
        :class="boardType === t.id
          ? 'bg-emerald-600 text-white'
          : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
      >{{ t.label }}</button>

      <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

      <!-- 时间范围（仅 GitHub 热门榜） -->
      <template v-if="boardType === 'github'">
        <button
          v-for="s in SINCE_FILTERS"
          :key="s.id"
          @click="since = s.id; loadGitHub()"
          class="px-2.5 py-1 text-xs rounded-full transition"
          :class="since === s.id
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'"
        >{{ s.label }}</button>
      </template>

      <span class="w-px h-5 bg-gray-200 dark:bg-gray-700 mx-1"></span>

      <!-- 语言筛选（仅 GitHub 热门榜） -->
      <template v-if="boardType === 'github'">
        <select
          v-model="language"
          @change="loadGitHub"
          class="px-2 py-1 text-xs rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300"
        >
          <option value="">全部语言</option>
          <option value="python">Python</option>
          <option value="javascript">JavaScript</option>
          <option value="typescript">TypeScript</option>
          <option value="go">Go</option>
          <option value="rust">Rust</option>
          <option value="java">Java</option>
          <option value="c++">C++</option>
          <option value="c">C</option>
          <option value="shell">Shell</option>
        </select>
      </template>

      <!-- 腾讯搜索 -->
      <template v-if="boardType === 'tencent'">
        <input
          v-model="tencentQuery"
          @keyup.enter="loadTencent"
          placeholder="搜索腾讯开源项目…"
          class="px-3 py-1 text-sm rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:border-emerald-500 w-64"
        />
      </template>
    </div>

    <!-- 内容 -->
    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="loading && activeList.length === 0" class="text-center text-gray-400 py-12 animate-pulse">
        <p class="text-lg">⏳ 加载中…</p>
        <p class="text-sm mt-2">从 GitHub 拉取热门项目</p>
      </div>

      <div v-else-if="activeList.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">📊 暂无数据</p>
      </div>

      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <div
          v-for="(item, idx) in activeList"
          :key="item.full_name || item.name"
          class="rounded-xl p-5 border bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-emerald-400 dark:hover:border-emerald-500 transition flex flex-col"
        >
          <!-- 标题行 -->
          <div class="flex items-start justify-between gap-3 mb-3">
            <div class="min-w-0 flex-1">
              <div class="flex items-center gap-2">
                <span class="text-sm font-mono text-gray-400">#{{ idx + 1 }}</span>
                <img v-if="item.owner_avatar" :src="item.owner_avatar" class="w-5 h-5 rounded-full" :alt="item.owner" />
                <h3 class="text-base font-semibold truncate text-gray-900 dark:text-gray-100">{{ item.name }}</h3>
              </div>
              <p class="text-xs text-gray-400 mt-0.5 font-mono truncate">{{ item.full_name || item.owner }}</p>
            </div>
            <div class="flex items-center gap-1.5 shrink-0">
              <span class="text-xs px-2 py-0.5 rounded-full bg-yellow-100 dark:bg-yellow-900/40 text-yellow-700 dark:text-yellow-300">⭐ {{ formatStars(item.stars) }}</span>
              <span v-if="item.language" class="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300">{{ item.language }}</span>
            </div>
          </div>

          <!-- 描述 -->
          <p class="text-sm text-gray-600 dark:text-gray-300 mb-3 line-clamp-2">{{ item.description || '（无描述）' }}</p>

          <!-- 标签 -->
          <div v-if="item.topics && item.topics.length" class="flex flex-wrap gap-1.5 mb-3">
            <span v-for="t in item.topics.slice(0, 4)" :key="t" class="text-xs px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-500 dark:text-blue-400">{{ t }}</span>
          </div>

          <!-- 元信息 -->
          <div class="flex flex-wrap items-center gap-3 text-xs text-gray-400 dark:text-gray-500 mb-4">
            <span>🍴 {{ formatStars(item.forks) }}</span>
            <span>🐛 {{ item.open_issues || 0 }}</span>
            <span v-if="item.pushed_at">📅 {{ formatDate(item.pushed_at) }}</span>
          </div>

          <!-- 操作 -->
          <div class="flex gap-2 mt-auto">
            <a
              :href="item.url"
              target="_blank"
              rel="noopener"
              class="flex-1 px-3 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition text-center text-gray-700 dark:text-gray-300"
            >🔗 打开仓库</a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { envHeaders } from '../utils/env'

const BOARD_TYPES = [
  { id: 'github', label: '🐙 GitHub 热门' },
  { id: 'tencent', label: '🐧 腾讯开源' },
]

const SINCE_FILTERS = [
  { id: 'daily', label: '日榜' },
  { id: 'weekly', label: '周榜' },
  { id: 'monthly', label: '月榜' },
]

const boardType = ref('github')
const since = ref('daily')
const language = ref('')
const tencentQuery = ref('')
const loading = ref(false)

const githubItems = ref([])
const tencentItems = ref([])

const activeList = computed(() => {
  return boardType.value === 'github' ? githubItems.value : tencentItems.value
})

async function loadAll() {
  if (boardType.value === 'github') {
    await loadGitHub()
  } else {
    await loadTencent()
  }
}

async function loadGitHub() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    params.set('since', since.value)
    if (language.value) params.set('language', language.value)
    params.set('limit', '25')
    const resp = await fetch(`/api/github/trending?${params}`, { headers: envHeaders() })
    const data = await resp.json()
    githubItems.value = data.items || []
  } catch (e) {
    console.error('GitHub trending failed', e)
  } finally {
    loading.value = false
  }
}

async function loadTencent() {
  loading.value = true
  try {
    const params = new URLSearchParams()
    if (tencentQuery.value) params.set('q', tencentQuery.value)
    params.set('limit', '25')
    const resp = await fetch(`/api/trending/tencent?${params}`, { headers: envHeaders() })
    const data = await resp.json()
    tencentItems.value = data.items || []
  } catch (e) {
    console.error('Tencent opensource failed', e)
  } finally {
    loading.value = false
  }
}

function formatStars(n) {
  if (!n) return '0'
  if (n >= 10000) return (n / 10000).toFixed(1) + 'w'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

function formatDate(d) {
  if (!d) return ''
  const date = new Date(d)
  const now = new Date()
  const days = Math.floor((now - date) / 86400000)
  if (days === 0) return '今天'
  if (days === 1) return '昨天'
  if (days < 7) return `${days}天前`
  if (days < 30) return `${Math.floor(days / 7)}周前`
  return date.toLocaleDateString('zh-CN')
}

onMounted(() => loadAll())
</script>
