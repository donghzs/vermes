<script setup>
import { ref, watch, nextTick, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import MarkdownIt from 'markdown-it'

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
})

// ✅ 登录状态
const isLoggedIn = ref(!!localStorage.getItem('vermes_token'))
const userAvatar = ref(localStorage.getItem('vermes_wechat_avatar') || '')
const userName = ref(localStorage.getItem('vermes_wechat_name') || '已登录')
const inputText = ref('')
const fileInput = ref(null)
const uploadedFiles = ref([])
const showModelSelect = ref(false)

// ✅ App.vue 已调用 chat.init()，这里不需要重复

// 模型列表：优先从 Settings 同步的模型，否则用默认列表
const defaultModels = [
  { id: 'deepseek/deepseek-v4-flash', name: 'DeepSeek V4 Flash (免费)', provider: 'vbit.top' },
  { id: 'openrouter/owl-alpha', name: 'Owl Alpha (免费)', provider: 'vbit.top' },
  { id: 'qwen/qwen3-coder', name: 'Qwen3 Coder (免费)', provider: 'vbit.top' },
  { id: 'deepseek-chat', name: 'DeepSeek Chat (自有Key)', provider: 'deepseek' },
  { id: 'deepseek-reasoner', name: 'DeepSeek R1 (自有Key)', provider: 'deepseek' },
  { id: 'gpt-4o', name: 'GPT-4o', provider: 'openai' },
  { id: 'claude-sonnet-4-20250514', name: 'Claude Sonnet 4', provider: 'openrouter' },
]

const models = computed(() => {
  // Read from localStorage (synced by Settings)
  try {
    const saved = localStorage.getItem('vermes-providers')
    if (saved) {
      const providers = JSON.parse(saved)
      const synced = []
      for (const p of providers) {
        if (p.models && p.models.length > 0) {
          for (const m of p.models) {
            synced.push({ id: m, name: m, provider: p.name, group: p.name })
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

// 微信扫码登录 - 使用微信官方 WxLogin JS SDK 在弹窗内直接显示二维码
const showWeChatModal = ref(false)
const wechatState = ref('')
const isPywebview = !!(window.pywebview || window.webkit?.messageHandlers || (typeof navigator !== 'undefined' && navigator.userAgent && navigator.userAgent.includes('Vermes')))

let pollTimer = null

function openWeChatQR() {
  console.log('[Vermes🔐] openWeChatQR() 调用')
  // 先同步打开空窗口（确保不被弹窗拦截），后续 fetch 完成后导航过去
  const win = window.open('', 'wechat-login', 'width=600,height=600,menubar=no,toolbar=no,location=no')
  // 从服务端获取注册好的 state
  fetch('https://vbit.top/api/wechat/qrurl')
    .then(r => r.json())
    .then(data => {
      wechatState.value = data.state
      showWeChatModal.value = true
      if (win && !win.closed) {
        win.location.href = data.url
      } else {
        // 弹窗被拦截，显示手动按钮
        nextTick(() => {
          const container = document.getElementById('wechat-qr-container')
          if (!container) return
          container.innerHTML = ''
          const link = document.createElement('a')
          link.href = data.url
          link.target = '_blank'
          link.textContent = '📱 点此打开微信扫码'
          link.style.cssText = 'display:block;text-align:center;padding:16px;font-size:16px;background:#07c160;color:white;border:none;border-radius:12px;cursor:pointer;font-weight:600;text-decoration:none;'
          container.appendChild(link)
        })
      }
      // 启动轮询
      startPolling()
    })
    .catch(() => {
      // fallback: 纯前端方式
      wechatState.value = Math.random().toString(36).substring(2, 18)
      showWeChatModal.value = true
      nextTick(() => {
        const container = document.getElementById('wechat-qr-container')
        if (!container) return
        container.innerHTML = ''
        const link = document.createElement('a')
        const url = `https://open.weixin.qq.com/connect/qrconnect?appid=wxfd680141e93226be&redirect_uri=${encodeURIComponent('https://vbit.top/api/wechat/callback')}&response_type=code&scope=snsapi_login&state=${wechatState.value}#wechat_redirect`
        link.href = url
        link.target = '_blank'
        link.textContent = '📱 点此打开微信扫码'
        link.style.cssText = 'display:block;text-align:center;padding:16px;font-size:16px;background:#07c160;color:white;border:none;border-radius:12px;cursor:pointer;font-weight:600;text-decoration:none;'
        container.appendChild(link)
        startPolling()
      })
    })
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch(`https://vbit.top/api/wechat/poll?state=${wechatState.value}`)
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
    } catch(e) {
      // 网络错误静默忽略
    }
  }, 2000)
  // 5 分钟后自动停止
  setTimeout(() => stopPolling(), 5 * 60 * 1000)
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
}

function onWeChatLogin(data) {
  showWeChatModal.value = false
  localStorage.setItem('vermes_token', data.token)
  localStorage.setItem('vermes_wechat_token', data.token)
  if (data.userName) localStorage.setItem('vermes_wechat_name', data.userName)
  if (data.userAvatar) localStorage.setItem('vermes_wechat_avatar', data.userAvatar)
  localStorage.removeItem('vermes_quota')
  isLoggedIn.value = true
  userName.value = data.userName || '微信用户'
  userAvatar.value = data.userAvatar || ''
}

function initWxLogin(container) {
  try {
    new WxLogin({
      self_redirect: false,
      id: 'wechat-qr-container',
      appid: 'wxfd680141e93226be',
      scope: 'snsapi_login',
      redirect_uri: encodeURIComponent('https://vbit.top/api/wechat/callback'),
      state: wechatState.value,
      style: 'black',
      href: ''
    })
    console.log('[Vermes🔐] WxLogin 初始化成功')
  } catch(e) {
    console.error('[Vermes🔐] WxLogin 初始化失败:', e)
    container.innerHTML = '<div style="text-align:center;padding:40px;color:#666">微信登录初始化失败</div>'
  }
}

// 监听 iframe 发来的 postMessage（WxLogin 双层 iframe 可能传不到，作为备选）
window.addEventListener('message', (e) => {
  if (e.data?.type === 'wechat_callback' && e.data?.token) {
    console.log('[Vermes🔐] 收到微信登录 postMessage:', e.data.userName, e.data.token?.substring(0,10))
    stopPolling()
    onWeChatLogin(e.data)
  }
})

// iframe 加载完成后的处理
function onIframeLoad() {
  console.log('[Vermes🔐] WeChat iframe loaded')
}

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
function selectModel(m) { chat.currentModel = m.id; chat.currentProvider = m.provider || m.group || ''; showModelSelect.value = false }

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
        <!-- ✅ 微信头像 - 左上角醒目展示 -->
        <div v-if="isLoggedIn && userAvatar" class="flex items-center gap-2 cursor-pointer group relative" @click="openWeChatQR()">
          <img :src="userAvatar" class="w-8 h-8 rounded-full object-cover ring-2 ring-green-400 shadow-sm" @error="$event.target.style.display='none'" />
          <div class="flex flex-col leading-tight">
            <span class="text-xs font-medium text-gray-700 dark:text-gray-200 group-hover:text-green-600 dark:group-hover:text-green-400 transition max-w-[80px] truncate">{{ userName }}</span>
            <span class="text-[10px] text-green-500">已登录</span>
          </div>
        </div>
        <div v-else-if="isLoggedIn" class="flex items-center gap-1.5 px-2 py-1 bg-green-50 dark:bg-green-900/30 rounded-full text-xs text-green-600 dark:text-green-400">
          <span class="w-6 h-6 rounded-full bg-green-400 flex items-center justify-center text-white text-xs font-bold">V</span>
          {{ userName }}
        </div>
        <div v-else @click="openWeChatQR()" class="flex items-center gap-2 cursor-pointer group">
          <div class="w-8 h-8 rounded-full bg-gray-200 dark:bg-gray-600 flex items-center justify-center text-gray-400 text-xs">?</div>
          <div class="flex flex-col leading-tight">
            <span class="text-xs text-gray-400 group-hover:text-gray-600 dark:group-hover:text-gray-300 transition">未登录</span>
            <span class="text-[10px] text-gray-300 dark:text-gray-600">点击登录</span>
          </div>
        </div>

        <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

        <button @click="chat.toggleSidebar()" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition" title="切换侧边栏">☰</button>
        <h2 class="font-semibold text-gray-800 dark:text-gray-200">{{ chat.currentSession?.name || '新会话' }}</h2>
        <span class="text-xs text-gray-400">{{ chat.filteredMessages.length }} 条消息</span>
      </div>
      <!-- 模型选择器 -->
      <div class="relative">
        <button
          @click.stop="showModelSelect = !showModelSelect"
          class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-600 transition flex items-center gap-1.5"
        >
          <span class="w-2 h-2 rounded-full bg-green-500"></span>
          {{ currentModelName() }}
          <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
        </button>
        <div v-if="showModelSelect" class="absolute right-0 top-full mt-1 w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl shadow-xl z-50 max-h-80 overflow-y-auto py-1">
          <template v-for="(group, gName) in modelGroups" :key="gName">
            <div class="px-3 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wide">{{ gName }}</div>
            <div
              v-for="m in group" :key="m.id"
              @click="selectModel(m)"
              class="px-3 py-2 text-sm cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center justify-between"
              :class="{ 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-400': m.id === chat.currentModel }"
            >
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
        <!-- 空消息时显示引导卡片 -->
        <div class="text-center mb-8">
          <div class="text-4xl mb-3">V</div>
          <h1 class="text-xl font-bold text-gray-800 dark:text-gray-100">欢迎使用 Vermes</h1>
          <p class="text-gray-400 text-sm mt-1">选择下方选项开始，或直接输入你的需求</p>
        </div>
        <!-- 快速操作 -->
        <div class="w-full max-w-md space-y-3">
          <!-- 微信扫码登录（首选） -->
          <div v-if="!isLoggedIn" @click="openWeChatQR()" class="w-full px-5 py-3.5 rounded-xl bg-green-500 hover:bg-green-600 text-white font-medium text-sm transition shadow-sm flex items-center justify-center gap-2 cursor-pointer">
            <span>💚 微信扫码登录（10万免费Token）</span>
          </div>
          <div v-else class="w-full px-5 py-3 rounded-xl bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 text-green-700 dark:text-green-300 text-sm flex items-center gap-3">
            <img v-if="userAvatar" :src="userAvatar" class="w-7 h-7 rounded-full object-cover ring-2 ring-green-400" @error="$event.target.style.display='none'" />
            <span class="font-medium">✅ 已登录：{{ userName }}</span>
          </div>
          <a href="https://openrouter.ai/" target="_blank" class="w-full px-5 py-3 rounded-xl bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 text-blue-600 dark:text-blue-300 font-medium text-sm transition border border-blue-200 dark:border-blue-800 flex items-center justify-between">
            <span>🌐 获取 OpenRouter 200+大模型（含免费）</span>
            <span class="text-xs">去注册 ↗</span>
          </a>
          <button @click="quickStart('我想用本地模型')" class="w-full px-5 py-3 rounded-xl bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 text-sm transition">🏠 本地模型</button>
          <div class="border-t border-gray-200 dark:border-gray-700 pt-3 mt-2">
            <p class="text-xs text-gray-400 text-center mb-2">直接在对话框说：</p>
            <div class="flex flex-wrap gap-2 justify-center">
              <span class="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded-lg text-xs">"帮我安装翻译技能"</span>
              <span class="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded-lg text-xs">"换个模型"</span>
              <span class="px-3 py-1.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded-lg text-xs">"调暗一点"</span>
            </div>
          </div>
        </div>
      </div>

      <div v-for="msg in chat.filteredMessages" :key="msg.id" class="flex gap-3" :class="msg.role === 'user' ? 'flex-row-reverse' : ''">
        <div class="w-8 h-8 rounded-full flex items-center justify-center text-white text-xs font-bold flex-shrink-0" :class="msg.role === 'user' ? 'bg-indigo-500' : 'bg-green-500'">
          {{ msg.role === 'user' ? '我' : 'V' }}
        </div>
        <div class="max-w-[75%] min-w-0 px-4 py-3 rounded-2xl text-sm leading-relaxed" :class="msg.role === 'user' ? 'bg-indigo-500 text-white rounded-br-md' : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 rounded-bl-md shadow-sm'">
          <!-- 用户消息：支持图片预览 -->
          <template v-if="msg.role === 'user'">
            <img v-if="msg.content && msg.content.includes('data:image')" :src="msg.content.match(/data:image[^)]+/)?.[0]" class="max-w-full rounded-lg mb-2" />
            <template v-if="!msg.content?.match(/^!\[.*\]\(data:image/)">
              <div style="white-space:pre-wrap;word-break:break-word;">{{ msg.content }}</div>
            </template>
          </template>
          <!-- AI 回复：Markdown 渲染 -->
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
      <!-- 上传中的提示 -->
      <div v-if="chat.uploading" class="mb-2 text-xs text-blue-500 flex items-center gap-1">
        <span class="animate-spin">⏳</span> 正在处理附件...
      </div>
      <!-- 已选文件列表 -->
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
        <input
          ref="inputRef" v-model="inputText"
          @keydown.enter.exact="send"
          placeholder="输入消息，Enter 发送..."
          class="flex-1 border border-gray-300 dark:border-gray-600 rounded-xl px-4 py-3 text-sm bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500"
        />
        <button v-if="chat.loading" @click="chat.stopGeneration()" class="px-5 py-3 bg-red-500 hover:bg-red-600 text-white rounded-xl text-sm transition">停止</button>
        <button v-else @click="send()" :disabled="!inputText.trim() && uploadedFiles.length===0" class="px-5 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm transition disabled:opacity-40">发送</button>
      </div>
    </div>
  </div>


  <!-- 微信登录弹窗 -->
  <div v-if="showWeChatModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="showWeChatModal = false">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative">
      <button @click="showWeChatModal = false" class="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-lg">✕</button>
      <h3 class="font-bold text-lg mb-4">微信扫码登录</h3>
      <div id="wechat-qr-container" class="w-full flex items-center justify-center" style="min-height:300px"></div>
    </div>
  </div>

  <!-- 配额耗尽弹窗 -->
  <div v-if="chat.showQuotaModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="chat.showQuotaModal = false">
    <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 max-w-sm w-full mx-4 relative text-center">
      <div class="text-4xl mb-3">{{ chat.quotaModalType === 'trial_expired' ? '📱' : '⏰' }}</div>
      <h3 class="font-bold text-lg mb-2">
        {{ chat.quotaModalType === 'trial_expired' ? '免费体验已用完' : '今日免费额度已用完' }}
      </h3>
      <p class="text-sm text-gray-500 dark:text-gray-400 mb-5">
        {{ chat.quotaModalType === 'trial_expired'
          ? '微信扫码登录后可继续免费使用，不限次数。'
          : '明天再来吧，或配置自己的 API Key 继续使用。' }}
      </p>
      <div class="flex flex-col gap-3">
        <button v-if="chat.quotaModalType === 'trial_expired'"
          @click="chat.showQuotaModal = false; openWeChatQR()"
          class="w-full py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl text-sm font-medium transition">
          📱 微信扫码免费续杯
        </button>
        <button @click="chat.showQuotaModal = false; router.push('/settings')"
          class="w-full py-3 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-xl text-sm transition">
          🔑 配置自己的 API Key
        </button>
        <button @click="chat.showQuotaModal = false"
          class="text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">
          {{ chat.quotaModalType === 'trial_expired' ? '先不用了' : '好的，明天再来' }}
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
.vermes-md :deep(code) {
  background: rgba(0,0,0,0.06); padding: 0.15em 0.4em; border-radius: 4px;
  font-size: 0.85em; font-family: 'SF Mono', Monaco, Consolas, monospace;
}
.dark .vermes-md :deep(code) { background: rgba(255,255,255,0.1); }
.vermes-md :deep(pre) {
  background: #1e1e2e; color: #cdd6f4; border-radius: 8px;
  padding: 12px 16px; overflow-x: auto; margin: 0.6em 0; font-size: 0.85em;
}
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
