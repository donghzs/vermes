<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { useRightPanel } from '../composables/useRightPanel'
import SkillManager from './SkillManager.vue'
import KnowledgeBase from './KnowledgeBase.vue'
import MCPManager from './MCPManager.vue'
import MemoryBrowser from './MemoryBrowser.vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'

const { open, tab, closePanel, setTab } = useRightPanel()

const tabs = [
  { id: 'skills', label: '技能', icon: '🧩' },
  { id: 'tools', label: '工具', icon: '🛠️' },
  { id: 'mcp', label: 'MCP', icon: '🔌' },
  { id: 'memory', label: '记忆', icon: '🧠' },
  { id: 'knowledge', label: '知识库', icon: '📚' },
]

// ── 工具集（tools）视图 ──
const toolsets = ref([])
const toolsLoading = ref(false)
async function loadToolsets() {
  toolsLoading.value = true
  try {
    const data = await api.getToolsets()
    toolsets.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('Failed to load toolsets:', e)
  } finally {
    toolsLoading.value = false
  }
}
async function toggleToolset(name, enabled) {
  try {
    await api.toggleToolset(name, enabled)
    const ts = toolsets.value.find(t => t.name === name)
    if (ts) ts.enabled = enabled
  } catch (e) {
    const ts = toolsets.value.find(t => t.name === name)
    if (ts) ts.enabled = !enabled
    toast.error('切换失败: ' + (e.message || e))
  }
}

// 切到「工具」标签或面板打开时按需加载工具集
watch(tab, (t) => { if (t === 'tools') loadToolsets() })
watch(open, (o) => { if (o && tab.value === 'tools' && toolsets.value.length === 0) loadToolsets() })

function onKey(e) {
  if (e.key === 'Escape' && open.value) closePanel()
}
onMounted(() => document.addEventListener('keydown', onKey))
onUnmounted(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <!-- 背景遮罩 -->
    <div v-if="open" class="fixed inset-0 z-[90] bg-black/40" @click="closePanel"></div>

    <!-- 右侧大面板：从右滑入，接近满高、足够宽 -->
    <transition name="drawer-slide">
      <aside
        v-if="open"
        class="fixed top-0 right-0 z-[91] h-full w-[600px] max-w-[94vw] bg-white dark:bg-gray-900 border-l border-gray-200 dark:border-gray-700 shadow-2xl flex flex-col"
      >
        <!-- 头部 -->
        <header class="shrink-0 px-5 py-3.5 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div class="flex items-center gap-3">
            <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100">Agent 管理</h2>
            <nav class="flex items-center gap-1">
              <button
                v-for="t in tabs" :key="t.id"
                @click="setTab(t.id)"
                :class="tab === t.id
                  ? 'bg-green-500 text-white'
                  : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800'"
                class="px-3 py-1.5 rounded-lg text-sm font-medium transition flex items-center gap-1"
              >
                <span>{{ t.icon }}</span><span>{{ t.label }}</span>
              </button>
            </nav>
          </div>
          <button @click="closePanel" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 transition" title="关闭 (Esc)">
            <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </header>

        <!-- 内容区：大界面，独立滚动 -->
        <div class="flex-1 overflow-y-auto px-5 py-4">
          <!-- 技能：复用完整 SkillManager（已装管理 + 发现市场） -->
          <SkillManager v-if="tab === 'skills'" />

          <!-- 工具：工具集总览与启停 -->
          <div v-else-if="tab === 'tools'" class="space-y-3">
            <div class="flex items-center justify-between">
              <p class="text-sm text-gray-500 dark:text-gray-400">工具按工具集组织，开启后对应能力在对话中可用。</p>
            </div>
            <div v-if="toolsLoading" class="text-center py-10 text-sm text-gray-400 animate-pulse">加载工具集中…</div>
            <div v-else-if="toolsets.length === 0" class="text-center py-10 text-sm text-gray-400">
              <div class="text-3xl mb-2">🛠️</div>
              <div>暂无工具集</div>
            </div>
            <div v-else class="space-y-2">
              <div
                v-for="ts in toolsets" :key="ts.name"
                class="flex items-start gap-3 px-3 py-3 rounded-xl border border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-800 transition"
              >
                <span class="text-lg mt-0.5">{{ ts.enabled ? '✅' : '⬜' }}</span>
                <div class="flex-1 min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium text-gray-800 dark:text-gray-100">{{ ts.label || ts.name }}</span>
                    <span v-if="ts.configured === false" class="text-[10px] px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/40 text-orange-500">未配置</span>
                  </div>
                  <div class="text-[11px] text-gray-400 mt-0.5 truncate">{{ ts.description || ts.name }}</div>
                  <div v-if="ts.tools && ts.tools.length" class="flex flex-wrap gap-1 mt-2">
                    <span
                      v-for="tool in ts.tools" :key="tool"
                      class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 rounded truncate max-w-[120px]"
                      :title="tool"
                    >{{ tool }}</span>
                  </div>
                </div>
                <button
                  @click="toggleToolset(ts.name, !ts.enabled)"
                  class="relative inline-flex h-5 w-9 items-center rounded-full transition-colors flex-shrink-0 mt-1"
                  :class="ts.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'"
                  :title="ts.enabled ? '已开启，点击关闭' : '已关闭，点击开启'"
                >
                  <span class="inline-block h-4 w-4 transform rounded-full bg-white transition-transform" :class="ts.enabled ? 'translate-x-4' : 'translate-x-0.5'"></span>
                </button>
              </div>
            </div>
          </div>

          <!-- MCP：复用完整 MCPManager -->
          <MCPManager v-else-if="tab === 'mcp'" />

          <!-- 记忆：全量浏览+搜索+恢复 -->
          <MemoryBrowser v-else-if="tab === 'memory'" />

          <!-- 知识库 -->
          <KnowledgeBase v-else-if="tab === 'knowledge'" />
        </div>
      </aside>
    </transition>
  </Teleport>
</template>

<style scoped>
.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 0.25s ease;
}
.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}
</style>
