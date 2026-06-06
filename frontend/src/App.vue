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
const showUpdateDetail = ref(false)

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
    <!-- 统一更新提示条 -->
    <div v-if="update.hasUpdate || update.agentHasUpdate" class="bg-emerald-600 text-white text-center py-2 px-4 text-sm shrink-0">
      <div class="flex items-center justify-center gap-3 flex-wrap">
        <!-- 壳更新 -->
        <span v-if="update.hasUpdate">🎉 新版本 v{{ update.latestVersion }}</span>
        <button v-if="update.hasUpdate" @click="update.startUpdate()" class="bg-white text-emerald-600 px-2 py-0.5 rounded font-medium hover:bg-emerald-50 text-xs">更新壳</button>
        <!-- 分隔 -->
        <span v-if="update.hasUpdate && update.agentHasUpdate" class="text-white/40">|</span>
        <!-- Agent 框架更新 -->
        <span v-if="update.agentHasUpdate">🧠 框架 v{{ update.agentLatestVersion }}</span>
        <button v-if="update.agentHasUpdate" @click="update.startAgentUpdate()" class="bg-white text-emerald-600 px-2 py-0.5 rounded font-medium hover:bg-emerald-50 text-xs">更新框架</button>
        <!-- 关闭 -->
        <button @click="update.hasUpdate && update.dismissUpdate(); update.agentHasUpdate && update.dismissAgentUpdate()" class="text-white/60 hover:text-white ml-1">✕</button>
      </div>
      <!-- 更新详情 -->
      <div v-if="showUpdateDetail" class="mt-1 text-xs text-white/80 max-w-lg mx-auto text-left leading-relaxed">
        <div v-if="update.releaseNotes" v-for="(line, i) in update.releaseNotes.split('\n')" :key="'shell-'+i" class="flex items-start gap-1">
          <span class="text-white/50 mt-0.5">•</span><span>{{ line }}</span>
        </div>
        <div v-if="update.agentChangelog?.length" v-for="(line, i) in update.agentChangelog" :key="'agent-'+i" class="flex items-start gap-1">
          <span class="text-white/50 mt-0.5">•</span><span>{{ line }}</span>
        </div>
      </div>
      <!-- 更新进度 -->
      <div v-if="update.updating" class="mt-1 flex flex-col items-center gap-1">
        <span class="text-xs">{{ update.updateMessage }}</span>
        <div v-if="update.updateStatus !== 'error'" class="w-48 h-1.5 bg-white/30 rounded-full overflow-hidden">
          <div class="h-full bg-white rounded-full transition-all duration-300" :style="{width: update.updateProgress + '%'}"></div>
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
