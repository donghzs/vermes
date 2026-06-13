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
          >{{ p.icon }} {{ p.label }}</button>
        </div>
      </div>
      <div class="config-row">
        <select v-model="selectedConfigIndex" @change="loadSavedConfig" class="cfg-input">
          <option value="-1">📂 已保存配置...</option>
          <option v-for="(cfg, i) in savedConfigs" :key="i" :value="i">{{ cfg.name || cfg.model }}</option>
        </select>
        <button class="btn-sm" @click="showSaveDialog = true">💾 保存</button>
        <button class="btn-sm" @click="deleteCurrentConfig" :disabled="selectedConfigIndex < 0">🗑️</button>
      </div>
      <div class="config-row">
        <input v-model="baseUrl" placeholder="API 地址" class="cfg-input" />
        <input v-model="model" placeholder="模型名" class="cfg-input" />
        <div class="key-wrap">
          <input :type="showKey ? 'text' : 'password'" v-model="apiKey" placeholder="API Key" class="cfg-input" />
          <button class="btn-eye" @click="showKey = !showKey">{{ showKey ? '🙈' : '👁️' }}</button>
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
import { ref, nextTick, watch } from 'vue'
import { useRouter } from 'vue-router'

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
const saveName = ref('')
const statusMsg = ref('')
const uploadedImage = ref(null) // { dataUrl, file }
const fileInput = ref(null)

const providerPresets = [
  { name: 'agnes', label: 'Agnes AI', icon: '🧠', baseUrl: 'https://apihub.agnes-ai.com/v1', text: 'agnes-2.0-flash', image: 'agnes-image-2.1-flash', video: 'agnes-video-v2.0', keyEnv: 'AGNES_API_KEY' },
  { name: 'deepseek', label: 'DeepSeek', icon: '🔍', baseUrl: 'https://api.deepseek.com', text: 'deepseek-chat', image: '', video: '' },
  { name: 'xiaomi', label: '小米 MiMo', icon: '📱', baseUrl: 'https://api.xiaomimimo.com/v1', text: 'mimo-v2.5-pro', image: 'mimo-v2.5-pro', video: 'mimo-v2.5-pro' },
  { name: 'openai', label: 'OpenAI', icon: '⚡', baseUrl: 'https://api.openai.com/v1', text: 'gpt-4o', image: 'dall-e-3', video: '' },
  { name: 'alibaba', label: '阿里通义', icon: '☁️', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', text: 'qwen-max', image: 'qwen-vl-max', video: '' },
]

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
    console.log('[VideoPoll] 无 Key/URL，跳过恢复。请先选预设')
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
  if (count) console.log(`[VideoPoll] 恢复了 ${count} 个视频任务`)
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
  const activePreset = providerPresets.find(p => p.baseUrl === baseUrl.value)
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
    const name = providerPresets.find(p => p.baseUrl === baseUrl.value)?.label || '当前供应商'
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
          temperature: 0.85,
        }),
      })
      const data = await resp.json()
      if (resp.ok) {
        messages.value.push({ role: 'assistant', text: data.choices[0].message.content })
      } else {
        messages.value.push({ role: 'assistant', error: `API 返回 ${resp.status}: ${data.error?.message || resp.statusText}` })
      }

    } else if (mode === 'image' || mode === 'image2image') {
      // 图片/图生图：POST /v1/images/generations
      const suppress = ', no text, no watermark, no signature, no logo, no frame, pure image, natural photography style'
      const finalPrompt = promptText.toLowerCase().includes('no text') ? promptText : promptText + suppress
      
      // 图生图：直发 base64（Agnes 不支持产品精确保持，复杂场景走 Agent）
      const body = {
        model: activeModel,
        prompt: finalPrompt,
        size: '1024x1024',
        n: 1,
      }
      if (hasImage) {
        body.image = uploadedImage.value.dataUrl
      }
      const resp = await fetch(`${root}/v1/images/generations`, {
        method: 'POST',
        headers,
        body: JSON.stringify(body),
      })
      const data = await resp.json()
      if (resp.ok) {
        messages.value.push({ role: 'assistant', image_url: data.data[0].url })
      } else {
        messages.value.push({ role: 'assistant', error: `图片生成失败: ${data.error?.message || resp.statusText}` })
      }
      uploadedImage.value = null

    } else if (mode === 'video' || mode === 'image2video') {
      // 视频/图生视频：POST /v1/video/generations
      const body = {
        model: activeModel,
        prompt: promptText,
        num_frames: 49,
        frame_rate: 24,
        width: 1152,
        height: 768,
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
        const msg = { role: 'assistant', video_id: taskId, text: `🎬 视频已提交，ID: ${taskId}，正在处理...` }
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
  console.log('[VideoPoll] 开始轮询:', msg.video_id)
  
  // 立即查一次
  const check = async () => {
    attempts++
    try {
      const resp = await fetch(`/api/studio/status/${msg.video_id}?base_url=${encodeURIComponent(baseUrl.value)}&api_key=${encodeURIComponent(apiKey.value)}`)
      if (!resp.ok) {
        console.log(`[VideoPoll] HTTP ${resp.status} (attempt ${attempts})`)
        if (attempts > 10) {
          msg.text = `🎬 查询失败，ID: ${msg.video_id}`
          clearInterval(msg._pollTimer)
        }
        return
      }
      const data = await resp.json()
      console.log(`[VideoPoll] 响应: success=${data.success} note=${data.note?.slice(0,30)} video=${!!data.video_url}`)
      
      if (data.success && data.video_url) {
        msg.video_url = data.video_url
        msg.video_id = data.video_id
        msg.text = undefined
        msg.note = undefined
        clearInterval(msg._pollTimer)
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

function deleteCurrentConfig() {
  const idx = Number(selectedConfigIndex.value)
  if (idx < 0 || idx >= savedConfigs.value.length) return
  if (!confirm(`删除配置「${savedConfigs.value[idx].name}」？`)) return
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
</style>
