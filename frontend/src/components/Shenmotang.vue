<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import BotRooms from './BotRooms.vue'
import AgentsPage from './AgentsPage.vue'
import KanbanBoard from './KanbanBoard.vue'
import ShenmotangImRail from './ShenmotangImRail.vue'
import ShenmotangPeerDm from './ShenmotangPeerDm.vue'

// ⛩️ 神魔堂 T1（2026-09-26 IM 壳）：常驻「联系人/群」左栏 + 会话/编排主区。
// 反转旧「进堂收起全局侧栏」逻辑 —— 神魔堂自带 IM 左栏，不再抢用户侧栏状态。
const router = useRouter()
const tab = ref('hall') // 'hall' = 会话 · 'roster' = 请神 · 'swarm' = 看板

// T1 IM 左栏选中态
const selectedKey = ref('') // 'contact:<id>' | 'room:<id>'
const imRail = ref(null)

// T2 1:1 默认面：选中联系人 → 右栏 peer_dm 会话
const activeContact = ref(null)

function onSelectContact(c) {
  selectedKey.value = 'contact:' + c.id
  activeContact.value = c
  tab.value = 'hall'
}
function onSelectRoom(r) {
  selectedKey.value = 'room:' + (r.id || r.room_id)
  activeContact.value = null
  tab.value = 'hall'
}
function onCreateGroup() {
  tab.value = 'hall' // 复用 BotRooms 建群=建组织弹窗
}
function onAddContact() {
  tab.value = 'roster' // 请神/造神
}
function onOpenUsage() {
  router.push('/usage')
}

// 旧逻辑（2026-09-26 退役）：进堂强制收起全局左栏、离开恢复。
// T1 反转：神魔堂有常驻 IM 左栏，不再劫持 chat.sidebarOpen。
</script>

<template>
  <div class="h-full flex bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- T1：常驻 IM 左栏（联系人 / 群 + ⊕） -->
    <ShenmotangImRail
      ref="imRail"
      :selected-key="selectedKey"
      @select-contact="onSelectContact"
      @select-room="onSelectRoom"
      @create-group="onCreateGroup"
      @add-contact="onAddContact"
      @open-usage="onOpenUsage"
    />

    <!-- 主区 -->
    <div class="flex-1 min-w-0 flex flex-col">
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
        <div class="flex items-center gap-2 min-w-0">
          <span class="text-2xl shrink-0">⛩️</span>
          <div class="min-w-0">
            <h1 class="text-lg font-bold leading-tight">神魔堂</h1>
            <p class="text-[11px] text-gray-400 leading-tight truncate">
              多 Agent 桌面 IM · 联系人 → 单聊 → 拉群 → 派活 → 协作
            </p>
          </div>
        </div>
        <div class="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-800 p-1 shrink-0">
          <button
            class="px-3 py-1.5 text-sm rounded-md transition"
            :class="tab === 'hall' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
            @click="tab = 'hall'"
          >💬 会话</button>
          <button
            class="px-3 py-1.5 text-sm rounded-md transition"
            :class="tab === 'roster' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
            @click="tab = 'roster'"
          >🔥 请神</button>
          <button
            class="px-3 py-1.5 text-sm rounded-md transition"
            :class="tab === 'swarm' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
            @click="tab = 'swarm'"
            title="蜂群看板：多 Agent 任务图的工程执行视图（workspace + git + 心跳回收）"
          >🐝 看板</button>
        </div>
      </div>

      <!-- v-if 只挂当前 tab（避免三重型组件并发挂载） -->
      <div class="flex-1 min-h-0">
        <!-- T2：选中联系人 → 1:1 默认面；选中群 / 无选中 → 房间主区 -->
        <ShenmotangPeerDm
          v-if="tab === 'hall' && activeContact"
          :key="'dm-' + activeContact.id"
          class="h-full"
          :contact="activeContact"
          @create-group="onCreateGroup"
        />
        <BotRooms v-else-if="tab === 'hall'" class="h-full" />
        <AgentsPage v-if="tab === 'roster'" class="h-full" />
        <KanbanBoard v-if="tab === 'swarm'" class="h-full" />
      </div>
    </div>
  </div>
</template>
