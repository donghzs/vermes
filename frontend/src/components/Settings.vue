<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useUpdateStore } from '../stores/update'
import * as api from '../services/api'
import { toast } from '../utils/toast'
import ProviderCard from './ProviderCard.vue'

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
  groq: 'https://api.groq.com/openai/v1',
  together: 'https://api.together.xyz/v1',
  agnes: 'https://apihub.agnes-ai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
}

const RECOMMENDED_IDS = ['vbit', 'agnes', 'deepseek', 'xiaomi', 'ollama']
const CHINESE_IDS = ['agnes','xiaomi','qwen','baidu','xinghuo','minimax','ant-ling','stepfun','yi','baichuan']
const INTERNATIONAL_IDS = ['openai','anthropic','gemini','openrouter','groq','together','agnes']

// 推荐区提供商的额外配置
const PROVIDER_EXTRAS = {
  vbit: { iconClass: 'bg-green-500 text-white w-10 h-10', iconText: 'V', isSpecial: true },
  agnes: { iconClass: 'bg-emerald-100 dark:bg-emerald-900 text-emerald-600 dark:text-emerald-400', iconText: 'A', description: '全球前十 AI Lab，文本/图片/视频全模态无限期免费', linkUrl: 'https://platform.agnes-ai.com/', linkText: '→ 去 Agnes AI 官网获取 Key ↗' },
  deepseek: { iconClass: 'bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400', iconText: 'D', description: '国产高性价比，注册即送额度', linkUrl: 'https://platform.deepseek.com/', linkText: '→ 去 DeepSeek 官网获取 Key ↗' },
  xiaomi: { iconClass: 'bg-orange-100 dark:bg-orange-900 text-orange-600 dark:text-orange-400', iconText: 'Mi', description: '国产高性价比，注册即送额度', linkUrl: 'https://platform.xiaomimimo.com?ref=KE64RG', linkText: '→ 去小米 MiMo 官网获取 Key ↗' },
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
  { id: 'groq', name: 'Groq (极速推理)', key: '', baseUrl: DEFAULT_BASE_URLS.groq, models: [], syncing: false },
  { id: 'together', name: 'Together AI', key: '', baseUrl: DEFAULT_BASE_URLS.together, models: [], syncing: false },
  { id: 'anthropic', name: 'Anthropic Claude', key: '', baseUrl: DEFAULT_BASE_URLS.anthropic, models: [], syncing: false },
  { id: 'gemini', name: 'Google Gemini', key: '', baseUrl: DEFAULT_BASE_URLS.gemini, models: [], syncing: false },
  { id: 'custom', name: '自定义提供商', key: '', baseUrl: '', models: [], syncing: false },
])

const customModelInputs = ref({})
const activeTab = ref('providers')
const saved = ref(false)
const maxTokensInput = ref(null)
const expandedProviders = ref(new Set())
const showAdvanced = ref(false)

function isExpanded(id) { return expandedProviders.value.has(id) }
function toggleProvider(id) {
  if (expandedProviders.value.has(id)) expandedProviders.value.delete(id)
  else expandedProviders.value.add(id)
}

const recommendedProviders = computed(() => providers.value.filter(p => RECOMMENDED_IDS.includes(p.id)))
const chineseProviders = computed(() => providers.value.filter(p => CHINESE_IDS.includes(p.id) && !RECOMMENDED_IDS.includes(p.id)))
const internationalProviders = computed(() => providers.value.filter(p => INTERNATIONAL_IDS.includes(p.id)))
const customProviders = computed(() => providers.value.filter(p => p.id === 'custom'))

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
  if (!confirm(`确定清除 ${p.name} 的 API Key 和模型配置？`)) return
  const envKey = getEnvKey(p.id)
  try { await fetch('/api/env', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: envKey }) }) } catch(e) {}
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
    try { localStorage.setItem('vermes-current-model', modelId) } catch(e) {}
    try { localStorage.setItem('vermes-current-provider', provider) } catch(e) {}
    window.dispatchEvent(new CustomEvent('model-changed', { detail: { model: modelId, provider } }))
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
    .filter(p => (p.key && p.key !== '●●●●●●●●' && p.key.trim() !== '') || (p.models && p.models.length > 0) || (p.baseUrl && p.baseUrl !== DEFAULT_BASE_URLS[p.id]))
    .map(p => ({
      id: p.id, name: p.name,
      key: (p.key && p.key !== '●●●●●●●●') ? '***saved***' : '',
      baseUrl: p.baseUrl, models: p.models || []
    }))
  try { localStorage.setItem('vermes-providers', JSON.stringify(data)) } catch(e) {}
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
        fetch('/api/env', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: envKey, value: p.key }) })
          .catch(() => {})
      )
      if (!firstRealKey && p.models && p.models.length > 0 && p.id !== 'vbit') firstRealKey = { id: p.id, name: p.name, model: p.models[0] }
    }
    if (p.baseUrl) {
      savePromises.push(
        fetch('/api/provider/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider_id: p.id, base_url: p.baseUrl, api_key: p.key && p.key !== '●●●●●●●●' ? p.key : '' }) })
          .catch(() => {})
      )
    }
  }
  
  await Promise.all(savePromises)
  
  if (firstRealKey) {
    const currentProvider = localStorage.getItem('vermes-current-provider')
    if (!currentProvider || currentProvider === 'vbit.top' || currentProvider === 'vbit') {
      try { localStorage.setItem('vermes-current-model', firstRealKey.model) } catch(e) {}
      try { localStorage.setItem('vermes-current-provider', firstRealKey.id) } catch(e) {}
      window.dispatchEvent(new CustomEvent('model-changed', { detail: { model: firstRealKey.model, provider: firstRealKey.id } }))
    }
  }
  saved.value = true; setTimeout(() => saved.value = false, 2000)
}

function clearAllSettings() {
  if (!confirm('清除所有本地配置？\n\n这将清除：\n- 所有提供商 API Key 和模型列表\n- 当前模型选择\n- 微信登录状态\n- 试用 Token\n\n聊天记录不受影响。')) return
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith('vermes-') && k !== 'vermes-sessions' && !k.startsWith('vermes-msgs-')) keys.push(k)
  }
  for (const k of keys) localStorage.removeItem(k)
  for (const p of providers.value) { p.key = ''; p.models = [] }
  saved.value = true; setTimeout(() => saved.value = false, 2000)
}

function back() { router.push('/') }

// ── ProviderCard 事件路由 ──
function onCardSync(p) { syncModels(p) }
function onCardSave() { save() }
function onCardDelete(p) { deleteProvider(p) }
function onCardSetModel(p, modelId) { setCurrentModel(p, modelId) }
function onCardAddModel(p, modelId) { addCustomModel(p, modelId) }
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
})

onUnmounted(() => { window.removeEventListener('trial-token', _onTrialToken) })
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
      <button @click="activeTab = 'about'" class="px-4 py-2 text-sm font-medium border-b-2 transition" :class="activeTab === 'about' ? 'border-green-500 text-green-600 dark:text-green-400' : 'border-transparent text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'">关于</button>
    </div>

    <!-- 内容区 -->
    <div class="flex-1 overflow-y-auto p-6 bg-gray-50 dark:bg-gray-900">

      <!-- 提供商配置 -->
      <div v-if="activeTab === 'providers'" class="max-w-2xl space-y-3">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-2">配置 API Key 后点击「同步模型」自动获取可用模型，也可手动添加自定义模型。</p>

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
                <div class="text-xs text-green-600 dark:text-green-400">微信扫码登录即可使用，每天 500 积分</div>
              </div>
            </div>
            <div class="text-xs text-green-600 dark:text-green-400">✅ 无需注册 · ✅ 无需 API Key · ✅ 开箱即用</div>
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
            :show-delete="false"
            @toggle="onCardToggle"
            @sync="onCardSync"
            @save="onCardSave"
            @set-model="onCardSetModel"
            @add-model="onCardAddModel"
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
                  @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel"
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
                  @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel"
                />
              </div>
            </div>

            <!-- 🔧 自定义 -->
            <div>
              <div class="text-xs font-medium text-gray-400 dark:text-gray-500 mb-2">🔧 自定义</div>
              <ProviderCard v-for="p in customProviders" :key="p.id"
                :provider="p" :expanded="isExpanded(p.id)" compact
                :show-delete="false" :default-base-url="''"
                @toggle="onCardToggle" @sync="onCardSync" @save="onCardSave"
                @set-model="onCardSetModel" @add-model="onCardAddModel" @remove-model="onCardRemoveModel"
              />
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

      <!-- 关于 -->
      <div v-if="activeTab === 'about'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-center space-y-3">
          <div class="w-16 h-16 bg-green-500 rounded-2xl flex items-center justify-center text-white text-2xl font-bold mx-auto">V</div>
          <h3 class="text-lg font-bold text-gray-800 dark:text-gray-200">Vermes</h3>
          <p class="text-sm text-gray-500 dark:text-gray-400">AI Agent by vbit.top</p>
          <p class="text-xs text-gray-400">版本 {{ update.currentVersion }} · 基于 Hermes Agent</p>
          <a href="https://vbit.top" target="_blank" class="text-sm text-green-600 dark:text-green-400 hover:underline">访问 vbit.top →</a>
        </div>
      </div>
    </div>
  </div>
</template>
