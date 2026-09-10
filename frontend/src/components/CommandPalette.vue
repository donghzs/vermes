<template>
  <Transition name="fade">
    <div v-if="open" class="fixed inset-0 z-50 flex items-start justify-center pt-[15vh] bg-black/40" @click.self="open = false">
      <div class="w-full max-w-lg mx-4 rounded-2xl bg-white dark:bg-gray-800 shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden">
        <!-- 搜索框 -->
        <div class="flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <span class="text-gray-400">🔍</span>
          <input
            ref="inputRef"
            v-model="query"
            placeholder="搜索页面、会话、动作…"
            class="flex-1 bg-transparent outline-none text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400"
            @keydown.enter="runFirst"
            @keydown.escape="open = false"
            @keydown.down.prevent="moveSel(1)"
            @keydown.up.prevent="moveSel(-1)"
          />
          <kbd class="text-[10px] text-gray-400 px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700">ESC</kbd>
        </div>
        <!-- 结果列表 -->
        <div class="max-h-80 overflow-y-auto">
          <div v-if="results.length === 0" class="px-4 py-6 text-center text-sm text-gray-400">无匹配结果</div>
          <button
            v-for="(item, i) in results"
            :key="item.key"
            @click="select(item)"
            @mouseenter="sel = i"
            class="w-full flex items-center gap-3 px-4 py-2.5 text-left transition"
            :class="i === sel ? 'bg-emerald-50 dark:bg-emerald-900/30' : ''"
          >
            <span class="text-base flex-shrink-0">{{ item.icon }}</span>
            <div class="flex-1 min-w-0">
              <div class="text-sm text-gray-900 dark:text-gray-100 truncate">{{ item.label }}</div>
              <div v-if="item.hint" class="text-[11px] text-gray-400 truncate">{{ item.hint }}</div>
            </div>
            <span v-if="item.kind" class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-400 flex-shrink-0">{{ item.kind }}</span>
          </button>
        </div>
        <!-- 底部提示 -->
        <div class="px-4 py-2 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between text-[10px] text-gray-400">
          <span>↑↓ 选择 · Enter 确认 · Esc 关闭</span>
          <span>{{ results.length }} 条结果</span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, nextTick, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'

const router = useRouter()
const chat = useChatStore()

const open = ref(false)
const query = ref('')
const sel = ref(0)
const inputRef = ref(null)

// 页面命令
const pageCommands = [
  { key: 'page:chat', icon: '💬', label: '对话', hint: '回到聊天', kind: '页面', action: () => router.push('/') },
  { key: 'page:shenmotang', icon: '⛩️', label: '神魔堂', hint: '多 Agent 群聊 + 请神登堂', kind: '页面', action: () => router.push('/shenmotang') },
  { key: 'page:studio', icon: '🎨', label: '创作工作室', kind: '页面', action: () => router.push('/studio') },
  { key: 'page:scholarforge', icon: '📝', label: '论文写作', kind: '页面', action: () => router.push('/scholarforge') },
  { key: 'page:3d', icon: '🏭', label: '3D 建模', kind: '页面', action: () => router.push('/3d-studio') },
  { key: 'page:workflows', icon: '🔀', label: '工作流编排', kind: '页面', action: () => router.push('/workflows') },
  { key: 'page:bricks', icon: '🧱', label: '积木市场', kind: '页面', action: () => router.push('/bricks') },
  { key: 'page:growth', icon: '🌱', label: '成长', kind: '页面', action: () => router.push('/growth') },
  { key: 'page:benchmark', icon: '📊', label: 'Benchmark 大盘', kind: '页面', action: () => router.push('/benchmark') },
  { key: 'page:settings', icon: '⚙️', label: '设置', kind: '页面', action: () => router.push('/settings') },
]

// 动作命令
const actionCommands = [
  { key: 'act:new-chat', icon: '💬', label: '新建对话', kind: '动作', action: () => chat.createSession('新会话') },
  { key: 'act:toggle-theme', icon: '🌙', label: '切换深色/浅色主题', kind: '动作', action: () => chat.toggleTheme() },
]

// 全部命令
const allCommands = computed(() => [...pageCommands, ...actionCommands])

// 会话命令（动态）
const sessionCommands = computed(() => {
  const sessions = chat.sessions || []
  return sessions.slice(0, 20).map(s => ({
    key: 'session:' + s.id,
    icon: '💬',
    label: s.title || s.name || s.id.slice(0, 12),
    hint: s.preview || '',
    kind: '会话',
    action: () => { router.push('/'); nextTick(() => chat.switchSession(s.id)) },
  }))
})

// 搜索过滤
const results = computed(() => {
  const q = query.value.trim().toLowerCase()
  const all = [...allCommands.value, ...sessionCommands.value]
  if (!q) return all.slice(0, 12)
  return all.filter(c =>
    c.label.toLowerCase().includes(q) ||
    (c.hint && c.hint.toLowerCase().includes(q)) ||
    (c.kind && c.kind.toLowerCase().includes(q))
  ).slice(0, 20)
})

watch(results, () => { sel.value = 0 })

function select(item) {
  open.value = false
  query.value = ''
  if (item.action) item.action()
}

function runFirst() {
  if (results.value.length > 0) select(results.value[sel.value])
}

function moveSel(dir) {
  const n = results.value.length
  if (n === 0) return
  sel.value = (sel.value + dir + n) % n
}

// 全局快捷键 Cmd/Ctrl+K
function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault()
    open.value = !open.value
    if (open.value) {
      nextTick(() => inputRef.value?.focus())
    }
  }
}

// 暴露给父组件
defineExpose({
  toggle() {
    open.value = !open.value
    if (open.value) nextTick(() => inputRef.value?.focus())
  }
})

if (typeof window !== 'undefined') {
  window.addEventListener('keydown', onKeydown)
}
// 监听必须成对：组件卸载（HMR / KeepAlive 重建）时若不解绑，监听器会累积，
// 每次 Cmd+K 触发多次 toggle —— 表现是「按了没反应」（开了又关）。
onUnmounted(() => {
  if (typeof window !== 'undefined') window.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.15s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
