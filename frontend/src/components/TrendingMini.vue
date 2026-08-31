<template>
  <div class="space-y-3">
    <!-- 切换 -->
    <div class="flex items-center gap-2 flex-wrap">
      <button
        v-for="t in boards"
        :key="t.id"
        @click="board = t.id; load()"
        class="px-2.5 py-1 text-xs rounded-full transition"
        :class="board === t.id ? 'bg-emerald-600 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'"
      >{{ t.label }}</button>
      <button @click="$emit('openFull')" class="ml-auto text-xs text-blue-500 hover:underline">查看全部 →</button>
    </div>

    <!-- 列表 -->
    <div v-if="loading" class="text-center text-sm text-gray-400 py-6 animate-pulse">加载中…</div>
    <div v-else-if="items.length === 0" class="text-center text-sm text-gray-400 py-6">暂无数据</div>
    <div v-else class="space-y-2">
      <a
        v-for="(item, idx) in items.slice(0, 8)"
        :key="item.full_name || item.name"
        :href="item.url"
        target="_blank"
        rel="noopener"
        class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-gray-200 dark:border-gray-700 hover:border-emerald-400 dark:hover:border-emerald-500 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
      >
        <span class="text-xs font-mono text-gray-400 w-5 text-center shrink-0">#{{ idx + 1 }}</span>
        <img v-if="item.owner_avatar" :src="item.owner_avatar" class="w-6 h-6 rounded-full shrink-0" />
        <div class="min-w-0 flex-1">
          <div class="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">{{ item.name }}</div>
          <div class="text-xs text-gray-400 truncate">{{ item.description || '（无描述）' }}</div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <span class="text-xs text-yellow-600 dark:text-yellow-400">⭐ {{ formatStars(item.stars) }}</span>
          <span v-if="item.language" class="text-xs text-blue-500">{{ item.language }}</span>
        </div>
      </a>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { envHeaders } from '../utils/env'

defineEmits(['openFull'])

const boards = [
  { id: 'github-daily', label: '🐙 日榜', api: '/api/github/trending?since=daily&limit=25' },
  { id: 'github-weekly', label: '📅 周榜', api: '/api/github/trending?since=weekly&limit=25' },
  { id: 'tencent', label: '🐧 腾讯', api: '/api/trending/tencent?limit=25' },
]

const board = ref('github-daily')
const loading = ref(false)
const items = ref([])

async function load() {
  const b = boards.find(b => b.id === board.value)
  if (!b) return
  loading.value = true
  try {
    const resp = await fetch(b.api, { headers: envHeaders() })
    const data = await resp.json()
    items.value = data.items || []
  } catch (e) {
    console.error('Trending mini load failed', e)
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

onMounted(() => load())
</script>
