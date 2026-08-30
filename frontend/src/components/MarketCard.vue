<template>
  <div class="rounded-xl p-5 border transition flex flex-col bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-emerald-400 dark:hover:border-emerald-500">
    <!-- 标题行 -->
    <div class="flex items-start justify-between gap-3 mb-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span class="text-lg">{{ typeEmoji }}</span>
          <h3 class="text-base font-semibold truncate text-gray-900 dark:text-gray-100">{{ item.name }}</h3>
          <span v-if="item.version" class="text-xs text-gray-400 shrink-0">v{{ item.version }}</span>
        </div>
        <p class="text-xs text-gray-400 mt-0.5 font-mono truncate">{{ item.id }}</p>
      </div>
      <div class="flex items-center gap-1.5 shrink-0">
        <span v-if="item.recommended" class="text-xs px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300">推荐</span>
        <span v-if="item.source && item.source !== 'catalog'" class="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300">{{ sourceLabel }}</span>
        <span class="text-xs px-2 py-1 rounded-full" :class="stateClass">{{ stateLabel }}</span>
      </div>
    </div>

    <!-- 描述 -->
    <p class="text-sm text-gray-600 dark:text-gray-300 mb-3 line-clamp-2" v-if="item.description">{{ item.description }}</p>
    <p class="text-sm text-gray-400 dark:text-gray-600 mb-3 italic" v-else>（无描述）</p>

    <!-- 元信息 -->
    <div class="flex flex-wrap items-center gap-3 text-xs text-gray-400 dark:text-gray-500 mb-4">
      <span v-if="item.trust" :title="`信任等级：${item.trust}`">🔒 {{ item.trust }}</span>
      <span v-if="item.tools_count">🛠 {{ item.tools_count }} 工具</span>
      <span v-if="item.size_label">📦 {{ item.size_label }}</span>
    </div>

    <!-- 操作 -->
    <div class="flex gap-2 mt-auto">
      <button
        v-if="item.install_state === 'installed'"
        @click="$emit('install', item)"
        disabled
        class="flex-1 px-3 py-2 text-sm rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 text-center cursor-default"
      >✅ 已安装</button>
      <button
        v-else
        @click="$emit('install', item)"
        :disabled="busy"
        class="flex-1 px-3 py-2 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 transition font-medium text-white"
      >
        {{ busy ? '安装中…' : '⬇ 安装' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['install'])

const TYPE_EMOJI = {
  skill: '🧩',
  module: '📦',
  software: '🖥',
}

const typeEmoji = computed(() => TYPE_EMOJI[props.item._type] || '🧱')

const sourceLabel = computed(() => {
  const map = { official: '官方', clawhub: 'QClaw', github: 'GitHub', skillhub: 'Skillhub', lobehub: 'LobeHub', adapter: '适配器', recommended: '推荐' }
  return map[props.item.source] || props.item.source || ''
})

const stateClass = computed(() => {
  if (props.item.install_state === 'installed') return 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300'
  return 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
})

const stateLabel = computed(() => props.item.install_state === 'installed' ? '已安装' : '可安装')
</script>
