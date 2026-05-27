<script setup>
import { ref, watch, nextTick, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { getRemainingQuota, getWechatDailyQuota, getTrialDaysLeft, isTrialExpired, checkQuotaServer } from '../services/api'
import MarkdownIt from 'markdown-it'
import QRCode from 'qrcode'

const md = new MarkdownIt({ html: true, breaks: true, linkify: true })

const router = useRouter()
const chat = useChatStore()
const inputRef = ref(null)
const chatContainer = ref(null)

// 监听模型变更事件（来自 Settings 页面的「设为当前」）
onMounted(() => {
  const handler = (e) => {
    chat.currentModel = e.detail.model
    chat.currentProvider = e.detail.provider
  }
  window.addEventListener('model-changed', handler)
  window.addEventListener('quota-updated', () => refreshQuota())
})

// ✅ 登录状态
const isLoggedIn = ref(!!localStorage.getItem('vermes_token'))
const userAvatar = ref(localStorage.getItem('vermes_wechat_avatar') || '')
const userName = ref(localStorage.getItem('vermes_wechat_name') || '已登录')

// 生成二维码
const qrCodeDataUrl = ref('')
const wechatState = ref('')
const qrLoading = ref(false)
const qrError = ref('')

async function loadQR() {
  if (isLoggedIn.value) return
  qrLoading.value = true
  qrError.value = ''
  try {
    const res = await fetch('/api/wechat/qrurl', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    const data = await res.json()
    wechatState.value = data.state
    // 生成二维码图片（200x200，白色背景填充留白边）
    const qrUrl = await QRCode.toDataURL(data.url, {
      width: 280, margin: 2, color: { dark: '#000000', light: '#ffffff' }
    })
    qrCodeDataUrl.value = qrUrl
    startPolling()
  } catch(e) {
    console.error('[Vermes🔐] 加载二维码失败:', e)
    qrError.value = '加载失败，请重试'
  } finally {
    qrLoading.value = false
  }
}

// 登出功能
function logout() {
  localStorage.removeItem('vermes_token')
  localStorage.removeItem('vermes_wechat_token')
  localStorage.removeItem('vermes_wechat_name')
  localStorage.removeItem('vermes_wechat_avatar')
  localStorage.removeItem('vermes_wechat_openid')
  isLoggedIn.value = false
  userAvatar.value = ''
  userName.value = '已登录'
  qrCodeDataUrl.value = ''
  // 重新加载二维码
  loadQR()
  console.log('[Vermes🔐] 已退出微信登录')
}

const inputText = ref('')
const fileInput = ref(null)
const uploadedFiles = ref([])
const showModelSelect = ref(false)

// ── 服务端积分配额 ──
const serverQuota = ref({ remaining: 200, total_limit: 200, spent_today: 0, bonus_points: 0, days_left: 31, trial_expired: false, is_wechat: false })
const referralCode = ref('')

async function refreshQuota() {
  try {
    const deviceId = localStorage.getItem('vermes_device_id')
    if (!deviceId) return
    const resp = await checkQuotaServer(deviceId)
    if (resp.success) {
      serverQuota.value = resp.data
    }
  } catch (e) { console.warn('[Vermes] 刷新配额失败:', e) }
}

async function loadReferralCode() {
  try {
    const deviceId = localStorage.getItem('vermes_device_id')
    if (!deviceId) return
    const resp = await fetch(`/api/quota/referral/code?device_id=${encodeURIComponent(deviceId)}`)
    const data = await resp.json()
    if (data.success) referralCode.value = data.data.code
  } catch (e) {}
}

onMounted(() => {
  refreshQuota()
  loadReferralCode()
})

const quotaDisplay = computed(() => {
  if (serverQuota.value.trial_expired) return { text: '试用已结束', remaining: 0 }
  const q = serverQuota.value
  return { text: `✨ ${q.remaining}/${q.total_limit} 积分 · ${q.days_left}天`, remaining: q.remaining }
})

// ✅ App.vue 已调用 chat.init()，这里不需要重复

// 模型列表
const defaultModels = [
  { id: 'mimo-v2.5', name: '⚡ MiMo V2.5（小米）', provider: 'vbit.top' },
  { id: 'deepseek-v4-flash', name: '🚀 DeepSeek V4 Flash', provider: 'vbit.top' },
]

const models = computed(() => {
  try {
    const saved = localStorage.getItem('vermes-providers')
    if (saved) {
      const providers = JSON.parse(saved)
      const synced = []
      for (const p of providers) {
        if (p.models && p.models.length > 0) {
          for (const m of p.models) {
            synced.push({ id: m, name: m, provider: p.id, group: p.name })
          }
        }
      }
      if (synced.length > 0) return synced
    }
  } catch(e) {}
  return defaultModels
})

const modelGroups = computed(() => {
  const groups = {}
  for (const m of models.value) {
    const g = m.group || m.provider || '其他'
    if (!groups[g]) groups[g] = []
    groups[g].push(m)
  }
  return groups
})

function renderMd(content) {
  if (!content) return ''
  try { return md.render(content) } catch(e) { return content }
}

function quickStart(text) {
  inputText.value = text
  send()
}

// 微信扫码登录 - 弹窗打开官方 OAuth 页面
const showWeChatModal = ref(false)
// wechatState, qrCodeDataUrl, qrLoading, qrError already declared above

let pollTimer = null
async function openWeChatQR() {
  console.log('[Vermes🔐] 微信扫码登录...')
  showWeChatModal.value = true
  qrLoading.value = true
  qrError.value = ''
  try {
    const res = await fetch('/api/wechat/qrurl', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    const data = await res.json()
    wechatState.value = data.state
    // 在原生窗口内渲染二维码（不弹浏览器）
    const qrUrl = await QRCode.toDataURL(data.url, { width: 280, margin: 2, color: { dark: '#000000', light: '#ffffff' } })
    qrCodeDataUrl.value = qrUrl
    startPolling()
  } catch(e) {
    console.error('[Vermes🔐] 加载微信登录失败:', e)
    qrError.value = '加载失败，请重试'
  } finally {
    qrLoading.value = false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`/api/wechat/poll?state=${wechatState.value}`)
      const data = await resp.json()
      if (data.expired) {
        stopPolling()
        console.log('[Vermes🔐] 微信登录 session 已过期')
        return
      }
      if (data.scanned && data.token) {
        stopPolling()
        console.log('[Vermes🔐] 轮询获取到登录信息:', data.userName)
        onWeChatLogin(data)
      }
    } catch(e) {}
  }, 2000)
  setTimeout(() => stopPolling(), 5 * 60 * 1000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function onWeChatLogin(data) {
  showWeChatModal.value = false
  chat.showQuotaModal = false  // 关闭配额弹窗
  qrCodeDataUrl.value = ''
  // 关闭二维码弹窗
  showWeChatModal.value = false
  localStorage.setItem('vermes_wechat_token', data.token)
  // 同步到后端 .env，让聊天时后端能用这个 token
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
  // 登录后创建新会话
  if (chat.sessions.length === 0) {
    chat.createSession('新会话')
  }
}

// 监听 postMessage（微信回调窗口通知）
window.addEventListener('message', (e) => {
  if (e.data?.type === 'wechat_callback' && e.data?.token) {
    console.log('[Vermes🔐] 收到微信登录 postMessage')
    stopPolling()
    onWeChatLogin(e.data)
  }
})

async function send() {
  const input = inputText.value.trim()
  const files = [...uploadedFiles.value]
  const model = chat.currentModel
  const provider = chat.currentProvider
  console.log('[Vermes📤] send() input:', JSON.stringify(input), 'files:', files.length, 'model:', model, 'provider:', provider)
  if ((!input && files.length === 0) || chat.loading) {
    console.log('[Vermes📤] send() blocked: empty or loading, loading:', chat.loading)
    return
  }
  inputText.value = ''
  uploadedFiles.value = []
  try {
    await chat.sendMessage(input, files)
    console.log('[Vermes📤] send() → sendMessage() completed')
  } catch(e) {
    console.error('[Vermes📤] send() → sendMessage() error:', e)
    alert('❌ 发送失败：' + e.message)
  }
}

function triggerFileUpload() { fileInput.value?.click() }

function handleFileSelect(e) {
  const files = Array.from(e.target.files)
  for (const f of files) {
    if (f.size > 20 * 1024 * 1024) { alert(`文件 ${f.name} 超过 20MB`); continue }
    uploadedFiles.value.push({
      name: f.name,
      size: f.size,
      file: f,
      preview: f.type.startsWith('image/') ? URL.createObjectURL(f) : null
    })
  }
  e.target.value = ''
}

function removeFile(idx) { uploadedFiles.value.splice(idx, 1) }

function copyReferralCode() {
  if (!referralCode.value) return
  const text = `我在用 Vermes AI 助手，免费体验中！用我的推荐码 ${referralCode.value} 注册，我俩都能获得额外 200 积分/天。下载: https://vbit.top/vermes/#downloads`
  navigator.clipboard.writeText(text).then(() => {
    alert('✅ 推荐码已复制到剪贴板！分享给朋友即可获得 +200 积分/天')
  }).catch(() => {
    prompt('复制以下内容分享给朋友:', text)
  })
}
function selectModel(m) {
  chat.currentModel = m.id
  chat.currentProvider = m.provider || m.group || ''
  localStorage.setItem('vermes-current-model', m.id)
  localStorage.setItem('vermes-current-provider', m.provider || m.group || '')
  showModelSelect.value = false
}

function currentModelName() {
  const m = models.value.find(m => m.id === chat.currentModel)
  return m ? m.name : chat.currentModel
}

watch(() => chat.filteredMessages, async () => {
  await nextTick()
  if (chatContainer.value) chatContainer.value.scrollTop = chatContainer.value.scrollHeight
}, { deep: true })
</script>

<template>
  <div class="flex flex-col h-full">
    <!-- 顶部栏 -->
    <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between bg-white dark:bg-gray-800">
      <div class="flex items-center gap-3">
        <!-- 微信头像 - 点击弹出退出选项 -->
        <div v-if="isLoggedIn && userAvatar" class="flex items-center gap-2 cursor-pointer group relative">
          <img :src="userAvatar" class="w-8 h-8 rounded-full object-cover ring-2 ring-green-400 shadow-sm" @error="$event.target.style.display='none'" />
          <div class="flex flex-col leading-tight">
            <span class="text-xs font-medium text-gray-700 dark:text-gray-200 group-hover:text-green-600 dark:group-hover:text-green-400 transition max-w-[80px] truncate">{{ userName }}</span>
            <span class="text-[10px] text-green-500">已登录</span>
          </div>
          <!-- 悬停显示退出按钮 -->
          <button @click="logout()" class="ml-1 text-[10px] text-red-400 hover:text-red-600 transition opacity-0 group-hover:opacity-100" title="退出登录">退出</button>
        </div>
        <div v-else-if="isLoggedIn" class="flex items-center gap-1.5 px-2 py-1 bg-green-50 dark:bg-green-900/30 rounded-full text-xs text-green-600 dark:text-green-400">
          <span class="w-6 h-6 rounded-full bg-green-400 flex items-center justify-center text-white text-xs font-bold">V</span>
          {{ userName }}
          <button @click="logout()" class="ml-1 text-[10px] text-red-400 hover:text-red-600 transition" title="退出登录">退出</button>
        </div>
        <div v-else @click="openWeChatQR()" class="flex items-center gap-2 cursor-pointer group">
          <div class="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center text-gray-400 text-xs">?</div>
          <div class="flex flex-col leading-tight">
            <span class="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300 transition">微信未登录</span>
            <span class="text-[10px] text-gray-300 dark:text-gray-600">点击登录</span>
          </div>
        </div>

        <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

        <button @click="chat.toggleSidebar()" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition" title="切换侧边栏">☰</button>
        <h2 class="font-semibold text-gray-800 dark:text-gray-200">{{ chat.currentSession?.name || '新会话' }}</h2>
        <span class="text-xs text-gray-400">{{ chat.filteredMessages.length }} 条消息</span>
        <span v-if="quotaDisplay" class="text-xs px-2 py-0.5 rounded-full"
          :class="quotaDisplay.remaining <= 10 ? 'bg-amber-100 text-amber-600 dark:bg-amber-900/30 dark:text-amber-400' : 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400'">
          {{ quotaDisplay.text }}
        </span>
      </div>
      <!-- 模型选择器 -->
      <div class="relative">
        <button @click.stop="showModelSelect = !showModelSelect"
          class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600 transition flex items-center gap-1.5">
          <span class="w-2 h-2 rounded-full bg-green-500"></span>
          {{ currentModelName() }}
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </button>
        <div v-if="showModelSelect" class="absolute right-0 top-full mt-1 w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl z-50 max-h-80 overflow-y-auto py-1">
          <template v-for="(group, gName) in modelGroups" :key="gName">
            <div class="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ gName }}</div>
            <div v-for="m in group" :key="m.id" @click="selectModel(m)"
              class="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between"
              :class="{ 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400': m.id === chat.currentModel }">
              <span>{{ m.name }}</span>
              <span v-if="m.id === chat.currentModel" class="text-green-500 text-xs">✓</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- 点击外部关闭下拉 -->
    <div v-if="showModelSelect" @click="showModelSelect = false" class="fixed inset-0 z-40"></div>

    <!-- 消息列表 -->
    <div ref="chatContainer" class="flex-1 overflow-y-auto px-4 py-6 space-y-4 bg-gray-50 dark:bg-gray-900">
      <div v-if="chat.filteredMessages.length === 0" class="flex-1 flex flex-col items-center justify-center px-8 py-16">
        <div class="text-center mb-8">
          <div class="text-4xl mb-3">V</div>
          <h1 class="text-xl font-bold text-gray-800 dark:text-gray-100">欢迎使用 Vermes</h1>
          <p class="text-gray-400 text-sm mt-1">直接在对话框输入你的需求</p>
        </div>
      </div>

      <div v-for="msg in chat.filteredMessages" :key="msg.id" class="flex gap-3" :class="msg.role === 'user' ? 'flex-row-reverse' : ''">
        <div class="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0" :class="msg.role === 'user' ? 'bg-indigo-500' : 'bg-green-500'">
          {{ msg.role === 'user' ? '我' : 'V' }}
        </div>
        <div class="max-w-[75%] min-w-0 px-4 py-3 rounded-2xl text-sm leading-relaxed" :class="msg.role === 'user' ? 'bg-indigo-500 text-white rounded-br-md' : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-bl-md shadow-sm'">
          <template v-if="msg.role === 'user'">
            <img v-if="msg.content && msg.content.includes('data:image')" :src="msg.content.match(/data:image[^)]+/)?.[0]" class="max-w-full rounded-lg mb-2" />
            <template v-if="!msg.content?.match(/^!\[.*\]\(data:image/)">
              <div style="white-space:pre-wrap;word-break:break-word;">{{ msg.content }}</div>
            </template>
          </template>
          <template v-else>
            <div v-if="msg.content" class="vermes-md" v-html="renderMd(msg.content)"></div>
            <span v-else class="text-gray-400 text-xs">等待中...</span>
            <span v-if="msg.streaming" class="inline-block w-2 h-4 bg-green-500 animate-pulse ml-1"></span>
          </template>
        </div>
      </div>
    </div>

    <!-- 输入区 -->
    <div class="px-4 py-3 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700">
      <div v-if="chat.uploading" class="mb-2 text-xs text-blue-500 flex items-center gap-1"><span class="animate-spin">⏳</span> 正在处理附件...</div>
      <div v-if="uploadedFiles.length > 0" class="flex flex-wrap gap-2 mb-2">
        <div v-for="(f, idx) in uploadedFiles" :key="idx" class="flex items-center gap-2 bg-gray-100 dark:bg-gray-700 rounded-lg px-3 py-1.5 text-xs">
          <img v-if="f.preview" :src="f.preview" class="w-6 h-6 object-cover rounded" />
          <span class="truncate max-w-[120px]">{{ f.name }}</span>
          <span class="text-gray-400">{{ chat.formatSize(f.size) }}</span>
          <button @click="removeFile(idx)" class="text-red-400 hover:text-red-600 font-bold">×</button>
        </div>
      </div>
      <div class="flex gap-3 items-end">
        <input ref="fileInput" type="file" multiple accept="image/*,.pdf,.txt,.md,.csv,.json,.py,.js,.html,.css" class="hidden" @change="handleFileSelect" />
        <button @click="triggerFileUpload()" class="p-3 rounded-xl border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 transition text-base" title="上传文件/图片">📎</button>
        <input ref="inputRef" v-model="inputText" @keydown.enter.exact="send"
          placeholder="输入消息，Enter 发送..." class="flex-1 border border-gray-300 dark:border-gray-600 rounded-xl px-4 py-3 text-sm bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
        <button v-if="chat.loading" @click="chat.stopGeneration()" class="px-5 py-3 bg-red-500 hover:bg-red-600 text-white rounded-xl text-sm transition">停止</button>
        <button v-else @click="send()" :disabled="!inputText.trim() && uploadedFiles.length===0" class="px-5 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm transition disabled:opacity-40">发送</button>
      </div>
    </div>
  </div>

  <!-- 微信登录弹窗（点击未登录头像触发的二维码弹窗） -->
  <div v-if="showWeChatModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showWeChatModal = false">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">
      <button @click="showWeChatModal = false" class="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 transition text-lg">✕</button>
      <h3 class="font-bold text-lg mb-4">微信扫码登录</h3>
      <div v-if="qrLoading" class="flex items-center justify-center" style="min-height:200px">
        <span class="text-gray-400 text-sm">加载中...</span>
      </div>
      <img v-else-if="qrCodeDataUrl" :src="qrCodeDataUrl" alt="微信扫码" class="mx-auto rounded-xl shadow-lg" style="width:220px;height:220px" />
      <p class="text-xs text-gray-400 mt-3">使用微信扫一扫登录</p>
    </div>
  </div>

  <!-- 配额耗尽弹窗 -->
  <div v-if="chat.showQuotaModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="chat.showQuotaModal = false">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">
      <div class="text-4xl mb-3">{{ chat.quotaModalType === 'trial_expired' ? '⏰' : '💡' }}</div>
      <h3 class="font-bold text-lg mb-2">
        {{ chat.quotaModalType === 'trial_expired' ? '免费体验已过期' : '今日积分已用完' }}
      </h3>
      <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">
        {{ chat.quotaModalType === 'trial_expired'
          ? '免费体验截止至 2026年6月26日'
          : `今日已用 ${serverQuota.spent_today}/${serverQuota.total_limit} 积分` }}
      </p>
      <p v-if="serverQuota.bonus_points > 0" class="text-xs text-green-500 mb-3">
        🎁 推荐奖励: +{{ serverQuota.bonus_points }} 积分/天
      </p>
      <p class="text-xs text-amber-500 mb-5">⏰ 每日积分凌晨自动重置</p>
      <div class="flex flex-col gap-3">
        <!-- 选项1: 推荐朋友 -->
        <button @click="copyReferralCode"
          class="w-full py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm font-medium transition">
          🎁 推荐朋友 +200 积分
        </button>
        <p v-if="referralCode" class="text-xs text-gray-400 -mt-1">
          你的推荐码: <span class="font-mono text-green-500">{{ referralCode }}</span>
          <button @click="copyReferralCode" class="ml-1 text-green-500 hover:underline">复制</button>
        </p>
        <!-- 选项2: 明天再来 -->
        <button @click="chat.showQuotaModal = false"
          class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
          ⏰ 明天再来（凌晨重置）
        </button>
        <!-- 选项3: 配置自己的 API Key -->
        <button v-if="!chat.isOnline" @click="chat.showQuotaModal = false; router.push('/settings')"
          class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
          🔑 配置自己的 API Key
        </button>
        <button @click="chat.showQuotaModal = false"
          class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">
          关闭
        </button>
      </div>
    </div>
  </div>

</template>

<style scoped>
.vermes-md :deep(p) { margin: 0.4em 0; line-height: 1.7; }
.vermes-md :deep(h1), .vermes-md :deep(h2), .vermes-md :deep(h3) { font-weight: 600; margin: 0.6em 0 0.3em; }
.vermes-md :deep(h1) { font-size: 1.2em; }
.vermes-md :deep(h2) { font-size: 1.1em; }
.vermes-md :deep(h3) { font-size: 1.05em; }
.vermes-md :deep(ul), .vermes-md :deep(ol) { padding-left: 1.5em; margin: 0.3em 0; }
.vermes-md :deep(li) { margin: 0.15em 0; line-height: 1.6; }
.vermes-md :deep(code) { background: rgba(0,0,0,0.06); padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.85em; font-family: 'SF Mono', Monaco, Consolas, monospace; }
.dark .vermes-md :deep(code) { background: rgba(255,255,255,0.1); }
.vermes-md :deep(pre) { background: #1e1e2e; color: #cdd6f4; border-radius: 8px; padding: 12px 16px; overflow-x: auto; margin: 0.6em 0; font-size: 0.85em; }
.vermes-md :deep(pre code) { background: none; padding: 0; color: inherit; font-size: 1em; }
.vermes-md :deep(blockquote) { border-left: 3px solid #22c55e; padding-left: 12px; margin: 0.5em 0; color: #666; }
.dark .vermes-md :deep(blockquote) { color: #aaa; }
.vermes-md :deep(table) { width: 100%; border-collapse: collapse; margin: 0.6em 0; font-size: 0.9em; }
.vermes-md :deep(th), .vermes-md :deep(td) { border: 1px solid #e5e7eb; padding: 6px 10px; text-align: left; }
.dark .vermes-md :deep(th), .dark .vermes-md :deep(td) { border-color: #374151; }
.vermes-md :deep(th) { background: rgba(0,0,0,0.04); font-weight: 600; }
.dark .vermes-md :deep(th) { background: rgba(255,255,255,0.06); }
.vermes-md :deep(strong) { font-weight: 700; }
.vermes-md :deep(hr) { border: none; border-top: 1px solid #e5e7eb; margin: 1em 0; }
.dark .vermes-md :deep(hr) { border-top-color: #374151; }
.vermes-md :deep(a) { color: #16a34a; text-decoration: none; }
.vermes-md :deep(a:hover) { text-decoration: underline; }
</style>
