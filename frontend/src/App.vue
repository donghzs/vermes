<script setup>
import { computed, onMounted, ref } from 'vue'
import ErrorBoundary from './components/ErrorBoundary.vue'
import Sidebar from './components/Sidebar.vue'
import ToastContainer from './components/ToastContainer.vue'
import ApprovalDialog from './components/ApprovalDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import ToolSkillDrawer from './components/ToolSkillDrawer.vue'
import { useChatStore } from './stores/chat'
import { useBackendConnectionStore } from './stores/backendConnection'

const chat = useChatStore()
const backendConn = useBackendConnectionStore()
const theme = computed(() => chat.theme)
const loading = ref(true)

// G5 启动守卫：profile 错配横幅（不阻断，仅提醒）
// 数据本身未坏，但当前激活 profile 与进程实际使用的 profile 不一致，
// 数据可能写错位置。后端 /health 已透传 integrity.profile_mismatch（c2 落地）。
// Bug B: 同时检查 rolled_back，崩溃看门狗自动回滚后通知用户
const profileMismatch = ref(false)
const rolledBackVersion = ref(null)
async function checkProfileMismatch() {
  if (!(typeof window !== 'undefined' && window.vermes?.isDesktop)) return
  try {
    const r = await fetch('/health')
    const d = await r.json()
    if (d && d.integrity && d.integrity.profile_mismatch) {
      profileMismatch.value = true
    }
    if (d && d.rolled_back) {
      rolledBackVersion.value = d.rolled_back
    }
  } catch (_) {}
}

onMounted(async () => {
  // A.4.3: 订阅主进程后端连接状态广播（掉线/重连中/恢复 → 全局 store）
  backendConn.init()
  try {
    await chat.init()
  } catch(e) {
    console.error('chat.init() failed:', e)
  } finally {
    loading.value = false
  }
  // G5：主界面加载后判定（splash 阶段已判定 corrupt/missing 阻断，此处仅横幅）
  checkProfileMismatch()
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
    <!-- G5 启动守卫：profile 错配横幅（不阻断，仅提醒） -->
    <div v-if="profileMismatch" class="bg-amber-50 dark:bg-amber-900/30 border-b border-amber-300 dark:border-amber-700 px-4 py-2 text-sm text-amber-800 dark:text-amber-200 flex items-center justify-between">
      <span>⚠️ 检测到 profile 配置不一致：当前激活 profile 与进程实际使用的 profile 不同，数据可能写入非预期位置。如无需保留旧目录数据可忽略；否则请在设置中校准 profile。</span>
      <button class="ml-3 shrink-0 text-amber-600 dark:text-amber-400 underline" @click="profileMismatch = false">知道了</button>
    </div>
    <!-- Bug B: 崩溃看门狗自动回滚通知 -->
    <div v-if="rolledBackVersion" class="bg-orange-50 dark:bg-orange-900/30 border-b border-orange-300 dark:border-orange-700 px-4 py-2 text-sm text-orange-800 dark:text-orange-200 flex items-center justify-between">
      <span>⚠️ 检测到上次启动异常，已自动回滚到 v{{ rolledBackVersion }}。如反复出现请联系支持。</span>
      <button class="ml-3 shrink-0 text-orange-600 dark:text-orange-400 underline" @click="rolledBackVersion = null">知道了</button>
    </div>
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
      <ToolSkillDrawer />
    </ErrorBoundary>
  </div>
</template>
