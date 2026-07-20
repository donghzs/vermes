<template>
  <div class="studio-chat">
    <!-- 顶部：模型配置（可折叠） -->
    <div class="studio-topbar">
      <button class="btn-back" @click="goBack">← 返回</button>
      <span class="studio-title">🎨 创作工作室</span>
      <button class="btn-config-toggle" @click="showConfig = !showConfig">
        {{ showConfig ? '收起配置 ▲' : '模型配置 ▼' }}
      </button>
    </div>

    <!-- 配置区（折叠） -->
    <div v-if="showConfig" class="config-bar">
      <div class="config-row">
        <div class="preset-chips">
          <button v-for="p in providerPresets" :key="p.name"
            @click="applyPreset(p)"
            class="chip"
            :class="{ active: baseUrl === p.baseUrl }"
          >
            {{ p.icon }} {{ p.label }}
            <span v-if="p.name !== 'custom' && !isBuiltinPreset(p)" class="chip-delete" @click.stop="removeProvider(p.name)">✕</span>
          </button>
        </div>
      </div>
      <div class="config-row">
        <select v-model="selectedConfigIndex" @change="loadSavedConfig" class="cfg-input">
          <option value="-1">📂 已保存配置...</option>
          <option v-for="(cfg, i) in savedConfigs" :key="i" :value="i">{{ cfg.name || cfg.model }}</option>
        </select>
        <button class="btn-sm" @click="showSaveDialog = true">💾 保存</button>
        <button class="btn-sm" @click="showAddProviderDialog = true">➕ 添加厂商</button>
        <button class="btn-sm" @click="deleteCurrentConfig" :disabled="selectedConfigIndex < 0">🗑️</button>
      </div>
      <div class="config-row">
        <input v-model="baseUrl" placeholder="API 地址" class="cfg-input" />
        <div class="model-input-wrap">
          <input v-model="model" placeholder="模型名" class="cfg-input" list="studio-model-list" />
          <button class="btn-refresh" @click="fetchModels" :disabled="loadingModels" title="获取可用模型列表">{{ loadingModels ? '⏳' : '🔄' }}</button>
        </div>
        <datalist id="studio-model-list">
          <option v-for="m in availableModels" :key="m.id" :value="m.id">{{ m.display }}{{ m.owned_by ? ' (' + m.owned_by + ')' : '' }}</option>
        </datalist>
        <div class="key-wrap">
          <input :type="showKey ? 'text' : 'password'" v-model="apiKey" placeholder="API Key" class="cfg-input" />
          <button class="btn-eye" @click="showKey = !showKey">{{ showKey ? '🙈' : '👁️' }}</button>
        </div>
      </div>

      <!-- 模型选择弹窗 -->
      <div v-if="showModelPicker && availableModels.length > 0" class="model-picker-overlay" @click.self="showModelPicker = false">
        <div class="model-picker">
          <div class="model-picker-header">
            <span>📋 可用模型 ({{ availableModels.length }})</span>
            <button @click="showModelPicker = false">✕</button>
          </div>
          <div class="model-picker-list">
            <button v-for="m in availableModels" :key="m.id"
              @click="selectModel(m.id)"
              class="model-item"
              :class="{ active: model === m.id }"
            >
              <span class="model-id">{{ m.id }}</span>
              <span v-if="m.owned_by" class="model-owner">{{ m.owned_by }}</span>
            </button>
          </div>
        </div>
      </div>

      <!-- 保存配置弹窗 -->
      <Teleport to="body">
        <div v-if="showSaveDialog" class="modal-overlay" @click.self="showSaveDialog = false">
          <div class="modal-box">
            <h4>保存配置</h4>
            <input v-model="saveName" placeholder="输入配置名称（如 Agnes、DeepSeek）" class="modal-input" @keydown.enter="confirmSave" />
            <div class="modal-actions">
              <button @click="showSaveDialog = false" class="btn-cancel">取消</button>
              <button @click="confirmSave" class="btn-confirm">保存</button>
            </div>
          </div>
        </div>
      </Teleport>

      <!-- 添加厂商弹窗 -->
      <Teleport to="body">
        <div v-if="showAddProviderDialog" class="modal-overlay" @click.self="showAddProviderDialog = false">
          <div class="modal-box modal-box-wide">
            <h4>➕ 添加厂商</h4>
            <p class="modal-hint">填入厂商信息，保存后 Studio 和 Agent 都可直接使用</p>
            <div class="modal-form">
              <div class="form-row">
                <input v-model="newProvider.name" placeholder="厂商标识（如 minimax）" class="modal-input" />
                <input v-model="newProvider.label" placeholder="显示名称（如 MiniMax）" class="modal-input" />
              </div>
              <input v-model="newProvider.baseUrl" placeholder="API 地址（如 https://api.minimax.chat/v1）" class="modal-input" />
              <input v-model="newProvider.apiKey" type="password" placeholder="API Key" class="modal-input" />
              <div class="form-row">
                <input v-model="newProvider.text" placeholder="文本模型（可留空）" class="modal-input" />
                <input v-model="newProvider.image" placeholder="图片模型（可留空）" class="modal-input" />
                <input v-model="newProvider.video" placeholder="视频模型（可留空）" class="modal-input" />
              </div>
            </div>
            <div class="modal-actions">
              <button @click="showAddProviderDialog = false" class="btn-cancel">取消</button>
              <button @click="confirmAddProvider" :disabled="!newProvider.name || !newProvider.baseUrl" class="btn-confirm">保存到配置</button>
            </div>
          </div>
        </div>
      </Teleport>
      <div v-if="statusMsg" class="status-msg">{{ statusMsg }}</div>
    </div>

    <!-- 消息列表 -->
    <div class="messages" ref="msgListRef">
      <div v-for="(msg, i) in messages" :key="i" class="msg" :class="msg.role">
        <div class="msg-avatar">{{ msg.role === 'user' ? '👤' : 'V' }}</div>
        <div class="msg-body">
          <!-- 文本 -->
          <div v-if="msg.text" class="msg-text" v-text="msg.text"></div>
          <!-- 用户上传的图片 -->
          <div v-if="msg.image_data" class="msg-image">
            <img :src="msg.image_data" />
          </div>
          <!-- 生成结果图片 -->
          <div v-if="msg.image_url" class="msg-image">
            <img :src="msg.image_url" @click="openImage(msg.image_url)" />
            <div class="msg-actions">
              <button class="msg-action-btn" @click="downloadResult(msg.image_url)">⬇️ 下载</button>
              <button class="msg-action-btn" @click="regenerate(msg)">🔄 重新生成</button>
            </div>
          </div>
          <!-- 视频 -->
          <div v-if="msg.video_url" class="msg-video">
            <video :src="msg.video_url" controls></video>
          </div>
          <!-- 错误 -->
          <div v-if="msg.error" class="msg-error">❌ {{ msg.error }}</div>
        </div>
      </div>
      <div v-if="loading" class="msg assistant">
        <div class="msg-avatar">V</div>
        <div class="msg-body"><span class="typing">⏳ 生成中...</span></div>
      </div>
    </div>

    <!-- 创作模板栏 -->
    <div class="template-bar">
      <button v-for="t in templates" :key="t.name" 
        @click="applyTemplate(t)" 
        class="template-chip" 
        :class="{ active: activeTemplate === t.name }"
        :title="t.prompt"
      >{{ t.icon }} {{ t.name }}</button>
      <button @click="showParams = !showParams" class="template-chip" :class="{ active: showParams }">⚙️ 参数</button>
      <button @click="showGallery = !showGallery" class="template-chip" :class="{ active: showGallery }">🖼️ 画廊</button>
    </div>

    <!-- 参数控制面板 -->
    <div v-if="showParams" class="params-panel">
      <div class="param-row">
        <label>尺寸</label>
        <select v-model="params.size" class="param-select">
          <option value="1024x1024">1024×1024 (方)</option>
          <option value="1280x720">1280×720 (横)</option>
          <option value="720x1280">720×1280 (竖)</option>
          <option value="768x768">768×768</option>
        </select>
      </div>
      <div class="param-row">
        <label>数量</label>
        <select v-model="params.n" class="param-select">
          <option :value="1">1 张</option>
          <option :value="2">2 张</option>
          <option :value="4">4 张</option>
        </select>
      </div>
      <div class="param-row">
        <label>温度 {{ params.temperature.toFixed(2) }}</label>
        <input type="range" v-model.number="params.temperature" min="0" max="2" step="0.05" class="param-slider" />
      </div>
      <div class="param-row" v-if="params.mode === 'video'">
        <label>帧数</label>
        <select v-model.number="params.numFrames" class="param-select">
          <option :value="25">25 帧</option>
          <option :value="49">49 帧</option>
          <option :value="81">81 帧</option>
        </select>
      </div>
      <div class="param-row" v-if="params.mode === 'video'">
        <label>帧率</label>
        <select v-model.number="params.fps" class="param-select">
          <option :value="12">12 fps</option>
          <option :value="24">24 fps</option>
          <option :value="30">30 fps</option>
        </select>
      </div>
    </div>

    <!-- 历史画廊 -->
    <div v-if="showGallery" class="gallery-panel">
      <div v-if="gallery.length === 0" class="gallery-empty">暂无生成历史</div>
      <div v-else class="gallery-grid">
        <div v-for="item in gallery" :key="item.id" class="gallery-item" @click="reopenGalleryItem(item)">
          <img v-if="item.type === 'image'" :src="item.url" :alt="item.prompt" />
          <div v-else-if="item.type === 'video'" class="gallery-video-thumb">🎬 {{ item.prompt.slice(0, 12) }}</div>
          <div v-else class="gallery-text-thumb">📝 {{ item.prompt.slice(0, 12) }}</div>
          <div class="gallery-meta">{{ item.prompt.slice(0, 20) }}</div>
        </div>
      </div>
      <button v-if="gallery.length > 0" @click="clearGallery" class="btn-clear-gallery">清空</button>
    </div>

    <!-- 输入区 -->
    <div class="input-bar" @dragover.prevent @drop="onDrop">
      <label class="btn-upload" title="上传图片">
        <input type="file" accept="image/*" @change="onImageUpload" ref="fileInput" hidden />
        📷
      </label>
      <div v-if="uploadedImage" class="upload-preview">
        <img :src="uploadedImage.dataUrl" />
        <button class="btn-remove" @click="removeUploadedImage">✕</button>
      </div>
      <input
        v-model="prompt"
        @keydown.enter="send"
        placeholder="输入指令...（支持图片拖拽/上传）"
        :disabled="loading"
        class="chat-input"
      />
      <button @click="send" :disabled="loading || (!prompt.trim() && !uploadedImage)" class="btn-send">
        {{ loading ? '⏳' : '➡️' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, watch, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { logger } from '@/utils/logger'
import { useConfirm } from '../composables/useConfirm'
const { confirm } = useConfirm()

const router = useRouter()

function apiRoot(baseUrl) {
  return baseUrl.replace(/\/v1\/?$/, '')
}

// ── 配置 ──
const STORAGE_KEY = 'vermes-studio-config'
const SAVED_LIST_KEY = 'vermes-studio-saved'

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {} } catch { return {} }
}

function loadSavedList() {
  try { return JSON.parse(localStorage.getItem(SAVED_LIST_KEY)) || [] } catch { return [] }
}

function saveSavedList() {
  localStorage.setItem(SAVED_LIST_KEY, JSON.stringify(savedConfigs.value))
}

const saved = loadConfig()
const savedConfigs = ref(loadSavedList())
const selectedConfigIndex = ref(-1)
const baseUrl = ref(saved.baseUrl || '')
const model = ref(saved.model || '')
const apiKey = ref(saved.apiKey || '')

// 自动保存配置
watch([baseUrl, model, apiKey], () => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({
    baseUrl: baseUrl.value, model: model.value, apiKey: apiKey.value
  })) } catch {}
})
const showKey = ref(false)
const showConfig = ref(true)
const showSaveDialog = ref(false)
const showAddProviderDialog = ref(false)
const saveName = ref('')
const newProvider = ref({ name: '', label: '', baseUrl: '', apiKey: '', text: '', image: '', video: '' })
const statusMsg = ref('')
const uploadedImage = ref(null) // { dataUrl, file }
const fileInput = ref(null)

// ── 参数控制 ──
const showParams = ref(false)
const showGallery = ref(false)
const params = ref({
  size: '1024x1024',
  n: 1,
  temperature: 0.85,
  numFrames: 49,
  fps: 24,
  mode: 'auto',
})

// ── 创作模板 ──
const activeTemplate = ref('')
const templates = [
  { name: '海报', icon: '🎯', prompt: '设计一张精美的海报，主题：', mode: 'image' },
  { name: '头像', icon: '😀', prompt: '生成一个个性化的头像，风格：', mode: 'image' },
  { name: 'Logo', icon: '🏷️', prompt: '设计一个简洁现代的Logo，品牌名：', mode: 'image' },
  { name: '插画', icon: '🎨', prompt: '创作一幅插画，场景描述：', mode: 'image' },
  { name: '短剧', icon: '🎬', prompt: '生成一段5秒的短剧视频，内容：', mode: 'video' },
  { name: '动画', icon: '🌀', prompt: '制作一段动画效果，描述：', mode: 'video' },
  { name: '写作', icon: '✍️', prompt: '请帮我写：', mode: 'text' },
]
function applyTemplate(t) {
  activeTemplate.value = t.name
  prompt.value = t.prompt
  params.value.mode = t.mode
  if (t.mode === 'video') {
    params.value.size = '1280x720'
  } else if (t.mode === 'image') {
    params.value.size = '1024x1024'
  }
}

// ── 历史画廊 ──
const GALLERY_KEY = 'vermes-studio-gallery'
const gallery = ref(loadGallery())
function loadGallery() {
  try { return JSON.parse(localStorage.getItem(GALLERY_KEY)) || [] } catch { return [] }
}
function saveGallery() {
  try { localStorage.setItem(GALLERY_KEY, JSON.stringify(gallery.value.slice(0, 50))) } catch {}
}
function addToGallery(item) {
  gallery.value.unshift({ ...item, id: Date.now() + Math.random() })
  if (gallery.value.length > 50) gallery.value = gallery.value.slice(0, 50)
  saveGallery()
}
async function clearGallery() {
  if (!await confirm({ title: '清空历史', message: '清空所有生成历史？', confirmText: '清空', danger: true })) return
  gallery.value = []
  saveGallery()
}
function reopenGalleryItem(item) {
  if (item.type === 'image') {
    window.open(item.url, '_blank')
  } else if (item.type === 'text') {
    prompt.value = item.prompt
  }
}

const providerPresets = ref([
  { name: 'agnes', label: 'Agnes AI', icon: '🧠', baseUrl: 'https://apihub.agnes-ai.com/v1', text: 'agnes-2.0-flash', image: 'agnes-image-2.1-flash', video: 'agnes-video-v2.0', keyEnv: 'AGNES_API_KEY' },
  { name: 'custom', label: '自定义', icon: '🔧', baseUrl: '', text: '', image: '', video: '' },
])
const loadingProviders = ref(false)
const availableModels = ref([])
const loadingModels = ref(false)
const showModelPicker = ref(false)

// 从后端动态加载 provider 列表
async function loadProviders() {
  loadingProviders.value = true
  try {
    const resp = await fetch('/api/studio/providers')
    if (resp.ok) {
      const data = await resp.json()
      if (data.providers && data.providers.length > 0) {
        providerPresets.value = data.providers
      }
    }
  } catch (e) {
    console.warn('[Studio] 加载 provider 列表失败，使用内置预设:', e.message)
  } finally {
    loadingProviders.value = false
  }
}

// 从厂商 API 拉取实时模型列表
async function fetchModels() {
  if (!baseUrl.value || !apiKey.value) {
    statusMsg.value = '⚠️ 请先填写 API 地址和 Key'
    setTimeout(() => { statusMsg.value = '' }, 2000)
    return
  }
  loadingModels.value = true
  showModelPicker.value = true
  try {
    const resp = await fetch('/api/studio/models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: baseUrl.value, api_key: apiKey.value }),
    })
    const data = await resp.json()
    if (data.success) {
      availableModels.value = data.models || []
      statusMsg.value = `✅ 获取到 ${data.count} 个模型`
      setTimeout(() => { statusMsg.value = '' }, 2000)
    } else {
      availableModels.value = []
      statusMsg.value = `❌ 获取模型失败: ${data.error}`
      setTimeout(() => { statusMsg.value = '' }, 3000)
    }
  } catch (e) {
    console.error('[Studio] fetchModels error:', e)
    statusMsg.value = `❌ 请求失败: ${e.message}`
    setTimeout(() => { statusMsg.value = '' }, 3000)
  } finally {
    loadingModels.value = false
  }
}

function selectModel(mid) {
  model.value = mid
  showModelPicker.value = false
}

// 初始化时加载 provider 列表
loadProviders()

// 判断是否为内置预设（不可删除）
const _BUILTIN_NAMES = new Set(['agnes', 'deepseek', 'xiaomi', 'openai', 'alibaba', 'custom'])
function isBuiltinPreset(p) {
  return _BUILTIN_NAMES.has(p.name) && !p.baseUrl?.includes('minimax')
}

function applyPreset(preset) {
  baseUrl.value = preset.baseUrl
  model.value = preset.text || ''
  // 自动匹配 Key
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    if (saved.apiKey && saved.baseUrl === preset.baseUrl) apiKey.value = saved.apiKey
  } catch {}
  // 即时反馈支持能力
  const caps = []
  if (preset.text) caps.push('文本')
  if (preset.image) caps.push('图片')
  if (preset.video) caps.push('视频')
  statusMsg.value = `✅ ${preset.label}：支持 ${caps.join('、')}`
  setTimeout(() => { statusMsg.value = '' }, 3000)
  // 清空之前的模型列表
  availableModels.value = []
  // Key 就绪后恢复未完成的视频任务
  restoreVideoPolls()
}

// ── 消息 ──
const MESSAGES_KEY = 'vermes-studio-messages'
const messages = ref(loadMessages())
const prompt = ref('')
const loading = ref(false)
const msgListRef = ref(null)

function loadMessages() {
  try { return JSON.parse(sessionStorage.getItem(MESSAGES_KEY)) || [] } catch { return [] }
}
function saveMessages() {
  try { sessionStorage.setItem(MESSAGES_KEY, JSON.stringify(messages.value)) } catch {}
}

// 恢复未完成的视频轮询
function restoreVideoPolls() {
  const key = apiKey.value || (() => { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}').apiKey } catch { return '' } })()
  if (!key || !baseUrl.value) {
    logger.log('[VideoPoll] 无 Key/URL，跳过恢复。请先选预设')
    return
  }
  let count = 0
  for (const msg of messages.value) {
    if (msg.video_id && !msg.video_url && !msg.error) {
      count++
      msg.text = `🎬 恢复轮询中... ID: ${msg.video_id}`
      startVideoPoll(msg)
    }
  }
  if (count) logger.log(`[VideoPoll] 恢复了 ${count} 个视频任务`)
}
restoreVideoPolls()

// 自动持久化
watch(messages, () => saveMessages(), { deep: true })

function scrollBottom() {
  nextTick(() => {
    const el = msgListRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ── 自动识别模式 ──
function detectMode(text) {
  const videoKeywords = ['视频', 'video', '短剧', '动画', '镜头', '运镜', '转场']
  const imageKeywords = ['画', '图', '图片', 'image', '生成.*图', '设计', '海报']
  // 先检查是不是想让 AI 理解/回答问题（文本）
  const questionPatterns = ['?', '？', '什么', '如何', '怎么', '为什么', '介绍', '解释', '分析']
  const isQuestion = questionPatterns.some(p => text.includes(p))
  if (isQuestion && text.length < 200) return 'text'
  const hasVideo = videoKeywords.some(k => text.includes(k))
  const hasImage = imageKeywords.some(k => new RegExp(k).test(text))
  if (hasVideo) return 'video'
  if (hasImage) return 'image'
  // 默认文本
  return 'text'
}

// ── 根据 mode 获取对应的模型名，不支持则返回 null ──
function getModelForMode(mode) {
  const activePreset = providerPresets.value.find(p => p.baseUrl === baseUrl.value)
  if (!activePreset) return model.value
  if (mode === 'image' || mode === 'image2image') return activePreset.image || null
  if (mode === 'video' || mode === 'image2video' || mode === 'multi2video' || mode === 'keyframes') return activePreset.video || null
  return model.value
}

// ── 图片上传 ──
function onImageUpload(e) {
  const file = e.target.files?.[0]
  if (!file) return
  // 重置 input 值，允许再次上传同一文件
  if (fileInput.value) fileInput.value.value = ''
  const reader = new FileReader()
  reader.onload = () => {
    uploadedImage.value = { dataUrl: reader.result, file }
    statusMsg.value = '📷 图片已上传'
    setTimeout(() => { statusMsg.value = '' }, 2000)
  }
  reader.readAsDataURL(file)
}

function removeUploadedImage() {
  uploadedImage.value = null
}

function openImage(url) {
  window.open(url, '_blank')
}

// Canvas 压缩图片到指定最大尺寸
function resizeImage(file, maxSize) {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      let { width, height } = img
      if (width <= maxSize && height <= maxSize) {
        // 无需压缩，直接转 base64
        const canvas = document.createElement('canvas')
        canvas.width = width; canvas.height = height
        canvas.getContext('2d').drawImage(img, 0, 0)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
        return
      }
      const ratio = Math.min(maxSize / width, maxSize / height)
      width = Math.round(width * ratio)
      height = Math.round(height * ratio)
      const canvas = document.createElement('canvas')
      canvas.width = width; canvas.height = height
      canvas.getContext('2d').drawImage(img, 0, 0, width, height)
      resolve(canvas.toDataURL('image/jpeg', 0.85))
    }
    img.onerror = reject
    img.src = URL.createObjectURL(file)
  })
}

// ── 拖拽上传 ──
function onDrop(e) {
  const file = e.dataTransfer?.files?.[0]
  if (!file || !file.type.startsWith('image/')) return
  const reader = new FileReader()
  reader.onload = () => {
    uploadedImage.value = { dataUrl: reader.result, file }
    statusMsg.value = '📷 图片已上传'
    setTimeout(() => { statusMsg.value = '' }, 2000)
  }
  reader.readAsDataURL(file)
}

// ── 发送 ──
async function send() {
  const text = prompt.value.trim()
  const hasImage = !!uploadedImage.value
  if ((!text && !hasImage) || loading.value || !baseUrl.value || !apiKey.value) return
  prompt.value = ''

  // 有图片且无文字描述时自动用默认提示
  const promptText = text || (hasImage ? '基于这张图片进行创作' : '')

  // 添加用户消息（含图片预览）
  const userMsg = { role: 'user', text: promptText }
  if (hasImage) userMsg.image_data = uploadedImage.value.dataUrl
  messages.value.push(userMsg)
  scrollBottom()

  loading.value = true
  // 有图片时自动切换为图生图/图生视频
  const mode = hasImage ? (detectMode(promptText) === 'video' ? 'image2video' : 'image2image') : detectMode(promptText)
  const activeModel = getModelForMode(mode)

  // 检查模式是否支持
  if (!activeModel) {
    const name = providerPresets.value.find(p => p.baseUrl === baseUrl.value)?.label || '当前供应商'
    messages.value.push({ role: 'assistant', error: `${name} 不支持 ${mode === 'image' ? '图片' : '视频'}生成` })
    loading.value = false
    scrollBottom()
    return
  }

  try {
    const root = apiRoot(baseUrl.value)
    const headers = {
      'Authorization': `Bearer ${apiKey.value}`,
      'Content-Type': 'application/json',
    }

    if (mode === 'text') {
      // 文本：POST /v1/chat/completions
      const resp = await fetch(`${root}/v1/chat/completions`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model: activeModel,
          messages: [{ role: 'user', content: text }],
          max_tokens: 4096,
          temperature: params.value.temperature,
        }),
      })
      const data = await resp.json()
      if (resp.ok) {
        const replyText = data.choices[0].message.content
        messages.value.push({ role: 'assistant', text: replyText })
        addToGallery({ type: 'text', url: '', prompt: promptText, model: activeModel, content: replyText })
      } else {
        messages.value.push({ role: 'assistant', error: `API 返回 ${resp.status}: ${data.error?.message || resp.statusText}` })
      }

    } else if (mode === 'image' || mode === 'image2image') {
      // 图片/图生图：POST /v1/images/generations
      const suppress = ', no text, no watermark, no signature, no logo, no frame, pure image, natural photography style'
      const finalPrompt = promptText.toLowerCase().includes('no text') ? promptText : promptText + suppress
      
      // 图生图：用 extra_body.image 数组（OpenAI 兼容格式，per Agnes API 文档）
      const body = {
        model: activeModel,
        prompt: finalPrompt,
        size: params.value.size,
        n: params.value.n,
      }
      if (hasImage) {
        body.extra_body = { image: [uploadedImage.value.dataUrl] }
      }
      const resp = await fetch(`${root}/v1/images/generations`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      })
      const data = await resp.json()
      if (resp.ok) {
        for (const d of (data.data || [data.data?.[0]]).filter(Boolean)) {
          messages.value.push({ role: 'assistant', image_url: d.url })
          addToGallery({ type: 'image', url: d.url, prompt: promptText, model: activeModel })
        }
      } else {
        messages.value.push({ role: 'assistant', error: `图片生成失败: ${data.error?.message || resp.statusText}` })
      }
      uploadedImage.value = null

    } else if (mode === 'video' || mode === 'image2video') {
      // 视频/图生视频：POST /v1/video/generations
      const body = {
        model: activeModel,
        prompt: promptText,
        num_frames: params.value.numFrames,
        frame_rate: params.value.fps,
        width: parseInt(params.value.size.split('x')[0]),
        height: parseInt(params.value.size.split('x')[1]),
      }
      if (hasImage) {
        body.image = uploadedImage.value.dataUrl
      }
      const respV = await fetch(`${root}/v1/video/generations`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      })
      const data = await respV.json()
      if (respV.ok && (data.task_id || data.id)) {
        const taskId = data.task_id || data.id
        const queryUrl = `${root}/v1/video/generations/${taskId}`
        const msg = { role: 'assistant', video_id: taskId, text: `🎬 视频已提交，ID: ${taskId}，正在处理...`, _origPrompt: promptText }
        messages.value.push(msg)
        // 自动轮询查状态
        startVideoPoll(msg)
        uploadedImage.value = null
      } else {
        messages.value.push({ role: 'assistant', error: `视频提交失败: ${data.error?.message || respV.statusText}` })
      }
    }

  } catch (e) {
    console.error('[Studio] fetch error:', e)
    messages.value.push({ role: 'assistant', error: `请求失败: ${e.message}` })
  } finally {
    loading.value = false
    scrollBottom()
  }
}

// ── 视频轮询 ──
function startVideoPoll(msg) {
  if (!msg.video_id) return
  const root = apiRoot(baseUrl.value)
  let attempts = 0
  logger.log('[VideoPoll] 开始轮询:', msg.video_id)
  
  // 立即查一次
  const check = async () => {
    attempts++
    try {
      const resp = await fetch(`/api/studio/status/${msg.video_id}?base_url=${encodeURIComponent(baseUrl.value)}&api_key=${encodeURIComponent(apiKey.value)}`)
      if (!resp.ok) {
        logger.log(`[VideoPoll] HTTP ${resp.status} (attempt ${attempts})`)
        if (attempts > 10) {
          msg.text = `🎬 查询失败，ID: ${msg.video_id}`
          clearInterval(msg._pollTimer)
        }
        return
      }
      const data = await resp.json()
      logger.log(`[VideoPoll] 响应: success=${data.success} note=${data.note?.slice(0,30)} video=${!!data.video_url}`)
      
      if (data.success && data.video_url) {
        msg.video_url = data.video_url
        msg.video_id = data.video_id
        msg.text = undefined
        msg.note = undefined
        clearInterval(msg._pollTimer)
        addToGallery({ type: 'video', url: data.video_url, prompt: msg._origPrompt || '', model: '' })
        scrollBottom()
      } else if (data.error) {
        msg.error = data.error
        clearInterval(msg._pollTimer)
      } else if (data.note) {
        msg.text = `🎬 ${data.note}`
      }
    } catch (e) {
      console.error(`[VideoPoll] 异常:`, e.message)
      if (attempts > 5) {
        msg.text = `🎬 查询失败，ID: ${msg.video_id}`
        clearInterval(msg._pollTimer)
      }
    }
  }
  check()  // 立即执行第一次
  msg._pollTimer = setInterval(check, 8000)
}

function goBack() {
  router.push('/')
}

// ── 卸载时清理所有视频轮询 timer，防内存泄漏 ──
onUnmounted(() => {
  messages.value.forEach(msg => {
    if (msg._pollTimer) {
      clearInterval(msg._pollTimer)
      msg._pollTimer = null
    }
  })
})

function downloadResult(url) {
  const a = document.createElement('a')
  a.href = url
  a.download = `vermes-studio-${Date.now()}.png`
  a.target = '_blank'
  a.click()
}

function regenerate(msg) {
  // 找到对应的用户消息
  const idx = messages.value.indexOf(msg)
  if (idx > 0) {
    const userMsg = messages.value[idx - 1]
    prompt.value = userMsg.text || ''
    if (userMsg.image_data) {
      // 恢复图片
      uploadedImage.value = { dataUrl: userMsg.image_data, file: null }
    }
  }
}

// ── 配置管理 ──
function loadSavedConfig() {
  const idx = Number(selectedConfigIndex.value)
  if (idx < 0 || idx >= savedConfigs.value.length) return
  const cfg = savedConfigs.value[idx]
  baseUrl.value = cfg.baseUrl || ''
  model.value = cfg.model || ''
  apiKey.value = cfg.apiKey || ''
}

function saveAsNewConfig(name) {
  if (!name || !name.trim()) return
  const cfg = { name: name.trim(), baseUrl: baseUrl.value, model: model.value, apiKey: apiKey.value, createdAt: Date.now() }
  savedConfigs.value.push(cfg)
  selectedConfigIndex.value = savedConfigs.value.length - 1
  saveSavedList()
}

function confirmSave() {
  if (saveName.value && saveName.value.trim()) saveAsNewConfig(saveName.value.trim())
  showSaveDialog.value = false
  saveName.value = ''
}

// 保存新厂商到后端 config.yaml
async function confirmAddProvider() {
  const p = newProvider.value
  if (!p.name || !p.baseUrl) return

  try {
    const resp = await fetch('/api/studio/providers', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: p.name.trim(),
        label: p.label || p.name,
        icon: '🔧',
        baseUrl: p.baseUrl.trim(),
        text: p.text || '',
        image: p.image || '',
        video: p.video || '',
        apiKey: p.apiKey || '',
      }),
    })
    const data = await resp.json()
    if (data.success) {
      statusMsg.value = `✅ 厂商「${p.label || p.name}」已保存`
      showAddProviderDialog.value = false
      newProvider.value = { name: '', label: '', baseUrl: '', apiKey: '', text: '', image: '', video: '' }
      // 重新加载 provider 列表
      await loadProviders()
      // 自动选中刚添加的厂商
      const added = providerPresets.value.find(pp => pp.name === p.name.trim())
      if (added) applyPreset(added)
      setTimeout(() => { statusMsg.value = '' }, 3000)
    } else {
      statusMsg.value = `❌ 保存失败: ${data.error}`
      setTimeout(() => { statusMsg.value = '' }, 3000)
    }
  } catch (e) {
    statusMsg.value = `❌ 请求失败: ${e.message}`
    setTimeout(() => { statusMsg.value = '' }, 3000)
  }
}

// 从后端删除厂商
async function removeProvider(providerName) {
  if (!await confirm({ title: '删除厂商', message: `删除厂商「${providerName}」？`, confirmText: '删除', danger: true })) return
  try {
    const resp = await fetch(`/api/studio/providers/${encodeURIComponent(providerName)}`, { method: 'DELETE' })
    const data = await resp.json()
    if (data.success) {
      statusMsg.value = `✅ 厂商「${providerName}」已删除`
      await loadProviders()
      setTimeout(() => { statusMsg.value = '' }, 3000)
    } else {
      statusMsg.value = `❌ 删除失败: ${data.error}`
      setTimeout(() => { statusMsg.value = '' }, 3000)
    }
  } catch (e) {
    statusMsg.value = `❌ 请求失败: ${e.message}`
    setTimeout(() => { statusMsg.value = '' }, 3000)
  }
}

async function deleteCurrentConfig() {
  const idx = Number(selectedConfigIndex.value)
  if (idx < 0 || idx >= savedConfigs.value.length) return
  if (!await confirm({ title: '删除配置', message: `删除配置「${savedConfigs.value[idx].name}」？`, confirmText: '删除', danger: true })) return
  savedConfigs.value.splice(idx, 1)
  selectedConfigIndex.value = -1
  saveSavedList()
}
</script>

<style scoped>
.studio-chat {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
}

.studio-topbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.btn-back {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  cursor: pointer;
}

.studio-title {
  flex: 1;
  font-size: 14px;
  font-weight: 600;
  color: #333;
}

.btn-config-toggle {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #f0f7ff;
  font-size: 12px;
  cursor: pointer;
  color: #409EFF;
}

.config-bar {
  padding: 8px 12px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.config-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.config-row:last-child { margin-bottom: 0; }

.preset-chips {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.chip {
  padding: 3px 8px;
  border: 1px solid #ddd;
  border-radius: 12px;
  background: #fafafa;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.chip.active, .chip:hover {
  border-color: #409EFF;
  background: #e6f0ff;
  color: #409EFF;
}

.cfg-input {
  flex: 1;
  min-width: 80px;
  padding: 5px 8px;
  border: 1px solid #e0e0e0;
  border-radius: 6px;
  font-size: 12px;
}

.key-wrap {
  flex: 1;
  min-width: 80px;
  display: flex;
  position: relative;
}

.key-wrap input { flex: 1; padding-right: 28px; }

.btn-eye {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  padding: 2px;
}

/* 消息列表 */
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.msg {
  display: flex;
  gap: 8px;
  max-width: 85%;
}

.msg.user { align-self: flex-end; flex-direction: row-reverse; }
.msg.assistant { align-self: flex-start; }

.msg-avatar {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.msg.user .msg-avatar { background: #409EFF; }
.msg.assistant .msg-avatar { background: #e6f0ff; }

.msg-body {
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
  word-break: break-word;
}

.msg.user .msg-body {
  background: #409EFF;
  color: #fff;
  border-top-right-radius: 2px;
}

.msg.assistant .msg-body {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-top-left-radius: 2px;
}

.msg-text { white-space: pre-wrap; }

.msg-image img {
  max-width: 100%;
  max-height: 300px;
  border-radius: 8px;
  cursor: pointer;
}

.upload-preview {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.upload-preview img {
  height: 36px;
  border-radius: 4px;
}
.btn-remove {
  position: absolute;
  top: -6px;
  right: -6px;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  border: none;
  background: #e53935;
  color: #fff;
  font-size: 10px;
  line-height: 16px;
  text-align: center;
  cursor: pointer;
  padding: 0;
}

.msg-video video {
  max-width: 100%;
  max-height: 400px;
  border-radius: 8px;
}

.msg-video-pending {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #666;
}

.btn-retry {
  padding: 3px 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 11px;
  cursor: pointer;
  align-self: flex-start;
}

.msg-error {
  color: #e53935;
  font-size: 12px;
}

.status-msg {
  padding: 4px 12px 8px;
  font-size: 12px;
  color: #52c41a;
}

.typing { color: #999; }

/* 输入区 */
.input-bar {
  display: flex;
  gap: 6px;
  padding: 8px 12px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
}

.chat-input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 20px;
  font-size: 13px;
  outline: none;
}

.chat-input:focus { border-color: #409EFF; }

.btn-send {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: #409EFF;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  flex-shrink: 0;
}

.btn-send:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── 弹窗 ── */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.4);
  display: flex; align-items: center; justify-content: center; z-index: 1000;
}
.modal-box {
  background: #fff; border-radius: 14px; padding: 24px; width: 360px; box-shadow: 0 8px 30px rgba(0,0,0,0.2);
}
.modal-box h4 { margin: 0 0 14px 0; font-size: 15px; color: #333; }
.modal-input {
  width: 100%; padding: 10px 12px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; box-sizing: border-box;
}
.modal-input:focus { outline: none; border-color: #409EFF; }
.modal-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.btn-cancel { padding: 6px 16px; border: 1px solid #ddd; border-radius: 8px; background: #fff; font-size: 13px; cursor: pointer; }
.btn-confirm { padding: 6px 16px; border: none; border-radius: 8px; background: #409EFF; color: #fff; font-size: 13px; cursor: pointer; }
.btn-sm { padding: 4px 8px; border: 1px solid #ddd; border-radius: 6px; background: #fafafa; font-size: 11px; cursor: pointer; white-space: nowrap; }
.btn-sm:disabled { opacity: 0.4; cursor: not-allowed; }

/* ── 创作模板栏 ── */
.template-bar {
  display: flex;
  gap: 4px;
  padding: 6px 12px;
  background: #fafbfc;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
  overflow-x: auto;
  flex-wrap: nowrap;
}
.template-chip {
  padding: 3px 10px;
  border: 1px solid #e0e0e0;
  border-radius: 14px;
  background: #fff;
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s;
  color: #666;
}
.template-chip:hover { border-color: #409EFF; color: #409EFF; }
.template-chip.active { border-color: #409EFF; background: #e6f0ff; color: #409EFF; font-weight: 500; }

/* ── 参数面板 ── */
.params-panel {
  display: flex;
  gap: 16px;
  padding: 10px 12px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
  flex-wrap: wrap;
}
.param-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 100px;
}
.param-row label {
  font-size: 11px;
  color: #888;
}
.param-select {
  padding: 4px 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 12px;
  background: #fff;
}
.param-slider {
  width: 120px;
  accent-color: #409EFF;
}

/* ── 画廊 ── */
.gallery-panel {
  padding: 10px 12px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  flex-shrink: 0;
  max-height: 200px;
  overflow-y: auto;
  position: relative;
}
.gallery-empty {
  text-align: center;
  color: #999;
  font-size: 12px;
  padding: 20px;
}
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(80px, 1fr));
  gap: 8px;
}
.gallery-item {
  cursor: pointer;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e5e7eb;
  background: #f5f7fa;
  transition: transform 0.15s;
}
.gallery-item:hover { transform: scale(1.05); border-color: #409EFF; }
.gallery-item img {
  width: 100%;
  height: 60px;
  object-fit: cover;
  display: block;
}
.gallery-video-thumb, .gallery-text-thumb {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: #666;
}
.gallery-meta {
  padding: 3px 4px;
  font-size: 10px;
  color: #888;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.btn-clear-gallery {
  position: absolute;
  top: 6px;
  right: 8px;
  padding: 2px 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 10px;
  cursor: pointer;
  color: #e53935;
}

/* ── 生成结果操作 ── */
.msg-actions {
  display: flex;
  gap: 4px;
  margin-top: 4px;
}
.msg-action-btn {
  padding: 2px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 4px;
  background: #fafafa;
  font-size: 10px;
  cursor: pointer;
  color: #666;
}
.msg-action-btn:hover { border-color: #409EFF; color: #409EFF; }

/* ── 模型刷新按钮 ── */
.model-input-wrap {
  display: flex;
  gap: 4px;
  align-items: center;
  flex: 1;
}
.model-input-wrap .cfg-input { flex: 1; }
.btn-refresh {
  flex-shrink: 0;
  padding: 4px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  transition: all 0.2s;
}
.btn-refresh:hover { border-color: #409EFF; background: #f0f7ff; }
.btn-refresh:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── 模型选择弹窗 ── */
.model-picker-overlay {
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.model-picker {
  background: #fff;
  border-radius: 12px;
  width: 90%;
  max-width: 500px;
  max-height: 60vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0,0,0,0.15);
}
.model-picker-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
  font-weight: 600;
  font-size: 14px;
}
.model-picker-header button {
  border: none;
  background: none;
  cursor: pointer;
  font-size: 16px;
  color: #999;
}
.model-picker-list {
  overflow-y: auto;
  padding: 8px;
}
.model-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  padding: 8px 12px;
  border: none;
  border-radius: 6px;
  background: none;
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  transition: background 0.15s;
}
.model-item:hover { background: #f5f7fa; }
.model-item.active { background: #e6f4ff; color: #409EFF; }
.model-id { font-family: monospace; }
.model-owner { font-size: 11px; color: #999; }

/* ── 添加厂商弹窗 ── */
.modal-box-wide {
  width: 90%;
  max-width: 480px;
}
.modal-hint {
  font-size: 12px;
  color: #999;
  margin: 4px 0 12px;
}
.modal-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.form-row {
  display: flex;
  gap: 8px;
}
.form-row .modal-input { flex: 1; }

/* ── Provider chip 删除按钮 ── */
.chip-delete {
  margin-left: 6px;
  font-size: 10px;
  color: #999;
  cursor: pointer;
  padding: 0 2px;
  border-radius: 3px;
  transition: all 0.15s;
}
.chip-delete:hover {
  color: #e53935;
  background: rgba(229,57,53,0.1);
}
</style>
