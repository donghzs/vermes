<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useBotRoomStore } from '../stores/botRoom'
import { showToast as toast } from '../utils/toast'
import api from '../services/api'

const bot = useBotRoomStore()

// ── 新建群弹窗（微信式：只填群名，勾选拉谁） ──
const createModal = ref({ open: false, name: '', announcement: '', tasks: '', selected: [] })
const contacts = ref([])        // 联系人列表（可拉人进群的全量 agent）
const loadingContacts = ref(false)

// ── 拉人进群弹窗（2026-09-07 神魔堂收口：👥 拉人不再死链，多选未入群联系人直接拉入） ──
const inviteModal = ref({ open: false, selected: [], submitting: false })
// ── 群成员管理弹窗（微信式：点成员看详情/移出） ──
const memberModal = ref({ open: false })

// ── 群公告/群任务编辑弹窗 ──
const editModal = ref({ open: false, title: '', announcement: '', tasks: '' })

// ── 输入 ──
const inputText = ref('')
const timelineRef = ref(null)

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const messages = computed(() => bot.timeline)
const currentRoom = computed(() => bot.currentRoom)
// 流式：正在输入的 agent（按 author_ref 过滤出 active 的）
const streamingAgents = computed(() => {
  const out = {}
  for (const [aid, s] of Object.entries(bot.streaming || {})) {
    if (s && s.active) out[aid] = s
  }
  return out
})

// 自动滚到底部
async function scrollToBottom() {
  await nextTick()
  const el = timelineRef.value
  if (el) el.scrollTop = el.scrollHeight
}
watch(messages, scrollToBottom)

// ── 联系人加载 ──
async function loadContacts() {
  loadingContacts.value = true
  try {
    const r = await api.listAgentContacts()
    if (r && r.ok) contacts.value = r.contacts || []
  } catch (e) {
    console.error('联系人加载失败', e)
    contacts.value = []
  } finally {
    loadingContacts.value = false
  }
}

// 已入群的成员 id 集合（用于联系人列表里标记"已在群"）
const memberIds = computed(() => new Set((bot.members || []).map(m => m.ref_id)))

// 可拉人进群的联系人 = 全量联系人（未入群的）
const addableContacts = computed(() => contacts.value.filter(c => !memberIds.value.has(c.id)))

// ── 新建群 ──
function openCreate() {
  createModal.value = { open: true, name: '', announcement: '', tasks: '', selected: [] }
  loadContacts()
}
function toggleSelect(id) {
  const s = createModal.value.selected
  const i = s.indexOf(id)
  if (i >= 0) s.splice(i, 1)
  else s.push(id)
}
async function handleCreate() {
  const m = createModal.value
  const name = m.name.trim()
  if (!name) { toast('请填写群名称', 'error'); return }
  const r = await bot.createRoom(name, m.selected, { announcement: m.announcement, tasks: m.tasks })
  if (r && r.ok) {
    createModal.value = { open: false, name: '', announcement: '', tasks: '', selected: [] }
    toast('群已创建', 'success')
    await scrollToBottom()
  } else {
    toast((r && r.error) || '创建失败', 'error')
  }
}

// ── 群公告/群任务编辑 ──
function openEdit() {
  const r = currentRoom.value
  if (!r) return
  editModal.value = {
    open: true,
    title: r.title || r.id,
    announcement: r.announcement || '',
    tasks: r.tasks || '',
  }
}
async function handleEditSave() {
  const m = editModal.value
  const r = await bot.updateRoom({ title: m.title, announcement: m.announcement, tasks: m.tasks })
  if (r && r.ok) {
    editModal.value = { open: false }
    toast('已保存', 'success')
  } else {
    toast((r && r.error) || '保存失败', 'error')
  }
}

// ── 拉人/踢人 ──
async function addMember(id) {
  const r = await bot.addMember(id)
  if (r && r.ok) toast('已拉入群', 'success')
  else toast((r && r.error) || '拉人失败', 'error')
}
async function removeMember(id) {
  const r = await bot.removeMember(id)
  if (r && r.ok) toast('已移出群', 'success')
  else toast((r && r.error) || '移出失败', 'error')
}

// 拉人弹窗：打开时载入联系人，未入群的可多选拉入（已入群的置灰标记）
function openInvite() {
  if (!bot.currentRoomId) { toast('请先选择或创建群', 'error'); return }
  inviteModal.value = { open: true, selected: [], submitting: false }
  loadContacts()
}
function toggleInvite(id) {
  const s = inviteModal.value.selected
  const i = s.indexOf(id)
  if (i >= 0) s.splice(i, 1)
  else s.push(id)
}
async function handleInvite() {
  const sel = inviteModal.value.selected
  if (sel.length === 0) { toast('请勾选要拉入的 Agent', 'error'); return }
  inviteModal.value.submitting = true
  try {
    let ok = true
    for (const id of sel) {
      const r = await bot.addMember(id)
      if (!(r && r.ok)) { ok = false; toast((r && r.error) || `拉入 ${id} 失败`, 'error') }
    }
    if (ok) toast(`已拉入 ${sel.length} 个 Agent`, 'success')
    inviteModal.value.open = false
  } finally {
    inviteModal.value.submitting = false
  }
}

// 群成员管理：微信式弹窗，点成员可看详情 / 移出群
function openMembers() {
  if (!bot.currentRoomId) return
  memberModal.value = { open: true }
}
async function removeMemberFromModal(m) {
  const nm = (m && (m.name || m.ref_id)) || ''
  if (!confirm(`确定把「${nm}」移出群？`)) return
  await removeMember(m.ref_id)
  // 若移出后群已空，保持弹窗开着但列表同步为空
}

async function handleSelect(id) {
  await bot.selectRoom(id)
  await scrollToBottom()
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || bot.sending) return
  if (!bot.currentRoomId) { toast('请先选择或创建群', 'error'); return }
  bot.sendMessage(text)
  inputText.value = ''
  closeMention()
}

// ── Phase 2：完整 @ 补全交互 ──
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
    start: pos - m[1].length - 1,
    index: 0,
  }
}

function _insertToken(token) {
  const el = textareaRef.value
  const text = inputText.value || ''
  if (!el) return
  const pos = el.selectionStart ?? text.length
  const needSpace = pos > 0 && !/\s$/.test(text.slice(0, pos))
  const full = (needSpace ? ' ' : '') + token + ' '
  inputText.value = text.slice(0, pos) + full + text.slice(pos)
  closeMention()
  nextTick(() => {
    const np = pos + full.length
    el.focus()
    try { el.setSelectionRange(np, np) } catch { /* ignore */ }
  })
}

function applyMention(c) {
  _insertToken('@' + (c.insert || c.ref_id))
}

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

function vis(refId) {
  const hit = bot.memberByRef[refId]
  if (hit) return hit
  let h = 0
  for (const ch of String(refId || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return { name: refId, hue: h, initial: String(refId || '?').slice(0, 1) }
}

function _onRoomUpdate(e) { bot.onRoomUpdate(e.detail) }

onMounted(async () => {
  window.addEventListener('vermes:room_update', _onRoomUpdate)
  await bot.loadRooms()
  if (bot.currentRoomId) {
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
    <div v-if="bot.botModeDisabled" class="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
      <div class="text-4xl">💤</div>
      <div class="text-lg font-semibold">Bot Mode 未启用</div>
      <div class="text-sm text-gray-400 max-w-sm">当前配置已关闭 Bot Mode（bot_mode.enabled=false）。开启后此处可进行群聊多 Agent 协作；单聊功能不受影响。</div>
    </div>
    <template v-else>
    <!-- 左：群列表 + 新建群 -->
    <aside class="w-64 shrink-0 border-r border-gray-200 dark:border-gray-700 flex flex-col">
      <div class="p-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div class="text-sm font-semibold">群聊</div>
        <button
          class="px-2 py-1 text-xs rounded bg-blue-500 hover:bg-blue-600 text-white transition"
          @click="openCreate"
        >+ 建群</button>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-1">
        <div v-if="bot.loadingRooms" class="text-xs text-gray-400 px-1">加载中…</div>
        <div v-else-if="bot.rooms.length === 0" class="text-xs text-gray-400 px-1">还没有群，点「+ 建群」创建一个。</div>
        <button
          v-for="r in bot.rooms"
          :key="r.id"
          class="w-full text-left px-2 py-2 rounded text-sm transition"
          :class="r.id === bot.currentRoomId ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700'"
          @click="handleSelect(r.id)"
        >
          <div class="font-medium truncate">{{ r.title || r.id }}</div>
          <div v-if="r.tasks" class="text-[11px] opacity-70 truncate">📋 {{ r.tasks }}</div>
        </button>
      </div>
    </aside>

    <!-- 右：群详情 + 时间线 + 输入 -->
    <main class="flex-1 flex flex-col min-w-0">
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="font-semibold truncate">{{ currentRoom ? (currentRoom.title || currentRoom.id) : '未选择群' }}</div>
            <div class="text-xs text-gray-400">输入 @ 唤起补全 · 不 @ 则默认 Agent 应答</div>
          </div>
          <div class="flex items-center gap-1">
            <!-- 群公告/群任务入口 -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
              title="编辑群公告 / 群任务"
              @click="openEdit"
            >📋 群公告</button>
            <!-- 群成员管理（微信式：看谁在群、点成员详情/移出） -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
              :title="`群成员 ${(bot.members || []).length} 人`"
              @click="openMembers"
            >👥 群成员{{ (bot.members || []).length ? `(${(bot.members || []).length})` : '' }}</button>
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-blue-500 hover:bg-blue-600 text-white transition"
              title="拉新 Agent 进群"
              @click="openInvite"
            >＋ 拉人</button>
          </div>
        </div>
        <!-- 群公告条 -->
        <div v-if="currentRoom && (currentRoom.announcement || currentRoom.tasks)" class="mt-2 flex flex-wrap gap-2">
          <span v-if="currentRoom.announcement" class="px-2 py-0.5 text-[11px] rounded bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">📢 {{ currentRoom.announcement }}</span>
          <span v-if="currentRoom.tasks" class="px-2 py-0.5 text-[11px] rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">📋 {{ currentRoom.tasks }}</span>
        </div>
        <!-- 房间成员：点击插入 @，长按/右键可踢 -->
        <div class="mt-2 flex items-center gap-1 flex-wrap">
          <button
            v-for="m in bot.mentionCandidates"
            :key="m.ref_id"
            class="group flex items-center gap-1 pl-0.5 pr-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
            :title="`@${m.insert}（点击 @；点 × 移出群）`"
            @click="mentionAtInput(m)"
          >
            <svg viewBox="0 0 32 32" class="w-5 h-5 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${m.hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ m.initial }}</text>
            </svg>
            <span class="text-xs">{{ m.name }}</span>
            <span
              class="text-[10px] text-gray-400 hover:text-red-500 px-0.5"
              title="移出群"
              @click.stop="removeMember(m.ref_id)"
            >×</span>
          </button>
          <span v-if="bot.mentionCandidates.length === 0" class="text-xs text-gray-400">（群里还没有 agent，点「👥 拉人」）</span>
        </div>
      </div>

      <!-- 时间线 -->
      <div ref="timelineRef" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        <div v-if="bot.loadingTimeline" class="text-xs text-gray-400">加载消息…</div>
        <div v-else-if="messages.length === 0" class="text-sm text-gray-400 mt-8 text-center">
          还没有消息。在下方输入并 @ 一个 Agent 开始对话。
        </div>
        <template v-for="m in messages" :key="m.id">
          <div v-if="m.author_type === 'user'" class="flex justify-end">
            <div class="max-w-[75%] px-3 py-2 rounded-2xl rounded-tr-sm bg-blue-500 text-white text-sm whitespace-pre-wrap break-words">
              {{ m.content }}
            </div>
          </div>
          <div v-else-if="m.author_type === 'system'" class="flex justify-center">
            <div class="max-w-[80%] px-3 py-1.5 rounded text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 text-center">
              {{ m.content }}
            </div>
          </div>
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

        <!-- 流式：正在输入的 agent（真群聊流式体验，逐 delta 累积） -->
        <template v-for="(s, aid) in streamingAgents" :key="'stream-' + aid">
          <div class="flex justify-start gap-2">
            <svg viewBox="0 0 32 32" class="w-7 h-7 rounded-full shrink-0 mt-4">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${vis(aid).hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ vis(aid).initial }}</text>
            </svg>
            <div class="max-w-[72%]">
              <div class="text-[11px] text-gray-400 mb-0.5 px-1">@{{ vis(aid).name || aid }} <span class="text-emerald-500">▌正在输入…</span></div>
              <div class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm whitespace-pre-wrap break-words">
                {{ s.text || '…' }}
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
              placeholder="输入消息，输入 @ 唤起成员补全，例如：@法律顾问 帮我看看这份合同"
              class="w-full resize-none px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
              :disabled="!bot.currentRoomId || bot.sending"
              @keydown="onKeydown"
              @input="updateMention"
              @click="updateMention"
            ></textarea>
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

    <!-- 新建群弹窗（微信式：只填群名，勾选拉谁） -->
    <div
      v-if="createModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="createModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">+ 建群</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">群名自由命名，勾选要拉进群的 Agent（想拉谁拉谁）。</p>
        <div class="flex flex-col gap-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群名称 *</label>
            <input v-model="createModal.name" placeholder="如：法务项目组 / 我的智囊团" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" @keydown.enter="handleCreate" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群公告</label>
            <input v-model="createModal.announcement" placeholder="可留空，建群后也能编辑" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群任务</label>
            <input v-model="createModal.tasks" placeholder="可留空，建群后也能编辑" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">拉谁进群</label>
            <div v-if="loadingContacts" class="text-xs text-gray-400">加载联系人…</div>
            <div v-else-if="contacts.length === 0" class="text-xs text-gray-400">暂无联系人（请先在「神魔架」造神或登堂）</div>
            <div v-else class="max-h-48 overflow-y-auto space-y-1">
              <label
                v-for="c in contacts"
                :key="c.id"
                class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
              >
                <input type="checkbox" :checked="createModal.selected.includes(c.id)" @change="toggleSelect(c.id)" class="accent-blue-500" />
                <svg viewBox="0 0 32 32" class="w-6 h-6 rounded-full shrink-0">
                  <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue || 0}, 65%, 45%)`" />
                  <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="14" font-weight="600">{{ (c.name || '?').slice(0, 1) }}</text>
                </svg>
                <div class="min-w-0">
                  <div class="text-sm truncate">{{ c.name }}</div>
                  <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.id }}</div>
                </div>
                <span class="ml-auto text-[10px] px-1.5 py-0.5 rounded" :class="c.transport === 'acp' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ c.transport === 'acp' ? '登堂' : '原生' }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="createModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleCreate" class="px-3 py-1.5 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 text-white">创建</button>
        </div>
      </div>
    </div>

    <!-- 群公告/群任务编辑弹窗 -->
    <div
      v-if="editModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="editModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">编辑群信息</h3>
        <div class="flex flex-col gap-3 mt-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群名称</label>
            <input v-model="editModal.title" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群公告</label>
            <textarea v-model="editModal.announcement" rows="3" placeholder="群公告，成员可见" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500 resize-none"></textarea>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群任务</label>
            <textarea v-model="editModal.tasks" rows="3" placeholder="群任务，成员可见" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500 resize-none"></textarea>
          </div>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="editModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleEditSave" class="px-3 py-1.5 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 text-white">保存</button>
        </div>
      </div>
    </div>

    <!-- 拉人进群弹窗（2026-09-07：微信式多选拉人，未入群的可勾选，已入群的置灰标记） -->
    <div
      v-if="inviteModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="inviteModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">拉人进群</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">勾选要拉入的 Agent（原生 + 已登堂的封神榜 Agent 都能拉，@ 它即可协作）。</p>
        <div v-if="loadingContacts" class="text-xs text-gray-400">加载联系人…</div>
        <div v-else-if="addableContacts.length === 0" class="text-sm text-gray-400 py-6 text-center">
          没有可拉入的新 Agent（全部已在群）。<br/>可到「🔥 神魔架」造神或从封神榜登堂后再拉。
        </div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1">
          <label
            v-for="c in addableContacts"
            :key="c.id"
            class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
          >
            <input type="checkbox" :checked="inviteModal.selected.includes(c.id)" @change="toggleInvite(c.id)" class="accent-blue-500" />
            <svg viewBox="0 0 32 32" class="w-6 h-6 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue || 0}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="14" font-weight="600">{{ (c.name || '?').slice(0, 1) }}</text>
            </svg>
            <div class="min-w-0">
              <div class="text-sm truncate">{{ c.name }}</div>
              <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.id }}</div>
            </div>
            <span class="ml-auto text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="c.transport === 'acp' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ c.transport === 'acp' ? '⛩️ 登堂' : '原生' }}</span>
          </label>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="inviteModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleInvite" :disabled="inviteModal.submitting || inviteModal.selected.length === 0" class="px-3 py-1.5 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white">{{ inviteModal.submitting ? '拉入中…' : `拉入 ${inviteModal.selected.length} 个` }}</button>
        </div>
      </div>
    </div>

    <!-- 群成员管理弹窗（微信式：看谁在群、点移出；transport 徽标区分原生/登堂） -->
    <div
      v-if="memberModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="memberModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <div class="flex items-center justify-between mb-1">
          <h3 class="text-base font-semibold">群成员（{{ (bot.members || []).length }}）</h3>
          <button @click="memberModal.open = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none px-1">✕</button>
        </div>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">点成员名可快速 @；移出后该 Agent 不再应答群消息。</p>
        <div v-if="bot.loadingMembers" class="text-xs text-gray-400">加载成员…</div>
        <div v-else-if="(bot.members || []).length === 0" class="text-sm text-gray-400 py-6 text-center">群里还没有成员，点「＋ 拉人」把 Agent 拉进来。</div>
        <div v-else class="space-y-1">
          <div
            v-for="m in bot.members"
            :key="m.ref_id"
            class="flex items-center gap-2 px-2 py-2 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <svg viewBox="0 0 32 32" class="w-8 h-8 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${Number(m.hue) || ((m.name || m.ref_id || '').split('').reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 0))}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="14" font-weight="600">{{ (m.name || m.ref_id || '?').slice(0, 1) }}</text>
            </svg>
            <div class="min-w-0 flex-1">
              <div class="text-sm truncate flex items-center gap-1.5">
                <span class="truncate">{{ m.name || m.ref_id }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="m.transport === 'acp' || (m.ref_id || '').startsWith('a2a:') ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ m.transport === 'acp' || (m.ref_id || '').startsWith('a2a:') ? '⛩️ 登堂' : '原生' }}</span>
              </div>
              <div class="text-[11px] text-gray-400 truncate">{{ m.ref_id }}</div>
            </div>
            <button
              class="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition shrink-0"
              title="在输入框 @ 这个 Agent"
              @click="mentionAtInput({ ...m, name: m.name || m.ref_id, insert: (m.name || m.ref_id), hue: m.hue })"
            >@</button>
            <button
              class="text-xs px-2 py-1 rounded bg-red-50 dark:bg-red-900/30 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/50 transition shrink-0"
              title="移出群"
              @click="removeMemberFromModal(m)"
            >移出</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
