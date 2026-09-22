<script setup>
import { computed, onMounted, ref } from 'vue'
import ErrorBoundary from './components/ErrorBoundary.vue'
import Sidebar from './components/Sidebar.vue'
import ToastContainer from './components/ToastContainer.vue'
import ApprovalDialog from './components/ApprovalDialog.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
// ToolSkillDrawer 已由全宽 AgentManagement.vue 取代
import ArtifactPanel from './components/ArtifactPanel.vue'
import UpdateDialog from './components/UpdateDialog.vue'
import CommandPalette from './components/CommandPalette.vue'
import PrereqBanner from './components/PrereqBanner.vue'
import { useChatStore } from './stores/chat'
import { useBackendConnectionStore } from './stores/backendConnection'
import { useUpdateStore } from './stores/update'

const chat = useChatStore()
const backendConn = useBackendConnectionStore()
const update = useUpdateStore()
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

// P1-5（2026-09-22）：原实现把「profile 错配」与「崩溃回滚」渲染成两条独立横幅，
// 同时命中时顶部堆叠两行、且逐条 dismiss。这里合并为单条：
// tone 取更严重的一方（回滚 red > 错配 amber），一次 dismiss 全部收起，顶部恒占 ≤1 行。
const bannerVisible = computed(() => profileMismatch.value || !!rolledBackVersion.value)
const bannerTone = computed(() => (rolledBackVersion.value ? 'red' : 'amber'))
const bannerText = computed(() => {
  const parts = []
  if (rolledBackVersion.value) {
    parts.push(`检测到上次启动异常，已自动回滚到 v${rolledBackVersion.value}。如反复出现请联系支持。`)
  }
  if (profileMismatch.value) {
    parts.push('检测到 profile 配置不一致：当前激活 profile 与进程实际使用的 profile 不同，数据可能写入非预期位置。如无需保留旧目录数据可忽略；否则请在设置中校准 profile。')
  }
  return parts.join('　　|　　')
})
function dismissBanner() {
  profileMismatch.value = false
  rolledBackVersion.value = null
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
  // ── 应用自动更新：启动即检查（桌面端走 electron-updater，Web 走 version.json 轮询）──
  // checkUpdate 内部已做去重（checked）与桌面/Web 分支分流，失败静默不打断用户。
  update.checkUpdate().catch(() => {})
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
    <!-- G5 启动守卫 + Bug B 崩溃回滚：合并为单条顶部横幅（P1-5，顶部恒占 ≤1 行） -->
    <PrereqBanner
      :visible="bannerVisible"
      :tone="bannerTone"
      :text="bannerText"
      dismissible
      @dismiss="dismissBanner"
    />
    <ErrorBoundary>
      <div class="flex flex-1 overflow-hidden">
        <Sidebar />
        <div class="flex-1 flex flex-row h-full overflow-hidden min-w-0">
          <div class="flex-1 flex flex-col h-full overflow-hidden min-w-0">
            <router-view />
          </div>
          <!-- 产物右侧面板：与对话区并排，由 ChatHeader「详情面板」按钮控制 -->
          <ArtifactPanel />
        </div>
      </div>
      <ToastContainer />
      <ApprovalDialog />
      <ConfirmDialog />
      <UpdateDialog />
      <!-- ToolSkillDrawer 已由 /agents 路由取代 -->
      <CommandPalette />
    </ErrorBoundary>
  </div>
</template>
