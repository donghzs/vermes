<template>
  <div class="delivery-card my-3 mx-2 rounded-xl border border-green-200 dark:border-green-800 bg-gradient-to-br from-green-50 to-white dark:from-green-900/20 dark:to-gray-800 overflow-hidden shadow-sm">
    <!-- 头部 -->
    <div class="flex items-center gap-2 px-4 py-2.5 bg-green-50 dark:bg-green-900/30 border-b border-green-100 dark:border-green-800/50">
      <span class="text-lg">✅</span>
      <span class="font-semibold text-sm text-green-700 dark:text-green-300">任务完成</span>
      <span v-if="summary.cancelled > 0" class="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400">{{ summary.cancelled }} 步取消</span>
      <span v-if="summary.in_progress > 0" class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400">{{ summary.in_progress }} 步进行中</span>
      <span class="text-xs text-gray-500 dark:text-gray-400 ml-auto">
        <template v-if="summary.total > 0">完成 {{ summary.completed }}/{{ summary.total }} 步</template>
        <span v-if="duration" class="ml-1">· 耗时 {{ duration }}</span>
      </span>
    </div>

    <!-- U-P0-2 契约摘要 + 路由 -->
    <div v-if="text || routeLabel" class="px-4 pt-2 text-xs text-gray-600 dark:text-gray-300">
      <div v-if="text">{{ text }}</div>
      <div v-if="routeLabel" class="mt-0.5 text-[11px] text-gray-400 dark:text-gray-500">本次路由：{{ routeLabel }}</div>
    </div>

    <!-- U-P0-2 交付条目（契约 items：项目/外证） -->
    <div v-if="items.length > 0" class="px-4 pt-2">
      <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">📋 交付清单 ({{ items.length }})</div>
      <div class="space-y-1">
        <div
          v-for="(it, idx) in items.slice(0, 8)"
          :key="idx"
          class="flex items-start gap-2 px-2 py-1 rounded text-xs"
        >
          <span class="text-sm">{{ it.kind === 'project_section' ? '📝' : it.kind === 'file' ? '📄' : '📦' }}</span>
          <span class="flex-1 text-gray-700 dark:text-gray-300 break-all">{{ it.title || it.path || it.source_tool || '交付项' }}</span>
          <span class="text-[10px] shrink-0" :class="verifyClass(it.verified)">{{ verifyLabel(it.verified) }}</span>
        </div>
      </div>
    </div>

    <!-- 交付产物列表（核心：用户真正关心的最终产物） -->
    <div v-if="artifacts.length > 0" class="px-4 py-2.5">
      <div class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5">📦 交付产物 ({{ artifacts.length }})</div>
      <div class="space-y-1">
        <button
          v-for="a in displayArtifacts"
          :key="a.id"
          @click="openArtifact(a)"
          class="flex items-center gap-2 w-full text-left px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 transition group"
        >
          <span class="text-sm">{{ iconFor(a) }}</span>
          <span class="text-xs text-gray-700 dark:text-gray-300 truncate flex-1 group-hover:text-green-600 dark:group-hover:text-green-400 transition">
            {{ a.title || a.path?.split('/').pop() || '未知文件' }}
          </span>
          <span class="text-[10px] text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition">点击查看 ›</span>
        </button>
        <button
          v-if="artifacts.length > showLimit"
          @click="showAllArtifacts"
          class="text-xs text-gray-500 dark:text-gray-400 hover:text-green-600 dark:hover:text-green-400 px-2 py-1"
        >
          查看全部 {{ artifacts.length }} 个产物 ›
        </button>
      </div>
    </div>

    <!-- 空态：有 items 时不显示「无产物」误导 -->
    <div v-if="artifacts.length === 0 && items.length === 0" class="px-4 py-3 text-xs text-gray-400 dark:text-gray-500 text-center">
      任务已完成（无产物产出）
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  summary: { type: Object, default: () => ({ total: 0, completed: 0, in_progress: 0 }) },
  artifacts: { type: Array, default: () => [] },
  // U-P0-2 E-P0-5：契约 items / text / route（缺省不编造）
  items: { type: Array, default: () => [] },
  text: { type: String, default: '' },
  route: { type: Object, default: null },
  startTime: { type: Number, default: 0 },
  endTime: { type: Number, default: 0 },
})

const emit = defineEmits(['openArtifact', 'showAllArtifacts'])

const routeLabel = computed(() => {
  const r = props.route
  if (!r || typeof r !== 'object') return ''
  if (r.signal === 'unavailable' || r.ok === false) return ''
  const res = r.resolved || {}
  const model = res.model || r.model || ''
  const provider = res.provider || r.provider || ''
  const strategy = r.strategy && r.strategy !== 'none' ? ` · ${r.strategy}` : ''
  if (!model && !provider) return ''
  return `${provider ? provider + '/' : ''}${model || '—'}${strategy}`
})

function verifyLabel(v) {
  if (v === 'verified') return '已核验'
  if (v === 'unverified_tool') return '未独立核验'
  if (v === 'verify_failed') return '核验失败'
  return '暂无信号'
}
function verifyClass(v) {
  if (v === 'verified') return 'text-green-600 dark:text-green-400'
  if (v === 'verify_failed') return 'text-red-500 dark:text-red-400'
  return 'text-gray-400 dark:text-gray-500'
}

// 默认显示最多 8 个产物，超出的折叠
const showLimit = ref(8)
const displayArtifacts = computed(() => props.artifacts.slice(0, showLimit.value))

const duration = computed(() => {
  if (!props.startTime || !props.endTime) return ''
  const secs = Math.round((props.endTime - props.startTime) / 1000)
  if (secs < 60) return `${secs}秒`
  const mins = Math.floor(secs / 60)
  const rem = secs % 60
  return rem ? `${mins}分${rem}秒` : `${mins}分钟`
})

function iconFor(a) {
  const ext = (a.path || '').split('.').pop()?.toLowerCase()
  if (['md', 'markdown'].includes(ext)) return '📄'
  if (['html', 'htm'].includes(ext)) return '🌐'
  if (['pdf'].includes(ext)) return '📕'
  if (['docx', 'doc'].includes(ext)) return '📘'
  if (['xlsx', 'xls', 'csv'].includes(ext)) return '📊'
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return '🖼️'
  if (['py', 'js', 'ts', 'sh'].includes(ext)) return '🐍'
  if (['step', 'stp', 'stl', 'obj', 'fcdoc', 'dxf', 'gcode', 'iges', '3mf', 'gltf', 'glb'].includes(ext)) return '🧊'
  if (['mp4', 'mov', 'avi', 'webm'].includes(ext)) return '🎬'
  if (['mp3', 'wav', 'm4a', 'ogg'].includes(ext)) return '🔊'
  return '📄'
}

function openArtifact(a) {
  emit('openArtifact', a)
}
function showAllArtifacts() {
  emit('showAllArtifacts')
}
</script>

<style scoped>
.delivery-card {
  max-width: 420px;
}
</style>
