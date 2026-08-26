<script setup>
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useChatStore, SESSION_TEMPLATES } from '../stores/chat'
import { useRouter, useRoute } from 'vue-router'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
const { confirm } = useConfirm()
import { loadMessagesFromIDB } from '../stores/chat-storage'
import api from '../services/api'
import EvolutionPanel from './EvolutionPanel.vue'
import KnowledgeBase from './KnowledgeBase.vue'

// ExpertCatalog 已迁至 ToolSkillDrawer 专家 tab
import { useRightPanel } from '../composables/useRightPanel'
// 生态模块前端动态加载已弃用，改为 Agent 工具集模式

const chat = useChatStore()
const router = useRouter()
const route = useRoute()
const { openPanel } = useRightPanel()

function goSettings() { router.push('/settings') }
function goMobileConnect() { router.push('/settings?tab=channels') }
function goStudio() { router.push('/studio') }
function goScholarForge() { router.push('/scholarforge') }
function go3DStudio() { router.push('/3d-studio') }
function goSkillMarket() { router.push('/skill-market') }
function goKanban() { router.push('/kanban') }
// goModuleStore 已移除（模块商店归入 Agent 管理→软件 tab）

// 点击会话项：切换会话 + 如果不在聊天页则跳回
function switchAndGoChat(id) {
  chat.switchSession(id)
  if (route.path !== '/') router.push('/')
}

// ScholarForge: 22 个 Agent 工具（对话中自动调用）；另提供专用面板入口 /scholarforge
// （前端/src 新建，复用主框架 Vue+Pinia+Tailwind，A 入口与对话式 C 并存）

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

// ── 搜索 ──
const searchQuery = ref('')

// ── 日期分组 ──
const DATE_LABELS = ['今天', '昨天', '本周', '本月', '更早']

function getDateGroup(ts) {
  if (!ts) return '更早'
  const d = new Date(ts)
  const now = new Date()
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const startOfYesterday = new Date(startOfToday)
  startOfYesterday.setDate(startOfYesterday.getDate() - 1)
  const startOfWeek = new Date(startOfToday)
  startOfWeek.setDate(startOfWeek.getDate() - startOfWeek.getDay())
  const startOfMonth = new Date(now.getFullYear(), now.getMonth(), 1)

  if (d >= startOfToday) return '今天'
  if (d >= startOfYesterday) return '昨天'
  if (d >= startOfWeek) return '本周'
  if (d >= startOfMonth) return '本月'
  return '更早'
}

// 本地 web 会话 + state.db 渠道会话（统一视图，双源合并，按 id 去重、本地优先）
const mergedSessions = computed(() => {
  const localIds = new Set(chat.sessions.map(s => s.id))
  return [...chat.sessions, ...chat.channelSessions.filter(s => !localIds.has(s.id))]
})

// 按搜索过滤后的会话列表
const filteredSessions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return mergedSessions.value
  return mergedSessions.value.filter(s =>
    (s.name || '新 Agent').toLowerCase().includes(q) || (s.source || '').toLowerCase().includes(q)
  )
})

// 渠道来源徽标
const SOURCE_ICONS = {
  telegram: '✈️', discord: '🎮', slack: '💼', whatsapp: '💬',
  cli: '⌨️', email: '📧', matrix: '🔷', signal: '🔵', wechat: '🟢',
}
function sourceBadge(s) {
  if (!s || !s.channel) return ''
  return `${SOURCE_ICONS[s.source] || '📡'} ${s.source}`
}

// 置顶 + 分组
const groupedSessions = computed(() => {
  const list = filteredSessions.value
  const pinned = list.filter(s => s.pinned).sort((a, b) => new Date(b.lastActive || b.createdAt) - new Date(a.lastActive || a.createdAt))
  const unpinned = list.filter(s => !s.pinned)

  const groups = {}
  for (const label of DATE_LABELS) groups[label] = []

  for (const s of unpinned) {
    const g = getDateGroup(s.lastActive || s.createdAt)
    groups[g].push(s)
  }
  // 每组内部按 lastActive/createdAt 降序
  for (const label of DATE_LABELS) {
    groups[label].sort((a, b) => new Date(b.lastActive || b.createdAt) - new Date(a.lastActive || a.createdAt))
  }

  // 按固定顺序返回带标签的组
  const result = []
  for (const label of DATE_LABELS) {
    if (groups[label].length > 0) {
      result.push({ type: 'header', label, key: `h-${label}` })
      for (const s of groups[label]) result.push({ type: 'session', data: s, key: s.id })
    }
  }
  return { pinned, items: result }
})

// ── 重命名 ──
const renamingId = ref(null)
const renameInput = ref('')
const renameRef = ref(null)

function startRename(s) {
  renamingId.value = s.id
  renameInput.value = s.name || '新 Agent'
  closeContextMenu()
  nextTick(() => {
    const el = document.querySelector('.rename-input')
    if (el) { el.focus(); el.select() }
  })
}

function confirmRename() {
  if (renamingId.value && renameInput.value.trim()) {
    chat.renameSession(renamingId.value, renameInput.value.trim())
  }
  renamingId.value = null
}

function cancelRename() {
  renamingId.value = null
}

// ── 置顶 ──
function togglePin(s) {
  chat.pinSession(s.id, !s.pinned)
  closeContextMenu()
}

// ── 消息数 & 首条消息缓存（IndexedDB 异步加载） ──
const sessionMeta = ref(new Map())  // sessionId → { count, firstMsg }

async function loadSessionMeta(sessionId) {
  try {
    const msgs = await loadMessagesFromIDB(sessionId)
    if (msgs && msgs.length > 0) {
      const userMsg = msgs.find(m => m.role === 'user')
      const text = userMsg ? userMsg.content.replace(/!\[[^\]]*\]\([^)]+\)/g, '🖼️图片').replace(/📎[^\n]*/g, '📎附件') : ''
      sessionMeta.value.set(sessionId, {
        count: msgs.length,
        firstMsg: text.length > 40 ? text.slice(0, 40) + '...' : text
      })
      return
    }
  } catch {}
  // 降级 localStorage
  try {
    const msgs = JSON.parse(localStorage.getItem('vermes-msgs-' + sessionId)) || []
    const userMsg = msgs.find(m => m.role === 'user')
    const text = userMsg ? userMsg.content.replace(/!\[[^\]]*\]\([^)]+\)/g, '🖼️图片').replace(/📎[^\n]*/g, '📎附件') : ''
    sessionMeta.value.set(sessionId, {
      count: msgs.length,
      firstMsg: text.length > 40 ? text.slice(0, 40) + '...' : text
    })
  } catch {
    sessionMeta.value.set(sessionId, { count: 0, firstMsg: '' })
  }
}

// 批量加载所有会话元数据
async function loadAllSessionMeta() {
  const promises = chat.sessions.map(s => loadSessionMeta(s.id))
  await Promise.all(promises)
}

function getMessageCount(sessionId) {
  // 渠道会话：直接用 state.db 返回的 message_count
  const ch = chat.channelSessions.find(s => s.id === sessionId)
  if (ch) return ch.messageCount || 0
  const meta = sessionMeta.value.get(sessionId)
  if (meta) return meta.count
  // 同步降级（旧数据）
  return chat.getMessageCount(sessionId)
}

// ── 任务状态 chip：返回会话的 todo 总数 / 已完成数 ──
function getTodoCount(sessionId) {
  const items = chat.sessionTodoItems?.[sessionId]
  return items ? items.length : 0
}
function getTodoCompleted(sessionId) {
  const items = chat.sessionTodoItems?.[sessionId]
  if (!items) return 0
  return items.filter(i => i.status === 'completed' || i.status === 'cancelled').length
}

function getFirstMessagePreview(sessionId) {
  // 渠道会话：直接用 state.db 返回的 preview
  const ch = chat.channelSessions.find(s => s.id === sessionId)
  if (ch) return ch.preview || ''
  const meta = sessionMeta.value.get(sessionId)
  if (meta) return meta.firstMsg
  // 同步降级
  return chat.getFirstMessage(sessionId)
}

// 会话变化时刷新元数据
watch(() => chat.sessions.length, async () => {
  await loadAllSessionMeta()
})

// 组件挂载时加载元数据 + 刷新渠道会话列表 + 启动全渠道实时同步
onMounted(async () => {
  await loadAllSessionMeta()
  chat.loadChannelSessions().catch(() => {})
  chat.initChannelSync()
})

// ── 渠道会话定时轮询（每 5 秒刷新会话列表，发现新消息/新会话）──
let _channelPollTimer = null
onMounted(() => {
  _channelPollTimer = setInterval(() => {
    chat.loadChannelSessions().catch(() => {})
  }, 5000)
})
onUnmounted(() => {
  if (_channelPollTimer) { clearInterval(_channelPollTimer); _channelPollTimer = null }
})

// ── 右键菜单 ──
const contextMenu = ref({ show: false, x: 0, y: 0, session: null })

function onContextMenu(e, s) {
  e.preventDefault()
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, session: s }
}

function closeContextMenu() { contextMenu.value.show = false }

async function handleDelete(id) {
  if (await confirm({ title: '删除会话', message: '确定删除此会话？', confirmText: '删除', danger: true })) chat.deleteSession(id)
  closeContextMenu()
}

// ── 模板选择 ──
const showTemplateMenu = ref(false)
const showCustomPrompt = ref(false)
const showKnowledgeBase = ref(false)  // 已迁至 Agent 管理面板
// showExpert 已移除（专家面板迁至 ToolSkillDrawer）
const customPromptInput = ref('')
const customPromptRef = ref(null)

function selectTemplate(tpl) {
  if (tpl.id === 'custom') {
    // 自定义模板：弹出输入框
    showTemplateMenu.value = false
    customPromptInput.value = ''
    showCustomPrompt.value = true
    nextTick(() => customPromptRef.value?.focus())
    return
  }
  chat.createSession(tpl.name, tpl)
  showTemplateMenu.value = false
}

function confirmCustomPrompt() {
  const prompt = customPromptInput.value.trim()
  if (!prompt) {
    // 空的就当空白会话
    chat.createSession('新 Agent', SESSION_TEMPLATES[0])
  } else {
    chat.createSession('自定义', { id: 'custom', name: '自定义', icon: '⚙️', systemPrompt: prompt })
  }
  showCustomPrompt.value = false
}

function cancelCustomPrompt() {
  showCustomPrompt.value = false
}

// ── 导出 ──
async function handleExport(id, format) {
  // md/html 走后端导出（含 tool_calls/完整消息），json 走前端本地
  if (format === 'markdown' || format === 'html') {
    try {
      const fmt = format === 'html' ? 'html' : 'md'
      const res = await api.exportSession(id, fmt)
      const blob = await res.blob()
      const session = chat.allSessions.find(s => s.id === id)
      const name = session?.name || id.slice(0, 8)
      const ext = fmt === 'html' ? 'html' : 'md'
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${name}.${ext}`
      a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      console.error('后端导出失败，回退前端:', e)
      chat.exportSession(id, 'md')
    }
  } else {
    chat.exportSession(id, format)
  }
  closeContextMenu()
}

// ── 导入 ──
const importInput = ref(null)

function triggerImport() {
  importInput.value?.click()
}

async function handleImportFile(e) {
  const file = e.target.files[0]
  if (!file) return
  const text = await file.text()
  const result = await chat.importSession(text)
  if (result.success) {
    toast.success(`导入成功：${result.name}`)
  } else {
    toast.error(`导入失败：${result.error}`)
  }
  e.target.value = ''
}
</script>

<template>
  <div
    class="bg-gray-50 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col transition-all duration-300"
    :class="chat.sidebarOpen ? 'w-64' : 'w-10'"
    @click.self="closeContextMenu()"
  >
    <!-- 收起状态：窄边栏 -->
    <template v-if="!chat.sidebarOpen">
      <div class="flex flex-col items-center py-3 gap-2">
        <button @click="chat.toggleSidebar()" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition" title="展开侧边栏">
          <div class="w-6 h-6 bg-green-500 rounded flex items-center justify-center text-white font-bold text-xs">V</div>
        </button>
        <button @click="chat.createSession('新 Agent')" class="p-1.5 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-lg transition text-sm" title="新 Agent">
          ➕
        </button>
      </div>
    </template>

    <!-- 展开状态：完整侧边栏 -->
    <template v-else>
      <!-- 顶部 Logo -->
      <div class="p-4 border-b border-gray-200 dark:border-gray-700 shrink-0">
        <div class="flex items-center gap-2">
          <div class="w-8 h-8 bg-green-500 rounded-lg flex items-center justify-center text-white font-bold text-sm">V</div>
          <span class="font-semibold text-gray-800 dark:text-gray-200">Vermes</span>
        </div>
      </div>

      <!-- 新 Agent按钮 + 模板选择 -->
      <div class="p-3 shrink-0 relative">
        <button
          @click="showTemplateMenu = !showTemplateMenu"
          class="w-full px-4 py-2 bg-green-500 hover:bg-green-600 text-white rounded-lg text-sm font-medium transition"
        >＋ 新 Agent</button>
        <div v-if="showTemplateMenu" class="absolute left-3 right-3 mt-1 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl z-50 py-1">
          <div v-for="tpl in SESSION_TEMPLATES" :key="tpl.id"
            @click="selectTemplate(tpl)"
            class="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2">
            <span>{{ tpl.icon }}</span>
            <span class="text-gray-700 dark:text-gray-300">{{ tpl.name }}</span>
          </div>
        </div>
      </div>
      <!-- 点击外部关闭模板菜单 -->
      <div v-if="showTemplateMenu" @click="showTemplateMenu = false" class="fixed inset-0 z-40"></div>

      <!-- 自定义提示词输入弹窗 -->
      <Teleport to="body">
        <div v-if="showCustomPrompt" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/40" @click.self="cancelCustomPrompt">
          <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-[480px] max-w-[90vw] p-5 border border-gray-200 dark:border-gray-700">
            <h3 class="text-base font-semibold text-gray-800 dark:text-gray-200 mb-1">⚙️ 自定义系统提示词</h3>
            <p class="text-xs text-gray-400 mb-3">设定 AI 的角色、行为和规则，留空则创建空白会话</p>
            <textarea
              ref="customPromptRef"
              v-model="customPromptInput"
              rows="6"
              placeholder="例如：你是一位资深的产品经理，擅长需求分析和竞品调研。回答要结构化，使用表格对比..."
              class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-700 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-green-400 focus:border-green-400 resize-y transition"
              @keydown.meta.enter="confirmCustomPrompt"
              @keydown.ctrl.enter="confirmCustomPrompt"
              @keydown.escape="cancelCustomPrompt"
            ></textarea>
            <div class="flex justify-between items-center mt-3">
              <span class="text-[10px] text-gray-400">⌘/Ctrl+Enter 确认</span>
              <div class="flex gap-2">
                <button @click="cancelCustomPrompt" class="px-4 py-1.5 text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition">取消</button>
                <button @click="confirmCustomPrompt" class="px-4 py-1.5 text-sm bg-green-500 hover:bg-green-600 text-white rounded-lg font-medium transition">创建会话</button>
              </div>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- 导入按钮 -->
      <div class="px-3 pb-2 shrink-0">
        <input ref="importInput" type="file" accept=".json" class="hidden" @change="handleImportFile" />
        <button @click="triggerImport()" class="w-full px-3 py-1.5 text-xs border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:border-gray-400 dark:hover:border-gray-500 transition">
          📥 导入会话
        </button>
      </div>

      <!-- 搜索框 -->
      <div class="px-3 pb-2 shrink-0">
        <div class="relative">
          <span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 text-xs pointer-events-none">🔍</span>
          <input
            v-model="searchQuery"
            type="text"
            placeholder="搜索会话..."
            class="w-full pl-7 pr-7 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-green-400 focus:border-green-400 transition"
          />
          <button
            v-if="searchQuery"
            @click="searchQuery = ''"
            class="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-xs"
          >×</button>
        </div>
      </div>

      <!-- 会话列表 -->
      <div class="flex-1 overflow-y-auto" @click="closeContextMenu()">

        <!-- 置顶会话 -->
        <template v-if="groupedSessions.pinned.length > 0">
          <div class="px-4 pt-1 pb-0.5 text-[10px] font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider">📌 已置顶</div>
          <div
            v-for="s in groupedSessions.pinned" :key="'p-' + s.id"
            @click="switchAndGoChat(s.id)"
            @contextmenu.prevent="onContextMenu($event, s)"
            class="px-3 py-2 mx-2 mb-0.5 rounded-lg cursor-pointer text-sm transition-all duration-200 group relative"
            :class="s.id === chat.currentSessionId
              ? 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 shadow-sm'
              : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 hover:shadow-sm'"
            :style="s.id === chat.currentSessionId ? 'border-left: 3px solid #22c55e' : 'border-left: 3px solid transparent'"
          >
            <!-- 重命名模式 -->
            <div v-if="renamingId === s.id" @click.stop>
              <input
                v-model="renameInput"
                class="rename-input w-full text-sm bg-white dark:bg-gray-700 border border-green-400 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-green-400 text-gray-700 dark:text-gray-200"
                @keydown.enter="confirmRename"
                @keydown.escape="cancelRename"
                @blur="confirmRename"
              />
            </div>
            <template v-else>
              <div class="flex items-center gap-1">
                <span class="text-[10px] shrink-0">📌</span>
                <span class="truncate font-medium flex-1">{{ s.name || '新 Agent' }}</span>
                <span v-if="chat.sessionLoading[s.id]" class="shrink-0 w-2 h-2 rounded-full bg-green-500 animate-pulse" title="运行中"></span>
                <!-- 任务状态 chip：进行中 📋 N/M / 完成 ✅ -->
                <span v-if="getTodoCount(s.id) > 0" class="shrink-0 text-[10px] px-1 py-0.5 rounded font-medium" :class="chat.sessionTodoAllDone[s.id] ? 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/30' : 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/30'" :title="chat.sessionTodoAllDone[s.id] ? '任务已完成' : '任务进行中'">
                  {{ chat.sessionTodoAllDone[s.id] ? '✅' : '📋' }} {{ chat.sessionTodoAllDone[s.id] ? '' : getTodoCompleted(s.id) + '/' + getTodoCount(s.id) }}
                </span>
                <span v-if="getMessageCount(s.id) > 0" class="shrink-0 ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 dark:bg-green-800 text-green-600 dark:text-green-300 font-medium">{{ getMessageCount(s.id) }}</span>
              </div>
              <div class="text-xs text-gray-400 mt-0.5 truncate" v-if="getFirstMessagePreview(s.id)">{{ getFirstMessagePreview(s.id) }}</div>
              <div class="text-[10px] text-gray-400 mt-0.5 flex justify-between items-center">
                <span>{{ formatTime(s.lastActive || s.createdAt) }}</span>
                <button
                  @click.stop="handleDelete(s.id)"
                  class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600 transition ml-1"
                  title="删除会话"
                >×</button>
              </div>
            </template>
          </div>
        </template>

        <!-- 按日期分组的会话 -->
        <template v-for="item in groupedSessions.items" :key="item.key">
          <!-- 分组标题 -->
          <div v-if="item.type === 'header'" class="px-4 pt-3 pb-0.5 text-[10px] font-medium text-gray-400 dark:text-gray-500 uppercase tracking-wider">
            {{ item.label }}
          </div>
          <!-- 会话项 -->
          <div
            v-else-if="item.type === 'session'"
            @click="switchAndGoChat(item.data.id)"
            @contextmenu.prevent="onContextMenu($event, item.data)"
            class="px-3 py-2 mx-2 mb-0.5 rounded-lg cursor-pointer text-sm transition-all duration-200 group relative"
            :class="item.data.id === chat.currentSessionId
              ? 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200 shadow-sm'
              : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 hover:shadow-sm'"
            :style="item.data.id === chat.currentSessionId ? 'border-left: 3px solid #22c55e' : 'border-left: 3px solid transparent'"
          >
            <!-- 重命名模式 -->
            <div v-if="renamingId === item.data.id" @click.stop>
              <input
                v-model="renameInput"
                class="rename-input w-full text-sm bg-white dark:bg-gray-700 border border-green-400 rounded px-1.5 py-0.5 focus:outline-none focus:ring-1 focus:ring-green-400 text-gray-700 dark:text-gray-200"
                @keydown.enter="confirmRename"
                @keydown.escape="cancelRename"
                @blur="confirmRename"
              />
            </div>
            <template v-else>
              <div class="flex items-center gap-1">
                <span class="truncate font-medium flex-1">{{ item.data.name || '新 Agent' }}</span>
                <span v-if="item.data.channel" class="shrink-0 text-[9px] px-1 py-0.5 rounded bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-300" :title="'来自渠道: ' + item.data.source">{{ sourceBadge(item.data) }}</span>
                <span v-if="chat.channelUnread[item.data.id] > 0" class="shrink-0 min-w-[16px] h-4 px-1 flex items-center justify-center text-[10px] font-semibold rounded-full bg-red-500 text-white" :title="chat.channelUnread[item.data.id] + ' 条未读'">{{ chat.channelUnread[item.data.id] > 99 ? '99+' : chat.channelUnread[item.data.id] }}</span>
                <span v-if="chat.sessionLoading[item.data.id]" class="shrink-0 w-2 h-2 rounded-full bg-green-500 animate-pulse" title="运行中"></span>
                <span v-if="getMessageCount(item.data.id) > 0" class="shrink-0 ml-1 text-[10px] px-1.5 py-0.5 rounded-full bg-green-100 dark:bg-green-800 text-green-600 dark:text-green-300 font-medium">{{ getMessageCount(item.data.id) }}</span>
              </div>
              <div class="text-xs text-gray-400 mt-0.5 truncate" v-if="getFirstMessagePreview(item.data.id)">{{ getFirstMessagePreview(item.data.id) }}</div>
              <div class="text-[10px] text-gray-400 mt-0.5 flex justify-between items-center">
                <span>{{ formatTime(item.data.createdAt) }}</span>
                <button
                  @click.stop="handleDelete(item.data.id)"
                  class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600 transition ml-1"
                  title="删除会话"
                >×</button>
              </div>
            </template>
          </div>
        </template>

        <!-- 空状态 -->
        <div v-if="groupedSessions.pinned.length === 0 && groupedSessions.items.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-xs py-6">
          {{ searchQuery ? '没有匹配的会话' : '暂无会话' }}
        </div>
      </div>

      <!-- 底部工具栏 -->
      <div class="p-3 border-t border-gray-200 dark:border-gray-700 grid grid-cols-3 gap-2 shrink-0">
        <button @click="goStudio()" class="group relative px-3 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition" title="创作工作室">
          <span class="text-base">🎨</span>
          <span class="sidebar-tooltip group-hover:opacity-100">创作工作室</span>
        </button>
        <button @click="goSettings()" class="group relative px-3 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition" title="设置">
          <span class="text-base">⚙️</span>
          <span class="sidebar-tooltip group-hover:opacity-100">设置</span>
        </button>
        <button @click="chat.toggleTheme()" class="group relative px-3 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition" :title="chat.theme === 'dark' ? '浅色模式' : '深色模式'">
          <span class="text-base">{{ chat.theme === 'dark' ? '☀️' : '🌙' }}</span>
          <span class="sidebar-tooltip group-hover:opacity-100">{{ chat.theme === 'dark' ? '浅色模式' : '深色模式' }}</span>
        </button>
        <button @click="openPanel('skills')" class="group relative px-3 py-2 rounded-lg text-sm transition bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600" title="Agent 管理（技能/工具/软件/专家/MCP/记忆/知识库）">
          <span class="text-base">🤖</span>
          <span class="sidebar-tooltip group-hover:opacity-100">Agent 管理</span>
        </button>
        <button @click="goMobileConnect()" class="group relative px-3 py-2 rounded-lg text-sm transition bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600" title="移动接入">
          <span class="text-base">📱</span>
          <span class="sidebar-tooltip group-hover:opacity-100">移动接入</span>
        </button>
        <button @click="goScholarForge()" class="group relative px-3 py-2 rounded-lg text-sm transition" :class="$route.path === '/scholarforge' ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600'" title="论文写作">
          <span class="text-base">📝</span>
          <span class="sidebar-tooltip group-hover:opacity-100">论文写作</span>
        </button>
        <button @click="go3DStudio()" class="group relative px-3 py-2 rounded-lg text-sm transition" :class="$route.path === '/3d-studio' ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600'" title="3D 建模">
          <span class="text-base">🏭</span>
          <span class="sidebar-tooltip group-hover:opacity-100">3D 建模</span>
        </button>
        <button @click="goSkillMarket()" class="group relative px-3 py-2 rounded-lg text-sm transition" :class="$route.path === '/skill-market' ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600'" title="技能市场">
          <span class="text-base">🧩</span>
          <span class="sidebar-tooltip group-hover:opacity-100">技能市场</span>
        </button>
        <button @click="goKanban()" class="group relative px-3 py-2 rounded-lg text-sm transition" :class="$route.path === '/kanban' ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600'" title="蜂群看板">
          <span class="text-base">🐝</span>
          <span class="sidebar-tooltip group-hover:opacity-100">蜂群看板</span>
        </button>
        </div>

      <!-- MCP / 技能 / 工具 / 软件 / 专家 管理已统一迁至右侧大面板 ToolSkillDrawer -->
      <!-- 进化系统面板 -->
      <div class="shrink-0 max-h-[60vh] overflow-y-auto evolution-panel-wrapper">
        <EvolutionPanel />
      </div>
    </template>
  </div>

  <!-- 右键菜单 -->
  <Teleport to="body">
    <div v-if="contextMenu.show"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      class="fixed z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl py-1 min-w-[140px]"
      @click.stop
    >
      <button @click="startRename(contextMenu.session)" class="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition">✏️ 重命名</button>
      <button @click="togglePin(contextMenu.session)" class="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition">
        {{ contextMenu.session?.pinned ? '📌 取消置顶' : '📌 置顶' }}
      </button>
      <div class="border-t border-gray-200 dark:border-gray-600 my-1"></div>
      <button @click="handleDelete(contextMenu.session?.id)" class="w-full px-3 py-1.5 text-left text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition">🗑 删除会话</button>
      <div class="border-t border-gray-200 dark:border-gray-600 my-1"></div>
      <button @click="handleExport(contextMenu.session?.id, 'markdown')" class="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition">📄 导出 Markdown</button>
      <button @click="handleExport(contextMenu.session?.id, 'html')" class="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition">🌐 导出 HTML</button>
      <button @click="handleExport(contextMenu.session?.id, 'json')" class="w-full px-3 py-1.5 text-left text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 transition">📋 导出 JSON</button>
    </div>
  </Teleport>

</template>
