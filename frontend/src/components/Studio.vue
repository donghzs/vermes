<template>
  <div class="studio">
    <div class="studio-header">
      <div class="header-left">
        <button class="btn-back" @click="goBack">← 返回</button>
        <div>
          <h1>🎨 创作工作室</h1>
          <p class="subtitle">直连多模态大模型 · 独立于 Agent · 零 Token 消耗</p>
        </div>
      </div>
    </div>

    <div class="studio-layout">
      <!-- 左侧：配置 -->
      <div class="config-panel">
        <h3>⚙️ 模型配置</h3>

        <!-- 已保存的配置 -->
        <div class="form-group">
          <label>已保存配置</label>
          <div class="saved-config-row">
            <select v-model="selectedConfigIndex" @change="loadSavedConfig">
              <option value="-1">— 新建配置 —</option>
              <option v-for="(cfg, i) in savedConfigs" :key="i" :value="i">
                {{ cfg.name || cfg.model || '未命名' }}
              </option>
            </select>
            <button class="btn-icon" @click="deleteCurrentConfig" title="删除当前配置">🗑️</button>
          </div>
        </div>

        <div class="form-group">
          <label>API 地址</label>
          <input v-model="baseUrl" placeholder="https://apihub.agnes-ai.com/v1" />
        </div>

        <div class="form-group">
          <label>模型名</label>
          <input v-model="model" placeholder="agnes-2.5-flash" />
        </div>

        <div class="form-group">
          <label>API Key</label>
          <div class="key-input">
            <input :type="showKey ? 'text' : 'password'" v-model="apiKey" placeholder="sk-..." />
            <button class="btn-icon" @click="showKey = !showKey">{{ showKey ? '🙈' : '👁️' }}</button>
          </div>
        </div>

        <div class="form-group">
          <button class="btn-save-config" @click="showSaveDialog = true">💾 保存为配置</button>
        </div>

        <!-- 快捷预设 -->
        <div class="form-group">
          <label>快捷选择</label>
          <div class="preset-grid">
            <button v-for="p in providerPresets" :key="p.name"
              @click="applyPreset(p)"
              class="preset-card"
              :class="{ active: baseUrl === p.baseUrl }"
            >
              <span class="preset-icon">{{ p.icon }}</span>
              <span class="preset-name">{{ p.label }}</span>
              <span class="preset-key" v-if="p.keyEnv && envKeys[p.keyEnv]">✅ Key 已配置</span>
            </button>
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

        <div class="form-group">
          <label>生成模式</label>
          <select v-model="mode" @change="onModeChange">
            <option value="text">📝 文本</option>
            <option value="image">🎨 文生图</option>
            <option value="image2image">🖼️→🖼️ 图生图</option>
            <option value="video">🎬 文生视频</option>
            <option value="image2video">🖼️→🎬 图生视频</option>
            <option value="multi2video">🖼️🖼️→🎬 多图视频</option>
            <option value="keyframes">🎞️ 关键帧动画</option>
          </select>
        </div>

        <!-- 图片上传（图生图/图生视频时显示） -->
        <div v-if="mode === 'image2image' || mode === 'image2video'" class="form-group">
          <label>参考图片</label>
          <div
            class="drop-zone"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onDrop"
            :class="{ 'drag-over': dragOver }"
            @click="triggerFileInput"
          >
            <template v-if="refImage">
              <img :src="refImage" class="drop-preview" />
              <button class="btn-remove" @click.stop="refImage = ''; refImageFile = null">✕</button>
            </template>
            <template v-else>
              <div class="drop-hint">
                <span class="drop-icon">📁</span>
                <span>拖拽图片到这里，或点击上传</span>
                <span class="drop-sub">支持 JPG / PNG / WebP</span>
              </div>
            </template>
          </div>
          <input ref="fileInput" type="file" accept="image/*" class="hidden" @change="onFileSelect" />
        </div>

        <!-- 多图上传（多图视频/关键帧） -->
        <div v-if="mode === 'multi2video' || mode === 'keyframes'" class="form-group">
          <label>参考图片（多张）</label>
          <div class="multi-drop-area">
            <div class="thumbnails" v-if="refImages.length > 0">
              <div v-for="(img, i) in refImages" :key="i" class="thumbnail">
                <img :src="img.dataUrl" />
                <button class="btn-remove-sm" @click="removeRefImage(i)">✕</button>
                <span class="thumb-index">{{ i + 1 }}</span>
              </div>
            </div>
            <div
              class="drop-zone add-more"
              @dragover.prevent="dragOver = true"
              @dragleave="dragOver = false"
              @drop.prevent="onDropMulti"
              :class="{ 'drag-over': dragOver }"
              @click="triggerMultiFileInput"
            >
              <div class="drop-hint">
                <span class="drop-icon">➕</span>
                <span>添加图片</span>
              </div>
            </div>
          </div>
          <input ref="multiFileInput" type="file" accept="image/*" multiple class="hidden" @change="onMultiFileSelect" />
          <p class="help-text" v-if="mode === 'keyframes'">关键帧将按上传顺序生成平滑过渡动画</p>
        </div>

        <div v-if="mode === 'image' || mode === 'image2image'" class="form-group">
          <label>图片尺寸</label>
          <div class="size-row">
            <select v-model="sizePreset" @change="onSizePresetChange">
              <option value="1024x1024">1024×1024</option>
              <option value="1024x1792">1024×1792 (竖图)</option>
              <option value="1792x1024">1792×1024 (横图)</option>
              <option value="custom">自定义</option>
            </select>
            <template v-if="sizePreset === 'custom'">
              <input v-model="customWidth" class="size-input" placeholder="宽" type="number" min="256" max="4096" />
              <span class="size-x">×</span>
              <input v-model="customHeight" class="size-input" placeholder="高" type="number" min="256" max="4096" />
            </template>
          </div>
        </div>

        <!-- 视频参数 -->
        <div v-if="mode === 'video' || mode === 'image2video' || mode === 'multi2video' || mode === 'keyframes'" class="form-group">
          <label>视频参数</label>
          <div class="video-params">
            <div class="param-row">
              <span class="param-label">帧数</span>
              <input v-model.number="videoFrames" type="number" min="9" max="441" step="8" class="param-input" />
              <span class="param-hint">8n+1</span>
            </div>
            <div class="param-row">
              <span class="param-label">帧率</span>
              <input v-model.number="videoFps" type="number" min="1" max="60" class="param-input" />
              <span class="param-hint">fps</span>
            </div>
            <div class="param-row">
              <span class="param-label">分辨率</span>
              <select v-model="videoRes">
                <option value="1152x768">1152×768 (默认)</option>
                <option value="1280x768">1280×768</option>
                <option value="1920x1080">1920×1080</option>
                <option value="768x1152">768×1152 (竖屏)</option>
              </select>
            </div>
            <div class="param-row">
              <span class="param-label">时长</span>
              <div class="duration-presets">
                <button v-for="d in durationPresets" :key="d.label"
                  @click="setDuration(d.seconds)"
                  class="dur-btn"
                  :class="{ active: Math.abs(((videoFrames || 121) / (videoFps || 24)) - d.seconds) < 0.3 }"
                >{{ d.label }}</button>
              </div>
            </div>
            <div class="param-row">
              <span class="param-label"></span>
              <span class="param-value">{{ ((videoFrames || 121) / (videoFps || 24)).toFixed(1) }} 秒</span>
              <span class="param-hint">= {{ videoFrames || 121 }}帧 / {{ videoFps || 24 }}fps</span>
            </div>
          </div>
        </div>

        <div v-if="mode === 'text'" class="form-group">
          <label>系统提示词（可选）</label>
          <textarea v-model="systemPrompt" placeholder="设定 AI 的角色和风格..." rows="3"></textarea>
        </div>

        <div class="quick-presets">
          <span class="preset-label">快速填入:</span>
          <button class="preset-btn" @click="fillPreset('agnes')">Agnes</button>
          <button class="preset-btn" @click="fillPreset('deepseek')">DeepSeek</button>
          <button class="preset-btn" @click="fillPreset('xiaomi')">小米</button>
          <button class="preset-btn" @click="fillPreset('custom')">清空</button>
        </div>

        <div v-if="imageUrl && mode === 'image2video'" class="form-group">
          <label>参考图片</label>
          <div class="ref-image-preview">
            <img :src="imageUrl" />
            <button class="btn-sm" @click="imageUrl = ''">清除</button>
          </div>
        </div>
      </div>

      <!-- 右侧：输入+输出 -->
      <div class="main-panel">
        <div class="input-area">
          <textarea
            v-model="prompt"
            :placeholder="placeholderText"
            rows="6"
            @keydown.enter.exact="!$event.shiftKey && generate()"
          ></textarea>
          <div class="input-actions">
            <span class="char-count">{{ prompt.length }} 字</span>
            <button
              class="btn-generate"
              :disabled="!canGenerate || loading"
              @click="generate"
            >
              {{ loading ? '⏳ 生成中...' : '🚀 生成' }}
            </button>
          </div>
        </div>

        <!-- 输出：消息流 -->
        <div v-if="messages.length" class="output-area">
          <div class="msg-stream">
            <div v-for="(msg, i) in messages" :key="i" class="msg" :class="msg.role">
              <div class="msg-header">
                <span v-if="msg.role === 'user'" class="msg-label user-label">👤 你</span>
                <span v-else class="msg-label ai-label">🤖 Studio</span>
              </div>
              <div class="msg-body">
                <!-- 文本 -->
                <div v-if="msg.text" class="msg-text" v-text="msg.text"></div>
                <!-- 图片 -->
                <div v-if="msg.image_url" class="image-output">
                  <img :src="msg.image_url" />
                  <div class="image-actions">
                    <a :href="msg.image_url" target="_blank" class="btn-sm">🔗 原图</a>
                  </div>
                </div>
                <!-- 视频完成 -->
                <div v-if="msg.video_url" class="video-output">
                  <video :src="msg.video_url" controls></video>
                  <div class="video-actions">
                    <a :href="msg.video_url" target="_blank" class="btn-sm">🔗 视频链接</a>
                  </div>
                </div>
                <!-- 视频生成中 -->
                <div v-if="msg.video_id && !msg.video_url" class="video-pending">
                  <span>🎬 {{ msg.note || '生成中...' }}</span>
                  <div class="poll-actions">
                    <span v-if="msg.polling" class="poll-status">⏳ 自动轮询中 (5s)</span>
                    <button class="btn-sm" @click="pollVideo(msg)">🔄 手动查询</button>
                  </div>
                </div>
                <!-- 错误 -->
                <div v-if="msg.error" class="msg-error">❌ {{ msg.error }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { logger } from '@/utils/logger'
import { useConfirm } from '../composables/useConfirm'
const { confirm } = useConfirm()

// ── 状态 ──
const router = useRouter()

// ── 状态 ──
const STORAGE_KEY = 'vermes-studio-config'
const SAVED_LIST_KEY = 'vermes-studio-saved'

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {} }
  catch { return {} }
}

function loadSavedList() {
  try { return JSON.parse(localStorage.getItem(SAVED_LIST_KEY)) || [] }
  catch { return [] }
}

function saveSavedList() {
  localStorage.setItem(SAVED_LIST_KEY, JSON.stringify(savedConfigs.value))
}

function saveConfig() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    baseUrl: baseUrl.value,
    model: model.value,
    apiKey: apiKey.value,
    size: size.value,
  }))
}

const savedConfigs = ref(loadSavedList())
const selectedConfigIndex = ref(-1)

const saved = loadConfig()
const baseUrl = ref(saved.baseUrl || '')
const model = ref(saved.model || '')
const apiKey = ref(saved.apiKey || '')
const showKey = ref(false)
const mode = ref('text')
const size = ref('1024x1024')
const sizePreset = ref('1024x1024')
const customWidth = ref(1024)
const customHeight = ref(1024)
const videoFrames = ref(121)
const videoFps = ref(24)
const videoRes = ref('1152x768')
const durationPresets = [
  { label: '3秒', seconds: 3 },
  { label: '5秒', seconds: 5 },
  { label: '10秒', seconds: 10 },
  { label: '18秒', seconds: 18 },
]

// ── 厂商预设 ──
const providerPresets = [
  { name: 'agnes', label: 'Agnes AI', icon: '🧠', baseUrl: 'https://apihub.agnes-ai.com/v1', text: 'agnes-2.5-flash', image: 'agnes-image-2.1-flash', video: 'agnes-video-v2.0', keyEnv: 'AGNES_API_KEY' },
  { name: 'deepseek', label: 'DeepSeek', icon: '🔍', baseUrl: 'https://api.deepseek.com', text: 'deepseek-chat', image: '', video: '', keyEnv: 'DEEPSEEK_API_KEY' },
  { name: 'xiaomi', label: '小米 MiMo', icon: '📱', baseUrl: 'https://api.xiaomimimo.com/v1', text: 'mimo-v2.5-pro', image: 'mimo-v2.5-pro', video: 'mimo-v2.5-pro', keyEnv: 'XIAOMI_API_KEY' },
  { name: 'openai', label: 'OpenAI', icon: '⚡', baseUrl: 'https://api.openai.com/v1', text: 'gpt-4o', image: 'dall-e-3', video: '', keyEnv: 'OPENAI_API_KEY' },
  { name: 'alibaba', label: '阿里通义', icon: '☁️', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', text: 'qwen-max', image: 'qwen-vl-max', video: '', keyEnv: 'QWEN_API_KEY' },
]

// 尝试从 localStorage 读已存的 Key（用户之前配过的不需要再填）
const envKeys = {}
for (const p of providerPresets) {
  if (p.keyEnv) {
    try {
      const cfg = JSON.parse(localStorage.getItem('vermes-studio-config') || '{}')
      if (cfg.apiKey && cfg.baseUrl === p.baseUrl) envKeys[p.keyEnv] = true
    } catch {}
  }
}

function applyPreset(preset) {
  baseUrl.value = preset.baseUrl
  const modeMap = { text: 'text', image: 'image', video: 'video', image2video: 'video', multi2video: 'video', keyframes: 'video', image2image: 'image' }
  const key = modeMap[mode.value] || 'text'
  model.value = preset[key] || preset.text || ''
  // 如果 localStorage 有对应 Key 就自动填入
  try {
    const saved = JSON.parse(localStorage.getItem('vermes-studio-config') || '{}')
    if (saved.apiKey && saved.baseUrl === preset.baseUrl) {
      apiKey.value = saved.apiKey
    }
  } catch {}
}
const systemPrompt = ref('')
const prompt = ref('')
const refImage = ref('')     // 预览 URL
const refImageFile = ref(null)  // 原始 File 对象
const refImages = ref([])    // 多图：{dataUrl, file}
const dragOver = ref(false)
const fileInput = ref(null)
const multiFileInput = ref(null)

const result = ref(null)
const loading = ref(false)
const elapsed = ref(0)
const messages = ref([])
const showSaveDialog = ref(false)
const saveName = ref('')
const videoLoadError = ref(false)

// ── 快速预设 ──
const presets = {
  agnes: {
    baseUrl: 'https://apihub.agnes-ai.com/v1',
    text: 'agnes-2.5-flash',
    image: 'agnes-image-2.1-flash',
    video: 'agnes-video-v2.0',
    keyEnv: 'AGNES_API_KEY',
  },
  deepseek: {
    baseUrl: 'https://api.deepseek.com',
    text: 'deepseek-chat',
    image: '',
    video: '',
    keyEnv: 'DEEPSEEK_API_KEY',
  },
  xiaomi: {
    baseUrl: 'https://api.xiaomimimo.com/v1',
    text: 'mimo-v2.5-pro',
    image: 'mimo-v2.5-pro',
    video: 'mimo-v2.5-pro',
    keyEnv: 'XIAOMI_API_KEY',
  },
}

function fillPreset(name) {
  if (name === 'custom') {
    baseUrl.value = ''
    model.value = ''
    apiKey.value = ''
    return
  }
  const p = presets[name]
  baseUrl.value = p.baseUrl
  const modeMap = { text: 'text', image: 'image', video: 'video', image2video: 'video' }
  model.value = p[modeMap[mode.value]] || p.text
}

// ── Placeholder ──
const placeholderText = computed(() => {
  const map = {
    text: '输入提示词，让 AI 为你创作内容...',
    image: '描述你想要的画面，例如：一只橘猫坐在窗边看日落，暖色调...',
    video: '描述你想要的视频画面，例如：日落海滩，海浪拍岸，慢动作...',
    image2video: '已选择参考图片，输入画面的动态描述...',
  }
  return map[mode.value]
})

const canGenerate = computed(() => {
  return baseUrl.value && model.value && apiKey.value && prompt.value
})

// ── 生成 ──
async function generate() {
  if (!canGenerate.value || loading.value) return

  loading.value = true
  const start = Date.now()

  // 添加用户消息
  const userMsg = { role: 'user', text: prompt.value }
  messages.value.push(userMsg)
  scrollBottom()

  try {
    // 自定义尺寸
    let effectiveSize = size.value
    if (sizePreset.value === 'custom') {
      effectiveSize = `${customWidth.value}x${customHeight.value}`
    }

    // 视频参数
    let vWidth = 1152, vHeight = 768, vFrames = 121, vFps = 24
    if (mode.value === 'video' || mode.value === 'image2video') {
      const resParts = videoRes.value.split('x')
      vWidth = parseInt(resParts[0]) || 1152
      vHeight = parseInt(resParts[1]) || 768
      vFps = parseInt(videoFps.value) || 24
      vFrames = parseInt(videoFrames.value) || 121
    }

    const body = {
      base_url: baseUrl.value,
      model: model.value,
      api_key: apiKey.value,
      mode: mode.value,
      prompt: prompt.value,
      system: systemPrompt.value,
      size: effectiveSize,
      num_frames: vFrames,
      frame_rate: vFps,
      width: vWidth,
      height: vHeight,
    }

    // 图生图/图生视频：上传参考图片
    if ((mode.value === 'image2image' || mode.value === 'image2video') && refImageFile.value) {
      const b64 = await fileToBase64(refImageFile.value)
      body.image_data = b64
    }

    // 多图视频/关键帧：上传多张图片
    if ((mode.value === 'multi2video' || mode.value === 'keyframes') && refImages.value.length > 0) {
      const b64 = await fileToBase64(refImages.value[0].file)
      body.image_data = b64
      body.image_urls = refImages.value.map(img => img.dataUrl)
      if (mode.value === 'keyframes') {
        body.video_mode = 'keyframes'
      }
    }

    const resp = await fetch('/api/studio/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })

    const data = await resp.json()
    saveConfig()

    // 视频：添加 pending 消息并启动轮询
    const _videoModes = ['video', 'image2video', 'multi2video', 'keyframes']
    if (data.video_id && _videoModes.includes(mode.value)) {
      const msg = { role: 'assistant', video_id: data.video_id, note: '已提交，正在排队...', polling: true }
      messages.value.push(msg)
      pollVideo(msg)
    } else if (data.image_url || data.video_url || data.text) {
      messages.value.push({ role: 'assistant', ...data })
    } else if (data.error) {
      messages.value.push({ role: 'assistant', error: data.error })
    }
  } catch (e) {
    messages.value.push({ role: 'assistant', error: `请求失败: ${e.message}` })
  } finally {
    loading.value = false
    scrollBottom()
  }
}

function scrollBottom() {
  nextTick(() => {
    const el = document.querySelector('.output-area')
    if (el) el.scrollTop = el.scrollHeight
  })
}

// ── 视频轮询（per-message）──
const _pollTimeouts = new Set()

async function pollVideo(msg) {
  if (!msg.video_id || !baseUrl.value || !apiKey.value) return
  try {
    const resp = await fetch(`/api/studio/status/${msg.video_id}?base_url=${encodeURIComponent(baseUrl.value)}&api_key=${encodeURIComponent(apiKey.value)}`)
    const data = await resp.json()
    if (data.success && data.video_url) {
      msg.video_url = data.video_url
      msg.note = undefined
      msg.polling = false
    } else if (data.note && data.note.includes('processing')) {
      msg.note = data.note
      msg.polling = true
      const t = setTimeout(() => pollVideo(msg), 5000)
      _pollTimeouts.add(t)
    } else if (data.error) {
      msg.error = data.error
      msg.polling = false
    }
  } catch (e) {
    const t = setTimeout(() => pollVideo(msg), 5000)
    _pollTimeouts.add(t)
  }
}

// ── 卸载时清理所有轮询 timeout ──
onUnmounted(() => {
  _pollTimeouts.forEach(t => clearTimeout(t))
  _pollTimeouts.clear()
})

function goBack() {
  router.push('/')
}

// ── 图片拖拽/上传 ──
function triggerFileInput() {
  fileInput.value?.click()
}

function onFileSelect(e) {
  const file = e.target.files[0]
  if (file) loadRefImage(file)
}

function onDrop(e) {
  dragOver.value = false
  const file = e.dataTransfer.files[0]
  if (file && file.type.startsWith('image/')) loadRefImage(file)
}

function loadRefImage(file) {
  refImageFile.value = file
  const reader = new FileReader()
  reader.onload = (e) => { refImage.value = e.target.result }
  reader.readAsDataURL(file)
}

// ── 多图拖拽/上传 ──
function triggerMultiFileInput() {
  multiFileInput.value?.click()
}

function onMultiFileSelect(e) {
  const files = Array.from(e.target.files)
  for (const file of files) {
    if (file.type.startsWith('image/')) loadMultiImage(file)
  }
}

function onDropMulti(e) {
  dragOver.value = false
  const files = Array.from(e.dataTransfer.files)
  for (const file of files) {
    if (file.type.startsWith('image/')) loadMultiImage(file)
  }
}

function loadMultiImage(file) {
  const reader = new FileReader()
  reader.onload = (e) => {
    refImages.value.push({ dataUrl: e.target.result, file })
  }
  reader.readAsDataURL(file)
}

function removeRefImage(i) {
  refImages.value.splice(i, 1)
}

function onModeChange() {
  // 清掉不需要的图片
  if (mode.value !== 'image2image' && mode.value !== 'image2video') {
    refImage.value = ''
    refImageFile.value = null
  }
  if (mode.value !== 'multi2video' && mode.value !== 'keyframes') {
    refImages.value = []
  }
  // 视频模式冻结非视频模式参数
  if (mode.value !== 'video' && mode.value !== 'image2video' && mode.value !== 'multi2video' && mode.value !== 'keyframes') {
    // 图片模式也显示参数
  }
}

function onSizePresetChange() {
  if (sizePreset.value !== 'custom') {
    size.value = sizePreset.value
  } else {
    size.value = `${customWidth.value}x${customHeight.value}`
  }
}

function setDuration(seconds) {
  // 根据目标时长和帧率计算帧数（8n+1）
  const fps = videoFps.value || 24
  let frames = Math.round(seconds * fps)
  // 调整到最近的 8n+1
  frames = Math.max(9, Math.min(441, frames))
  frames = Math.floor((frames - 1) / 8) * 8 + 1
  videoFrames.value = frames
}

async function fileToBase64(file) {
  return new Promise((resolve) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target.result)
    reader.readAsDataURL(file)
  })
}

// ── 配置管理 ──
function loadSavedConfig() {
  const idx = Number(selectedConfigIndex.value)
  if (idx < 0 || idx >= savedConfigs.value.length) return
  const cfg = savedConfigs.value[idx]
  baseUrl.value = cfg.baseUrl || ''
  model.value = cfg.model || ''
  apiKey.value = cfg.apiKey || ''
  size.value = cfg.size || '1024x1024'
}

function saveAsNewConfig(name) {
  if (!name || !name.trim()) return
  const cfg = {
    name: name.trim(),
    baseUrl: baseUrl.value,
    model: model.value,
    apiKey: apiKey.value,
    size: size.value,
    createdAt: Date.now(),
  }
  savedConfigs.value.push(cfg)
  selectedConfigIndex.value = savedConfigs.value.length - 1
  saveSavedList()
}

function confirmSave() {
  if (saveName.value && saveName.value.trim()) {
    saveAsNewConfig(saveName.value.trim())
  }
  showSaveDialog.value = false
  saveName.value = ''
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
.studio {
  padding: 24px;
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f7fa;
  overflow-y: auto;
}

.studio-header h1 {
  font-size: 22px;
  margin: 0 0 4px 0;
  color: #1a1a2e;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.btn-back {
  padding: 6px 14px;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}

.btn-back:hover {
  background: #f0f0f0;
  border-color: #bbb;
}

.subtitle {
  color: #888;
  font-size: 13px;
  margin: 0 0 20px 0;
}

.studio-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 20px;
  flex: 1;
}

.config-panel {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.config-panel h3 {
  margin: 0 0 16px 0;
  font-size: 15px;
  color: #333;
}

.form-group {
  margin-bottom: 14px;
}

.form-group label {
  display: block;
  font-size: 12px;
  color: #666;
  margin-bottom: 4px;
  font-weight: 500;
}

.form-group input,
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 13px;
  background: #fafafa;
  transition: border-color 0.2s;
}

.form-group input:focus,
.form-group select:focus,
.form-group textarea:focus {
  outline: none;
  border-color: #409EFF;
  background: #fff;
}

.key-input {
  display: flex;
  gap: 4px;
}

.key-input input {
  flex: 1;
}

.btn-icon {
  background: #f0f0f0;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 4px 8px;
  cursor: pointer;
}

.btn-save-config {
  width: 100%;
  padding: 8px;
  border: 1px dashed #409EFF;
  border-radius: 8px;
  background: #f0f7ff;
  color: #409EFF;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}

.btn-save-config:hover {
  background: #409EFF;
  color: #fff;
}

.preset-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.preset-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 8px 4px;
  border: 1px solid #eee;
  border-radius: 10px;
  background: #fafafa;
  cursor: pointer;
  transition: all 0.15s;
}

.preset-card:hover {
  border-color: #409EFF;
  background: #f0f7ff;
}

.preset-card.active {
  border-color: #409EFF;
  background: #e6f0ff;
}

.preset-icon {
  font-size: 20px;
}

.preset-name {
  font-size: 11px;
  color: #555;
  font-weight: 500;
}

.preset-key {
  font-size: 9px;
  color: #52c41a;
}

.saved-config-row {
  display: flex;
  gap: 4px;
}

.saved-config-row select {
  flex: 1;
}

.video-params {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.param-label {
  font-size: 12px;
  color: #888;
  min-width: 36px;
}

.param-row select {
  flex: 1;
}

.param-input {
  width: 70px !important;
  text-align: center;
}

.param-hint {
  font-size: 10px;
  color: #bbb;
}

.param-value {
  font-size: 13px;
  color: #409EFF;
  font-weight: 500;
  min-width: 50px;
}

.duration-presets {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}

.dur-btn {
  padding: 2px 8px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.dur-btn:hover {
  border-color: #409EFF;
  color: #409EFF;
}

.dur-btn.active {
  background: #409EFF;
  color: #fff;
  border-color: #409EFF;
}

.size-row {
  display: flex;
  gap: 6px;
  align-items: center;
}

.size-row select {
  flex: 1;
}

.size-input {
  width: 70px !important;
  text-align: center;
}

.size-x {
  color: #999;
  font-size: 13px;
}

.quick-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid #eee;
}

.preset-label {
  font-size: 11px;
  color: #999;
  margin-right: 4px;
}

.preset-btn {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 14px;
  background: #f8f8f8;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.preset-btn:hover {
  background: #409EFF;
  color: #fff;
  border-color: #409EFF;
}

.ref-image-preview {
  position: relative;
}

.ref-image-preview img {
  width: 100%;
  border-radius: 8px;
  margin-top: 6px;
}

.ref-image-preview .btn-sm {
  position: absolute;
  top: 10px;
  right: 10px;
}

/* ── 拖拽上传区 ── */
.drop-zone {
  border: 2px dashed #ccc;
  border-radius: 10px;
  padding: 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  min-height: 100px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  background: #fafafa;
}

.drop-zone:hover {
  border-color: #409EFF;
  background: #f0f7ff;
}

.drop-zone.drag-over {
  border-color: #409EFF;
  background: #e6f0ff;
  transform: scale(1.02);
}

.drop-hint {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  color: #999;
  font-size: 13px;
}

.drop-icon {
  font-size: 28px;
}

.drop-sub {
  font-size: 11px;
  color: #bbb;
}

.drop-preview {
  max-height: 120px;
  border-radius: 6px;
}

.multi-drop-area {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.thumbnails {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.thumbnail {
  position: relative;
  width: 80px;
  height: 80px;
  border-radius: 8px;
  overflow: hidden;
  border: 2px solid #e0e0e0;
}

.thumbnail img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.thumb-index {
  position: absolute;
  bottom: 2px;
  right: 2px;
  background: rgba(0,0,0,0.6);
  color: #fff;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
}

.btn-remove-sm {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  border: none;
  background: rgba(0,0,0,0.5);
  color: #fff;
  font-size: 10px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.add-more {
  width: 80px;
  height: 80px;
  min-height: unset;
  padding: 0;
}

.help-text {
  font-size: 11px;
  color: #999;
  margin: 6px 0 0 0;
}

.btn-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: none;
  background: rgba(0,0,0,0.5);
  color: #fff;
  font-size: 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.hidden { display: none; }

/* ── 弹窗 ── */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-box {
  background: #fff;
  border-radius: 14px;
  padding: 24px;
  width: 360px;
  box-shadow: 0 8px 30px rgba(0,0,0,0.2);
}

.modal-box h4 {
  margin: 0 0 14px 0;
  font-size: 15px;
  color: #333;
}

.modal-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  box-sizing: border-box;
}

.modal-input:focus {
  outline: none;
  border-color: #409EFF;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 14px;
}

.btn-cancel {
  padding: 6px 16px;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #fff;
  font-size: 13px;
  cursor: pointer;
}

.btn-confirm {
  padding: 6px 16px;
  border: none;
  border-radius: 8px;
  background: #409EFF;
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.main-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.input-area {
  background: #fff;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}

.input-area textarea {
  width: 100%;
  padding: 10px;
  border: 1px solid #ddd;
  border-radius: 8px;
  font-size: 14px;
  resize: vertical;
  min-height: 120px;
  font-family: inherit;
}

.input-area textarea:focus {
  outline: none;
  border-color: #409EFF;
}

.input-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}

.char-count {
  font-size: 12px;
  color: #aaa;
}

.btn-generate {
  padding: 8px 24px;
  background: linear-gradient(135deg, #409EFF, #6366F1);
  color: #fff;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-generate:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-generate:not(:disabled):hover {
  opacity: 0.9;
}

.output-area {
  background: #fff;
  border-radius: 12px;
  padding: 16px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
  overflow-y: auto;
  max-height: 60vh;
}

.output-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.output-header h3 {
  margin: 0;
  font-size: 14px;
  color: #333;
}

.text-output {
  white-space: pre-wrap;
  font-size: 14px;
  line-height: 1.7;
  color: #333;
  max-height: 500px;
  overflow-y: auto;
  padding: 12px;
  background: #f8f9fa;
  border-radius: 8px;
}

.image-output img {
  max-width: 100%;
  border-radius: 8px;
}

.local-path {
  font-size: 11px;
  color: #999;
  margin-top: 6px;
}

.image-actions, .video-actions {
  margin-top: 10px;
}

.btn-sm {
  padding: 4px 10px;
  border: 1px solid #ddd;
  border-radius: 6px;
  background: #fff;
  font-size: 12px;
  cursor: pointer;
  text-decoration: none;
  color: #333;
}

.btn-sm:hover {
  background: #f0f0f0;
}

.video-output video {
  max-width: 100%;
  border-radius: 8px;
}

.video-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px;
  background: #f8f9fa;
  border-radius: 8px;
  text-align: center;
  color: #666;
  font-size: 14px;
}

.video-icon {
  font-size: 32px;
}

.video-note {
  font-size: 12px;
  color: #999;
}

.poll-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 8px;
}

.poll-status {
  font-size: 11px;
  color: #409EFF;
}

.error-output {
  padding: 12px;
  background: #fff2f0;
  border: 1px solid #ffccc7;
  border-radius: 8px;
  color: #cf1322;
  font-size: 13px;
  white-space: pre-wrap;
}

.elapsed {
  font-size: 11px;
  color: #999;
  text-align: center;
  margin-top: 8px;
}

/* ── 消息流 ── */
.msg-stream {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.msg { }

.msg-header { margin-bottom: 4px; }

.msg-label {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
}

.user-label { color: #409EFF; }
.ai-label { color: #52c41a; }

.msg-text {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  color: #333;
}

.video-pending {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: #666;
}

.poll-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.poll-status { font-size: 11px; color: #409EFF; }

.msg-error { color: #e53935; font-size: 12px; }
</style>
