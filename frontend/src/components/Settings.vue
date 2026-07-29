<script setup>
import { ref, reactive, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useUpdateStore } from '../stores/update'
import * as api from '../services/api'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
const { confirm } = useConfirm()
import ProviderCard from './ProviderCard.vue'

// P0-c 加固后 /api/env 需携带 session token（裸 fetch 不走 api.js 封装，否则 401）
function envHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Vermes-Session-Token': (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || '',
  }
}

const chat = useChatStore()
const update = useUpdateStore()
const router = useRouter()

// ── 提供商列表 ──
const DEFAULT_BASE_URLS = {
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com',
  qwen: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  openrouter: 'https://openrouter.ai/api/v1',
  vbit: 'https://api.vbit.top/v1',
  xiaomi: 'https://api.xiaomimimo.com/v1',
  'ant-ling': 'https://api.ant-ling.com/v1',
  ollama: 'http://localhost:11434/v1',
  minimax: 'https://api.minimax.chat/v1',
  baidu: 'https://qianfan.baidubce.com/v2',
  xinghuo: 'https://spark-api.xf-yun.com/v1',
  stepfun: 'https://api.stepfun.com/v1',
  yi: 'https://api.lingyiwanwu.com/v1',
  baichuan: 'https://api.baichuan-ai.com/v1',
  zhipu: 'https://open.bigmodel.cn/api/paas/v4',
  hunyuan: 'https://api.hunyuan.cloud.tencent.com/v1',
  moonshot: 'https://api.moonshot.cn/v1',
  groq: 'https://api.groq.com/openai/v1',
  together: 'https://api.together.xyz/v1',
  agnes: 'https://apihub.agnes-ai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
}

const RECOMMENDED_IDS_FALLBACK = ['vbit', 'agnes', 'deepseek', 'xiaomi', 'ollama']
const CHINESE_IDS = ['xiaomi','qwen','baidu','xinghuo','minimax','ant-ling','stepfun','yi','baichuan','zhipu','hunyuan','moonshot']
const INTERNATIONAL_IDS = ['openai','anthropic','gemini','openrouter','groq','together']

// 推荐列表从后端配置派生，fallback 到硬编码（注意：必须是普通数组，不能是 computed ref，否则 .includes() 会报错）
function getRecommendedIds() {
  const ids = api.getRecommendedIds()
  return ids.length > 0 ? ids : RECOMMENDED_IDS_FALLBACK
}

// 推荐区提供商的额外配置
const PROVIDER_EXTRAS = {
  vbit: { iconClass: 'bg-green-500 text-white w-10 h-10', iconText: 'V', isSpecial: true },
  agnes: { iconClass: 'bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400', iconText: 'A', description: '全球前十 AI Lab，文本/图片/视频全模态免费', linkUrl: 'https://platform.agnes-ai.com/', linkText: '→ 去 Agnes AI 官网获取 Key ↗' },
  deepseek: { iconClass: 'bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400', iconText: 'D', description: '国产高性价比，注册即送额度', linkUrl: 'https://platform.deepseek.com/', linkText: '→ 去 DeepSeek 官网获取 Key ↗' },
  xiaomi: { iconClass: 'bg-orange-100 dark:bg-orange-900 text-orange-600 dark:text-orange-400', iconText: 'Mi', description: '国产高性价比，注册即送额度', linkUrl: 'https://platform.xiaomimimo.com?ref=KE64RG', linkText: '→ 去小米 MiMo 官网获取 Key ↗' },
  zhipu: { iconClass: 'bg-indigo-100 dark:bg-indigo-900 text-indigo-600 dark:text-indigo-400', iconText: '智', description: 'GLM 系列大模型，注册送免费额度', linkUrl: 'https://bigmodel.cn/usercenter/proj-mgmt/apikeys', linkText: '→ 去智谱开放平台获取 Key ↗' },
  hunyuan: { iconClass: 'bg-sky-100 dark:bg-sky-900 text-sky-600 dark:text-sky-400', iconText: '混', description: '腾讯全链路自研大模型，中文能力强', linkUrl: 'https://hunyuan.cloud.tencent.com/', linkText: '→ 去腾讯混元官网获取 Key ↗' },
  moonshot: { iconClass: 'bg-violet-100 dark:bg-violet-900 text-violet-600 dark:text-violet-400', iconText: 'K', description: 'Kimi 长文本能力强，支持 20 万汉字', linkUrl: 'https://platform.moonshot.cn/', linkText: '→ 去 Moonshot 开放平台获取 Key ↗' },
  ollama: { iconClass: 'bg-purple-100 dark:bg-purple-900 text-purple-600 dark:text-purple-400', iconText: '💻', description: '完全免费，数据不离开你的电脑', hideKeyInput: true },
}

const providers = ref([
  { id: 'openai', name: 'OpenAI', key: '', baseUrl: DEFAULT_BASE_URLS.openai, models: [], syncing: false },
  { id: 'deepseek', name: 'DeepSeek', key: '', baseUrl: DEFAULT_BASE_URLS.deepseek, models: [], syncing: false },
  { id: 'qwen', name: '通义千问', key: '', baseUrl: DEFAULT_BASE_URLS.qwen, models: [], syncing: false },
  { id: 'agnes', name: 'Agnes AI', key: '', baseUrl: DEFAULT_BASE_URLS.agnes, models: [], syncing: false },
  { id: 'openrouter', name: 'OpenRouter', key: '', baseUrl: DEFAULT_BASE_URLS.openrouter, models: [], syncing: false },
  { id: 'vbit', name: 'vbit.top', key: '', baseUrl: DEFAULT_BASE_URLS.vbit, models: [], syncing: false },
  { id: 'xiaomi', name: '小米 MiMo', key: '', baseUrl: DEFAULT_BASE_URLS.xiaomi, models: [], syncing: false },
  { id: 'ant-ling', name: '蚂蚁百灵', key: '', baseUrl: DEFAULT_BASE_URLS['ant-ling'], models: [], syncing: false },
  { id: 'ollama', name: '本地模型', key: 'ollama', baseUrl: DEFAULT_BASE_URLS.ollama, models: [], syncing: false },
  { id: 'minimax', name: 'MiniMax', key: '', baseUrl: DEFAULT_BASE_URLS.minimax, models: [], syncing: false },
  { id: 'baidu', name: '百度文心', key: '', baseUrl: DEFAULT_BASE_URLS.baidu, models: [], syncing: false },
  { id: 'xinghuo', name: '讯飞星火', key: '', baseUrl: DEFAULT_BASE_URLS.xinghuo, models: [], syncing: false },
  { id: 'stepfun', name: '阶跃星辰', key: '', baseUrl: DEFAULT_BASE_URLS.stepfun, models: [], syncing: false },
  { id: 'yi', name: '零一万物', key: '', baseUrl: DEFAULT_BASE_URLS.yi, models: [], syncing: false },
  { id: 'baichuan', name: '百川智能', key: '', baseUrl: DEFAULT_BASE_URLS.baichuan, models: [], syncing: false },
  { id: 'zhipu', name: '智谱 GLM', key: '', baseUrl: DEFAULT_BASE_URLS.zhipu, models: [], syncing: false },
  { id: 'hunyuan', name: '腾讯混元', key: '', baseUrl: DEFAULT_BASE_URLS.hunyuan, models: [], syncing: false },
  { id: 'moonshot', name: 'Kimi (月之暗面)', key: '', baseUrl: DEFAULT_BASE_URLS.moonshot, models: [], syncing: false },
  { id: 'groq', name: 'Groq (极速推理)', key: '', baseUrl: DEFAULT_BASE_URLS.groq, models: [], syncing: false },
  { id: 'together', name: 'Together AI', key: '', baseUrl: DEFAULT_BASE_URLS.together, models: [], syncing: false },
  { id: 'anthropic', name: 'Anthropic Claude', key: '', baseUrl: DEFAULT_BASE_URLS.anthropic, models: [], syncing: false },
  { id: 'gemini', name: 'Google Gemini', key: '', baseUrl: DEFAULT_BASE_URLS.gemini, models: [], syncing: false },
  { id: 'custom', name: '自定义提供商', key: '', baseUrl: '', models: [], syncing: false },
])

const customModelInputs = ref({})
const activeTab = ref('providers')

// ── 安全设置 ──
const yoloEnabled = ref(localStorage.getItem('vermes-yolo-default') !== 'false')
async function toggleYolo() {
  yoloEnabled.value = !yoloEnabled.value
  localStorage.setItem('vermes-yolo-default', yoloEnabled.value ? 'true' : 'false')
  // 同步到后端 config
  try {
    await fetch('/api/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ approvals: { yolo_default: yoloEnabled.value } }),
    })
  } catch (e) { console.error('[Config] Failed to sync yolo:', e) }
}

// ── 缓存监控 ──
const cacheMetrics = ref(null)
const cacheRefreshing = ref(false)

// ── 知识库 (RAG) ──
const ragDocs = ref([])
const ragLoading = ref(false)
const ragUploading = ref(false)
const ragDragging = ref(false)
const ragFileInput = ref(null)
const ragPreview = ref(null)  // { doc, chunks, loading }
const ragSearchQuery = ref('')
const ragSearchResults = ref([])
const ragSearching = ref(false)

async function fetchRagDocs() {
  ragLoading.value = true
  try {
    const resp = await fetch('/api/rag/documents')
    const data = await resp.json()
    ragDocs.value = data.documents || []
  } catch (e) {
    console.error('[RAG] fetch docs error:', e)
  } finally {
    ragLoading.value = false
  }
}

async function uploadRagFile(file) {
  ragUploading.value = true
  try {
    const reader = new FileReader()
    const b64 = await new Promise((resolve, reject) => {
      reader.onload = () => {
        const result = reader.result.split(',')[1]
        resolve(result)
      }
      reader.onerror = reject
      reader.readAsDataURL(file)
    })
    const resp = await fetch('/api/rag/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, content: b64, file_type: '' }),
    })
    const data = await resp.json()
    if (data.error) {
      toast.error(`上传失败: ${data.error}`)
    } else {
      toast.success(`${file.name} 已索引 (${data.chunks} 块)`)
      await fetchRagDocs()
    }
  } catch (e) {
    toast.error(`上传失败: ${e.message}`)
  } finally {
    ragUploading.value = false
  }
}

function onRagFileSelect(e) {
  const files = Array.from(e.target.files || [])
  files.forEach(uploadRagFile)
  if (ragFileInput.value) ragFileInput.value.value = ''
}

function onRagDrop(e) {
  ragDragging.value = false
  const files = Array.from(e.dataTransfer?.files || [])
  files.forEach(uploadRagFile)
}

async function deleteRagDoc(id) {
  if (!await confirm({ title: '删除文档', message: '删除这个文档？', confirmText: '删除', danger: true })) return
  try {
    const resp = await fetch(`/api/rag/delete/${id}`, { method: 'DELETE' })
    const data = await resp.json()
    if (data.deleted) {
      toast.success('已删除')
      await fetchRagDocs()
    } else {
      toast.error('删除失败')
    }
  } catch (e) {
    toast.error(`删除失败: ${e.message}`)
  }
}

function getFileIcon(type) {
  const t = (type || '').toLowerCase()
  if (['.md', '.markdown'].includes(t)) return '📝'
  if (['.py'].includes(t)) return '🐍'
  if (['.js', '.ts'].includes(t)) return '📜'
  if (['.json'].includes(t)) return '🗂️'
  if (['.csv', '.tsv'].includes(t)) return '📊'
  if (['.html', '.css'].includes(t)) return '🌐'
  if (['.sql'].includes(t)) return '🗄️'
  if (['.pdf'].includes(t)) return '📕'
  if (['.docx', '.doc'].includes(t)) return '📘'
  if (['.xlsx', '.xls'].includes(t)) return '📗'
  if (['.pptx', '.ppt'].includes(t)) return '📙'
  return '📄'
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function previewRagDoc(doc) {
  ragPreview.value = { doc, chunks: [], loading: true }
  try {
    const resp = await fetch(`/api/rag/chunks/${doc.id}`)
    const data = await resp.json()
    ragPreview.value = { doc, chunks: data.chunks || [], loading: false }
  } catch (e) {
    console.error('[RAG] preview error:', e)
    ragPreview.value = { doc, chunks: [], loading: false }
  }
}

function closeRagPreview() {
  ragPreview.value = null
}

async function runRagSearch() {
  const q = ragSearchQuery.value.trim()
  if (!q) return
  ragSearching.value = true
  ragSearchResults.value = []
  try {
    const resp = await fetch('/api/rag/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: q, limit: 5 })
    })
    const data = await resp.json()
    ragSearchResults.value = data.results || []
  } catch (e) {
    console.error('[RAG] search error:', e)
  } finally {
    ragSearching.value = false
  }
}

function clearRagSearch() {
  ragSearchQuery.value = ''
  ragSearchResults.value = []
}

async function fetchCacheMetrics() {
  cacheRefreshing.value = true
  try {
    const r = await fetch('/api/cache/metrics')
    if (r.ok) cacheMetrics.value = await r.json()
  } catch (e) {
    console.error('[CacheMetrics]', e)
  } finally {
    cacheRefreshing.value = false
  }
}

// 命中率颜色
function hitRateColor(rate) {
  if (rate >= 80) return 'text-green-600 dark:text-green-400'
  if (rate >= 50) return 'text-yellow-600 dark:text-yellow-400'
  return 'text-red-600 dark:text-red-400'
}

// ── API 接入 ──
const apiBaseUrl = ref(window.location.origin)
const apiTesting = ref(false)
const apiTestResult = ref(null)

function copyApiCurl() {
  const cmd = `curl -X POST ${apiBaseUrl.value}/api/agent/run -H 'Content-Type: application/json' -d '{"task":"检查磁盘空间"}'`
  navigator.clipboard.writeText(cmd).then(() => toast.success('✅ 已复制'))
}

async function testApi() {
  apiTesting.value = true
  apiTestResult.value = null
  try {
    const r = await fetch('/api/agent/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task: '回复“API 测试成功”这四个字', session_id: 'api-test' })
    })
    const data = await r.json()
    apiTestResult.value = data
  } catch (e) {
    apiTestResult.value = { ok: false, error: e.message }
  } finally {
    apiTesting.value = false
  }
}
const providerSearch = ref('')
const saved = ref(false)
const maxTokensInput = ref(null)
const expandedProviders = ref(new Set())
const storageUsage = ref(null)
const showAdvanced = ref(false)

function isExpanded(id) { return expandedProviders.value.has(id) }
function toggleProvider(id) {
  if (expandedProviders.value.has(id)) expandedProviders.value.delete(id)
  else expandedProviders.value.add(id)
}

const recommendedProviders = computed(() => {
  const q = providerSearch.value.trim().toLowerCase()
  return providers.value.filter(p => getRecommendedIds().includes(p.id) && (!q || p.name.toLowerCase().includes(q) || p.id.includes(q)))
})
const chineseProviders = computed(() => {
  const q = providerSearch.value.trim().toLowerCase()
  return providers.value.filter(p => CHINESE_IDS.includes(p.id) && !getRecommendedIds().includes(p.id) && (!q || p.name.toLowerCase().includes(q) || p.id.includes(q)))
})
const internationalProviders = computed(() => {
  const q = providerSearch.value.trim().toLowerCase()
  return providers.value.filter(p => INTERNATIONAL_IDS.includes(p.id) && (!q || p.name.toLowerCase().includes(q) || p.id.includes(q)))
})
const customProviders = computed(() => {
  const q = providerSearch.value.trim().toLowerCase()
  return providers.value.filter(p => p.id === 'custom' && (!q || p.name.toLowerCase().includes(q) || p.id.includes(q)))
})

// ── Provider 操作 ──

function getEnvKey(providerId) {
  const map = {
    deepseek: 'DEEPSEEK_API_KEY', openai: 'OPENAI_API_KEY',
    anthropic: 'ANTHROPIC_API_KEY', gemini: 'GEMINI_API_KEY',
    openrouter: 'OPENROUTER_API_KEY', vbit: 'VBIT_API_KEY',
    alibaba: 'QWEN_API_KEY', qwen: 'QWEN_API_KEY',
    zhipu: 'ZHIPU_API_KEY', doubao: 'DOUBAO_API_KEY',
    moonshot: 'MOONSHOT_API_KEY', baichuan: 'BAICHUAN_API_KEY',
    yi: 'YI_API_KEY', spark: 'SPARK_API_KEY',
    siliconflow: 'SILICONFLOW_API_KEY', mistral: 'MISTRAL_API_KEY',
    cohere: 'COHERE_API_KEY', custom: 'CUSTOM_API_KEY',
    xiaomi: 'XIAOMI_API_KEY', ollama: null, agnes: 'AGNES_API_KEY',
    'ant-ling': 'ANT_LING_API_KEY', minimax: 'MINIMAX_API_KEY',
    baidu: 'BAIDU_API_KEY', xinghuo: 'XINGHUO_API_KEY',
    stepfun: 'STEPFUN_API_KEY', groq: 'GROQ_API_KEY',
    together: 'TOGETHER_API_KEY',
  }
  return map[providerId] || providerId.toUpperCase() + '_API_KEY'
}

async function syncModels(p) {
  if (!p.key && p.id !== 'ollama') { toast.warning('请先填写 API Key'); return }
  if (!p.baseUrl) { toast.warning('请先填写 Base URL'); return }
  p.syncing = true
  try {
    if (p.id === 'ollama') {
      const resp = await fetch('/api/model/discover', { method: 'POST' })
      const data = await resp.json()
      if (data.ok) { p.models = data.models; saveProvidersToStorage() }
      else toast.error('同步失败: ' + (data.error || 'Ollama 未运行'))
      return
    }
    const body = { provider_id: p.id }
    if (p.baseUrl && p.baseUrl !== DEFAULT_BASE_URLS[p.id]) body.base_url = p.baseUrl
    if (p.key && p.key !== '●●●●●●●●') body.api_key = p.key
    const resp = await fetch('/api/provider/sync-models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    })
    const data = await resp.json()
    if (data.ok && data.models && data.models.length > 0) {
      const synced = new Set(data.models)
      const manual = (p.models || []).filter(m => !synced.has(m))
      p.models = [...data.models, ...manual]
      saveProvidersToStorage()
      if (manual.length > 0) toast.success('已同步 ' + data.models.length + ' 个模型，保留 ' + manual.length + ' 个手动添加的模型')
    } else {
      if (data.error) toast.error('同步失败: ' + data.error + '\n\n已保留现有 ' + (p.models||[]).length + ' 个模型')
      else toast.warning('该接口未返回模型列表，请手动添加模型名称')
    }
  } catch (e) {
    toast.error('同步请求失败: ' + e.message)
  } finally { p.syncing = false }
}

function addCustomModel(p, modelId) {
  if (!modelId) return
  if (!p.models) p.models = []
  if (!p.models.includes(modelId)) { p.models.push(modelId); saveProvidersToStorage() }
}

function removeModel(p, modelId) {
  p.models = p.models.filter(m => m !== modelId)
  saveProvidersToStorage()
}

async function deleteProvider(p) {
  if (!await confirm({ title: '清除配置', message: `确定清除 ${p.name} 的 API Key 和模型配置？`, confirmText: '清除', danger: true })) return
  const envKey = getEnvKey(p.id)
  try { await fetch('/api/env', { method: 'DELETE', headers: envHeaders(), body: JSON.stringify({ key: envKey }) }) } catch(e) {}
  p.key = ''; p.models = []; saveProvidersToStorage()
  saved.value = true; setTimeout(() => saved.value = false, 2000)
}

async function setCurrentModel(p, modelId) {
  const provider = p.id
  try {
    const resp = await fetch('/api/model/set', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'main', provider, model: modelId })
    })
    const data = await resp.json()
    if (!data.ok) { toast.error('设置失败: ' + (data.detail || JSON.stringify(data))); return }
    chat.setCurrentModel(modelId, provider)   // 3.4：统一走 store，取代 localStorage + window 事件中转
    saved.value = true; setTimeout(() => saved.value = false, 2000)
  } catch (e) { toast.error('设置失败: ' + e.message) }
}

async function loadMaxTokens() {
  try {
    const resp = await fetch('/api/model/info')
    const data = await resp.json()
    maxTokensInput.value = data.config_max_tokens || null
  } catch (e) {}
}

async function saveMaxTokens() {
  const maxTokens = maxTokensInput.value > 0 ? maxTokensInput.value : 0
  try {
    const resp = await fetch('/api/model/set', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'main', provider: localStorage.getItem('vermes-current-provider') || '', model: localStorage.getItem('vermes-current-model') || '', max_tokens: maxTokens })
    })
    const data = await resp.json()
    if (!data.ok) { toast.error('保存失败: ' + (data.detail || JSON.stringify(data))); return }
    toast.success(maxTokens > 0 ? `已设置 max_tokens = ${maxTokens}` : '已清除 max_tokens（让模型自己决定）')
  } catch (e) { toast.error('保存失败: ' + e.message) }
}

function saveProvidersToStorage() {
  const data = providers.value
    // A masked key ('●●●●●●●●') means "key already saved in .env" — keep it.
    // Previously this filter dropped masked providers, so saving *any* other
    // provider would silently drop a working one from storage and the UI
    // would show it as cleared on next open.
    .filter(p => (p.key && p.key.trim() !== '') || (p.models && p.models.length > 0) || (p.baseUrl && p.baseUrl !== DEFAULT_BASE_URLS[p.id]))
    .map(p => ({
      id: p.id, name: p.name,
      key: (p.key && p.key.trim() !== '') ? '***saved***' : '',
      baseUrl: p.baseUrl, models: p.models || []
    }))
  try { localStorage.setItem('vermes-providers', JSON.stringify(data)) } catch(e) {}
  // 通知 ChatHeader 等组件模型列表已更新
  window.dispatchEvent(new CustomEvent('providers-updated'))
}

async function save() {
  saveProvidersToStorage()
  let firstRealKey = null
  
  // 并行保存所有提供商配置
  const savePromises = []
  for (const p of providers.value) {
    if (p.key && p.key !== '●●●●●●●●' && p.id !== 'ollama') {
      const envKey = getEnvKey(p.id)
      savePromises.push(
        fetch('/api/env', { method: 'PUT', headers: envHeaders(), body: JSON.stringify({ key: envKey, value: p.key }) })
          .catch(() => {})
      )
      if (!firstRealKey && p.models && p.models.length > 0 && p.id !== 'vbit') firstRealKey = { id: p.id, name: p.name, model: p.models[0] }
    }
    if (p.baseUrl) {
      const payload = { provider_id: p.id, base_url: p.baseUrl }
      // Only include a real key. A masked provider ('●●●●●●●●') already has
      // its key in .env; sending the empty mask would wipe it. (Backend also
      // guards against empty api_key, but we avoid the bad request entirely.)
      if (p.key && p.key !== '●●●●●●●●') payload.api_key = p.key
      savePromises.push(
        fetch('/api/provider/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
          .catch(() => {})
      )
    }
  }
  
  await Promise.all(savePromises)
  
  if (firstRealKey) {
    const currentProvider = localStorage.getItem('vermes-current-provider')
    if (!currentProvider || currentProvider === 'vbit.top' || currentProvider === 'vbit') {
      chat.setCurrentModel(firstRealKey.model, firstRealKey.id)   // 3.4：统一走 store
    }
  }
  saved.value = true; setTimeout(() => saved.value = false, 2000)
}

async function clearAllSettings() {
  if (!await confirm({ title: '清除所有配置', message: '这将清除：\n- 所有提供商 API Key 和模型列表\n- 当前模型选择\n- 微信登录状态\n- 试用 Token\n\n聊天记录不受影响。', confirmText: '全部清除', danger: true })) return
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith('vermes-') && k !== 'vermes-sessions' && !k.startsWith('vermes-msgs-')) keys.push(k)
  }
  for (const k of keys) localStorage.removeItem(k)
  for (const p of providers.value) { p.key = ''; p.models = [] }
  window.dispatchEvent(new CustomEvent('providers-updated'))
  saved.value = true; setTimeout(() => saved.value = false, 2000)
}

// ── 服务 API（统一凭证表单，源自 GET /api/config/schema 的 services 分区） ──
const serviceGroups = ref({})      // sid -> { sid, label, apiKeyEnv, apiKeyVal, saved }
const servicesLoading = ref(false)
const servicesSaving = ref(false)
const _MASK = '●●●●●●●●'

async function loadServices() {
  servicesLoading.value = true
  try {
    // 1) schema -> 服务字段（category === 'services'）
    const schemaResp = await fetch('/api/config/schema')
    const schema = await schemaResp.json()
    const fields = schema.fields || {}
    const groups = {}
    for (const [key, f] of Object.entries(fields)) {
      if (f.category !== 'services') continue
      const m = key.match(/^services\.([^.]+)\.api_key$/)
      if (!m) continue
      const sid = m[1]
      groups[sid] = {
        sid,
        label: (f.description || '').replace(/ API key$/, '') || sid,
        apiKeyEnv: f.env_var || null,
        apiKeyVal: '',
        saved: false,
      }
    }
    // 2) env 状态 -> 已配置的服务显示掩码，避免保存时覆盖真实值
    try {
      const envResp = await fetch('/api/env', { headers: envHeaders() })
      const envData = await envResp.json()
      for (const sid of Object.keys(groups)) {
        const g = groups[sid]
        if (g.apiKeyEnv && envData[g.apiKeyEnv] && envData[g.apiKeyEnv].is_set) {
          g.apiKeyVal = _MASK
        }
      }
    } catch (e) { console.error('[Services] env status error:', e) }
    serviceGroups.value = groups
  } catch (e) {
    console.error('[Services] load schema error:', e)
  } finally {
    servicesLoading.value = false
  }
}

async function saveService(sid) {
  const g = serviceGroups.value[sid]
  if (!g || !g.apiKeyEnv) return
  if (!g.apiKeyVal || g.apiKeyVal === _MASK) { toast.warning('请输入 ' + g.label + ' 的 API Key 后再保存'); return }
  servicesSaving.value = true
  try {
    const resp = await fetch('/api/env', {
      method: 'PUT',
      headers: envHeaders(),
      body: JSON.stringify({ key: g.apiKeyEnv, value: g.apiKeyVal }),
    })
    if (resp.ok) {
      g.apiKeyVal = _MASK
      g.saved = true
      setTimeout(() => (g.saved = false), 2000)
      toast.success('已保存 ' + g.label)
    } else {
      const d = await resp.json().catch(() => ({}))
      toast.error('保存失败: ' + (d.detail || resp.status))
    }
  } catch (e) {
    toast.error('保存请求失败: ' + e.message)
  } finally {
    servicesSaving.value = false
  }
}

async function clearService(sid) {
  const g = serviceGroups.value[sid]
  if (!g || !g.apiKeyEnv) return
  if (!await confirm({ title: '清除 API Key', message: '清除 ' + g.label + ' 的 API Key？', confirmText: '清除', danger: true })) return
  try {
    const resp = await fetch('/api/env', {
      method: 'DELETE',
      headers: envHeaders(),
      body: JSON.stringify({ key: g.apiKeyEnv }),
    })
    if (resp.ok) {
      g.apiKeyVal = ''
      toast.success('已清除 ' + g.label)
    } else {
      toast.error('清除失败')
    }
  } catch (e) {
    toast.error('清除失败: ' + e.message)
  }
}

// 聚焦输入框时清除掩码，方便直接输入新 Key
function focusServiceKey(g) {
  if (g.apiKeyVal === _MASK) g.apiKeyVal = ''
}

// ── 文献源（category === 'literature'，源自 GET /api/registered-services） ──
// 与大模型厂商 API Key 同一套体验：用户自备 API Key / 网关地址 / 账号密码，
// 填入即启用对应文献库（多字段卡片，逐字段落盘到统一 .env）。
// 分组：内置付费源、免费源、自定义源
const literaturePaid = ref([])     // 内置付费源（IEEE/Scopus/知网等）
const literatureFree = ref([])     // 免费源（PubMed/arXiv/OpenAlex等）
const literatureCustom = ref([])   // 自定义源
const literatureLoading = ref(false)
const literatureSaving = ref(false)
const literatureExpanded = ref({}) // sid -> bool 展开状态
// 自定义文献源完整定义（用于编辑模态框预填），sid -> full definition
const customDefs = ref({})

// 本地文献库（用户本地文件夹 / USB）
const localLibs = ref([])
const localDraft = reactive({ path: '', label: '' })
const localAdding = ref(false)
const localMsg = ref('')
const localMsgOk = ref(true)
const showCustomModal = ref(false)
const customEditingId = ref(null)
const customSaving = ref(false)
const customForm = reactive({
  label: '', description: '', url: '', base_url: '',
  auth_scheme: 'bearer', api_key_header: 'X-API-KEY',
  query_param: 'q', method: 'GET',
  api_key: true, base_url_field: false, user: false, password: false,
  login_url: '', login_user_field: 'user', login_password_field: 'password', search_url: '',
})
// 粘贴凭证自动识别（卡号/密码/网址 → 一键接入）
const pasteBlock = ref('')
const pasteMsg = ref('')
const pasteLoading = ref(false)

function customFieldTypes() {
  const t = []
  if (customForm.api_key) t.push('api_key')
  if (customForm.base_url_field) t.push('base_url')
  if (customForm.user) t.push('user')
  if (customForm.password) t.push('password')
  return t
}

// 认证方式 ↔ 凭证字段联动
function onAuthSchemeChange() {
  const scheme = customForm.auth_scheme
  // 先全部清空
  customForm.api_key = false
  customForm.base_url_field = false
  customForm.user = false
  customForm.password = false
  // 根据认证方式自动勾选
  if (scheme === 'bearer' || scheme === 'header' || scheme === 'query') {
    customForm.api_key = true
  } else if (scheme === 'basic') {
    customForm.user = true
    customForm.password = true
  }
  // 'none' 不勾选任何字段
  // 'form' 卡号+密码表单登录（第三方文献网关）：自动勾选 账号/密码/接口地址
  if (scheme === 'form') {
    customForm.user = true
    customForm.password = true
    customForm.base_url_field = true
  }
}

function openAddCustom() {
  customEditingId.value = null
  Object.assign(customForm, {
    label: '', description: '', url: '', base_url: '',
    auth_scheme: 'bearer', api_key_header: 'X-API-KEY',
    query_param: 'q', method: 'GET',
    api_key: true, base_url_field: false, user: false, password: false,
    login_url: '', login_user_field: 'user', login_password_field: 'password', search_url: '',
  })
  showCustomModal.value = true
}

function openEditCustom(sid) {
  const d = customDefs.value[sid]
  if (!d) return
  customEditingId.value = sid
  const ft = new Set(d.field_types || [])
  Object.assign(customForm, {
    label: d.label || '', description: d.description || '', url: d.url || '',
    base_url: d.base_url || '', auth_scheme: d.auth_scheme || 'bearer',
    api_key_header: d.api_key_header || 'X-API-KEY', query_param: d.query_param || 'q',
    method: d.method || 'GET',
    api_key: ft.has('api_key'), base_url_field: ft.has('base_url'),
    user: ft.has('user'), password: ft.has('password'),
    login_url: d.login_url || '', login_user_field: d.login_user_field || 'user',
    login_password_field: d.login_password_field || 'password', search_url: d.search_url || '',
  })
  showCustomModal.value = true
}

function closeCustomModal() { showCustomModal.value = false }

async function saveCustom() {
  if (!customForm.label.trim()) { toast.warning('请填写文献库名称'); return }
  const ft = customFieldTypes()
  if (ft.length === 0 && customForm.auth_scheme !== 'none') { toast.warning('请至少勾选一种凭证字段（如 API Key）'); return }
  customSaving.value = true
  const payload = {
    label: customForm.label.trim(),
    description: customForm.description.trim(),
    url: customForm.url.trim(),
    base_url: customForm.base_url.trim(),
    auth_scheme: customForm.auth_scheme,
    api_key_header: customForm.api_key_header.trim() || 'X-API-KEY',
    query_param: customForm.query_param.trim() || 'q',
    method: customForm.method,
    field_types: ft,
  }
  if (customForm.auth_scheme === 'form') {
    payload.login_url = customForm.login_url.trim()
    payload.login_user_field = customForm.login_user_field.trim() || 'user'
    payload.login_password_field = customForm.login_password_field.trim() || 'password'
    payload.search_url = customForm.search_url.trim()
    payload.login_extra_fields = {}
  }
  try {
    const url = customEditingId.value
      ? `/api/literature-custom-sources/${customEditingId.value}`
      : '/api/literature-custom-sources'
    const resp = await fetch(url, {
      method: customEditingId.value ? 'PUT' : 'POST',
      headers: envHeaders(),
      body: JSON.stringify(payload),
    })
    const data = await resp.json().catch(() => ({}))
    if (resp.ok) {
      toast.success(customEditingId.value ? '已更新自定义文献库' : '已添加自定义文献库')
      closeCustomModal()
      await loadLiterature()
    } else {
      toast.error(data.detail || '保存失败')
    }
  } catch (e) {
    toast.error('保存失败: ' + e.message)
  } finally {
    customSaving.value = false
  }
}

async function deleteCustomSource(sid) {
  const d = customDefs.value[sid]
  const name = (d && d.label) || sid
  if (!await confirm({ title: '删除自定义文献库', message: `删除「${name}」？其已保存凭证也会一并清除。`, confirmText: '删除', danger: true })) return
  try {
    const resp = await fetch(`/api/literature-custom-sources/${sid}`, { method: 'DELETE', headers: envHeaders() })
    if (resp.ok) { toast.success('已删除 ' + name); await loadLiterature() }
    else { const dd = await resp.json().catch(() => ({})); toast.error(dd.detail || '删除失败') }
  } catch (e) { toast.error('删除失败: ' + e.message) }
}

// 粘贴凭证块 → 自动识别并一键接入为自定义文献源
async function parseAndAddSource() {
  const text = pasteBlock.value.trim()
  if (!text) { toast.warning('请粘贴文献库凭证（卡号/密码/网址）'); return }
  pasteLoading.value = true
  pasteMsg.value = ''
  try {
    const resp = await fetch('/api/literature-custom-sources/parse', {
      method: 'POST',
      headers: envHeaders(),
      body: JSON.stringify({ text }),
    })
    const data = await resp.json().catch(() => ({}))
    if (resp.ok && data.success) {
      const s = data.source || {}
      pasteMsg.value = `已识别并接入「${s.label || data.source_id}」` + (data.summary ? `（${data.summary}）` : '')
      if (data.warnings && data.warnings.length) {
        pasteMsg.value += ' ⚠️ ' + data.warnings.join('；')
      }
      toast.success('已识别并接入文献库')
      pasteBlock.value = ''
      await loadLiterature()
    } else {
      pasteMsg.value = data.detail || '识别失败'
      toast.error(data.detail || '识别失败')
    }
  } catch (e) {
    pasteMsg.value = '识别失败: ' + e.message
    toast.error('识别失败: ' + e.message)
  } finally {
    pasteLoading.value = false
  }
}

async function loadLiterature() {
  literatureLoading.value = true
  try {
    const resp = await fetch('/api/registered-services', { headers: envHeaders() })
    const data = await resp.json()
    const services = data.services || {}
    
    const paid = []
    const free = []
    const custom = []
    
    for (const [sid, meta] of Object.entries(services)) {
      if (meta.category !== 'literature') continue
      
      const fields = (meta.fields || []).map(f => ({
        key: f.key,
        label: f.label || f.key,
        secret: !!f.secret,
        val: '',
        isSet: false,
        saved: false,
      }))
      
      const item = {
        sid,
        label: meta.label || sid,
        description: meta.description || '',
        url: meta.url || '',
        fields,
        custom: !!meta.custom,
        // 仅无任何配置字段才是免费源（无需凭证即可使用）
        // CORE/SemanticScholar 有可选 API Key 字段，归入付费区但标记"可选"
        isFree: fields.length === 0,
        isOptional: fields.length > 0 && (
          (meta.description || '').includes('可选') ||
          (meta.description || '').includes('提升限额') ||
          (meta.description || '').includes('提升速率')
        ),
      }
      
      if (item.custom) {
        custom.push(item)
      } else if (item.isFree) {
        free.push(item)
      } else {
        paid.push(item)
      }
    }
    
    // 按名称排序
    paid.sort((a, b) => a.label.localeCompare(b.label))
    free.sort((a, b) => a.label.localeCompare(b.label))
    custom.sort((a, b) => a.label.localeCompare(b.label))
    
    // env 状态 -> 已配置字段显示掩码，避免保存时覆盖真实值
    try {
      const envResp = await fetch('/api/env', { headers: envHeaders() })
      const envData = await envResp.json()
      for (const g of [...paid, ...free, ...custom]) {
        for (const f of g.fields) {
          if (envData[f.key] && envData[f.key].is_set) {
            f.isSet = true
            f.val = _MASK
          }
        }
      }
    } catch (e) { console.error('[Literature] env status error:', e) }
    
    // 拉取自定义源完整定义（含端点/认证方式），供编辑模态框预填
    try {
      const cdResp = await fetch('/api/literature-custom-sources', { headers: envHeaders() })
      const cdData = await cdResp.json()
      const cmap = {}
      for (const s of (cdData.sources || [])) cmap[s.id] = s
      customDefs.value = cmap
    } catch (e) { console.error('[Literature] custom defs error:', e) }
    
    literaturePaid.value = paid
    literatureFree.value = free
    literatureCustom.value = custom
    await loadLocalLibraries()
  } catch (e) {
    console.error('[Literature] load error:', e)
  } finally {
    literatureLoading.value = false
  }
}

function litConfigured(g) {
  return g.fields.some(f => f.isSet)
}

// ── 本地文献库（文件夹 / USB）──

async function loadLocalLibraries() {
  try {
    const resp = await fetch('/api/literature-local-sources', { headers: envHeaders() })
    const data = await resp.json()
    localLibs.value = (data.sources || []).map(s => ({ ...s, _busy: false }))
  } catch (e) {
    console.error('[LocalLib] load error:', e)
  }
}

async function addLocalLibrary() {
  const path = (localDraft.path || '').trim()
  if (!path) { localMsg.value = '请填写文献文件夹路径'; localMsgOk.value = false; return }
  localAdding.value = true
  localMsg.value = ''
  try {
    const resp = await fetch('/api/literature-local-sources', {
      method: 'POST',
      headers: { ...envHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ root: path, label: localDraft.label.trim() || undefined }),
    })
    const data = await resp.json()
    if (resp.ok && data.ok) {
      localMsg.value = `已添加并索引：${data.source.label}（${ (data.source.index_summary?.indexed||0) + (data.source.index_summary?.updated||0) } 篇）`
      localMsgOk.value = true
      localDraft.path = ''
      localDraft.label = ''
      await loadLocalLibraries()
    } else {
      localMsg.value = data.detail || '添加失败'
      localMsgOk.value = false
    }
  } catch (e) {
    localMsg.value = '添加失败: ' + e.message
    localMsgOk.value = false
  } finally {
    localAdding.value = false
  }
}

async function reindexLocalLibrary(id) {
  const lib = localLibs.value.find(x => x.id === id)
  if (lib) lib._busy = true
  try {
    const resp = await fetch(`/api/literature-local-sources/${id}/index`, {
      method: 'POST', headers: envHeaders(),
    })
    const data = await resp.json()
    if (resp.ok && data.ok) {
      toast.success(`已重新索引：${data.summary?.indexed || 0} 篇新增`)
    } else {
      toast.error(data.detail || '重新索引失败')
    }
  } catch (e) {
    toast.error('重新索引失败: ' + e.message)
  } finally {
    if (lib) lib._busy = false
    await loadLocalLibraries()
  }
}

async function deleteLocalLibrary(id) {
  const lib = localLibs.value.find(x => x.id === id)
  if (!lib) return
  if (!await confirm({ title: '删除本地文献库', message: `删除「${lib.label}」？本地索引会一并清除（源文件夹本身不动）。`, confirmText: '删除', danger: true })) return
  try {
    const resp = await fetch(`/api/literature-local-sources/${id}`, { method: 'DELETE', headers: envHeaders() })
    if (resp.ok) { toast.success('已删除 ' + lib.label); await loadLocalLibraries() }
    else { const dd = await resp.json().catch(() => ({})); toast.error(dd.detail || '删除失败') }
  } catch (e) { toast.error('删除失败: ' + e.message) }
}

function focusLitField(f) {
  if (f.val === _MASK) f.val = ''
}

function _findLitGroup(sid) {
  return literaturePaid.value.find(x => x.sid === sid) 
      || literatureFree.value.find(x => x.sid === sid)
      || literatureCustom.value.find(x => x.sid === sid)
}

async function saveLiterature(sid) {
  const g = _findLitGroup(sid)
  if (!g) return
  const dirty = g.fields.filter(f => f.val && f.val !== _MASK)
  if (dirty.length === 0) { toast.warning('请先填写 ' + g.label + ' 的凭证字段再保存'); return }
  literatureSaving.value = true
  let okCount = 0
  try {
    for (const f of dirty) {
      const resp = await fetch('/api/env', {
        method: 'PUT',
        headers: envHeaders(),
        body: JSON.stringify({ key: f.key, value: f.val }),
      })
      if (resp.ok) {
        f.val = _MASK
        f.isSet = true
        f.saved = true
        setTimeout(() => (f.saved = false), 2000)
        okCount++
      } else {
        const d = await resp.json().catch(() => ({}))
        toast.error(f.label + ' 保存失败: ' + (d.detail || resp.status))
      }
    }
    if (okCount > 0) toast.success('已保存 ' + g.label + '（' + okCount + ' 个字段）')
  } catch (e) {
    toast.error('保存请求失败: ' + e.message)
  } finally {
    literatureSaving.value = false
  }
}

async function clearLiterature(sid) {
  const g = _findLitGroup(sid)
  if (!g) return
  if (!await confirm({ title: '清除文献源凭证', message: '清除 ' + g.label + ' 的全部已保存凭证？', confirmText: '清除', danger: true })) return
  try {
    for (const f of g.fields) {
      if (!f.isSet) continue
      const resp = await fetch('/api/env', {
        method: 'DELETE',
        headers: envHeaders(),
        body: JSON.stringify({ key: f.key }),
      })
      if (resp.ok) { f.val = ''; f.isSet = false }
    }
    toast.success('已清除 ' + g.label)
  } catch (e) {
    toast.error('清除失败: ' + e.message)
  }
}

function back() { router.push('/') }

// ── 2.5 逐提供商连接测试（复用 sync-models 接口校验 Key 有效性）──
async function testProvider(p) {
  p.testing = true
  p.testResult = null
  try {
    if (!p.key && p.id !== 'ollama') {
      p.testResult = { ok: false, error: '请先填写 API Key' }
      return
    }
    let data
    if (p.id === 'ollama') {
      const resp = await fetch('/api/model/discover', { method: 'POST' })
      data = await resp.json()
    } else {
      const body = { provider_id: p.id }
      if (p.baseUrl && p.baseUrl !== DEFAULT_BASE_URLS[p.id]) body.base_url = p.baseUrl
      if (p.key && p.key !== '●●●●●●●●') body.api_key = p.key
      const resp = await fetch('/api/provider/sync-models', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      })
      data = await resp.json()
    }
    if (data && data.ok) {
      p.testResult = { ok: true, message: '✅ 连接成功' + (data.models ? `（${data.models.length} 个模型）` : '') }
    } else {
      p.testResult = { ok: false, error: (data && data.error) || '连接失败' }
    }
  } catch (e) {
    p.testResult = { ok: false, error: e.message }
  } finally {
    p.testing = false
  }
}
function onCardTest(p) { testProvider(p) }

// ── ProviderCard 事件路由 ──
function onCardSync(p) { syncModels(p) }
function onCardSave() { save() }
function onCardDelete(p) { deleteProvider(p) }
function onCardSetModel(p, modelId) { setCurrentModel(p, modelId) }
function onCardAddModel(p, modelId) { addCustomModel(p, modelId) }

function addCustomProvider() {
  const id = 'custom-' + Date.now()
  providers.value.push({
    id, name: '自定义', key: '', baseUrl: '', models: [], syncing: false
  })
  expandedProviders.value.add(id)
}
function onCardRemoveModel(p, modelId) { removeModel(p, modelId) }
function onCardToggle(id) { toggleProvider(id) }

const _onTrialToken = (e) => {
  const { token } = e.detail
  if (!token) return
  const vbit = providers.value.find(p => p.id === 'vbit')
  if (vbit) { vbit.key = token; saveProvidersToStorage() }
}

onMounted(() => {
  const saved_data = localStorage.getItem('vermes-providers')
  if (saved_data) {
    try {
      const parsed = JSON.parse(saved_data)
      for (const p of parsed) {
        const target = providers.value.find(pp => pp.id === p.id)
        if (target) {
          if (p.key === '***saved***') target.key = '●●●●●●●●'
          if (p.baseUrl) target.baseUrl = p.baseUrl
          if (p.models && p.models.length > 0) target.models = p.models
        } else {
          providers.value.push({
            id: p.id, name: p.name,
            key: p.key === '***saved***' ? '●●●●●●●●' : (p.key || ''),
            baseUrl: p.baseUrl || '', models: p.models || [], syncing: false
          })
        }
      }
    } catch(e) {}
  }
  window.addEventListener('trial-token', _onTrialToken)
  loadMaxTokens()
  // 加载存储用量
  fetch('/api/storage/usage').then(r => r.ok && r.json().then(d => storageUsage.value = d)).catch(() => {})
  // 加载缓存性能指标
  fetchCacheMetrics()
  // 加载知识库文档列表
  fetchRagDocs()
  // 加载统一服务 API 凭证表单（services 分区）
  loadServices()
  // 加载文献源凭证表单（literature 分区）
  loadLiterature()
  // 加载移动渠道（channels 分区）
  loadChannels()
})

onUnmounted(() => { window.removeEventListener('trial-token', _onTrialToken) })

// ── 移动接入渠道（gateway channels） ──
const channelsData = ref(null)       // { channels, grouped, total, configured_count }
const channelsLoading = ref(false)
const channelExpanded = reactive({})  // { platform_key: true/false }
const channelForms = reactive({})     // { platform_key: { field_key: value } }
const channelSaving = ref(false)

const channelCategories = computed(() => {
  if (!channelsData.value?.grouped) return []
  return Object.entries(channelsData.value.grouped).map(([cat, items]) => ({ cat, items }))
})

async function loadChannels() {
  channelsLoading.value = true
  try {
    const data = await api.default.listGatewayChannels()
    channelsData.value = data
    // 初始化表单数据
    for (const ch of data.channels || []) {
      channelForms[ch.key] = {}
      for (const f of ch.fields) {
        channelForms[ch.key][f.key] = f.has_value ? '' : ''  // 不回显密钥，用户重新输入
      }
    }
  } catch (e) {
    console.error('loadChannels:', e)
  } finally {
    channelsLoading.value = false
  }
}

async function saveChannel(platformKey) {
  const form = channelForms[platformKey]
  if (!form) return
  channelSaving.value = true
  try {
    // 只发送有值的字段
    const fields = {}
    for (const [k, v] of Object.entries(form)) {
      if (v && v.trim()) fields[k] = v.trim()
    }
    const result = await api.default.saveGatewayChannel(platformKey, fields)
    if (result.ok) {
      // 更新 UI 状态
      if (channelsData.value) {
        const idx = channelsData.value.channels.findIndex(c => c.key === platformKey)
        if (idx >= 0) channelsData.value.channels[idx] = result.channel
        // 更新 grouped
        for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
          const i2 = items.findIndex(c => c.key === platformKey)
          if (i2 >= 0) items[i2] = result.channel
        }
      }
      // 清空表单
      for (const k of Object.keys(form)) form[k] = ''
      toast.success('渠道凭据已保存')
    }
  } catch (e) {
    toast.error('保存失败: ' + (e.message || e))
  } finally {
    channelSaving.value = false
  }
}

async function clearChannel(platformKey) {
  const ch = channelsData.value?.channels.find(c => c.key === platformKey)
  if (!ch) return
  if (!await confirm({ title: '清除渠道凭据', message: `确认清除 ${ch.label} 的凭据？`, confirmText: '清除', danger: true })) return
  try {
    await api.default.clearGatewayChannel(platformKey)
    // 更新 UI
    if (channelsData.value) {
      const idx = channelsData.value.channels.findIndex(c => c.key === platformKey)
      if (idx >= 0) {
        channelsData.value.channels[idx].configured = false
        channelsData.value.channels[idx].enabled = false
        for (const f of channelsData.value.channels[idx].fields) {
          f.value = ''
          f.has_value = false
        }
      }
      for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
        const i2 = items.findIndex(c => c.key === platformKey)
        if (i2 >= 0) {
          items[i2].configured = false
          items[i2].enabled = false
          for (const f of items[i2].fields) {
            f.value = ''
            f.has_value = false
          }
        }
      }
    }
    toast.success('已清除凭据')
  } catch (e) {
    toast.error('清除失败: ' + (e.message || e))
  }
}

async function toggleChannel(platformKey) {
  try {
    const result = await api.default.toggleGatewayChannel(platformKey)
    if (result.ok) {
      // 更新 UI
      if (channelsData.value) {
        const ch = channelsData.value.channels.find(c => c.key === platformKey)
        if (ch) ch.enabled = result.enabled
        for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
          const i2 = items.findIndex(c => c.key === platformKey)
          if (i2 >= 0) items[i2].enabled = result.enabled
        }
      }
      toast.success(result.enabled ? '已启用' : '已禁用')
    }
  } catch (e) {
    toast.error('操作失败: ' + (e.message || e))
  }
}
</script>

<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900">
    <!-- 顶部 -->
    <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center gap-3 bg-white dark:bg-gray-800">
      <button @click="back()" class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/></svg>
      </button>
      <h2 class="font-semibold text-gray-800 dark:text-gray-200">设置</h2>
    </div>

    <!-- Tab 栏 -->
    <div class="px-4 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex gap-0">
      <button @click="activeTab = 'providers'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'providers' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">提供商</button>
      <button @click="activeTab = 'services'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'services' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">🔑 服务</button>
      <button @click="activeTab = 'literature'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'literature' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">📖 文献源</button>
      <button @click="activeTab = 'channels'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'channels' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">📱 移动接入</button>
      <button @click="activeTab = 'security'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'security' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">🔒 安全</button>
      <button @click="activeTab = 'knowledge'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'knowledge' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">📚 知识库</button>
      <button @click="activeTab = 'about'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'about' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">关于</button>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto p-6 bg-gray-50 dark:bg-gray-900">

      <!-- 提供商配置 -->
      <div v-if="activeTab === 'providers'" class="max-w-2xl space-y-3">
        <div class="flex items-center gap-3 mb-2">
          <p class="text-sm text-gray-500 dark:text-gray-400 flex-1">配置 API Key 后点击「同步模型」自动获取可用模型</p>
          <div class="relative">
            <input v-model="providerSearch" placeholder="搜索提供商…" class="pl-8 pr-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500 w-40" />
            <span class="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400 text-xs">🔍</span>
          </div>
        </div>

        <!-- ⚙️ 全局模型设置 -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-4">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-lg">⚙️</span>
            <h3 class="font-medium text-gray-800 dark:text-gray-200">模型设置</h3>
          </div>
          <div class="flex items-center gap-3">
            <label class="text-sm text-gray-600 dark:text-gray-400 whitespace-nowrap">输出上限 (max_tokens)</label>
            <input v-model.number="maxTokensInput" type="number" min="0" placeholder="不设置（让模型自己决定）"
              class="w-40 px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
            <button @click="saveMaxTokens()" class="px-3 py-1.5 bg-green-500 hover:bg-green-600 text-white rounded-lg text-xs font-medium transition">保存</button>
          </div>
          <p class="text-xs text-gray-400 dark:text-gray-500 mt-2">不设置 = 让模型自己决定输出长度（推荐）。设置固定值可控制成本，如 4096 = 约 2000 中文字。</p>
        </div>

        <!-- 🌟 推荐区 -->
        <div class="space-y-3">
          <div class="flex items-center gap-2 mb-2">
            <span class="text-lg">🌟</span>
            <h3 class="font-medium text-gray-800 dark:text-gray-200">推荐</h3>
          </div>

          <!-- vbit 免费体验 (特殊卡片，不用 ProviderCard) -->
          <div class="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-xl p-4">
            <div class="flex items-center gap-3 mb-2">
              <div class="w-10 h-10 bg-green-500 rounded-xl flex items-center justify-center text-white font-bold">V</div>
              <div>
                <div class="font-medium text-green-700 dark:text-green-300">🔥 vbit.top 免费体验</div>
                <div class="text-xs text-green-600 dark:text-green-400">微信扫码登录即可免费使用 Agnes AI</div>
              </div>
            </div>
            <div class="text-xs text-green-600 dark:text-green-400">✅ 微信登录即用 · ✅ 无需 API Key · ✅ Agnes AI 免费驱动</div>
          </div>

          <!-- DeepSeek / Agnes / MiMo / Ollama — 使用 ProviderCard -->
          <ProviderCard
            v-for="p in providers.filter(pr => ['deepseek','agnes','xiaomi','ollama'].includes(pr.id))"
            :key="p.id"
            :provider="p"
            :expanded="isExpanded(p.id)"
            :icon-class="PROVIDER_EXTRAS[p.id]?.iconClass || ''"
            :icon-text="PROVIDER_EXTRAS[p.id]?.iconText || p.name.charAt(0)"
            :description="PROVIDER_EXTRAS[p.id]?.description || ''"
            :link-url="PROVIDER_EXTRAS[p.id]?.linkUrl || ''"
            :link-text="PROVIDER_EXTRAS[p.id]?.linkText || ''"
            :hide-key-input="PROVIDER_EXTRAS[p.id]?.hideKeyInput || false"
            :default-base-url="DEFAULT_BASE_URLS[p.id] || ''"
            :show-delete="true"
            @toggle="onCardToggle"
            @sync="onCardSync"
            @save="onCardSave"
            @delete="onCardDelete"
            @set-model="onCardSetModel"
            @add-model="onCardAddModel"
            @remove-model="onCardRemoveModel"
            @test="onCardTest"
          />
        </div>

        <!-- ⚙️ 高级选项 -->
        <div class="pt-4 border-t border-gray-200 dark:border-gray-700">
          <button @click="showAdvanced = !showAdvanced" class="flex items-center gap-2 text-sm text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition">
            <svg class="w-4 h-4 transition-transform" :class="showAdvanced ? 'rotate-90' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7"/></svg>
            ⚙️ 高级选项（其他提供商）
          </button>

          <div v-if="showAdvanced" class="mt-3 space-y-3">
            <!-- 🇨🇳 国产模型 -->
            <div>
              <div class="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2">🇨🇳 国产模型</div>
              <div class="space-y-2">
                <ProviderCard v-for="p in chineseProviders" :key="p.id"
                  :provider="p" :expanded="isExpanded(p.id)" compact
                  :show-delete="true" :default-base-url="DEFAULT_BASE_URLS[p.id] || ''"
                  @toggle="onCardToggle" @sync="onCardSync" @save="onCardSave" @delete="onCardDelete"
                  @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel" @test="onCardTest"
                />
              </div>
            </div>

            <!-- 🌍 国际模型 -->
            <div>
              <div class="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2">🌍 国际模型</div>
              <div class="space-y-2">
                <ProviderCard v-for="p in internationalProviders" :key="p.id"
                  :provider="p" :expanded="isExpanded(p.id)" compact
                  :show-delete="true" :default-base-url="DEFAULT_BASE_URLS[p.id] || ''"
                  @toggle="onCardToggle" @sync="onCardSync" @save="onCardSave" @delete="onCardDelete"
                  @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel" @test="onCardTest"
                />
              </div>
            </div>

            <!-- 🔧 自定义 -->
            <div>
              <div class="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2">🔧 自定义</div>
              <ProviderCard v-for="p in customProviders" :key="p.id"
                :provider="p" :expanded="isExpanded(p.id)" compact
                :show-delete="true" :default-base-url="''"
                @toggle="onCardToggle" @sync="onCardSync" @save="onCardSave" @delete="onCardDelete"
                @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel" @test="onCardTest"
              />
              <button @click="addCustomProvider"
                class="mt-2 w-full py-2 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-xl text-sm text-gray-400 hover:text-green-500 hover:border-green-400 dark:hover:border-green-600 transition">
                ＋ 添加自定义提供商
              </button>
            </div>
          </div>
        </div>

        <span v-if="saved" class="text-green-500 text-sm">✅ 已保存</span>

        <!-- Clear all -->
        <div class="pt-4 border-t border-gray-200 dark:border-gray-700">
          <button @click="clearAllSettings()" class="px-4 py-2 bg-red-50 dark:bg-red-900/20 hover:bg-red-100 dark:hover:bg-red-900/40 text-red-500 rounded-lg text-xs font-medium transition w-full border border-red-200 dark:border-red-800">
            🔄 清除所有本地配置（保留聊天记录）
          </button>
          <p class="text-xs text-gray-400 mt-2 text-center">清除 API Key、模型列表、微信登录状态、试用 Token 等配置历史</p>
        </div>
      </div>

      <!-- 服务（统一 API 凭证，源自 GET /api/config/schema 的 services 分区） -->
      <div v-if="activeTab === 'services'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <div class="flex items-center gap-2">
            <span class="text-lg">🔑</span>
            <h3 class="font-medium text-gray-800 dark:text-gray-200">服务 API 凭证</h3>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400">所有插件 / 工具 / 技能所需的额外 API Key 统一在此配置，与 Agent 共享同一套读取逻辑，无需逐插件单独设置。</p>

          <div v-if="servicesLoading" class="text-center text-sm text-gray-400 py-4">
            <div class="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-green-500 rounded-full mr-1"></div> 加载中...
          </div>
          <div v-else-if="Object.keys(serviceGroups).length === 0" class="text-center text-sm text-gray-400 py-4">暂无已注册的服务</div>
          <div v-else class="space-y-3">
            <div v-for="g in Object.values(serviceGroups)" :key="g.sid" class="p-4 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/50 space-y-2">
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ g.label }}</span>
                <span v-if="g.saved" class="text-green-500 text-xs">✅ 已保存</span>
              </div>
              <div class="flex gap-2">
                <input
                  v-model="g.apiKeyVal"
                  type="password"
                  :placeholder="g.apiKeyEnv"
                  @focus="focusServiceKey(g)"
                  class="flex-1 px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none font-mono"
                />
                <button @click="saveService(g.sid)" :disabled="servicesSaving" class="px-4 py-2 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">保存</button>
                <button @click="clearService(g.sid)" class="px-3 py-2 text-sm rounded-lg text-gray-400 hover:text-red-500 border border-gray-300 dark:border-gray-600 whitespace-nowrap">清除</button>
              </div>
              <p class="text-[11px] text-gray-400 font-mono">{{ g.apiKeyEnv }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- 文献源 -->
      <div v-if="activeTab === 'literature'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <div class="flex items-center justify-between">
            <div class="flex items-center gap-2">
              <span class="text-lg">📖</span>
              <h3 class="font-medium text-gray-800 dark:text-gray-200">文献源设置</h3>
            </div>
            <button @click="openAddCustom" class="px-3 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 whitespace-nowrap">➕ 添加自定义文献库</button>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400">与大模型厂商 API Key 相同的体验：用户自备 API Key / 网关地址 / 账号密码，填入即启用对应文献库。Agent 会自动路由到已配置的最优源。</p>

          <!-- 粘贴凭证自动识别 -->
          <div class="rounded-lg border border-dashed border-green-300 dark:border-green-700 bg-green-50/40 dark:bg-green-900/10 p-4 space-y-2">
            <div class="flex items-center gap-2">
              <span class="text-sm">📋</span>
              <span class="text-sm font-medium text-gray-700 dark:text-gray-200">粘贴凭证自动识别</span>
            </div>
            <p class="text-[11px] text-gray-500 dark:text-gray-400">把商家给的卡号/密码/网址整段粘贴进来，Vermes 会自动识别并接入为自定义文献源（如书童等第三方卡号卡密文献网关）。凭证仅存本机 .env 并自动掩码。</p>
            <textarea v-model="pasteBlock" rows="3" placeholder="例如：&#10;卡号：83219570&#10;密码：335779&#10;复制网址 http://3.shutong2.com/ 到浏览器登录即可" class="w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono resize-y"></textarea>
            <div class="flex items-center gap-2">
              <button @click="parseAndAddSource" :disabled="pasteLoading" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">识别并接入</button>
              <span v-if="pasteLoading" class="text-xs text-gray-400">识别中…</span>
            </div>
            <p v-if="pasteMsg" class="text-[11px] text-green-600 dark:text-green-400 break-all">{{ pasteMsg }}</p>
          </div>

          <div v-if="literatureLoading" class="text-center text-sm text-gray-400 py-4">
            <div class="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-green-500 rounded-full mr-1"></div> 加载中...
          </div>
          <div v-else-if="literaturePaid.length === 0 && literatureFree.length === 0 && literatureCustom.length === 0" class="text-center text-sm text-gray-400 py-4">暂无已注册的文献源</div>
          
          <div v-else class="space-y-4">
            <!-- 免费源（开箱即用） -->
            <div v-if="literatureFree.length > 0" class="space-y-2">
              <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <span class="text-green-500">✓</span> 免费源（开箱即用，{{ literatureFree.length }} 个）
              </h4>
              <div class="grid grid-cols-2 gap-2">
                <div v-for="g in literatureFree" :key="g.sid" class="p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30">
                  <div class="flex items-center justify-between">
                    <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ g.label }}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400">已启用</span>
                  </div>
                  <p v-if="g.description" class="text-[11px] text-gray-400 mt-1 truncate" :title="g.description">{{ g.description }}</p>
                </div>
              </div>
            </div>

            <!-- 内置付费源 -->
            <div v-if="literaturePaid.length > 0" class="space-y-2">
              <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <span>🔐</span> 内置付费源（填入凭证启用，{{ literaturePaid.length }} 个）
              </h4>
              <div class="space-y-2">
                <div v-for="g in literaturePaid" :key="g.sid" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 overflow-hidden">
                  <!-- 头部：点击展开 -->
                  <div @click="literatureExpanded[g.sid] = !literatureExpanded[g.sid]" class="p-3 flex items-center justify-between cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition">
                    <div class="flex items-center gap-2">
                      <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ g.label }}</span>
                      <span v-if="litConfigured(g)" class="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400">已配置</span>
                      <span v-if="g.isOptional" class="text-[10px] px-1.5 py-0.5 rounded bg-yellow-100 dark:bg-yellow-900/40 text-yellow-600 dark:text-yellow-400">可选</span>
                    </div>
                    <div class="flex items-center gap-2">
                      <a v-if="g.url" :href="g.url" target="_blank" rel="noopener" @click.stop class="text-[11px] text-blue-500 hover:underline">申请入口 ↗</a>
                      <span class="text-gray-400 text-xs">{{ literatureExpanded[g.sid] ? '▼' : '▶' }}</span>
                    </div>
                  </div>
                  <!-- 展开内容：凭证字段 -->
                  <div v-if="literatureExpanded[g.sid]" class="px-3 pb-3 space-y-2 border-t border-gray-200 dark:border-gray-700">
                    <p v-if="g.description" class="text-[11px] text-gray-400 pt-2">{{ g.description }}</p>
                    <div v-for="f in g.fields" :key="f.key" class="flex gap-2 items-center">
                      <label class="w-32 shrink-0 text-xs text-gray-500 dark:text-gray-400 truncate" :title="f.key">{{ f.label }}</label>
                      <input
                        v-model="f.val"
                        :type="f.secret ? 'password' : 'text'"
                        :placeholder="f.key"
                        @focus="focusLitField(f)"
                        class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none font-mono"
                      />
                      <span v-if="f.saved" class="text-green-500 text-xs whitespace-nowrap">✅</span>
                    </div>
                    <div class="flex gap-2 justify-end pt-1">
                      <button @click="saveLiterature(g.sid)" :disabled="literatureSaving" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">保存</button>
                      <button @click="clearLiterature(g.sid)" class="px-3 py-1.5 text-sm rounded-lg text-gray-400 hover:text-red-500 border border-gray-300 dark:border-gray-600 whitespace-nowrap">清除</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 自定义源 -->
            <div v-if="literatureCustom.length > 0" class="space-y-2">
              <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <span>📚</span> 自定义文献库（{{ literatureCustom.length }} 个）
              </h4>
              <div class="space-y-2">
                <div v-for="g in literatureCustom" :key="g.sid" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 overflow-hidden">
                  <!-- 头部：点击展开 -->
                  <div @click="literatureExpanded[g.sid] = !literatureExpanded[g.sid]" class="p-3 flex items-center justify-between cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition">
                    <div class="flex items-center gap-2">
                      <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ g.label }}</span>
                      <span v-if="litConfigured(g)" class="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400">已配置</span>
                      <span class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400">自定义</span>
                    </div>
                    <div class="flex items-center gap-2">
                      <button @click.stop="openEditCustom(g.sid)" class="text-[11px] text-gray-400 hover:text-green-500">✎ 编辑</button>
                      <button @click.stop="deleteCustomSource(g.sid)" class="text-[11px] text-gray-400 hover:text-red-500">🗑 删除</button>
                      <span class="text-gray-400 text-xs">{{ literatureExpanded[g.sid] ? '▼' : '▶' }}</span>
                    </div>
                  </div>
                  <!-- 展开内容：凭证字段 -->
                  <div v-if="literatureExpanded[g.sid]" class="px-3 pb-3 space-y-2 border-t border-gray-200 dark:border-gray-700">
                    <p v-if="g.description" class="text-[11px] text-gray-400 pt-2">{{ g.description }}</p>
                    <div v-for="f in g.fields" :key="f.key" class="flex gap-2 items-center">
                      <label class="w-32 shrink-0 text-xs text-gray-500 dark:text-gray-400 truncate" :title="f.key">{{ f.label }}</label>
                      <input
                        v-model="f.val"
                        :type="f.secret ? 'password' : 'text'"
                        :placeholder="f.key"
                        @focus="focusLitField(f)"
                        class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none font-mono"
                      />
                      <span v-if="f.saved" class="text-green-500 text-xs whitespace-nowrap">✅</span>
                    </div>
                    <div class="flex gap-2 justify-end pt-1">
                      <button @click="saveLiterature(g.sid)" :disabled="literatureSaving" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">保存</button>
                      <button @click="clearLiterature(g.sid)" class="px-3 py-1.5 text-sm rounded-lg text-gray-400 hover:text-red-500 border border-gray-300 dark:border-gray-600 whitespace-nowrap">清除</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- 本地文献库（用户本地文件夹 / USB） -->
            <div class="mt-6 pt-4 border-t border-gray-200 dark:border-gray-700 space-y-2">
              <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <span>💾</span> 本地文献库（文件夹 / USB）
              </h4>
              <div class="rounded-lg border border-dashed border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-700/30 p-4 space-y-2">
                <p class="text-[11px] text-gray-500 dark:text-gray-400">把你已准备好的文献文件夹（PDF / BibTeX / RIS 导出）路径填进来，Vermes 会建立本地索引；普通检索会自动并入这些本地论文，引号级引用与文献核实也能直接用本地 PDF 全文。</p>
                <div class="flex gap-2">
                  <input v-model="localDraft.path" placeholder="/Volumes/USB/文献 或 /Users/you/papers" class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
                  <input v-model="localDraft.label" placeholder="名称（可选）" class="w-40 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none" />
                  <button @click="addLocalLibrary" :disabled="localAdding" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">添加并索引</button>
                </div>
                <p v-if="localMsg" class="text-[11px]" :class="localMsgOk ? 'text-green-600 dark:text-green-400' : 'text-red-500'">{{ localMsg }}</p>
              </div>
              <div v-if="localLibs.length > 0" class="space-y-2">
                <div v-for="l in localLibs" :key="l.id" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 p-3">
                  <div class="flex items-center justify-between">
                    <div class="min-w-0">
                      <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ l.label }}</span>
                      <span class="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-600 text-gray-500 dark:text-gray-300 ml-1">本地</span>
                    </div>
                    <div class="flex items-center gap-2">
                      <button @click="reindexLocalLibrary(l.id)" :disabled="l._busy" class="text-[11px] text-gray-400 hover:text-green-500">↻ 重新索引</button>
                      <button @click="deleteLocalLibrary(l.id)" class="text-[11px] text-gray-400 hover:text-red-500">🗑 删除</button>
                    </div>
                  </div>
                  <p class="text-[11px] text-gray-400 mt-1 truncate font-mono" :title="l.root">{{ l.root }}</p>
                  <p class="text-[11px] text-gray-400 mt-0.5">
                    状态：{{ l.status === 'indexed' ? '已索引' : (l.status === 'error' ? '索引异常' : '待索引') }} · 文献 {{ l.file_count || 0 }} 篇
                    <template v-if="l.index_summary"> · 扫描 {{ l.index_summary.scanned }}，新增 {{ l.index_summary.indexed }}<template v-if="l.index_summary.errors">，错误 {{ l.index_summary.errors }}</template></template>
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 自定义文献库 模态框 -->
      <div v-if="showCustomModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" @click.self="closeCustomModal">
        <div class="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="font-medium text-gray-800 dark:text-gray-200">{{ customEditingId ? '编辑自定义文献库' : '添加自定义文献库' }}</h3>
            <button @click="closeCustomModal" class="text-gray-400 hover:text-gray-600 text-lg leading-none">✕</button>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400">用于接入高校 / 医院 / 企事业研究机构自建的内部文献数据库。填写接口与认证方式后，即可像其他文献源一样在 Agent 论文写作与文献检索中使用；凭证统一存入 .env 并自动掩码。</p>

          <div class="space-y-3">
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">名称 *</label>
              <input v-model="customForm.label" placeholder="如：我校医学图书馆" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none" />
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">描述</label>
              <input v-model="customForm.description" placeholder="可选，便于辨识" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none" />
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">接口地址（API 端点）</label>
              <input v-model="customForm.base_url" placeholder="https://api.your-lib.edu/search" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">主页链接</label>
              <input v-model="customForm.url" placeholder="https://lib.your-org.edu（可选）" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none" />
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">认证方式</label>
                <select v-model="customForm.auth_scheme" @change="onAuthSchemeChange" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none">
                  <option value="bearer">Bearer Token</option>
                  <option value="basic">账号密码 Basic</option>
                  <option value="header">自定义 Header</option>
                  <option value="query">Query 参数</option>
                  <option value="form">卡号+密码表单登录（第三方文献网关）</option>
                  <option value="none">无需认证</option>
                </select>
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">请求方法</label>
                <select v-model="customForm.method" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none">
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                </select>
              </div>
            </div>
            <div v-if="customForm.auth_scheme === 'header'">
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">自定义 Header 名</label>
              <input v-model="customForm.api_key_header" placeholder="X-API-KEY" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
            </div>
            <div v-if="customForm.auth_scheme === 'form'" class="space-y-3 border-t border-gray-200 dark:border-gray-700 pt-3">
              <p class="text-[11px] text-gray-500 dark:text-gray-400">表单登录网关：先 POST 登录拿到会话，再带会话检索。字段名若与商家网站不一致，请按实际网页表单的 name 修改。</p>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">登录地址（表单提交 URL）</label>
                <input v-model="customForm.login_url" placeholder="http://3.shutong2.com/login" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
              </div>
              <div class="grid grid-cols-2 gap-3">
                <div>
                  <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">登录用户名表单字段名</label>
                  <input v-model="customForm.login_user_field" placeholder="user / username / card" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
                </div>
                <div>
                  <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">登录密码表单字段名</label>
                  <input v-model="customForm.login_password_field" placeholder="password / pwd" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
                </div>
              </div>
              <div>
                <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">检索地址（留空则用登录地址）</label>
                <input v-model="customForm.search_url" placeholder="http://3.shutong2.com/search" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
              </div>
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">查询参数名</label>
              <input v-model="customForm.query_param" placeholder="q" class="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 outline-none font-mono" />
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">需要的凭证字段</label>
              <div class="flex flex-wrap gap-3 text-sm">
                <label class="flex items-center gap-1"><input type="checkbox" v-model="customForm.api_key" /> API Key</label>
                <label class="flex items-center gap-1"><input type="checkbox" v-model="customForm.base_url_field" /> 接口地址</label>
                <label class="flex items-center gap-1"><input type="checkbox" v-model="customForm.user" /> 账号</label>
                <label class="flex items-center gap-1"><input type="checkbox" v-model="customForm.password" /> 密码</label>
              </div>
              <p class="text-[11px] text-gray-400 mt-1">勾选后，该文献库卡片会显示对应输入框；填写的凭证统一存入 .env 并自动掩码。</p>
            </div>
          </div>

          <div class="flex justify-end gap-2 pt-2">
            <button @click="closeCustomModal" class="px-4 py-1.5 text-sm rounded-lg text-gray-400 hover:text-gray-600 border border-gray-300 dark:border-gray-600">取消</button>
            <button @click="saveCustom" :disabled="customSaving" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40">{{ customEditingId ? '保存修改' : '添加' }}</button>
          </div>
        </div>
      </div>

      <!-- 安全 -->
      <div v-if="activeTab === 'channels'" class="max-w-3xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <div class="flex items-center gap-2">
            <span class="text-lg">📱</span>
            <h3 class="font-medium text-gray-800 dark:text-gray-200">移动渠道接入</h3>
          </div>
          <p class="text-xs text-gray-500 dark:text-gray-400">将 Agent 接入即时通讯、邮件、智能家居等平台，实现跨渠道对话。填入平台凭据即可启用，支持 25+ 平台。</p>

          <!-- 状态概览 -->
          <div v-if="channelsData" class="flex items-center gap-4 text-xs">
            <span class="text-gray-500 dark:text-gray-400">已配置 <span class="text-green-600 dark:text-green-400 font-medium">{{ channelsData.configured_count }}</span> / {{ channelsData.total }}</span>
            <span class="text-gray-300 dark:text-gray-600">|</span>
            <span class="text-gray-500 dark:text-gray-400">启用后需点击「启动网关」生效</span>
          </div>

          <div v-if="channelsLoading" class="text-center text-sm text-gray-400 py-8">
            <div class="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-green-500 rounded-full mr-1"></div> 加载中...
          </div>

          <!-- 按分类分组展示 -->
          <div v-else-if="channelCategories.length > 0" class="space-y-6">
            <div v-for="group in channelCategories" :key="group.cat" class="space-y-2">
              <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
                <span v-if="group.cat === '国内'">🇨🇳</span>
                <span v-else-if="group.cat === '国际'">🌍</span>
                <span v-else>⚙️</span>
                {{ group.cat }}平台（{{ group.items.length }} 个）
              </h4>
              <div class="space-y-2">
                <div v-for="ch in group.items" :key="ch.key" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 overflow-hidden">
                  <!-- 头部：点击展开 -->
                  <div @click="channelExpanded[ch.key] = !channelExpanded[ch.key]" class="p-3 flex items-center justify-between cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition">
                    <div class="flex items-center gap-2">
                      <span class="text-base">{{ ch.icon }}</span>
                      <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ ch.label }}</span>
                      <span v-if="ch.configured" class="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400">已配置</span>
                      <span v-if="ch.enabled" class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400">已启用</span>
                    </div>
                    <span class="text-gray-400 text-xs">{{ channelExpanded[ch.key] ? '▼' : '▶' }}</span>
                  </div>
                  <!-- 展开内容 -->
                  <div v-if="channelExpanded[ch.key]" class="px-3 pb-3 space-y-3 border-t border-gray-200 dark:border-gray-700">
                    <!-- 凭据字段 -->
                    <div class="pt-2 space-y-2">
                      <div v-for="f in ch.fields" :key="f.key" class="flex gap-2 items-center">
                        <label class="w-36 shrink-0 text-xs text-gray-500 dark:text-gray-400 truncate" :title="f.key">
                          {{ f.label }}
                          <span v-if="!f.required" class="text-gray-400">(可选)</span>
                        </label>
                        <input
                          :value="channelForms[ch.key]?.[f.key] || ''"
                          @input="channelForms[ch.key][f.key] = $event.target.value"
                          :type="f.secret ? 'password' : 'text'"
                          :placeholder="f.has_value ? '已配置（重新输入可覆盖）' : f.placeholder"
                          class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none font-mono"
                        />
                        <span v-if="f.has_value" class="text-green-500 text-xs whitespace-nowrap">●●●●</span>
                      </div>
                    </div>
                    <!-- 操作按钮 -->
                    <div class="flex gap-2 justify-end pt-1">
                      <button v-if="ch.configured" @click="toggleChannel(ch.key)" class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 whitespace-nowrap">{{ ch.enabled ? '禁用' : '启用' }}</button>
                      <button v-if="ch.configured" @click="clearChannel(ch.key)" class="px-3 py-1.5 text-sm rounded-lg text-gray-400 hover:text-red-500 border border-gray-300 dark:border-gray-600 whitespace-nowrap">清除</button>
                      <button @click="saveChannel(ch.key)" :disabled="channelSaving" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">保存</button>
                    </div>
                    <!-- 接入教程 -->
                    <div class="rounded-lg bg-blue-50/50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 p-3 space-y-1">
                      <div class="flex items-center gap-1.5">
                        <span class="text-xs">📘</span>
                        <span class="text-xs font-medium text-blue-600 dark:text-blue-400">接入教程</span>
                        <a v-if="ch.apply_url" :href="ch.apply_url" target="_blank" rel="noopener" class="text-[11px] text-blue-500 hover:underline ml-auto">申请入口 ↗</a>
                      </div>
                      <p class="text-[11px] text-gray-500 dark:text-gray-400 whitespace-pre-wrap leading-relaxed pl-5">{{ ch.tutorial }}</p>
                      <p v-if="ch.note" class="text-[10px] text-gray-400 dark:text-gray-500 pl-5 pt-1">💡 {{ ch.note }}</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div v-else class="text-center text-sm text-gray-400 py-4">暂无可用渠道</div>
        </div>
      </div>

      <!-- 安全 -->
      <div v-if="activeTab === 'security'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 flex items-center gap-2">🔒 工具审批</h3>
          <div class="flex items-center justify-between py-2">
            <div>
              <div class="text-sm text-gray-800 dark:text-gray-200">自动批准所有命令 (YOLO 模式)</div>
              <div class="text-xs text-gray-500 dark:text-gray-400 mt-1">关闭后，Agent 执行危险命令前会弹出审批对话框</div>
            </div>
            <button @click="toggleYolo" class="relative inline-flex h-6 w-11 items-center rounded-full transition" :class="yoloEnabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'">
              <span class="inline-block h-4 w-4 transform rounded-full bg-white transition" :class="yoloEnabled ? 'translate-x-6' : 'translate-x-1'" />
            </button>
          </div>
        </div>
      </div>

      <!-- 知识库 -->
      <div v-if="activeTab === 'knowledge'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-4">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">📚 知识库管理</h3>
              <p class="text-xs text-gray-400 mt-1">上传文档到知识库，Agent 将自动检索相关内容辅助回答</p>
            </div>
            <span class="text-xs text-gray-400">{{ ragDocs.length }} 个文档</span>
          </div>

          <!-- 上传区 -->
          <div 
            class="border-2 border-dashed rounded-xl p-6 text-center transition cursor-pointer"
            :class="ragDragging ? 'border-green-500 bg-green-50 dark:bg-green-900/20' : 'border-gray-300 dark:border-gray-600 hover:border-green-400'"
            @dragover.prevent="ragDragging = true"
            @dragleave.prevent="ragDragging = false"
            @drop.prevent="onRagDrop"
            @click="ragFileInput?.click()"
          >
            <input type="file" ref="ragFileInput" class="hidden" multiple 
              accept=".txt,.md,.py,.js,.ts,.json,.yaml,.yml,.html,.css,.xml,.csv,.tsv,.sh,.sql,.log,.pdf,.docx,.xlsx,.pptx" 
              @change="onRagFileSelect" />
            <div class="text-3xl mb-2">📎</div>
            <p class="text-sm text-gray-500 dark:text-gray-400">
              {{ ragUploading ? '⏳ 正在上传...' : '点击或拖拽文件到此处' }}
            </p>
            <p class="text-xs text-gray-400 mt-1">支持 PDF / DOCX / XLSX / PPTX 及 txt/md/py/json 等文本文件</p>
          </div>

          <!-- 文档列表 -->
          <div v-if="ragDocs.length > 0" class="space-y-2">
            <div v-for="doc in ragDocs" :key="doc.id" 
              class="flex items-center gap-3 p-3 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/50 hover:border-green-400 dark:hover:border-green-600 cursor-pointer transition"
              @click="previewRagDoc(doc)">
              <div class="text-lg flex-shrink-0">{{ getFileIcon(doc.file_type) }}</div>
              <div class="flex-1 min-w-0">
                <p class="text-sm text-gray-700 dark:text-gray-300 truncate font-medium">{{ doc.filename }}</p>
                <p class="text-xs text-gray-400">
                  {{ doc.chunk_count }} 块 · {{ formatSize(doc.file_size) }} · {{ formatTime(doc.ingested_at) }}
                </p>
              </div>
              <button 
                @click.stop="deleteRagDoc(doc.id)" 
                class="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20"
              >🗑️</button>
            </div>
          </div>
          <div v-else-if="!ragLoading" class="text-center text-sm text-gray-400 py-4">
            暂无文档，上传一个试试吧
          </div>
          <div v-if="ragLoading" class="text-center text-sm text-gray-400 py-2">加载中...</div>

          <!-- 搜索测试面板 -->
          <div class="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
            <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2 flex items-center gap-1">
              🔍 检索测试
            </h4>
            <div class="flex gap-2">
              <input 
                v-model="ragSearchQuery" 
                @keydown.enter="runRagSearch"
                type="text" 
                placeholder="输入关键词测试知识库检索..."
                class="flex-1 px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none"
              />
              <button 
                @click="runRagSearch" 
                :disabled="ragSearching || !ragSearchQuery.trim()"
                class="px-4 py-2 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 disabled:cursor-not-allowed whitespace-nowrap"
              >
                {{ ragSearching ? '⌛' : '搜索' }}
              </button>
              <button 
                v-if="ragSearchResults.length > 0" 
                @click="clearRagSearch"
                class="px-2 py-2 text-sm rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
              >✕</button>
            </div>

            <!-- 搜索结果 -->
            <div v-if="ragSearching" class="mt-3 text-center text-sm text-gray-400">
              <div class="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-green-500 rounded-full mr-1"></div>
              检索中...
            </div>
            <div v-else-if="ragSearchResults.length > 0" class="mt-3 space-y-2">
              <p class="text-xs text-gray-400">找到 {{ ragSearchResults.length }} 条匹配结果：</p>
              <div v-for="(result, i) in ragSearchResults" :key="i" 
                class="p-3 rounded-lg bg-green-50 dark:bg-green-900/10 border border-green-200 dark:border-green-800">
                <div class="flex items-center justify-between mb-1">
                  <span class="text-xs font-medium text-green-600 dark:text-green-400">
                    {{ getFileIcon(result.file_type) }} {{ result.filename }} #{{ result.chunk_index }}
                  </span>
                  <span class="text-xs text-gray-400">{{ result.char_count }} 字符</span>
                </div>
                <p class="text-sm text-gray-600 dark:text-gray-300 leading-relaxed">{{ result.preview }}</p>
              </div>
            </div>
            <div v-else-if="ragSearchQuery && !ragSearching" class="mt-3 text-center text-xs text-gray-400 py-2">
              无匹配结果
            </div>
          </div>
        </div>

        <!-- 文档预览弹窗 -->
        <div v-if="ragPreview" 
          class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" 
          @click.self="closeRagPreview">
          <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[80vh] flex flex-col mx-4">
            <!-- 弹窗头 -->
            <div class="flex items-center justify-between p-4 border-b border-gray-200 dark:border-gray-700">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-xl flex-shrink-0">{{ getFileIcon(ragPreview.doc.file_type) }}</span>
                <div class="min-w-0">
                  <p class="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">{{ ragPreview.doc.filename }}</p>
                  <p class="text-xs text-gray-400">
                    {{ ragPreview.doc.chunk_count }} 块 · {{ formatSize(ragPreview.doc.file_size) }} · {{ ragPreview.chunks.length }} 个分块
                  </p>
                </div>
              </div>
              <button @click="closeRagPreview" 
                class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700">
                ✕
              </button>
            </div>
            <!-- 弹窗内容 -->
            <div class="flex-1 overflow-y-auto p-4 space-y-3">
              <div v-if="ragPreview.loading" class="text-center text-gray-400 py-8">
                <div class="animate-spin inline-block w-6 h-6 border-2 border-gray-300 border-t-green-500 rounded-full mb-2"></div>
                <p class="text-sm">加载中...</p>
              </div>
              <div v-else-if="ragPreview.chunks.length === 0" class="text-center text-gray-400 py-8">
                <p class="text-sm">该文档无分块内容</p>
              </div>
              <div v-else v-for="chunk in ragPreview.chunks" :key="chunk.id" 
                class="p-3 rounded-lg bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-700">
                <div class="flex items-center justify-between mb-2">
                  <span class="text-xs font-medium text-green-600 dark:text-green-400">📄 分块 {{ chunk.chunk_index + 1 }}</span>
                  <span class="text-xs text-gray-400">{{ chunk.char_count }} 字符</span>
                </div>
                <p class="text-sm text-gray-600 dark:text-gray-300 whitespace-pre-wrap break-words leading-relaxed">{{ chunk.content }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 关于 -->
      <div v-if="activeTab === 'about'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-center space-y-3">
          <div class="w-16 h-16 bg-green-500 rounded-2xl flex items-center justify-center text-white text-2xl font-bold mx-auto">V</div>
          <h3 class="text-lg font-bold text-gray-800 dark:text-gray-200">Vermes</h3>
          <p class="text-sm text-gray-500 dark:text-gray-400">AI Agent by vbit.top</p>
          <p class="text-xs text-gray-400">版本 {{ update.currentVersion }} · 基于 Vermes Agent</p>
          <a href="https://vbit.top" target="_blank" class="text-sm text-green-600 dark:text-green-400 hover:underline">访问 vbit.top →</a>
        </div>

        <!-- 存储用量 -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-3">
          <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 text-center">存储用量</h3>
          <div class="space-y-2 text-sm" v-if="storageUsage">
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">对话记录</span>
              <span class="text-gray-700 dark:text-gray-300">{{ storageUsage.sessions_db }} MB</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">记忆文件</span>
              <span class="text-gray-700 dark:text-gray-300">{{ storageUsage.memories }} MB</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">技能缓存</span>
              <span class="text-gray-700 dark:text-gray-300">{{ storageUsage.skills }} MB</span>
            </div>
            <div class="flex justify-between font-medium pt-1 border-t border-gray-200 dark:border-gray-700">
              <span class="text-gray-600 dark:text-gray-400">总计</span>
              <span class="text-gray-800 dark:text-gray-200">{{ storageUsage.total }} MB</span>
            </div>
          </div>
          <div v-else class="text-xs text-gray-400 dark:text-gray-500 text-center">加载中...</div>
        </div>

        <!-- 🧠 缓存性能 -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-3">
          <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 text-center flex items-center justify-center gap-2">
            🧠 缓存性能
            <button @click="fetchCacheMetrics" :disabled="cacheRefreshing" class="text-xs text-green-600 hover:text-green-700 disabled:opacity-50">
              {{ cacheRefreshing ? '⌛' : '🔄' }}
            </button>
          </h3>
          <div class="space-y-2 text-sm" v-if="cacheMetrics">
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">命中次数</span>
              <span class="text-gray-700 dark:text-gray-300">{{ cacheMetrics.hits?.toLocaleString() || 0 }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">未命中</span>
              <span class="text-gray-700 dark:text-gray-300">{{ cacheMetrics.misses?.toLocaleString() || 0 }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">驱逐次数</span>
              <span class="text-gray-700 dark:text-gray-300">{{ cacheMetrics.evictions?.toLocaleString() || 0 }}</span>
            </div>
            <div class="flex justify-between">
              <span class="text-gray-500 dark:text-gray-400">缓存用量</span>
              <span class="text-gray-700 dark:text-gray-300">{{ cacheMetrics.current_size || 0 }} / {{ cacheMetrics.max_size || 20 }}</span>
            </div>
            <div class="flex justify-between font-medium pt-1 border-t border-gray-200 dark:border-gray-700">
              <span class="text-gray-600 dark:text-gray-400">命中率</span>
              <span :class="hitRateColor(cacheMetrics.hit_rate || 0)">{{ (cacheMetrics.hit_rate || 0).toFixed(1) }}%</span>
            </div>
            <!-- 命中率进度条 -->
            <div class="w-full h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div class="h-full rounded-full transition-all duration-500"
                :class="(cacheMetrics.hit_rate || 0) >= 80 ? 'bg-green-500' : (cacheMetrics.hit_rate || 0) >= 50 ? 'bg-yellow-500' : 'bg-red-500'"
                :style="{ width: (cacheMetrics.hit_rate || 0) + '%' }"></div>
            </div>
          </div>
          <div v-else class="text-xs text-gray-400 dark:text-gray-500 text-center">加载中...</div>
        </div>

        <!-- 🔌 API 接入 -->
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 space-y-3">
          <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300 text-center">🔌 API 接入</h3>
          <p class="text-xs text-gray-500 dark:text-gray-400 text-center">让外部系统（cron/脚本/webhook）调用 Agent</p>
          <div class="bg-gray-50 dark:bg-gray-900 rounded-lg p-3 font-mono text-[11px] text-gray-600 dark:text-gray-300 overflow-x-auto">
            <div class="text-gray-400 mb-1"># 基础调用</div>
            <div>curl -X POST {{ apiBaseUrl }}/api/agent/run \</div>
            <div>  -H 'Content-Type: application/json' \</div>
            <div>  -d '{"task":"检查磁盘空间"}'</div>
            <div class="text-gray-400 mt-2 mb-1"># 定时任务</div>
            <div>0 9 * * * curl -s {{ apiBaseUrl }}/api/agent/run \</div>
            <div>  -d '{"task":"生成日报","session_id":"daily"}'</div>
          </div>
          <div class="flex gap-2">
            <button @click="copyApiCurl" class="flex-1 px-3 py-2 rounded-lg text-xs bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 transition">
              📋 复制 curl
            </button>
            <button @click="testApi" :disabled="apiTesting" class="flex-1 px-3 py-2 rounded-lg text-xs bg-green-50 dark:bg-green-900/20 hover:bg-green-100 dark:hover:bg-green-900/40 text-green-700 dark:text-green-400 transition border border-green-200 dark:border-green-800">
              {{ apiTesting ? '⏳ 测试中...' : '🧪 测试 API' }}
            </button>
          </div>
          <div v-if="apiTestResult !== null" class="text-xs rounded-lg p-3" :class="apiTestResult.ok ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400' : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'">
            {{ apiTestResult.ok ? '✅ ' + apiTestResult.response : '❌ ' + apiTestResult.error }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
