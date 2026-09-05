<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useBotRoomStore } from '../stores/botRoom'
import { showToast as toast } from '../utils/toast'

const bot = useBotRoomStore()

// ── 新建房间表单 ──
const newRoomId = ref('')
const newRoomName = ref('')

// ── 输入 ──
const inputText = ref('')
const timelineRef = ref(null)

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const messages = computed(() => bot.timeline)
const currentRoom = computed(() => bot.currentRoom)

// 自动滚到底部
async function scrollToBottom() {
  await nextTick()
  const el = timelineRef.value
  if (el) el.scrollTop = el.scrollHeight
}
watch(messages, scrollToBottom)

// ── 房间操作 ──
async function handleCreate() {
  const id = newRoomId.value.trim()
  const name = newRoomName.value.trim()
  if (!id || !name) { toast('房间 ID 与名称均必填', 'error'); return }
  const r = await bot.createRoom(id, name)
  if (r && r.ok) {
    newRoomId.value = ''
    newRoomName.value = ''
    toast('房间已创建', 'success')
    await scrollToBottom()
  } else {
    toast((r && r.error) || '创建失败', 'error')
  }
}

async function handleSelect(id) {
  await bot.selectRoom(id)
  await scrollToBottom()
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || bot.sending) return
  if (!bot.currentRoomId) { toast('请先选择或创建房间', 'error'); return }
  bot.sendMessage(text)
  inputText.value = ''
  closeMention()
}

// ── Phase 2：完整 @ 补全交互 ──
// 触发条件：光标前形如「行首或空白 + @ + 至光标无空白无@的词元」。
// 候选 = store.mentionCandidates（房间 agent 成员），按 name/id 子串过滤。
const textareaRef = ref(null)
const mention = ref({ active: false, query: '', start: -1, index: 0 })

const mentionMatches = computed(() => {
  if (!mention.value.active) return []
  const q = (mention.value.query || '').toLowerCase()
  const cands = bot.mentionCandidates
  if (!q) return cands
  return cands.filter(
    c => (c.name || '').toLowerCase().includes(q) ||
         (c.ref_id || '').toLowerCase().includes(q),
  )
})

function closeMention() {
  mention.value = { active: false, query: '', start: -1, index: 0 }
}

// 只在「文本变化」与「光标点击」时重算——导航键（↑↓）不改文本，
// 故 index 不会被重置，键盘导航才有效（若绑 keyup 则每次导航都被归零）。
function updateMention() {
  const el = textareaRef.value
  if (!el) return
  const pos = el.selectionStart ?? 0
  const before = (inputText.value || '').slice(0, pos)
  const m = /(?:^|\s)@([^\s@]*)$/.exec(before)
  if (!m) { closeMention(); return }
  mention.value = {
    active: true,
    query: m[1],
    start: pos - m[1].length - 1,  // '@' 所在下标
    index: 0,
  }
}

// 写入词元。尾部补空格：后端 ROOM_MENTION_RE 遇空白截断，
// 无空格会把后续文字并入 token（@研究助手你好 → token 变「研究助手你好」）。
function _insertToken(token) {
  const el = textareaRef.value
  const text = inputText.value || ''
  if (!el) return
  const pos = el.selectionStart ?? text.length
  // 前一个字符非空白 → 先补空格，避免紧贴上一个词被正则连成一个 token
  const needSpace = pos > 0 && !/\s$/.test(text.slice(0, pos))
  const full = (needSpace ? ' ' : '') + token + ' '
  inputText.value = text.slice(0, pos) + full + text.slice(pos)
  closeMention()
  nextTick(() => {
    const np = pos + full.length
    el.focus()
    try { el.setSelectionRange(np, np) } catch { /* 某些浏览器无 selection API */ }
  })
}

function applyMention(c) {
  _insertToken('@' + (c.insert || c.ref_id))
}

// 点击顶部成员胶囊 → 在光标处插入该成员的 @ 词元
function mentionAtInput(m) {
  _insertToken('@' + (m.insert || m.ref_id))
}

function onKeydown(e) {
  const ms = mention.value
  const list = mentionMatches.value
  if (ms.active && list.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault(); ms.index = (ms.index + 1) % list.length; return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault(); ms.index = (ms.index - 1 + list.length) % list.length; return
    }
    // Tab / Enter 确认选中项（下拉开启时 Enter 不发送消息，避免误发）
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
      e.preventDefault()
      applyMention(list[Math.min(ms.index, list.length - 1)])
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); closeMention(); return }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// 时间线头像视觉：优先取成员表（hue/首字），缺失（如已移出房间的历史作者）
// 按 author_ref 哈希兜底，保证同一作者颜色稳定。
function vis(refId) {
  const hit = bot.memberByRef[refId]
  if (hit) return hit
  let h = 0
  for (const ch of String(refId || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return { name: refId, hue: h, initial: String(refId || '?').slice(0, 1) }
}

// ── WS room_update 转发（chat.js initChannelSync → CustomEvent） ──
function _onRoomUpdate(e) { bot.onRoomUpdate(e.detail) }

onMounted(async () => {
  window.addEventListener('vermes:room_update', _onRoomUpdate)
  await bot.loadRooms()
  if (bot.currentRoomId) {
    // Phase 2：成员一并加载，@ 补全候选项随页面就绪（不额外阻塞时间线渲染）
    await Promise.all([bot.loadTimeline(bot.currentRoomId), bot.loadMembers(bot.currentRoomId)])
    await scrollToBottom()
  }
})
onUnmounted(() => {
  window.removeEventListener('vermes:room_update', _onRoomUpdate)
})
</script>

<template>
  <div class="flex h-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 开关关闭：优雅降级（T7） -->
    <div v-if="bot.botModeDisabled" class="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
      <div class="text-4xl">💤</div>
      <div class="text-lg font-semibold">Bot Mode 未启用</div>
      <div class="text-sm text-gray-400 max-w-sm">当前配置已关闭 Bot Mode（bot_mode.enabled=false）。开启后此处可进行单房间多 Agent 协作；单聊功能不受影响。</div>
    </div>
    <template v-else>
    <!-- 左：房间列表 -->
    <aside class="w-64 shrink-0 border-r border-gray-200 dark:border-gray-700 flex flex-col">
      <div class="p-3 border-b border-gray-200 dark:border-gray-700">
        <div class="text-sm font-semibold mb-2">群聊房间</div>
        <div class="flex flex-col gap-1">
          <input
            v-model="newRoomId"
            placeholder="房间 ID（如 r1）"
            class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
          />
          <input
            v-model="newRoomName"
            placeholder="房间名称（如 我的研究组）"
            class="px-2 py-1 text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
            @keydown.enter="handleCreate"
          />
          <button
            class="mt-1 px-2 py-1 text-xs rounded bg-blue-500 hover:bg-blue-600 text-white transition"
            @click="handleCreate"
          >+ 新建房间</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-1">
        <div v-if="bot.loadingRooms" class="text-xs text-gray-400 px-1">加载中…</div>
        <div v-else-if="bot.rooms.length === 0" class="text-xs text-gray-400 px-1">暂无房间，先建一个。</div>
        <button
          v-for="r in bot.rooms"
          :key="r.id"
          class="w-full text-left px-2 py-2 rounded text-sm transition"
          :class="r.id === bot.currentRoomId ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700'"
          @click="handleSelect(r.id)"
        >
          <div class="font-medium truncate">{{ r.title || r.id }}</div>
          <div class="text-[11px] opacity-70 truncate">{{ r.id }}</div>
        </button>
      </div>
    </aside>

    <!-- 右：时间线 + 输入 -->
    <main class="flex-1 flex flex-col min-w-0">
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="font-semibold truncate">{{ currentRoom ? (currentRoom.title || currentRoom.id) : '未选择房间' }}</div>
            <div class="text-xs text-gray-400">输入 @ 唤起补全 · 不 @ 则默认 Agent 应答</div>
          </div>
          <!-- 房间成员：点击即在输入框插入 @（Phase 2 体验增强） -->
          <div class="flex items-center gap-1 flex-wrap justify-end">
            <button
              v-for="m in bot.mentionCandidates"
              :key="m.ref_id"
              class="flex items-center gap-1 pl-0.5 pr-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
              :title="`点击 @${m.insert}${m.description ? ' · ' + m.description : ''}`"
              @click="mentionAtInput(m)"
            >
              <svg viewBox="0 0 32 32" class="w-5 h-5 rounded-full shrink-0">
                <circle cx="16" cy="16" r="16" :fill="`hsl(${m.hue}, 65%, 45%)`" />
                <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ m.initial }}</text>
              </svg>
              <span class="text-xs">{{ m.name }}</span>
            </button>
            <span v-if="bot.mentionCandidates.length === 0" class="text-xs text-gray-400">（房间暂无 agent 成员）</span>
          </div>
        </div>
      </div>

      <!-- 时间线 -->
      <div ref="timelineRef" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        <div v-if="bot.loadingTimeline" class="text-xs text-gray-400">加载消息…</div>
        <div v-else-if="messages.length === 0" class="text-sm text-gray-400 mt-8 text-center">
          还没有消息。在下方输入并 @ 一个 Agent 开始对话。
        </div>
        <template v-for="m in messages" :key="m.id">
          <!-- 用户：右对齐 -->
          <div v-if="m.author_type === 'user'" class="flex justify-end">
            <div class="max-w-[75%] px-3 py-2 rounded-2xl rounded-tr-sm bg-blue-500 text-white text-sm whitespace-pre-wrap break-words">
              {{ m.content }}
            </div>
          </div>
          <!-- 系统：居中灰 -->
          <div v-else-if="m.author_type === 'system'" class="flex justify-center">
            <div class="max-w-[80%] px-3 py-1.5 rounded text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 text-center">
              {{ m.content }}
            </div>
          </div>
          <!-- Agent：左对齐（带 SVG 头像，色相与 @ 补全下拉一致） -->
          <div v-else class="flex justify-start gap-2">
            <svg viewBox="0 0 32 32" class="w-7 h-7 rounded-full shrink-0 mt-4">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${vis(m.author_ref).hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ vis(m.author_ref).initial }}</text>
            </svg>
            <div class="max-w-[72%]">
              <div class="text-[11px] text-gray-400 mb-0.5 px-1">@{{ vis(m.author_ref).name || m.author_ref }}</div>
              <div class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm whitespace-pre-wrap break-words">
                {{ m.content }}
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 输入 -->
      <div class="border-t border-gray-200 dark:border-gray-700 p-3">
        <div class="flex items-end gap-2">
          <div class="relative flex-1">
            <textarea
              ref="textareaRef"
              v-model="inputText"
              rows="2"
              placeholder="输入消息，输入 @ 唤起成员补全，例如：@研究助手 帮我总结这篇论文"
              class="w-full resize-none px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
              :disabled="!bot.currentRoomId || bot.sending"
              @keydown="onKeydown"
              @input="updateMention"
              @click="updateMention"
            ></textarea>
            <!-- @ 补全下拉（向上弹出，避免被容器裁切） -->
            <div
              v-if="mention.active && mentionMatches.length"
              class="absolute bottom-full left-0 mb-1 w-72 max-h-56 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1 z-20"
            >
              <div class="px-2 pb-1 text-[11px] text-gray-400">↑↓ 选择 · Enter/Tab 确认 · Esc 取消</div>
              <button
                v-for="(c, i) in mentionMatches"
                :key="c.ref_id"
                class="w-full flex items-center gap-2 px-2 py-1.5 text-left transition"
                :class="i === mention.index ? 'bg-blue-50 dark:bg-blue-900/40' : 'hover:bg-gray-100 dark:hover:bg-gray-700'"
                @mousedown.prevent="applyMention(c)"
              >
                <svg viewBox="0 0 32 32" class="w-6 h-6 rounded-full shrink-0">
                  <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue}, 65%, 45%)`" />
                  <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ c.initial }}</text>
                </svg>
                <div class="min-w-0">
                  <div class="text-sm truncate">@{{ c.insert }}</div>
                  <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.ref_id }}</div>
                </div>
              </button>
            </div>
          </div>
          <button
            class="px-4 py-2 rounded-lg bg-blue-500 hover:bg-blue-600 text-white text-sm transition disabled:opacity-50"
            :disabled="!bot.currentRoomId || bot.sending"
            @click="handleSend"
          >{{ bot.sending ? '发送中…' : '发送' }}</button>
        </div>
      </div>
    </main>
    </template>
  </div>
</template>
