<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import BotRooms from './BotRooms.vue'
import AgentsPage from './AgentsPage.vue'
import KanbanBoard from './KanbanBoard.vue'

// ⛩️ 神魔堂：单一入口，融合「诸神会晤（群聊）」与「神魔架（请神/造神）」。
// 用户定调（2026-09-05 董董）：群聊与智能体市场本就是神魔堂一体两面，
// 不必拆两个前端入口——融合混淆，一个门进去。
const chat = useChatStore()
const route = useRoute()
const router = useRouter()
const tab = ref('hall') // 'hall' = 诸神会晤 · 'roster' = 神魔架 · 'swarm' = 蜂群看板

// ── 全局侧栏（单聊会话列表）联动 ──
// 神魔堂是群聊/多 agent 实验场，默认收起全局左栏让群聊区更宽裕；
// 顶部提供醒目「☰ 展开」按钮随时弹回单聊列表（单聊覆盖 90% 场景，需一键可回）。
const wasSidebarOpen = ref(true) // 记录进神魔堂前的侧栏状态，离开时恢复

function toggleSidebar() {
  chat.toggleSidebar()
}

// 进入神魔堂自动收起左栏（记住之前状态）；离开时恢复之前状态（回单聊主场景）
let stopRouteWatch = null
onMounted(() => {
  if (route.path === '/shenmotang') {
    wasSidebarOpen.value = chat.sidebarOpen
    if (chat.sidebarOpen) chat.sidebarOpen = false
  }
  stopRouteWatch = router.afterEach((to) => {
    if (to.path === '/shenmotang') {
      // 进入：收起（若已在神魔堂内切换 tab 不重复记录）
      if (route.path !== '/shenmotang') {
        wasSidebarOpen.value = chat.sidebarOpen
      }
      if (chat.sidebarOpen) chat.sidebarOpen = false
    } else {
      // 离开神魔堂 → 恢复之前状态
      if (chat.sidebarOpen === false && wasSidebarOpen.value === true) {
        chat.sidebarOpen = true
      }
    }
  })
})
onUnmounted(() => {
  if (stopRouteWatch) stopRouteWatch()
})
</script>

<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <!-- 顶部：神魔堂标题 + tab 切换 -->
    <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
      <div class="flex items-center gap-2 min-w-0">
        <!-- 醒目左栏弹出按钮：单聊列表（90% 场景）一键可回 -->
        <button
          @click="toggleSidebar"
          class="shrink-0 w-9 h-9 flex items-center justify-center text-lg rounded-xl bg-green-500 hover:bg-green-600 text-white shadow-sm transition"
          :title="chat.sidebarOpen ? '收起侧栏（给群聊更多空间）' : '展开侧栏（返回单聊列表）'"
        >☰</button>
        <span class="text-2xl shrink-0">⛩️</span>
        <div class="min-w-0">
          <h1 class="text-lg font-bold leading-tight">神魔堂</h1>
          <p class="text-[11px] text-gray-400 leading-tight truncate">诸神会晤 · 群聊多 Agent · 请神登堂 · 造神协作</p>
        </div>
      </div>
      <div class="flex items-center gap-1 rounded-lg bg-gray-100 dark:bg-gray-800 p-1 shrink-0">
        <button
          class="px-3 py-1.5 text-sm rounded-md transition"
          :class="tab === 'hall' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
          @click="tab = 'hall'"
        >💬 诸神会晤</button>
        <button
          class="px-3 py-1.5 text-sm rounded-md transition"
          :class="tab === 'roster' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
          @click="tab = 'roster'"
        >🔥 神魔架</button>
        <button
          class="px-3 py-1.5 text-sm rounded-md transition"
          :class="tab === 'swarm' ? 'bg-indigo-500 text-white' : 'hover:bg-gray-200 dark:hover:bg-gray-700'"
          @click="tab = 'swarm'"
          title="蜂群看板：多 Agent 任务图的工程执行视图（workspace + git + 心跳回收）"
        >🐝 蜂群看板</button>
      </div>
    </div>

    <!-- 内容：三个 tab 复用现有组件 -->
    <!-- 用 v-if 而非 v-show：只挂载当前 tab，避免一进神魔堂就并发挂载三个重型组件
         （BotRooms/AgentsPage/KanbanBoard 各自 onMounted 拉数据），消除整页卡顿。
         代价：切 tab 会重新挂载并刷新，但换来进入顺滑，符合用户「进来要快」诉求。 -->
    <div class="flex-1 min-h-0">
      <BotRooms v-if="tab === 'hall'" class="h-full" />
      <AgentsPage v-if="tab === 'roster'" class="h-full" />
      <KanbanBoard v-if="tab === 'swarm'" class="h-full" />
    </div>
  </div>
</template>
