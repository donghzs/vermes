<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()
const emit = defineEmits(['loginSuccess', 'loginError'])

const showModal = ref(false)
const qrError = ref('')
const wechatState = ref('')
const isElectron = ref(false)
const loginHint = ref('')
let pollTimer = null
let pollTimeout = null
let isPollingActive = false
let oauthWindow = null

function checkElectron() {
  return typeof window !== 'undefined' && !!window.vermes?.isDesktop
}

// ── 打开微信登录 ──
async function openLogin() {
  qrError.value = ''
  isElectron.value = checkElectron()

  // 先检查本地是否已有有效登录态
  const existingToken = localStorage.getItem('vermes_wechat_token')
  if (existingToken) {
    try {
      const checkResp = await fetch('/api/quota/check', {
        headers: { 'X-WeChat-Openid': localStorage.getItem('vermes_wechat_openid') || '' }
      })
      if (checkResp.ok) {
        const checkData = await checkResp.json()
        if (checkData.success) {
          // token 仍然有效，直接登录成功
          emit('loginSuccess', {
            isLoggedIn: true,
            userName: localStorage.getItem('vermes_wechat_name') || '微信用户',
            userAvatar: localStorage.getItem('vermes_wechat_avatar') || '',
          })
          return
        }
      }
    } catch {}
    // token 无效，清除后继续 OAuth 流程
    localStorage.removeItem('vermes_wechat_token')
  }

  if (isElectron.value) {
    // === Electron 模式 ===
    // 1. 调 /api/wechat/qrurl 拿 vbit.top 注册好的 state + OAuth URL
    // 2. IPC → 主进程打开 BrowserWindow 子窗口
    // 3. 扫码后 vbit.top 回调 → 前端轮询拿 token
    showModal.value = true
    try {
      const res = await fetch('/api/wechat/qrurl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      const data = await res.json()
      if (!data.state || !data.url) throw new Error('No state/url from vbit.top')
      wechatState.value = data.state
      // 传完整 OAuth URL（含 vbit.top 注册好的 state）给 Electron IPC
      const result = await window.vermes.wechatLogin(data.url)
      if (result && result.success) {
        loginHint.value = '扫码成功，正在验证登录状态…'
        startPolling()
      } else {
        showModal.value = false
        const errMap = { cancelled: '您取消了登录', timeout: '登录超时，请重试' }
        qrError.value = errMap[result?.error] || '登录未完成，请重试'
        emit('loginError', qrError.value)
      }
    } catch (e) {
      showModal.value = false
      qrError.value = '登录失败，请重试'
      emit('loginError', qrError.value)
    }
  } else {
    // === 浏览器模式 ===
    // 不用 iframe（sandbox/CSP/X-Frame-Options 全都会拦截）
    // 也不在模态框内展示（WeChat URL 是 HTML 不是图片）
    // 方案：window.open() 打开系统浏览器 → 轮询后端拿 token
    try {
      const res = await fetch('/api/wechat/qrurl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      })
      const data = await res.json()
      if (!data.state || !data.url) throw new Error('No state/url')
      wechatState.value = data.state

      // 在系统浏览器中打开微信扫码页
      oauthWindow = window.open(data.url, 'wechat_oauth', 'width=500,height=700')
      if (!oauthWindow) {
        // 弹窗被拦截 — 提示用户手动打开
        loginHint.value = '请点击下方链接在浏览器中打开，然后用微信扫码'
      } else {
        loginHint.value = '请在打开的浏览器窗口中扫码登录'
      }
      showModal.value = true
      startPolling()
    } catch (e) {
      qrError.value = '加载失败，请重试'
      emit('loginError', qrError.value)
    }
  }
}

// ── 轮询后端：vbit.top 回调后会把 token 写入 state 对应记录 ──
function startPolling() {
  stopPolling()
  isPollingActive = true
  let pollCount = 0
  pollTimer = setInterval(async () => {
    if (!isPollingActive) return
    pollCount++
    try {
      const resp = await fetch(`/api/wechat/poll?state=${wechatState.value}`)
      const data = await resp.json()
      if (data.expired) {
        stopPolling()
        showModal.value = false
        qrError.value = '二维码已过期，请重新登录'
        return
      }
      if (data.scanned && data.token) {
        isPollingActive = false
        stopPolling()
        // 关闭浏览器弹窗
        try { oauthWindow?.close() } catch (_) {}
        handleLoginSuccess(data)
      }
      // 超过 3 分钟提示
      if (pollCount > 90) {
        loginHint.value = '等待扫码中…请确保已在浏览器中完成扫码'
      }
    } catch (_) {}
  }, 2000)
  pollTimeout = setTimeout(() => {
    stopPolling()
    showModal.value = false
    qrError.value = '登录超时，请重试'
  }, 5 * 60 * 1000)
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

  localStorage.removeItem('vermes_wechat_openid')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_quota')
  try { localStorage.setItem('vermes_wechat_token', data.token) } catch (_) {}
  if (data.openid) try { localStorage.setItem('vermes_wechat_openid', data.openid) } catch (_) {}
  if (data.userName) try { localStorage.setItem('vermes_wechat_name', data.userName) } catch (_) {}
  if (data.userAvatar) try { localStorage.setItem('vermes_wechat_avatar', data.userAvatar) } catch (_) {}

  // P0-c 加固后 /api/env 需携带 session token（裸 fetch 不走 api.js 封装，否则 401）
  const envHeaders = {
    'Content-Type': 'application/json',
    'X-Vermes-Session-Token': (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || '',
  }
  fetch('/api/env', {
    method: 'PUT',
    headers: envHeaders,
    body: JSON.stringify({ key: 'VBIT_API_KEY', value: data.token })
  }).catch(() => {})

  // 通知全局（WelcomeGuide 等组件需要感知登录状态）
  window.dispatchEvent(new CustomEvent('wechat-login-success', { detail: data }))

  emit('loginSuccess', {
    isLoggedIn: true,
    userName: data.userName || '微信用户',
    userAvatar: data.userAvatar || ''
  })

  if (chat.sessions.length === 0) chat.createSession('新 Agent')
}

// ── 登出 ──
function logout() {
  localStorage.removeItem('vermes_token')
  localStorage.removeItem('vermes_wechat_token')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_wechat_openid')
  stopPolling()
  try { oauthWindow?.close() } catch (_) {}
  emit('loginSuccess', { isLoggedIn: false, userName: '访客', userAvatar: '' })
}

// postMessage 备用（如果 vbit.top 回调页支持）
const _postMessageHandler = (e) => {
  if (e.origin !== 'https://vbit.top') return
  if (e.data?.type === 'wechat_callback' && e.data?.token) {
    stopPolling()
    handleLoginSuccess(e.data)
  }
}

onMounted(() => window.addEventListener('message', _postMessageHandler))
onUnmounted(() => {
  window.removeEventListener('message', _postMessageHandler)
  stopPolling()
  try { oauthWindow?.close() } catch (_) {}
})

defineExpose({ openLogin, logout })
</script>

<template>
  <div
    v-if="showModal"
    class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
    @click.self="showModal = false; stopPolling(); try { oauthWindow?.close() } catch (_) {}"
  >
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center shadow-xl">
      <button
        @click="showModal = false; stopPolling(); try { oauthWindow?.close() } catch (_) {}"
        class="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition text-lg z-10"
      >✕</button>
      <h3 class="font-bold text-lg mb-3">微信登录</h3>
      <!-- 加载中 -->
      <div class="flex items-center justify-center h-40 flex-col gap-3">
        <div class="animate-spin rounded-full h-10 w-10 border-b-2 border-green-500"></div>
        <p class="text-sm text-gray-500">{{ loginHint || '正在准备登录…' }}</p>
      </div>
      <p v-if="qrError" class="text-xs text-red-400 mt-3">{{ qrError }}</p>
    </div>
  </div>
</template>
