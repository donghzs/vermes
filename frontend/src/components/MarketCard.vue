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
      <div class="flex items-center gap-1.5 shrink-0 flex-wrap justify-end">
        <span v-if="item.recommended" class="text-xs px-2 py-0.5 rounded-full bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300">推荐</span>
        <!-- 来源标签 -->
        <span v-if="sourceLabel" class="text-xs px-2 py-0.5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300">{{ sourceLabel }}</span>
        <!-- 信任等级徽标 -->
        <span v-if="trustBadge" class="text-xs px-2 py-0.5 rounded-full" :class="trustBadge.cls" :title="trustBadge.title">{{ trustBadge.label }}</span>
        <!-- 安装状态 -->
        <span class="text-xs px-2 py-1 rounded-full" :class="stateClass">{{ stateLabel }}</span>
      </div>
    </div>

    <!-- 描述 -->
    <p class="text-sm text-gray-600 dark:text-gray-300 mb-3 line-clamp-2" v-if="item.description">{{ item.description }}</p>
    <p class="text-sm text-gray-400 dark:text-gray-600 mb-3 italic" v-else>（无描述）</p>

    <!-- 标签 -->
    <div v-if="item.tags && item.tags.length" class="flex flex-wrap gap-1.5 mb-3">
      <span v-for="t in item.tags.slice(0, 4)" :key="t" class="text-xs px-1.5 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-500 dark:text-blue-400">{{ t }}</span>
    </div>

    <!-- 安全扫描结果（已有审计数据时展示） -->
    <div v-if="securityInfo" class="text-xs mb-3 p-2 rounded-lg" :class="securityInfo.cls">
      <span class="font-medium">{{ securityInfo.icon }} {{ securityInfo.label }}</span>
      <span v-if="securityInfo.detail" class="ml-1 opacity-75">{{ securityInfo.detail }}</span>
    </div>

    <!-- 元信息 -->
    <div class="flex flex-wrap items-center gap-3 text-xs text-gray-400 dark:text-gray-500 mb-4">
      <span v-if="item.tools_count">🛠 {{ item.tools_count }} 工具</span>
      <span v-if="item.size_label">📦 {{ item.size_label }}</span>
      <span v-if="item.repo" class="truncate">🔗 {{ item.repo }}</span>
    </div>

    <!-- 安装确认弹层（community 级技能安装前提示） -->
    <div v-if="showConfirm" class="mb-3 p-3 rounded-lg border border-amber-200 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20">
      <p class="text-sm text-amber-800 dark:text-amber-200 font-medium mb-2">⚠️ 安装确认</p>
      <p class="text-xs text-amber-700 dark:text-amber-300 mb-3">
        该技能来自社区来源（{{ sourceLabel || item.source || '未知' }}），
        安装前 Vermes 会自动进行安全扫描（检测数据外泄/prompt 注入/破坏性命令等）。
        扫描通过后才会安装，危险技能会被自动阻断。
      </p>
      <div class="flex gap-2">
        <button @click="confirmInstall" :disabled="busy"
          class="px-3 py-1.5 text-xs rounded-lg bg-amber-600 hover:bg-amber-500 disabled:opacity-50 text-white transition">
          {{ busy ? '扫描安装中…' : '确认并扫描' }}
        </button>
        <button @click="showConfirm = false" class="px-3 py-1.5 text-xs rounded-lg bg-gray-200 dark:bg-gray-700 hover:bg-gray-300 dark:hover:bg-gray-600 transition">
          取消
        </button>
      </div>
    </div>

    <!-- 操作 -->
    <div class="flex gap-2 mt-auto" v-if="!showConfirm">
      <div v-if="item.install_state === 'installed'" class="flex gap-2">
        <span class="flex-1 px-3 py-2 text-sm rounded-lg bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 text-center">✅ 已安装</span>
        <button
          @click="$emit('uninstall', item)"
          :disabled="busy"
          class="px-3 py-2 text-sm rounded-lg bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/50 disabled:opacity-50 transition"
          title="卸载"
        >🗑</button>
      </div>
      <button
        v-else-if="needsConfirm"
        @click="showConfirm = true"
        :disabled="busy"
        class="flex-1 px-3 py-2 text-sm rounded-lg bg-amber-500 hover:bg-amber-400 disabled:opacity-50 transition font-medium text-white"
      >
        ⬇ 安装
      </button>
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
import { computed, ref } from 'vue'

const props = defineProps({
  item: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['install', 'uninstall'])

const showConfirm = ref(false)

const TYPE_EMOJI = {
  skill: '🧩',
  module: '📦',
  software: '🖥',
}

const typeEmoji = computed(() => TYPE_EMOJI[props.item._type] || '🧱')

// 来源标签
const SOURCE_MAP = {
  official: '官方',
  clawhub: 'QClaw',
  github: 'GitHub',
  skillhub: 'Skillhub',
  lobehub: 'LobeHub',
  catalog: 'Catalog',
  adapter: '适配器',
  recommended: '推荐',
}
const sourceLabel = computed(() => {
  if (!props.item.source || props.item.source === 'catalog') return ''
  return SOURCE_MAP[props.item.source] || props.item.source
})

// 信任等级徽标
const TRUST_BADGES = {
  builtin: { label: '🔷 内置', cls: 'bg-cyan-100 dark:bg-cyan-900/40 text-cyan-700 dark:text-cyan-300', title: 'Vermes 内置技能，无需扫描' },
  trusted: { label: '🟢 可信', cls: 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300', title: '来自可信仓库（openai/anthropics/huggingface）' },
  community: { label: '🟡 社区', cls: 'bg-yellow-100 dark:bg-yellow-900/40 text-yellow-700 dark:text-yellow-300', title: '社区来源，安装前自动安全扫描' },
  official: { label: '⭐ 官方', cls: 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300', title: '官方维护' },
}
const trustBadge = computed(() => {
  const t = props.item.trust || props.item.trust_level
  if (!t) return null
  return TRUST_BADGES[t] || null
})

// 是否需要安装确认（community 级技能需要）
const needsConfirm = computed(() => {
  if (props.item._type !== 'skill') return false
  const t = props.item.trust || props.item.trust_level
  return t === 'community' || (!t && props.item.source && !['official', 'builtin', 'trusted'].includes(props.item.source))
})

// 安全扫描结果（如果 item 带了 security_audits 或 scan_verdict）
const securityInfo = computed(() => {
  // 已安装的有审计日志可查
  if (props.item.security_audits && Object.keys(props.item.security_audits).length) {
    const audits = props.item.security_audits
    const allClean = Object.values(audits).every(v => v.toLowerCase().includes('clean') || v.toLowerCase().includes('safe') || v.toLowerCase().includes('pass'))
    if (allClean) {
      return { icon: '🛡️', label: '安全扫描通过', detail: Object.entries(audits).map(([k,v]) => `${k}: ${v}`).join(' · '), cls: 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300' }
    }
    return { icon: '⚠️', label: '有安全发现', detail: Object.entries(audits).map(([k,v]) => `${k}: ${v}`).join(' · '), cls: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300' }
  }
  return null
})

const stateClass = computed(() => {
  if (props.item.install_state === 'installed') return 'bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300'
  return 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400'
})

const stateLabel = computed(() => props.item.install_state === 'installed' ? '已安装' : '可安装')

function confirmInstall() {
  showConfirm.value = false
  emit('install', props.item)
}
</script>
