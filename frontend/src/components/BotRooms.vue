<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useBotRoomStore } from '../stores/botRoom'
import { toast } from '../utils/toast'

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
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// ── WS room_update 转发（chat.js initChannelSync → CustomEvent） ──
function _onRoomUpdate(e) { bot.onRoomUpdate(e.detail) }

onMounted(async () => {
  window.addEventListener('vermes:room_update', _onRoomUpdate)
  await bot.loadRooms()
  if (bot.currentRoomId) { await bot.loadTimeline(bot.currentRoomId); await scrollToBottom() }
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
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div>
          <div class="font-semibold">{{ currentRoom ? (currentRoom.title || currentRoom.id) : '未选择房间' }}</div>
          <div class="text-xs text-gray-400">@研究助手 让特定 Agent 回答 · 空 @ 则默认 Agent 应答</div>
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
          <!-- Agent：左对齐 -->
          <div v-else class="flex justify-start">
            <div class="max-w-[75%]">
              <div class="text-[11px] text-gray-400 mb-0.5 px-1">@{{ m.author_ref }}</div>
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
          <textarea
            v-model="inputText"
            rows="2"
            placeholder="输入消息，例如：@研究助手 帮我总结这篇论文"
            class="flex-1 resize-none px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
            :disabled="!bot.currentRoomId || bot.sending"
            @keydown="onKeydown"
          ></textarea>
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
