<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore, SESSION_TEMPLATES } from '../stores/chat'
import { toast } from '../utils/toast'
import ChatHeader from './ChatHeader.vue'
import MessageList from './MessageList.vue'
import ChatInput from './ChatInput.vue'
import QuotaModal from './QuotaModal.vue'
import HistoryPanel from './HistoryPanel.vue'

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

// ── 登录状态（由子组件通过事件更新） ──
const isLoggedIn = ref(!!(localStorage.getItem('vermes_token') || localStorage.getItem('vermes_wechat_token')))
const userAvatar = ref(localStorage.getItem('vermes_wechat_avatar') || '')
const userName = ref(localStorage.getItem('vermes_wechat_name') || '已登录')

// ── 配额状态 ──
const serverQuota = ref({ remaining: 200, total_limit: 200, spent_today: 0, bonus_points: 0, days_left: 31, trial_expired: false, is_wechat: false })
const referralCode = ref('')

async function refreshQuota() {
  try {
    const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
    if (!wechatOpenid) {
      serverQuota.value = { remaining: 0, total_limit: 500, spent_today: 0, bonus_points: 0, days_left: Math.max(0, Math.ceil((new Date('2026-06-26') - new Date()) / 86400000)), trial_expired: false, is_wechat: false, need_login: true }
      return
    }
    const resp = await fetch('/api/quota/check?wechat_openid=' + encodeURIComponent(wechatOpenid))
    const data = await resp.json()
    if (data.success) serverQuota.value = data.data
  } catch (e) { console.warn('[Vermes] 刷新配额失败:', e) }
}

async function loadReferralCode() {
  try {
    const wechatOpenid = localStorage.getItem('vermes_wechat_openid')
    if (!wechatOpenid) return
    const resp = await fetch('/api/quota/referral/code?wechat_openid=' + encodeURIComponent(wechatOpenid))
    const data = await resp.json()
    if (data.success) referralCode.value = data.data
  } catch (e) {}
}

const quotaDisplay = computed(() => {
  if (serverQuota.value.need_login) return { text: '🔐 登录后免费使用', remaining: 0 }
  if (serverQuota.value.trial_expired) return { text: '试用已结束', remaining: 0 }
  const q = serverQuota.value
  return { text: `✨ ${q.remaining}/${q.total_limit} 积分 · ${q.days_left}天`, remaining: q.remaining }
})

// ── 登出 ──
function logout() {
  localStorage.removeItem('vermes_token')
  localStorage.removeItem('vermes_wechat_token')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_wechat_openid')
  isLoggedIn.value = false
  userAvatar.value = ''
  userName.value = '访客'
  chat.stopPolling?.()
  console.log('[Vermes🔐] 已退出微信登录')
}

// ── 微信登录 ──
const showWeChatModal = ref(false)
const isPywebview = typeof window !== 'undefined' && !!window.pywebview
const qrError = ref('')
let pollTimer = null
let pollTimeout = null
let isPollingActive = false
let wechatState = ref('')

async function openWeChatQR() {
  qrError.value = ''
  if (isPywebview) {
    try {
      const loginState = Date.now().toString()
      const result = await window.pywebview.api.open_oauth_window(
        'https://open.weixin.qq.com/connect/qrconnect?appid=wxfd680141e93226be&redirect_uri=' +
        encodeURIComponent('https://vbit.top/api/wechat/callback') +
        '&response_type=code&scope=snsapi_login&state=' + loginState + '#wechat_redirect'
      )
      if (result && result.success) {
        wechatState.value = result.state || loginState
        await pollForResult()
      } else {
        qrError.value = result?.error === 'timeout or cancelled' ? '已取消' : '登录失败'
      }
    } catch(e) { qrError.value = '登录失败，请重试' }
  } else {
    showWeChatModal.value = true
    try {
      const res = await fetch('/api/wechat/qrurl', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
      const data = await res.json()
      wechatState.value = data.state
      const w = 420, h = 620
      const left = Math.round((screen.width - w) / 2)
      const top = Math.round((screen.height - h) / 2)
      window.open(data.url, 'wechat-login', `width=${w},height=${h},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`)
      startPolling()
    } catch(e) { qrError.value = '加载失败，请重试' }
  }
}

async function pollForResult() {
  const state = wechatState.value
  if (!state) { qrError.value = '登录失败：state 丢失'; return }
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 1000))
    try {
      const resp = await fetch(`/api/wechat/poll?state=${state}`)
      const data = await resp.json()
      if (data.expired) { qrError.value = '登录已过期，请重试'; return }
      if (data.scanned && data.token) { onWeChatLogin(data); return }
    } catch(e) {}
  }
  qrError.value = '登录超时，请重试'
}

function startPolling() {
  stopPolling()
  isPollingActive = true
  pollTimer = setInterval(async () => {
    if (!isPollingActive) return
    try {
      const resp = await fetch(`/api/wechat/poll?state=${wechatState.value}`)
      const data = await resp.json()
      if (data.expired) { stopPolling(); return }
      if (data.scanned && data.token) {
        isPollingActive = false
        stopPolling()
        onWeChatLogin(data)
      }
    } catch(e) {}
  }, 2000)
  pollTimeout = setTimeout(() => stopPolling(), 5 * 60 * 1000)
}

function stopPolling() {
  isPollingActive = false
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (pollTimeout) { clearTimeout(pollTimeout); pollTimeout = null }
}

function onWeChatLogin(data) {
  showWeChatModal.value = false
  chat.showQuotaModal = false
  stopPolling()
  localStorage.setItem('vermes_wechat_token', data.token)
  if (data.openid) localStorage.setItem('vermes_wechat_openid', data.openid)
  fetch('/api/env', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: 'VBIT_API_KEY', value: data.token })
  }).catch(() => {})
  if (data.userName) localStorage.setItem('vermes_wechat_name', data.userName)
  if (data.userAvatar) localStorage.setItem('vermes_wechat_avatar', data.userAvatar)
  localStorage.removeItem('vermes_quota')
  isLoggedIn.value = true
  userName.value = data.userName || '微信用户'
  userAvatar.value = data.userAvatar || ''
  if (chat.sessions.length === 0) chat.createSession('新会话')
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
    await chat.sendMessage(input, files)
    console.log('[Vermes📤] send() → sendMessage() completed')
  } catch(e) {
    console.error('[Vermes📤] send() → sendMessage() error:', e)
    toast.error(getFriendlyError(e))
  }
}

// ── 推荐码复制 ──
function copyReferralCode() {
  if (!referralCode.value) return
  const text = `我在用 Vermes AI 助手，免费体验中！用我的推荐码 ${referralCode.value} 注册，我俩都能获得额外 200 积分/天。下载: https://vbit.top/vermes/#downloads`
  navigator.clipboard.writeText(text).then(() => {
    toast.success('✅ 推荐码已复制到剪贴板！分享给朋友即可获得 +200 积分/天')
  }).catch(() => {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    toast.success('✅ 推荐码已复制到剪贴板！')
  })
}

// ── 生命周期 ──
const _modelChangedHandler = (e) => {
  chat.currentModel = e.detail.model
  chat.currentProvider = e.detail.provider
}
const _quotaUpdatedHandler = () => refreshQuota()
const _postMessageHandler = (e) => {
  if (e.origin && e.origin !== 'https://vbit.top') return
  if (e.data?.type === 'wechat_callback' && e.data?.token) {
    stopPolling()
    onWeChatLogin(e.data)
  }
}

onMounted(() => {
  window.addEventListener('model-changed', _modelChangedHandler)
  window.addEventListener('quota-updated', _quotaUpdatedHandler)
  window.addEventListener('message', _postMessageHandler)
  refreshQuota()
  loadReferralCode()
})

onUnmounted(() => {
  window.removeEventListener('model-changed', _modelChangedHandler)
  window.removeEventListener('quota-updated', _quotaUpdatedHandler)
  window.removeEventListener('message', _postMessageHandler)
  stopPolling()
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
    <MessageList @quickStart="onQuickStart" />

    <!-- 输入区 -->
    <ChatInput ref="chatInputRef" @send="onSend" />

    <!-- 历史记录面板 -->
    <HistoryPanel ref="historyPanelRef" />

    <!-- 微信登录弹窗（浏览器开发模式） -->
    <div v-if="showWeChatModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showWeChatModal = false; stopPolling()">
      <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">
        <button @click="showWeChatModal = false; stopPolling()" class="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition text-lg">✕</button>
        <h3 class="font-bold text-lg mb-3">微信登录</h3>
        <div class="text-5xl mb-4">💬</div>
        <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">请在弹出的窗口中完成微信授权</p>
        <p v-if="qrError" class="text-xs text-red-400 mt-2">{{ qrError }}</p>
      </div>
    </div>

    <!-- 配额弹窗 -->
    <QuotaModal
      :serverQuota="serverQuota"
      :referralCode="referralCode"
      @wechatLogin="openWeChatQR"
      @copyReferralCode="copyReferralCode"
    />
  </div>
</template>
