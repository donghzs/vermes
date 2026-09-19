<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useNotifications } from '../stores/notifications'

const notif = useNotifications()
let timer = null

onMounted(() => {
  notif.syncChangeLedger()
  timer = setInterval(() => notif.syncChangeLedger(), 60000)
})
onUnmounted(() => { if (timer) clearInterval(timer) })

function fmt(ts) {
  try {
    return new Date(ts).toLocaleString('zh-CN', { hour12: false })
  } catch { return '' }
}
</script>

<template>
  <div class="relative">
    <button
      class="group relative flex items-center gap-1 px-2 py-0.5 rounded-full cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 transition"
      :class="notif.unreadCount.value > 0 ? 'text-amber-600 dark:text-amber-300' : 'text-gray-400'"
      title="通知中心"
      data-testid="notify-bell"
      @click="notif.panelOpen.value = !notif.panelOpen.value"
    >
      <span class="text-xs">🔔</span>
      <span
        v-if="notif.unreadCount.value > 0"
        class="text-[10px] font-mono"
        data-testid="notify-badge"
      >{{ notif.unreadCount.value }}</span>
    </button>

    <div
      v-if="notif.panelOpen.value"
      class="absolute right-0 top-full mt-2 z-50 w-80 max-h-96 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-xl p-3"
      data-testid="notify-panel"
    >
      <div class="flex items-center justify-between mb-2">
        <div class="text-sm font-semibold text-gray-700 dark:text-gray-200">通知中心</div>
        <div class="flex gap-2 text-[11px]">
          <button class="text-green-600 dark:text-green-400 hover:underline" @click="notif.markAllRead()">全部已读</button>
          <button class="text-gray-400 hover:underline" @click="notif.clearAll()">清空</button>
        </div>
      </div>

      <div class="mb-2 pb-2 border-b border-gray-100 dark:border-gray-700 grid grid-cols-2 gap-1">
        <label v-for="c in notif.CATEGORIES" :key="c.id" class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400">
          <input
            type="checkbox"
            class="accent-green-500"
            :checked="notif.prefs.value[c.id]"
            @change="notif.setPref(c.id, $event.target.checked)"
          />
          {{ c.label }}
        </label>
        <label class="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 col-span-2">
          <input
            type="checkbox"
            class="accent-green-500"
            data-testid="notify-system-toggle"
            :checked="!!notif.prefs.value.system_notify"
            :disabled="!notif.systemNotifySupported()"
            @change="notif.setPref('system_notify', $event.target.checked)"
          />
          系统通知（{{ notif.systemNotifySupported() ? '浏览器/桌面弹窗' : '当前环境不支持' }}）
        </label>
      </div>

      <div v-if="notif.items.value.length === 0" class="text-xs text-gray-400 text-center py-6">
        暂无通知
      </div>
      <div v-else class="space-y-1.5">
        <div
          v-for="it in notif.items.value"
          :key="it.id"
          class="px-2 py-1.5 rounded-lg border text-xs"
          :class="it.read
            ? 'border-gray-100 dark:border-gray-700 opacity-70'
            : 'border-amber-100 dark:border-amber-900/40 bg-amber-50/40 dark:bg-amber-900/10'"
        >
          <div class="flex items-start gap-1.5">
            <span>{{ it.level === 'error' ? '⚠️' : it.level === 'warning' ? '🛡' : '📌' }}</span>
            <div class="min-w-0 flex-1">
              <div class="text-gray-700 dark:text-gray-200 font-medium">{{ it.title }}</div>
              <div v-if="it.detail" class="text-[10px] text-gray-400 break-all mt-0.5">{{ it.detail }}</div>
              <div class="text-[10px] text-gray-400 mt-0.5">{{ fmt(it.ts) }}</div>
            </div>
            <button v-if="!it.read" class="shrink-0 text-[10px] text-green-600 hover:underline" @click="notif.markRead(it.id)">已读</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
