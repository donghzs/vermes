<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore, SESSION_TEMPLATES } from '../stores/chat'
import { toast } from '../utils/toast'
import { useQuota } from '../composables/useQuota'
import ChatHeader from './ChatHeader.vue'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import QuotaModal from './QuotaModal.vue'
import HistoryPanel from './HistoryPanel.vue'
import WechatLogin from './WechatLogin.vue'

const router = useRouter()
const chat = useChatStore()

// ── P0-5: 错误友好化映射 ──
const ERROR_MAP = {
  'NetworkError': '🌐 网络连接失败，请检查网络后重试',
  'Failed to fetch': '🌐 网络连接失败，请检查网络后重试',
  'fetch failed': '🌐 网络连接失败，请检查网络后重试',
  '401': '🔑 API Key 无效或已过期，请到设置页重新配置',
  'Unauthorized': '🔑 API Key 无效或已过期，请到设置页重新配置',
  '429': '⏳ 请求太频繁，请稍后再试',
  'Too Many Requests': '⏳ 请求太频繁，请稍后再试',
  '402': '💰 免费额度已用完',
  'insufficient_quota': '💰 免费额度已用完',
  '500': '⚠️ 服务暂时不可用，请切换其他模型或稍后重试',
  '502': '⚠️ 服务暂时不可用，请稍后重试',
  '503': '⚠️ 服务暂时不可用，请稍后重试',
  'timeout': '⏱️ 请求超时，请检查网络或切换模型',
}

function getFriendlyError(error) {
  const msg = error.message || String(error)
  for (const [key, friendly] of Object.entries(ERROR_MAP)) {
    if (msg.includes(key)) return friendly
  }
  return '❌ 出了点问题，请重试'
}

// ── 引用子组件 ──
const chatInputRef = ref(null)
const historyPanelRef = ref(null)
const wechatLoginRef = ref(null)

// ── 登录状态（由WechatLogin组件通过事件更新） ──
const isLoggedIn = ref(!!(localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')))
const userAvatar = ref(localStorage.getItem('vermes_wechat_avatar') || '')
const userName = ref(localStorage.getItem('vermes_wechat_name') || '已登录')

// ── 配额状态（useQuota composable） ──
const { serverQuota, referralCode, quotaDisplay, refreshQuota, copyReferralCode, setupQuotaEvents, teardownQuotaEvents } = useQuota()

// ── 微信登录事件处理 ──
function onWechatLoginSuccess(data) {
  isLoggedIn.value = data.isLoggedIn
  userName.value = data.userName
  userAvatar.value = data.userAvatar
  refreshQuota()
}

function onWechatLoginError(error) {
  toast.error(error)
}

function openWeChatQR() {
  wechatLoginRef.value?.openLogin()
}

function logout() {
  wechatLoginRef.value?.logout()
}

// ── 历史面板切换 ──
function toggleHistory() {
  historyPanelRef.value?.toggle()
}

// ── 快速开始 ──
function onQuickStart(text) {
  if (chatInputRef.value) {
    chatInputRef.value.inputText = text
    chatInputRef.value.$el.querySelector('textarea')?.focus()
  }
}

// ── 发送消息 ──
async function onSend(input, files) {
  const model = chat.currentModel
  const provider = chat.currentProvider
  console.log('[Vermes📤] send() input:', JSON.stringify(input), 'files:', files?.length, 'model:', model, 'provider:', provider)
  if ((!input && !files?.length) || chat.loading) return
  try {
    // P3-8: 多模型对比模式
    if (chat.compareModels && chat.compareModels.length >= 2) {
      await chat.sendCompareMessage(input, files, chat.compareModels)
    } else {
      await chat.sendMessage(input, files)
    }
    console.log('[Vermes📤] send() → completed')
  } catch(e) {
    console.error('[Vermes📤] send() error:', e)
    toast.error(getFriendlyError(e))
  }
}

// ── P3-7: 消息编辑 ──
function onEditMessage(msg) {
  if (!chatInputRef.value) return
  chatInputRef.value.inputText = msg.content || ''
  // 聚焦输入框并将光标移到末尾
  const ta = chatInputRef.value.inputRef
  if (ta) {
    ta.focus()
    ta.setSelectionRange(ta.value.length, ta.value.length)
  }
}

// ── 生命周期 ──
const _modelChangedHandler = (e) => {
  chat.currentModel = e.detail.model
  chat.currentProvider = e.detail.provider
}

onMounted(() => {
  window.addEventListener('model-changed', _modelChangedHandler)
  setupQuotaEvents()
})

onUnmounted(() => {
  window.removeEventListener('model-changed', _modelChangedHandler)
  teardownQuotaEvents()
})
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 顶部栏 -->
    <ChatHeader
      :isLoggedIn="isLoggedIn"
      :userAvatar="userAvatar"
      :userName="userName"
      :quotaDisplay="quotaDisplay"
      @logout="logout"
      @openWeChatQR="openWeChatQR"
      @toggleHistory="toggleHistory"
    />

    <!-- 消息列表 -->
    <MessageList @quickStart="onQuickStart" @editMessage="onEditMessage" />

    <!-- 输入区 -->
    <ChatInput ref="chatInputRef" @send="onSend" />

    <!-- 历史记录面板 -->
    <HistoryPanel ref="historyPanelRef" />

    <!-- 微信登录组件 -->
    <WechatLogin 
      ref="wechatLoginRef"
      @loginSuccess="onWechatLoginSuccess"
      @loginError="onWechatLoginError"
    />

    <!-- 配额弹窗 -->
    <QuotaModal
      :serverQuota="serverQuota"
      :referralCode="referralCode"
      @wechatLogin="openWeChatQR"
      @copyReferralCode="copyReferralCode"
    />
  </div>
</template>
