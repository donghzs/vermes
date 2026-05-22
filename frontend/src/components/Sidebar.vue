<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'
import { useRouter } from 'vue-router'

const chat = useChatStore()
const router = useRouter()

function goSettings() { router.push('/settings') }

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

// 右键菜单
const contextMenu = ref({ show: false, x: 0, y: 0, sessionId: null })

function onContextMenu(e, s) {
  e.preventDefault()
  contextMenu.value = { show: true, x: e.clientX, y: e.clientY, sessionId: s.id }
}

function closeContextMenu() { contextMenu.value.show = false }

function handleDelete(id) {
  if (confirm('确定删除此会话？')) chat.deleteSession(id)
  closeContextMenu()
}
</script>

<template>
  <div
    class="bg-gray-50 dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col transition-all duration-300"
    :class="chat.sidebarOpen ? 'w-64' : 'w-0 overflow-hidden'"
    @click.self="closeContextMenu()"
  >
    <!-- 顶部 Logo -->
    <div class="p-4 border-b border-gray-200 dark:border-gray-700">
      <div class="flex items-center gap-2">
        <div class="w-8 h-8 bg-green-500 rounded-lg flex items-center justify-center text-white font-bold text-sm">V</div>
        <span class="font-semibold text-gray-800 dark:text-gray-200">Vermes</span>
      </div>
    </div>

    <!-- 新会话按钮 -->
    <div class="p-3">
      <button
        @click="chat.createSession('新会话')"
        class="w-full px-4 py-2 bg-green-500 hover:bg-green-600 text-white rounded-lg text-sm font-medium transition"
      >＋ 新会话</button>
    </div>

    <!-- 会话列表 -->
    <div class="flex-1 overflow-y-auto" @click="closeContextMenu()">
      <div
        v-for="s in chat.sessions" :key="s.id"
        @click="chat.switchSession(s.id)"
        @contextmenu.prevent="onContextMenu($event, s)"
        class="px-3 py-2 mx-2 rounded-lg cursor-pointer text-sm transition group relative"
        :class="s.id === chat.currentSessionId
          ? 'bg-green-100 dark:bg-green-900 text-green-800 dark:text-green-200'
          : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300'"
      >
        <div class="truncate font-medium">{{ s.name || '新会话' }}</div>
        <div class="text-xs text-gray-400 mt-0.5 flex justify-between items-center">
          <span>{{ formatTime(s.createdAt) }}</span>
          <button
            @click.stop="handleDelete(s.id)"
            class="opacity-0 group-hover:opacity-100 text-red-400 hover:text-red-600 transition ml-1"
            title="删除会话"
          >×</button>
        </div>
      </div>
      <div v-if="chat.sessions.length === 0" class="text-center text-gray-400 dark:text-gray-500 text-xs py-6">暂无会话</div>
    </div>

    <!-- 底部工具栏 -->
    <div class="p-3 border-t border-gray-200 dark:border-gray-700 flex gap-2">
      <button @click="chat.toggleTheme()" class="flex-1 px-3 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition" :title="chat.theme === 'dark' ? '浅色模式' : '深色模式'">
        {{ chat.theme === 'dark' ? '☀️' : '🌙' }}
      </button>
      <button @click="goSettings()" class="flex-1 px-3 py-2 rounded-lg text-sm bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition" title="设置">
        ⚙️
      </button>
    </div>
  </div>

  <!-- 右键菜单 -->
  <Teleport to="body">
    <div v-if="contextMenu.show"
      :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
      class="fixed z-50 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-xl py-1 min-w-[120px]"
      @click.stop
    >
      <button @click="handleDelete(contextMenu.sessionId)" class="w-full px-3 py-1.5 text-left text-sm text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition">🗑 删除会话</button>
    </div>
  </Teleport>
</template>
