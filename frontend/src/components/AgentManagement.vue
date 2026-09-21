/**
 * Agent 管理 — 全宽页面（路由 /agents）
 * 融合：技能(已装) · 工具 · MCP(已装+监控+安全) · 专家 · 记忆 · 知识库
 * 取代旧 ToolSkillDrawer 半边抽屉 + MCPCommandCenter 独立页。
 * 注：神魔架（请神/造神）归属神魔堂（/shenmotang），此处不再重复承载。
 */
<script setup>
import { ref, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import api from '../services/api.js'
import { toast } from '../utils/toast'
import { useBrickEvents } from '../utils/brick-events'
import { useChatStore } from '../stores/chat'
import SkillManager from './SkillManager.vue'
import MCPManager from './MCPManager.vue'
import MemoryBrowser from './MemoryBrowser.vue'
import KnowledgeBase from './KnowledgeBase.vue'
import ExpertCatalog from './ExpertCatalog.vue'
import StateBlock from './StateBlock.vue'

const router = useRouter()
const route = useRoute()
const chat = useChatStore()

// ── tab 管理（支持 URL query ?tab=xxx） ──
const tabs = [
  { id: 'skills', label: '技能', icon: '🧩' },
  { id: 'tools', label: '工具', icon: '🛠️' },
  { id: 'mcp', label: 'MCP', icon: '🔌' },
  { id: 'experts', label: '专家', icon: '🎓' },
  { id: 'memory', label: '记忆', icon: '🧠' },
  { id: 'knowledge', label: '知识库', icon: '📚' },
]

const activeTab = ref(route.query.tab || 'skills')

watch(activeTab, (t) => {
  router.replace({ path: '/agents', query: t !== 'skills' ? { tab: t } : {} })
})

function back() { router.push('/') }

// 进入页面收起左栏，离开恢复（与神魔堂一致逻辑）
const wasSidebarOpen = ref(true)
let stopRouteWatch = null
onMounted(() => {
  wasSidebarOpen.value = chat.sidebarOpen
  if (chat.sidebarOpen) chat.sidebarOpen = false
  stopRouteWatch = router.afterEach((to) => {
    if (to.path !== '/agents' && chat.sidebarOpen === false && wasSidebarOpen.value === true) {
      chat.sidebarOpen = true
    }
  })
})
onUnmounted(() => {
  if (stopRouteWatch) stopRouteWatch()
  if (chat.sidebarOpen === false && wasSidebarOpen.value === true) {
    chat.sidebarOpen = true
  }
})

// ── 工具集 ──
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

// 按需加载
watch(activeTab, (t) => {
  if (t === 'tools') loadToolsets()
})
onMounted(() => {
  if (activeTab.value === 'tools') loadToolsets()
})

// ── 动态刷新 ──
const { onEvent } = useBrickEvents()
onEvent('tool.registered', () => { if (activeTab.value === 'tools') loadToolsets() })
onEvent('tool.deregistered', () => { if (activeTab.value === 'tools') loadToolsets() })
onEvent('mcp.changed', () => { if (activeTab.value === 'tools') loadToolsets() })

// ── MCP 安全校验 ──
const securityRules = ref(null)
async function loadSecurity() {
  try {
    securityRules.value = await api.mcpSecurityRules()
  } catch {
    securityRules.value = null
  }
}
onMounted(() => { if (activeTab.value === 'mcp') loadSecurity() })
watch(activeTab, (t) => { if (t === 'mcp') loadSecurity() })
</script>

<template>
  <div class="h-full flex flex-col bg-gray-50 dark:bg-gray-950 text-gray-900 dark:text-gray-100">
    <!-- 顶部导航条 -->
    <header class="shrink-0 px-6 py-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
      <div class="flex items-center justify-between gap-4 flex-wrap">
        <div class="flex items-center gap-3">
          <button @click="back" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500 flex-shrink-0" title="返回单聊主界面">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
          </button>
          <h1 class="text-xl font-bold">🤖 Agent 管理</h1>
          <span class="text-sm text-gray-400">已安装 · 已接入 · 配置管理</span>
        </div>
        <div class="flex items-center gap-1 flex-wrap">
          <button
            v-for="t in tabs" :key="t.id"
            @click="activeTab = t.id"
            :class="activeTab === t.id
              ? 'bg-emerald-600 text-white'
              : 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700'"
            class="px-4 py-2 rounded-xl text-sm font-medium transition flex items-center gap-1.5"
          >
            <span>{{ t.icon }}</span><span>{{ t.label }}</span>
          </button>
        </div>
      </div>
    </header>

    <!-- 内容区：全宽，独立滚动 -->
    <div class="flex-1 overflow-y-auto">
      <div class="max-w-6xl mx-auto px-6 py-6">
        <!-- 技能：已装管理 + 发现市场（复用 SkillManager 全量组件） -->
        <SkillManager v-if="activeTab === 'skills'" />

        <!-- 工具：工具集总览与启停 -->
        <div v-else-if="activeTab === 'tools'" class="space-y-3">
          <div class="mb-2">
            <h2 class="text-lg font-semibold">🛠️ 工具集</h2>
            <p class="text-sm text-gray-400 mt-0.5">启停各工具集，控制 Agent 可调用的能力范围</p>
          </div>
          <div v-if="toolsLoading" class="text-center py-16 text-gray-400 animate-pulse">加载工具集中…</div>
          <StateBlock v-else-if="toolsets.length === 0" state="empty" icon="🛠️" text="暂无工具集" />
          <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div
              v-for="ts in toolsets" :key="ts.name"
              class="flex items-start gap-3 px-4 py-4 rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 hover:shadow-sm transition"
            >
              <span class="text-lg mt-0.5">{{ ts.enabled ? '✅' : '⬜' }}</span>
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium">{{ ts.label || ts.name }}</span>
                  <span v-if="ts.configured === false" class="text-[10px] px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/40 text-orange-500">未配置</span>
                </div>
                <div class="text-xs text-gray-400 mt-0.5">{{ ts.description || ts.name }}</div>
                <div v-if="ts.tools && ts.tools.length" class="flex flex-wrap gap-1 mt-2">
                  <span
                    v-for="tool in ts.tools" :key="tool"
                    class="text-[10px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-500 rounded truncate max-w-[140px]"
                    :title="tool"
                  >{{ tool }}</span>
                </div>
              </div>
              <button
                @click="toggleToolset(ts.name, !ts.enabled)"
                class="relative inline-flex h-6 w-11 items-center rounded-full transition-colors flex-shrink-0 mt-1"
                :class="ts.enabled ? 'bg-emerald-500' : 'bg-gray-300 dark:bg-gray-600'"
                :title="ts.enabled ? '已开启，点击关闭' : '已关闭，点击开启'"
              >
                <span class="inline-block h-5 w-5 transform rounded-full bg-white transition-transform" :class="ts.enabled ? 'translate-x-5' : 'translate-x-0.5'"></span>
              </button>
            </div>
          </div>
        </div>

        <!-- MCP：已装 server + 调用监控 + 安全校验 -->
        <div v-else-if="activeTab === 'mcp'" class="space-y-6">
          <!-- MCP 服务管理（复用 MCPManager 组件） -->
          <div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5">
            <MCPManager />
          </div>

          <!-- 安全校验 -->
          <div class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 space-y-3">
            <div class="flex items-center gap-2">
              <h2 class="text-base font-semibold">🔒 安全校验</h2>
              <button @click="loadSecurity" class="text-xs px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200">⟳</button>
            </div>
            <p class="text-sm text-gray-400">所有 MCP server 在保存和启动时都会经过安全校验，拦截已知的后门/外泄/持久化攻击模式。</p>
            <StateBlock v-if="!securityRules" state="empty" compact text="暂无安全规则数据" />
            <div v-else class="space-y-2">
              <div
                v-for="rule in (securityRules.checks || [])"
                :key="rule.id"
                class="flex items-start gap-2 text-sm text-gray-600 dark:text-gray-300"
              >
                <span class="text-emerald-500 mt-0.5">✓</span>
                <div>
                  <div class="font-medium">{{ rule.name || rule.id }}</div>
                  <div class="text-xs text-gray-400">{{ rule.description || '' }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 专家 -->
        <div v-else-if="activeTab === 'experts'">
          <ExpertCatalog />
        </div>

        <!-- 记忆 -->
        <div v-else-if="activeTab === 'memory'">
          <MemoryBrowser />
        </div>

        <!-- 知识库 -->
        <div v-else-if="activeTab === 'knowledge'">
          <KnowledgeBase />
        </div>
      </div>
    </div>
  </div>
</template>
