<script setup>
import { computed, reactive } from 'vue'

const props = defineProps({
  items: { type: Array, required: true }, // 树根节点(含 children / depth)
})

// 折叠状态：存已折叠节点的 id
const collapsed = reactive(new Set())

function toggle(id) {
  if (collapsed.has(id)) collapsed.delete(id)
  else collapsed.add(id)
}

// 可见(考虑折叠)的深度优先扁平列表
const visible = computed(() => {
  const out = []
  const walk = (nodes, depth) => {
    for (const n of nodes) {
      out.push({ ...n, depth })
      if (n.children && n.children.length && !collapsed.has(n.id)) walk(n.children, depth + 1)
    }
  }
  walk(props.items || [], 0)
  return out
})

const total = computed(() => countNodes(props.items || []))
const completed = computed(() => countByStatus(props.items || [], 'completed'))
const inProgress = computed(() => countByStatus(props.items || [], 'in_progress'))
const failed = computed(() => countByStatus(props.items || [], 'failed'))
const percent = computed(() => (total.value ? Math.round((completed.value / total.value) * 100) : 0))

function countNodes(nodes) {
  return nodes.reduce((s, n) => s + 1 + countNodes(n.children || []), 0)
}
function countByStatus(nodes, st) {
  return nodes.reduce((s, n) => s + (n.status === st ? 1 : 0) + countByStatus(n.children || [], st), 0)
}

function statusIcon(st) {
  return ({ pending: '○', in_progress: '▶', completed: '✅', failed: '❌', cancelled: '⏭' })[st] || '○'
}
function statusClass(st) {
  return {
    pending: 'text-gray-400',
    in_progress: 'text-blue-500',
    completed: 'text-green-500',
    failed: 'text-red-500',
    cancelled: 'text-gray-400',
  }[st] || 'text-gray-400'
}
</script>

<template>
  <div class="mx-3 mt-3 rounded-xl border border-gray-200 dark:border-gray-700 bg-gray-50/70 dark:bg-gray-800/40 overflow-hidden">
    <!-- 头部：标题 + 进度 -->
    <div class="flex items-center justify-between px-3 py-2 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <span class="text-sm">🌳</span>
        <span class="text-xs font-semibold text-gray-700 dark:text-gray-200">任务流</span>
        <span class="text-[11px] text-gray-400">已完成 {{ completed }} / 共 {{ total }}</span>
      </div>
      <span class="text-[11px] text-gray-400">{{ percent }}%</span>
    </div>
    <!-- 总进度条 -->
    <div class="px-3 pt-2">
      <div class="w-full h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
        <div class="h-full bg-green-500 transition-all duration-500" :style="{ width: percent + '%' }"></div>
      </div>
    </div>
    <!-- 树形列表 -->
    <div class="px-2 py-2 max-h-64 overflow-y-auto">
      <div
        v-for="node in visible"
        :key="node.id"
        class="flex items-center gap-1.5 py-1 text-xs rounded hover:bg-gray-100 dark:hover:bg-gray-700/50"
        :style="{ paddingLeft: (node.depth * 14 + 4) + 'px' }"
      >
        <!-- 折叠按钮(仅父节点) -->
        <button
          v-if="node.children && node.children.length"
          @click="toggle(node.id)"
          class="w-4 h-4 flex items-center justify-center text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 shrink-0"
        >
          <span class="text-[10px] transition-transform" :class="collapsed.has(node.id) ? '' : 'rotate-90'">▶</span>
        </button>
        <span v-else class="w-4 shrink-0"></span>

        <span :class="statusClass(node.status)" class="shrink-0">{{ statusIcon(node.status) }}</span>
        <span
          class="truncate"
          :class="node.status === 'completed'
            ? 'text-gray-400 dark:text-gray-500 line-through'
            : 'text-gray-700 dark:text-gray-200'"
        >{{ node.content || node.title }}</span>
        <span v-if="node.agent_role && node.agent_role !== 'default'" class="ml-auto text-[10px] text-gray-400 shrink-0">{{ node.agent_role }}</span>
      </div>
    </div>
    <!-- 底部状态行 -->
    <div v-if="inProgress || failed" class="px-3 py-1.5 border-t border-gray-200 dark:border-gray-700 flex items-center gap-3 text-[11px]">
      <span v-if="inProgress" class="text-blue-500">▶ {{ inProgress }} 进行中</span>
      <span v-if="failed" class="text-red-500">❌ {{ failed }} 失败</span>
    </div>
  </div>
</template>
