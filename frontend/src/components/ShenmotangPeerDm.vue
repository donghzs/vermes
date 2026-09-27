<template>
  <!-- T2 1:1 默认面：选中联系人 → 右栏私聊会话（消息流 / 头像 / 状态） -->
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 min-w-0">
    <!-- 头：头像 · 名 · 状态 · 预留 ⊕ 拉群 -->
    <header class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center gap-3">
      <span
        class="w-10 h-10 rounded-full flex items-center justify-center text-base font-semibold text-white shrink-0"
        :style="{ background: `hsl(${contact.hue || 210}, 55%, 48%)` }"
      >{{ initial }}</span>
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-2">
          <h2 class="text-base font-semibold truncate">{{ contact.name || contact.id }}</h2>
          <span class="text-[11px] px-1.5 py-0.5 rounded-full" :class="statusClass">{{ statusLabel }}</span>
        </div>
        <p class="text-[11px] text-gray-400 truncate">
          1:1 私聊
          <template v-if="contact.transport"> · {{ contact.transport === 'acp' ? 'ACP' : contact.transport }}</template>
          <template v-if="contact.provider"> · {{ contact.provider }}</template>
        </p>
      </div>
      <button
        class="px-2.5 py-1.5 text-xs rounded-lg border border-gray-200 dark:border-gray-600 text-gray-500 hover:border-indigo-400 hover:text-indigo-500 transition shrink-0"
        title="拉群（T3）"
        @click="$emit('create-group')"
      >＋ 拉群</button>
    </header>

    <!-- T5 联邦状态条（只解析本会话 timeline，不跨房） -->
    <ShenmotangFederationBar
      :timeline="messages"
      :room-id="roomId"
      class="mx-4 mt-2"
    />

    <!-- 消息流 -->
    <div ref="streamEl" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
      <div v-if="loading && !messages.length" class="py-10 text-center text-sm text-gray-400">加载会话…</div>
      <div v-else-if="!messages.length" class="py-10 text-center">
        <div class="text-3xl mb-2">💬</div>
        <p class="text-sm text-gray-400">和 {{ contact.name || contact.id }} 的 1:1</p>
        <p class="text-xs text-gray-400 mt-1">发条消息开始私聊</p>
      </div>

      <template v-for="(m, i) in messages" :key="m.id || i">
        <!-- 用户：右对齐 -->
        <div v-if="m.author_type === 'user'" class="flex justify-end">
          <div class="max-w-[75%] px-3 py-2 rounded-2xl rounded-tr-sm bg-indigo-500 text-white text-sm whitespace-pre-wrap break-words">
            {{ m.content }}
          </div>
        </div>
        <!-- 系统：居中 -->
        <div v-else-if="m.author_type === 'system'" class="flex justify-center">
          <div class="max-w-[80%] px-3 py-1.5 rounded text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 text-center whitespace-pre-wrap break-words">
            {{ m.content }}
          </div>
        </div>
        <!-- 对方 agent：左对齐 + 头像 -->
        <div v-else class="flex justify-start gap-2">
          <span
            class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0 mt-1"
            :style="{ background: `hsl(${agentHue(m)}, 55%, 48%)` }"
          >{{ agentInitial(m) }}</span>
          <div class="max-w-[72%]">
            <div class="text-[11px] text-gray-400 mb-0.5 px-1">{{ agentName(m) }}</div>
            <div class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm text-gray-900 dark:text-gray-100 whitespace-pre-wrap break-words">
              {{ m.content }}
            </div>
          </div>
        </div>

        <!-- T4 行内任务卡（消息携带 task_id） -->
        <template v-if="taskIdsOf(m).length">
          <ShenmotangTaskCard
            v-for="tid in taskIdsOf(m)"
            :key="'task-' + (m.id || i) + '-' + tid"
            :task-id="tid"
            class="max-w-[85%]"
            :class="m.author_type === 'user' ? 'ml-auto' : 'mr-auto'"
          />
        </template>
      </template>

      <!-- 发送中 -->
      <div v-if="sending" class="flex justify-start gap-2">
        <span
          class="w-8 h-8 rounded-full flex items-center justify-center text-sm font-semibold text-white shrink-0 mt-1"
          :style="{ background: `hsl(${contact.hue || 210}, 55%, 48%)` }"
        >{{ initial }}</span>
        <div class="max-w-[72%]">
          <div class="text-[11px] text-gray-400 mb-0.5 px-1">{{ contact.name || contact.id }}</div>
          <div class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm text-gray-400">
            正在输入…
          </div>
        </div>
      </div>
    </div>

    <!-- 输入 -->
    <footer class="border-t border-gray-200 dark:border-gray-700 p-3">
      <div class="flex items-end gap-2">
        <textarea
          v-model="draft"
          rows="2"
          class="flex-1 px-3 py-2 text-sm rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
          :placeholder="`发消息给 ${contact.name || contact.id}`"
          @keydown.enter.exact.prevent="send"
        />
        <button
          class="px-4 py-2 rounded-xl bg-indigo-500 hover:bg-indigo-600 text-white text-sm font-medium transition disabled:opacity-50 shrink-0"
          :disabled="sending || !draft.trim()"
          @click="send"
        >发送</button>
      </div>
      <p v-if="error" class="mt-1.5 text-xs text-rose-500">{{ error }}</p>
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import api from '../services/api'
import ShenmotangTaskCard from './ShenmotangTaskCard.vue'
import ShenmotangFederationBar from './ShenmotangFederationBar.vue'
import { parseTaskIds, prepareDispatchSend } from '../utils/taskDispatch'

const props = defineProps({
  contact: { type: Object, required: true },
  /**
   * 可选：以某 agent 身份走 peer_dm（A2A 私聊）。
   * P2：预留未接线——父组件 Shenmotang.vue 目前只传 :contact，不传 peerFrom。
   * 用户→agent 默认仍走房间消息路径；A2A 接线在 T5 联邦可见性再做。
   */
  peerFrom: { type: String, default: '' },
})
const emit = defineEmits(['create-group', 'open-room'])

const draft = ref('')
const messages = ref([])
const loading = ref(false)
const sending = ref(false)
const error = ref('')
const streamEl = ref(null)
const roomId = ref('')

const initial = computed(() => String(props.contact.name || props.contact.id || '?').slice(0, 1))
// P3：has_api_key / transport 是配置态，不是真实在线——措辞用「已接入」
const statusLabel = computed(() => {
  if (props.contact.has_api_key || props.contact.transport === 'acp') return '已接入'
  if (props.contact.transport === 'cli' || props.contact.transport === 'native') return '本地已接入'
  return '就绪'
})
const statusClass = computed(() => {
  const s = statusLabel.value
  if (s.includes('已接入')) return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
  return 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
})

function agentHue(m) {
  if (m.author_ref === props.contact.id) return props.contact.hue || 210
  const t = String(m.author_ref || '')
  let h = 0
  for (const ch of t) h = (h * 31 + ch.charCodeAt(0)) % 360
  return h
}
function agentName(m) {
  if (m.author_ref === props.contact.id) return props.contact.name || props.contact.id
  return m.author_ref || 'agent'
}
function agentInitial(m) {
  return String(agentName(m) || '?').slice(0, 1)
}

/** T4：消息携带的 task_id → 行内任务卡 */
function taskIdsOf(m) {
  return parseTaskIds(m && m.content)
}

function scrollToEnd() {
  nextTick(() => {
    const el = streamEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/** 稳定 DM 房间：dm-<contactId>，单成员 → 后端秘书模式自动应答。 */
async function ensureDmRoom() {
  const cid = String(props.contact.id || '').trim()
  if (!cid) return ''
  const wantId = `dm-${cid}`
  try {
    const rs = await api.listBotRooms().catch(() => null)
    const rooms = (rs && rs.rooms) || (rs && Array.isArray(rs) ? rs : []) || []
    const hit = rooms.find(r => (r.id || r.room_id) === wantId)
    if (hit) return hit.id || hit.room_id
    const created = await api.createBotRoom(`1:1 · ${props.contact.name || cid}`, [cid], { id: wantId })
    if (created && created.ok && (created.room_id || created.id)) {
      return created.room_id || created.id
    }
  } catch (e) {
    console.error('[ShenmotangPeerDm] ensureDmRoom failed', e)
  }
  return wantId
}

async function load() {
  const cid = props.contact?.id
  if (!cid) return
  loading.value = true
  error.value = ''
  try {
    const rid = await ensureDmRoom()
    roomId.value = rid
    if (!rid) {
      messages.value = []
      return
    }
    const r = await api.getBotRoomTimeline(rid).catch(() => null)
    messages.value = (r && r.timeline) || []
    scrollToEnd()
  } catch (e) {
    error.value = e.message || '加载会话失败'
  } finally {
    loading.value = false
  }
}

async function send() {
  const text = (draft.value || '').trim()
  if (!text || sending.value) return
  sending.value = true
  error.value = ''
  try {
    if (!roomId.value) roomId.value = await ensureDmRoom()
    // T4 派活：检测「派活」→ 先建 kanban 任务 → 消息携带 task 标记
    let outText = text
    try {
      const prepared = await prepareDispatchSend(text, {
        contacts: [props.contact],
        assigneeId: props.contact.id,
      })
      outText = prepared.text
    } catch (e) {
      // 建任务失败不阻断聊天
      console.error('[ShenmotangPeerDm] dispatch task failed', e)
    }
    // peer_dm：显式 A2A（from/to 都是 agent）；否则走房间消息（用户→单 agent，秘书模式应答）
    let r = null
    if (props.peerFrom && props.peerFrom !== props.contact.id) {
      r = await api.sendPeerDm(roomId.value, props.peerFrom, props.contact.id, outText)
    } else {
      r = await api.sendBotRoomMessage(roomId.value, outText)
    }
    if (r && r.ok) {
      draft.value = ''
      if (Array.isArray(r.timeline)) {
        messages.value = r.timeline
      } else {
        await load()
      }
      scrollToEnd()
    } else {
      error.value = (r && r.error) || '发送失败'
    }
  } catch (e) {
    error.value = e.message || '发送失败'
  } finally {
    sending.value = false
  }
}

/** P0：秘书/直答回复经 WS room_update 推送，请求只秒回——必须订阅，否则回复永不出现。 */
function onRoomUpdate(e) {
  const msg = e && e.detail
  if (!msg || msg.type !== 'room_update') return
  const topicRoom = (msg.topic || '').replace(/^room:/, '')
  if (topicRoom && roomId.value && topicRoom !== roomId.value) return
  if (msg.event === 'room_message' || msg.event === 'room_message_delta') {
    load()
  }
}

watch(() => props.contact?.id, (id, old) => {
  if (id && id !== old) load()
})
onMounted(() => {
  window.addEventListener('vermes:room_update', onRoomUpdate)
  load()
})
onUnmounted(() => {
  window.removeEventListener('vermes:room_update', onRoomUpdate)
})

defineExpose({ reload: load, send })
</script>
