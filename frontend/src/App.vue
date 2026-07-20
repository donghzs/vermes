<script setup>
import { computed, onMounted, ref } from 'vue'
import ErrorBoundary from './components/ErrorBoundary.vue'
import Sidebar from './components/Sidebar.vue'
import ToastContainer from './components/ToastContainer.vue'
import ApprovalDialog from './components/ApprovalDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { useChatStore } from './stores/chat'

const chat = useChatStore()
const theme = computed(() => chat.theme)
const loading = ref(true)

onMounted(async () => {
  try {
    await chat.init()
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
    <ErrorBoundary>
      <div class="flex flex-1 overflow-hidden">
        <Sidebar />
        <div class="flex-1 flex flex-col h-full overflow-hidden">
          <router-view />
        </div>
      </div>
      <ToastContainer />
      <ApprovalDialog />
      <ConfirmDialog />
    </ErrorBoundary>
  </div>
</template>
