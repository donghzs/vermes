<script setup>
import { computed, onMounted, ref } from 'vue'
import Sidebar from './components/Sidebar.vue'
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
    <div v-if="update.hasUpdate" class="bg-blue-500 text-white text-center py-2 px-4 text-sm flex items-center justify-center gap-2 shrink-0">
      <span>🎉 发现新版本 v{{ update.latestVersion }}</span>
      <a href="https://vbit.top/vermes" target="_blank" class="underline hover:no-underline font-medium">立即更新</a>
      <button @click="update.hasUpdate = false" class="ml-2 text-white/60 hover:text-white">✕</button>
    </div>
    <div class="flex flex-1 overflow-hidden">
      <Sidebar />
      <div class="flex-1 flex flex-col h-full overflow-hidden">
        <router-view />
      </div>
    </div>
  </div>
</template>
