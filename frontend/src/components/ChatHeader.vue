<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useChatStore } from '../stores/chat'
import { useArtifactPanel } from '../composables/useArtifactPanel'
import HelpGuide from './HelpGuide.vue'

// ── 核心 store（必须在所有引用它的函数/computed 之前初始化） ──
const chat = useChatStore()
const { open: artifactOpen, togglePanel: toggleArtifactPanel } = useArtifactPanel()

// ── 进化指示器 ──
const evoStatus = ref(null)
async function fetchEvoStatus() {
  try {
    const r = await fetch('/api/evolution/status')
    if (r.ok) evoStatus.value = await r.json()
  } catch { /* 静默 */ }
}
onMounted(() => { fetchEvoStatus(); setInterval(fetchEvoStatus, 60000) })

// ── P0: Memory 指示器 ──
const memStatus = ref(null)
const showMemoryDetail = ref(false)

const memoryBlockNames = {
  handoff: { label: '上次会话', icon: '📋', color: 'text-blue-500' },
  evolution: { label: '经验记忆', icon: '🌱', color: 'text-green-500' },
  recall: { label: '相关召回', icon: '🔍', color: 'text-purple-500' },
  decisions: { label: '有效决策', icon: '⚡', color: 'text-amber-500' },
}

const memoryBlocksList = computed(() => {
  if (!memStatus.value?.blocks) return []
  return Object.entries(memStatus.value.blocks).map(([key, info]) => ({
    key,
    ...info,
    ...memoryBlockNames[key] || { label: key, icon: '❓', color: 'text-gray-400' },
  }))
})

async function fetchMemStatus() {
  try {
    const r = await fetch(`/api/memory/status${chat.currentSessionId ? '?session_id=' + chat.currentSessionId : ''}`)
    if (r.ok) memStatus.value = await r.json()
  } catch { /* 静默 */ }
}

// 首条消息后刷新 memory 状态（memory 在 turn 1 加载）
function refreshMemoryAfterFirstMessage() {
  if ((chat.filteredMessages?.length ?? 0) === 1) {
    setTimeout(fetchMemStatus, 500)
  }
}

watch(() => chat.filteredMessages?.length, refreshMemoryAfterFirstMessage)

onMounted(() => { 
  fetchMemStatus()
  // 每 30s 刷新一次（轻量接口）
  setInterval(fetchMemStatus, 30000) 
})

// 默认模型（未同步 provider 时的回退列表）
const defaultModels = [
  { id: 'agnes-2.0-flash', name: '✨ Agnes 2.0 Flash（免费）', provider: 'agnes' },
]

const showHelp = ref(false)

const props = defineProps({
  isLoggedIn: Boolean,
  userAvatar: String,
  userName: String,
  quotaDisplay: Object,
})

const emit = defineEmits(['logout', 'openWeChatQR', 'toggleHistory'])

const showModelSelect = ref(false)
const showStats = ref(false)
const sessionStats = computed(() => chat.getSessionStats(chat.currentSessionId))
const modelSearch = ref('')

// 模型列表刷新触发器 — Settings 保存/同步后递增，强制 computed 重算
const _providersVersion = ref(0)
function _onProvidersUpdated() { _providersVersion.value++ }
onMounted(() => window.addEventListener('providers-updated', _onProvidersUpdated))
onUnmounted(() => window.removeEventListener('providers-updated', _onProvidersUpdated))

// 最近使用的模型（最多3个）
const recentModels = computed(() => {
  try {
    const saved = localStorage.getItem('vermes-recent-models')
    if (saved) {
      const ids = JSON.parse(saved)
      return ids.map(id => models.value.find(m => m.id === id)).filter(Boolean)
    }
  } catch(e) {}
  return []
})

function addToRecent(modelId) {
  try {
    let recent = JSON.parse(localStorage.getItem('vermes-recent-models') || '[]')
    recent = recent.filter(id => id !== modelId)
    recent.unshift(modelId)
    recent = recent.slice(0, 3)
    localStorage.setItem('vermes-recent-models', JSON.stringify(recent))
  } catch(e) {}
}

// 模型列表

const models = computed(() => {
  _providersVersion.value  // 依赖触发器
  try {
    const saved = localStorage.getItem('vermes-providers')
    if (saved) {
      const providers = JSON.parse(saved)
      const synced = []
      for (const p of providers) {
        if (p.models && p.models.length > 0) {
          for (const m of p.models) {
            synced.push({ id: m, name: m, provider: p.id, group: p.name })
          }
        }
      }
      if (synced.length > 0) return synced
    }
  } catch(e) {}
  return defaultModels
})

const modelGroups = computed(() => {
  const groups = {}
  const search = modelSearch.value.toLowerCase().trim()
  for (const m of models.value) {
    // 搜索过滤
    if (search && !m.name.toLowerCase().includes(search) && !m.id.toLowerCase().includes(search)) continue
    const g = m.group || m.provider || '其他'
    if (!groups[g]) groups[g] = []
    groups[g].push(m)
  }
  return groups
})

function selectModel(m, event) {
  // P3-8: Shift/Cmd+click → 多选对比模式
  if (event && (event.shiftKey || event.metaKey || event.ctrlKey)) {
    const idx = chat.compareModels.findIndex(cm => cm.id === m.id)
    if (idx >= 0) {
      chat.compareModels.splice(idx, 1)
    } else {
      chat.compareModels.push({ id: m.id, provider: m.provider || m.group || '', name: m.name })
    }
    return  // 不关闭下拉、不改变主模型
  }
  // 普通点击 → 单选模式
  const oldModel = chat.currentModel
  const newModel = m.id
  const newProvider = m.provider || m.group || ''
  const sessionId = chat.currentSessionId

  // nextTurnSnapshot: 如果当前会话正在 streaming，先存到 pendingModel
  if (sessionId && chat.loading) {
    chat.pendingModel = { model: newModel, provider: newProvider, sessionId }
    showModelSelect.value = false
    modelSearch.value = ''
    return
  }

  // 立即生效
  chat.currentModel = newModel
  chat.currentProvider = newProvider
  const session = chat.sessions.find(s => s.id === sessionId)
  if (session) {
    session.model = newModel
    session.provider = newProvider
    chat.persistSessions()
  }
  // appendModelChange: 记录模型变更到消息流
  chat.appendModelChange(sessionId, oldModel, newModel)
  addToRecent(newModel)
  try { localStorage.setItem('vermes-current-model', newModel) } catch(e) { /* storage full */ }
  try { localStorage.setItem('vermes-current-provider', newProvider) } catch(e) { /* storage full */ }
  chat.compareModels = []
  showModelSelect.value = false
  modelSearch.value = ''
}

function isModelSelected(m) {
  return chat.compareModels.some(cm => cm.id === m.id) || m.id === chat.currentModel
}

function currentModelName() {
  const m = models.value.find(m => m.id === chat.currentModel)
  return m ? m.name : chat.currentModel
}

// ── 顶部长任务步骤条（常驻，避免只在抽屉里）──
const taskStats = computed(() => {
  const items = chat.todoItems || []
  return {
    total: items.length,
    completed: items.filter(i => i.status === 'completed').length,
    inProgress: items.filter(i => i.status === 'in_progress').length,
  }
})
const taskProgress = computed(() => {
  if (!taskStats.value.total) return 0
  return Math.round((taskStats.value.completed / taskStats.value.total) * 100)
})
const currentStepLabel = computed(() => {
  const items = chat.todoItems || []
  const cur = items.find(i => i.id === chat.currentTodoStepId)
    || items.find(i => i.status === 'in_progress')
  if (cur) return cur.content || '进行中…'
  if (taskStats.value.total && taskStats.value.completed === taskStats.value.total) return '全部完成 🎉'
  return '任务进行中…'
})

function closeDropdowns() {
  showModelSelect.value = false
  showStats.value = false
  showMemoryDetail.value = false
}
</script>

<template>
  <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
    <div class="flex items-center gap-3">
      <!-- 微信头像 -->
      <div v-if="isLoggedIn && userAvatar" class="flex items-center gap-2 cursor-pointer group relative">
        <img :src="userAvatar" class="w-8 h-8 rounded-full object-cover ring-2 ring-green-400 shadow-sm" @error="$event.target.style.display='none'" />
        <div class="flex flex-col leading-tight">
          <span class="text-xs font-medium text-gray-700 dark:text-gray-200 group-hover:text-green-600 dark:group-hover:text-green-400 transition max-w-[80px] truncate">{{ userName }}</span>
          <span class="text-[10px] text-green-500">已登录</span>
        </div>
        <button @click="emit('logout')" class="ml-1 text-[10px] text-red-400 hover:text-red-600 transition opacity-0 group-hover:opacity-100" title="退出登录">退出</button>
      </div>
      <div v-else-if="isLoggedIn" class="flex items-center gap-1.5 px-2 py-1 bg-green-50 dark:bg-green-900/30 rounded-full text-xs text-green-600 dark:text-green-400">
        <span class="w-6 h-6 rounded-full bg-green-400 flex items-center justify-center text-white text-xs font-bold">V</span>
        {{ userName }}
        <button @click="emit('logout')" class="ml-1 text-[10px] text-red-400 hover:text-red-600 transition" title="退出登录">退出</button>
      </div>
      <div v-else @click="emit('openWeChatQR')" class="flex items-center gap-2 cursor-pointer group">
        <div class="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center text-gray-400 text-xs">?</div>
        <div class="flex flex-col leading-tight">
          <span class="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300 transition">微信未登录</span>
          <span class="text-[10px] text-gray-300 dark:text-gray-600">点击登录</span>
        </div>
      </div>

      <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

      <button @click="chat.toggleSidebar()" class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition" title="切换侧边栏">☰<span class="header-tooltip group-hover:opacity-100">切换侧边栏</span></button>
      <h2 class="font-semibold text-gray-800 dark:text-gray-200">{{ chat.currentSession?.name || '新 Agent' }}</h2>
      <span @click="showStats = !showStats" class="text-xs text-gray-400 cursor-pointer hover:text-gray-600 dark:hover:text-gray-300 transition">{{ chat.filteredMessages?.length ?? 0 }} 条消息</span>
      <button @click="emit('toggleHistory')" class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-sm" title="历史记录">📋<span class="header-tooltip group-hover:opacity-100">历史记录</span></button>

      <!-- 顶部长任务步骤条（常驻，避免只在抽屉里；点击展开详情） -->
      <div v-if="chat.todoItems.length"
           @click="chat.toggleTaskDrawer()"
           class="hidden md:flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition cursor-pointer max-w-[300px]"
           :title="`任务进度：${taskStats.completed}/${taskStats.total} 步已完成`">
        <span class="text-xs flex-shrink-0" :class="{ 'animate-spin': taskStats.inProgress }">🔄</span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between gap-2 text-[10px] text-gray-400">
            <span class="truncate max-w-[150px]">{{ currentStepLabel }}</span>
            <span class="flex-shrink-0 tabular-nums">{{ taskStats.completed }}/{{ taskStats.total }}</span>
          </div>
          <div class="mt-0.5 w-full h-1 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
            <div class="h-full bg-blue-500 transition-all duration-500"
                 :style="{ width: taskProgress + '%' }"></div>
          </div>
        </div>
      </div>
      <!-- 任务清单（长任务分步骤 + 实时进度） -->
      <button @click="chat.toggleTaskDrawer()"
              class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-sm"
              title="任务清单">
        🗂️
        <span class="header-tooltip group-hover:opacity-100">任务清单</span>
        <span v-if="chat.todoItems.length"
              class="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-blue-500 text-white text-[10px] leading-4 text-center"
              :class="{ 'bg-green-500': chat.todoAllDone }">
          {{ chat.todoAllDone ? '✓' : (chat.todoInProgressCount || chat.todoItems.length) }}
        </span>
      </button>
      <!-- 消息搜索 -->
      <div v-if="chat.searchMode" class="flex items-center gap-1">
        <input v-model="chat.searchQuery" 
               placeholder="搜索消息…" 
               class="px-2 py-1 text-xs bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-1 focus:ring-green-500 text-gray-800 dark:text-gray-200 placeholder-gray-400 w-40"
               @keydown.escape="chat.searchMode = false; chat.searchQuery = ''" />
        <button @click="chat.searchMode = false; chat.searchQuery = ''" class="text-gray-400 hover:text-gray-600 text-xs">✕</button>
      </div>
      <button v-else @click="chat.searchMode = true" class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-sm" title="搜索消息 (⌘⇧S)">🔍<span class="header-tooltip group-hover:opacity-100">搜索消息 ⌘⇧S</span></button>
      <!-- 进化指示器 -->
      <div v-if="evoStatus?.active" @click="chat.toggleSidebar()" 
           class="group relative flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition"
           :title="`进化系统: ${evoStatus.total_outcomes} 次调用, 成功率 ${evoStatus.success_rate}%`">
        <span class="text-xs">🧠</span>
        <span class="text-[10px] font-mono" :class="evoStatus.success_rate >= 80 ? 'text-green-500' : 'text-yellow-500'">{{ evoStatus.success_rate }}%</span>
        <span class="header-tooltip group-hover:opacity-100">进化系统 · {{ evoStatus.total_outcomes }} 次调用 · 成功率 {{ evoStatus.success_rate }}%</span>
      </div>
      <!-- Memory 指示器 -->
      <div v-if="memStatus?.active && memoryBlocksList.length > 0" 
           @click="showMemoryDetail = !showMemoryDetail"
           class="group relative flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition"
           :title="`已加载 ${memoryBlocksList.length} 个记忆块 (~${memStatus.total_tokens_est} tokens)`">
        <span class="text-xs">📚</span>
        <span class="text-[10px] font-mono text-green-500">{{ memoryBlocksList.length }}</span>
        <span class="header-tooltip group-hover:opacity-100">已加载 {{ memoryBlocksList.length }} 个记忆块</span>
      </div>
      <button @click="showHelp = true" class="group relative p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-sm" title="使用帮助">❓<span class="header-tooltip group-hover:opacity-100">使用帮助</span></button>
      <span v-if="quotaDisplay" class="text-xs px-2 py-0.5 rounded-full"
        :class="quotaDisplay.remaining <= 10 ? 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'">
        {{ quotaDisplay.text }}
      </span>
      <!-- 产物详情面板开关：极简 hover 提示，无常驻文字 -->
      <button
        @click="toggleArtifactPanel('artifacts')"
        :class="artifactOpen ? 'bg-green-500 text-white hover:bg-green-600' : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'"
        class="group relative ml-1 px-2 py-1.5 rounded-lg text-xs font-medium transition flex items-center gap-1"
        :title="artifactOpen ? '关闭右栏' : '展开右栏'"
      >
        <span>📄</span>
        <span class="header-tooltip group-hover:opacity-100">{{ artifactOpen ? '关闭右栏' : '展开右栏' }}</span>
      </button>
    </div>

    <!-- 模型选择器 -->
    <div class="relative">
      <button @click.stop="showModelSelect = !showModelSelect"
        class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600 transition flex items-center gap-1.5">
        <span class="w-2 h-2 rounded-full bg-green-500"></span>
        {{ currentModelName() }}
        <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
      </button>
      <div v-if="showModelSelect" class="absolute right-0 top-full mt-1 w-72 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl z-50 max-h-96 overflow-hidden py-1">
        <!-- 搜索框 -->
        <div class="px-3 py-2 border-b border-gray-100 dark:border-gray-700">
          <input v-model="modelSearch" 
                 placeholder="搜索模型…" 
                 class="w-full px-3 py-1.5 text-sm bg-gray-50 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-green-500 text-gray-800 dark:text-gray-200 placeholder-gray-400"
                 @click.stop />
        </div>
        <div class="overflow-y-auto max-h-72">
          <!-- 最近使用 -->
          <div v-if="recentModels.length > 0 && !modelSearch.trim()" class="mb-1">
            <div class="px-3 py-1.5 text-xs font-semibold text-green-600 dark:text-green-400 flex items-center gap-1">
              <span>🕐</span> 最近使用
            </div>
            <div v-for="m in recentModels" :key="'recent-'+m.id" @click="selectModel(m, $event)"
              class="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between"
              :class="{ 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400': isModelSelected(m) }">
              <span>{{ m.name }}</span>
              <span v-if="isModelSelected(m)" class="text-green-500 text-xs">✓</span>
            </div>
            <div class="border-b border-gray-100 dark:border-gray-700 my-1"></div>
          </div>
          <!-- 提示 -->
          <div class="px-3 py-1.5 text-[10px] text-gray-400 border-b border-gray-100 dark:border-gray-700">
            💡 <kbd class="px-1 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-[9px]">Shift</kbd>+点击可多选对比
          </div>
          <!-- 分组模型列表 -->
          <template v-for="(group, gName) in modelGroups" :key="gName">
            <div class="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ gName }}</div>
            <div v-for="m in group" :key="m.id" @click="selectModel(m, $event)"
              class="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between"
              :class="{ 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400': isModelSelected(m) }">
              <span>{{ m.name }}</span>
              <span v-if="isModelSelected(m)" class="text-green-500 text-xs">✓</span>
            </div>
          </template>
          <!-- 无结果 -->
          <div v-if="Object.keys(modelGroups).length === 0" class="px-3 py-6 text-center text-sm text-gray-400">
            未找到匹配的模型
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 点击外部关闭下拉 -->
  <div v-if="showModelSelect || showStats" @click="closeDropdowns" class="fixed inset-0 z-40"></div>

  <!-- 统计弹窗 -->
  <div v-if="showStats" class="absolute right-60 top-14 z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl p-4 min-w-[200px]">
    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-3 uppercase tracking-wider">📊 会话统计</div>
    <div class="space-y-2 text-sm">
      <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">消息总数</span><span class="text-gray-800 dark:text-gray-200 font-medium">{{ sessionStats.count }}</span></div>
      <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">会话时长</span><span class="text-gray-800 dark:text-gray-200 font-medium">{{ sessionStats.duration }}</span></div>
      <div class="flex justify-between"><span class="text-gray-500 dark:text-gray-400">当前模型</span><span class="text-gray-800 dark:text-gray-200 font-medium">{{ sessionStats.model }}</span></div>
    </div>
  </div>

  <!-- 使用帮助弹窗 -->
  <HelpGuide v-if="showHelp" @close="showHelp = false" />

  <!-- Memory 详情弹窗 -->
  <div v-if="showMemoryDetail && memStatus?.active" 
       class="absolute left-20 top-14 z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl p-4 min-w-[320px] max-w-[400px]">
    <div class="flex items-center justify-between mb-3">
      <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">📚 跨会话记忆</div>
      <button @click="showMemoryDetail = false" class="text-gray-400 hover:text-gray-600 text-xs">✕</button>
    </div>
    <!-- 总览 -->
    <div class="flex items-center gap-3 mb-3 pb-3 border-b border-gray-100 dark:border-gray-700">
      <div class="flex-1">
        <div class="text-sm text-gray-800 dark:text-gray-200">{{ memoryBlocksList.length }} 个记忆块已加载</div>
        <div class="text-[10px] text-gray-400 mt-0.5">约 {{ memStatus.total_tokens_est }} tokens / 预算 {{ Math.round(memStatus.budget_limit / 4) }} tokens</div>
      </div>
      <!-- 预算条 -->
      <div class="w-20">
        <div class="h-1.5 bg-gray-200 dark:bg-gray-600 rounded-full overflow-hidden">
          <div class="h-full bg-green-500 rounded-full transition-all" 
               :style="{ width: Math.min(100, (memStatus.total_chars / memStatus.budget_limit) * 100) + '%' }"></div>
        </div>
        <div class="text-[9px] text-gray-400 text-center mt-0.5">{{ Math.round((memStatus.total_chars / memStatus.budget_limit) * 100) }}%</div>
      </div>
    </div>
    <!-- 各记忆块 -->
    <div class="space-y-2 max-h-64 overflow-y-auto">
      <div v-for="block in memoryBlocksList" :key="block.key" 
           class="p-2 rounded-lg bg-gray-50 dark:bg-gray-700/50">
        <div class="flex items-center justify-between mb-1">
          <div class="flex items-center gap-1.5">
            <span class="text-sm">{{ block.icon }}</span>
            <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ block.label }}</span>
          </div>
          <span class="text-[10px] text-gray-400 font-mono">{{ block.chars }} 字符</span>
        </div>
        <div class="text-[11px] text-gray-500 dark:text-gray-400 leading-relaxed line-clamp-3">{{ block.preview }}</div>
      </div>
    </div>
    <!-- handoff 来源 -->
    <div v-if="memStatus.handoff_source" class="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
      <div class="text-[10px] text-gray-400">来源会话: {{ memStatus.handoff_source.session_id?.slice(0, 12) }} · {{ memStatus.handoff_source.age_hours }} 小时前</div>
    </div>
  </div>
</template>
