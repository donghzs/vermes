<template>
  <div class="bg-gray-800 rounded-xl p-5 border border-gray-700 hover:border-gray-500 transition flex flex-col">
    <!-- 标题行：类型徽标 + 名称 + 装态 -->
    <div class="flex items-start justify-between gap-3 mb-3">
      <div class="min-w-0">
        <div class="flex items-center gap-2">
          <span class="text-lg" :title="typeLabel">{{ typeEmoji }}</span>
          <h3 class="text-base font-semibold truncate">{{ brick.name }}</h3>
          <span v-if="brick.version" class="text-xs text-gray-400 shrink-0">v{{ brick.version }}</span>
        </div>
        <p class="text-xs text-gray-500 mt-0.5 font-mono truncate" :title="brick.id">{{ brick.id }}</p>
      </div>
      <span class="text-xs px-2 py-1 rounded-full shrink-0" :class="stateClass">{{ stateLabel }}</span>
    </div>

    <!-- 描述 -->
    <p class="text-sm text-gray-300 mb-3 line-clamp-2" v-if="brick.description">{{ brick.description }}</p>
    <p class="text-sm text-gray-600 mb-3 italic" v-else>（无描述）</p>

    <!-- 能力 chips：skill 是散文弱结构，允许为空并灰显（设计 §十.5，不虚标覆盖） -->
    <div class="flex flex-wrap gap-1.5 mb-3 min-h-[22px]">
      <template v-if="caps.length">
        <span
          v-for="c in caps.slice(0, 5)"
          :key="c"
          class="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300"
        >{{ c }}</span>
        <span v-if="caps.length > 5" class="text-xs text-gray-500 self-center">
          +{{ caps.length - 5 }}
        </span>
      </template>
      <span v-else class="text-xs text-gray-600">· 能力待收录</span>
    </div>

    <!-- 元信息：域 / 工具数 / 体积 / 依赖 -->
    <div class="flex flex-wrap items-center gap-3 text-xs text-gray-500 mb-4">
      <span v-if="brick.domain">🏷 {{ brick.domain }}</span>
      <span v-if="toolsCount">🛠 {{ toolsCount }} 工具</span>
      <span v-if="sizeLabel">📦 {{ sizeLabel }}</span>
      <span v-if="requiresCount" :title="requiresTitle">⚙ 依赖 {{ requiresCount }}</span>
    </div>

    <!-- 操作：装 / 卸。
         tool 是进程内常驻（后端装/卸均为 noop），不给按钮以免误导——显示常驻标记。 -->
    <div class="flex gap-2 mt-auto">
      <span
        v-if="brick.type === 'tool'"
        class="flex-1 px-3 py-2 text-sm rounded-lg bg-gray-700/50 text-gray-400 text-center cursor-default"
        title="工具随应用启动自注册，无需安装"
      >🔒 内置常驻</span>

      <template v-else>
        <button
          v-if="brick.install_state === 'installed'"
          @click="$emit('uninstall', brick)"
          :disabled="busy"
          class="flex-1 px-3 py-2 text-sm rounded-lg bg-red-700 hover:bg-red-600 disabled:opacity-50 transition"
        >
          {{ busy ? '处理中…' : '🗑 卸载' }}
        </button>
        <button
          v-else
          @click="$emit('install', brick)"
          :disabled="busy"
          class="flex-1 px-3 py-2 text-sm rounded-lg bg-green-600 hover:bg-green-500 disabled:opacity-50 transition font-medium"
        >
          {{ busy ? '处理中…' : '⬇ 安装' }}
        </button>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  brick: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
defineEmits(['install', 'uninstall'])

const TYPE_META = {
  skill: { emoji: '🧩', label: '技能' },
  tool: { emoji: '🛠', label: '工具' },
  module: { emoji: '📦', label: '模块' },
  software: { emoji: '🖥', label: '第三方软件' },
  provider: { emoji: '🤖', label: '模型 provider' },
}

const STATE_META = {
  installed: { label: '✅ 已安装', cls: 'bg-green-900 text-green-300' },
  available: { label: '⬇ 可安装', cls: 'bg-gray-700 text-gray-300' },
  'not-installed': { label: '⬇ 未安装', cls: 'bg-gray-700 text-gray-400' },
}

const typeEmoji = computed(() => (TYPE_META[props.brick.type] || { emoji: '🧱' }).emoji)
const typeLabel = computed(() => (TYPE_META[props.brick.type] || { label: '积木' }).label)
const caps = computed(() => props.brick.capabilities || [])
const stateMeta = computed(() => STATE_META[props.brick.install_state] || STATE_META.available)
const stateLabel = computed(() => stateMeta.value.label)
const stateClass = computed(() => stateMeta.value.cls)
const toolsCount = computed(() => (props.brick.provides_tools || []).length)
const requiresCount = computed(() => (props.brick.requires || []).length)
const requiresTitle = computed(() => (props.brick.requires || []).join(', '))
const sizeLabel = computed(() => {
  const n = props.brick.size_bytes
  if (!n) return ''
  if (n >= 1024 * 1024) return (n / 1024 / 1024).toFixed(1) + 'MB'
  return Math.round(n / 1024) + 'KB'
})
</script>
