<script setup>
import { computed, onMounted, ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
import ToastContainer from './components/ToastContainer.vue'
import { useChatStore } from './stores/chat'
import { useUpdateStore } from './stores/update'

const chat = useChatStore()
const update = useUpdateStore()
const theme = computed(() => chat.theme)
const loading = ref(true)

onMounted(async () => {
  try {
    await chat.init()
    // 后台检查更新（不阻塞加载）
    update.checkUpdate()
  } catch(e) {
    console.error('chat.init() failed:', e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-if="loading" class="flex items-center justify-center h-screen text-gray-400 bg-white dark:bg-gray-900">
    <div class="text-center">
      <div class="text-3xl mb-4">V</div>
      <div>正在加载...</div>
    </div>
  </div>
  <div v-else class="flex flex-col h-screen bg-white dark:bg-gray-900" :data-theme="theme">
    <!-- 更新提示条 -->
    <div v-if="update.hasUpdate" class="bg-blue-500 text-white text-center py-2 px-4 text-sm shrink-0">
      <!-- 未更新状态 -->
      <div v-if="!update.updating" class="flex items-center justify-center gap-2">
        <span>🎉 发现新版本 v{{ update.latestVersion }}</span>
        <button @click="update.startUpdate()" class="underline hover:no-underline font-medium">立即更新</button>
        <button @click="update.dismissUpdate()" class="ml-2 text-white/60 hover:text-white">✕</button>
      </div>
      <!-- 更新中状态 -->
      <div v-else class="flex flex-col items-center gap-1">
        <div class="flex items-center gap-2">
          <span v-if="update.updateStatus === 'downloading'">⬇️ {{ update.updateMessage }}</span>
          <span v-else-if="update.updateStatus === 'extracting'">📦 正在解压...</span>
          <span v-else-if="update.updateStatus === 'verifying'">🔍 正在验证校验和...</span>
          <span v-else-if="update.updateStatus === 'backing_up'">💾 正在备份...</span>
          <span v-else-if="update.updateStatus === 'applying'">⚙️ 正在应用更新...</span>
          <span v-else-if="update.updateStatus === 'done'">✅ {{ update.updateMessage }}</span>
          <span v-else-if="update.updateStatus === 'error'">❌ {{ update.updateError }}</span>
        </div>
        <!-- 进度条 -->
        <div v-if="update.updateStatus !== 'error'" class="w-48 h-1.5 bg-white/30 rounded-full overflow-hidden">
          <div class="h-full bg-white rounded-full transition-all duration-300" :style="{width: update.updateProgress + '%'}"></div>
        </div>
        <!-- 详细信息 -->
        <div v-if="update.updateStatus === 'downloading'" class="text-xs text-white/70">
          {{ update.formatBytes(update.downloadedBytes) }}
          <span v-if="update.totalBytes"> / {{ update.formatBytes(update.totalBytes) }}</span>
          <span v-if="update.speedBps"> · {{ update.formatSpeed(update.speedBps) }}</span>
          <span v-if="update.etaSeconds > 0"> · 剩余 {{ update.formatEta(update.etaSeconds) }}</span>
        </div>
      </div>
    </div>
    <div class="flex flex-1 overflow-hidden">
      <Sidebar />
      <div class="flex-1 flex flex-col h-full overflow-hidden">
        <router-view />
      </div>
    </div>
    <ToastContainer />
  </div>
</template>
