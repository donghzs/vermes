<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

const emit = defineEmits(['loginSuccess', 'loginError'])

// ── 状态 ──
const showModal = ref(false)
const isPywebview = typeof window !== 'undefined' && !!window.pywebview
const qrError = ref('')
const wechatState = ref('')
let pollTimer = null
let pollTimeout = null
let isPollingActive = false

// ── 打开微信登录 ──
async function openLogin() {
  qrError.value = ''
  if (isPywebview) {
    // pywebview 原生窗口
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
        emit('loginError', qrError.value)
      }
    } catch(e) {
      qrError.value = '登录失败，请重试'
      emit('loginError', qrError.value)
    }
  } else {
    // 浏览器弹窗模式
    showModal.value = true
    try {
      const res = await fetch('/api/wechat/qrurl', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
      const data = await res.json()
      wechatState.value = data.state
      const w = 420, h = 620
      const left = Math.round((screen.width - w) / 2)
      const top = Math.round((screen.height - h) / 2)
      window.open(data.url, 'wechat-login', `width=${w},height=${h},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no`)
      startPolling()
    } catch(e) {
      qrError.value = '加载失败，请重试'
      emit('loginError', qrError.value)
    }
  }
}

// ── 轮询结果（pywebview模式） ──
async function pollForResult() {
  const state = wechatState.value
  if (!state) { qrError.value = '登录失败：state 丢失'; return }
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 1000))
    try {
      const resp = await fetch(`/api/wechat/poll?state=${state}`)
      const data = await resp.json()
      if (data.expired) { qrError.value = '登录已过期，请重试'; return }
      if (data.scanned && data.token) { handleLoginSuccess(data); return }
    } catch(e) {}
  }
  qrError.value = '登录超时，请重试'
}

// ── 轮询（浏览器模式） ──
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
        handleLoginSuccess(data)
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

// ── 登录成功处理 ──
function handleLoginSuccess(data) {
  showModal.value = false
  chat.showQuotaModal = false
  stopPolling()
  
  // 保存登录信息（先清理旧数据防止身份错位）
  localStorage.removeItem('vermes_wechat_openid')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_quota')
  localStorage.setItem('vermes_wechat_token', data.token)
  if (data.openid) localStorage.setItem('vermes_wechat_openid', data.openid)
  if (data.userName) localStorage.setItem('vermes_wechat_name', data.userName)
  if (data.userAvatar) localStorage.setItem('vermes_wechat_avatar', data.userAvatar)
  
  // 同步到后端
  fetch('/api/env', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: 'VBIT_API_KEY', value: data.token })
  }).catch(() => {})
  
  // 通知父组件
  emit('loginSuccess', {
    isLoggedIn: true,
    userName: data.userName || '微信用户',
    userAvatar: data.userAvatar || ''
  })
  
  // 创建默认会话
  if (chat.sessions.length === 0) chat.createSession('新会话')
}

// ── 登出 ──
function logout() {
  localStorage.removeItem('vermes_token')
  localStorage.removeItem('vermes_wechat_token')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_wechat_openid')
  chat.stopPolling?.()
  console.log('[Vermes🔐] 已退出微信登录')
  emit('loginSuccess', { isLoggedIn: false, userName: '访客', userAvatar: '' })
}

// ── postMessage监听（微信回调窗口通知） ──
const _postMessageHandler = (e) => {
  if (e.origin && e.origin !== 'https://vbit.top') return
  if (e.data?.type === 'wechat_callback' && e.data?.token) {
    stopPolling()
    handleLoginSuccess(e.data)
  }
}

onMounted(() => {
  window.addEventListener('message', _postMessageHandler)
})

onUnmounted(() => {
  window.removeEventListener('message', _postMessageHandler)
  stopPolling()
})

// ── 暴露方法给父组件 ──
defineExpose({ openLogin, logout })
</script>

<template>
  <!-- 微信登录弹窗（浏览器开发模式） -->
  <div v-if="showModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showModal = false; stopPolling()">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">
      <button @click="showModal = false; stopPolling()" class="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition text-lg">✕</button>
      <h3 class="font-bold text-lg mb-3">微信登录</h3>
      <div class="text-5xl mb-4">💬</div>
      <p class="text-sm text-gray-600 dark:text-gray-300 mb-4">请在弹出的窗口中完成微信授权</p>
      <p v-if="qrError" class="text-xs text-red-400 mt-2">{{ qrError }}</p>
    </div>
  </div>
</template>
