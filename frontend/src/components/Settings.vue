<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { useUpdateStore } from '../stores/update'
import * as api from '../services/api'

const chat = useChatStore()
const update = useUpdateStore()
const router = useRouter()

const providers = ref([
  { id: 'openai', name: 'OpenAI', key: '', baseUrl: 'https://api.openai.com/v1', models: [], syncing: false },
  { id: 'deepseek', name: 'DeepSeek', key: '', baseUrl: 'https://api.deepseek.com', models: [], syncing: false },
  { id: 'qwen', name: '通义千问', key: '', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: [], syncing: false },
  { id: 'openrouter', name: 'OpenRouter', key: '', baseUrl: 'https://openrouter.ai/api/v1', models: [], syncing: false },
  { id: 'vbit', name: 'vbit.top', key: '', baseUrl: 'https://api.vbit.top/v1', models: [], syncing: false },
  { id: 'xiaomi', name: '小米 MiMo', key: '', baseUrl: 'https://api.xiaomimimo.com/v1', models: [], syncing: false },
  { id: 'ant-ling', name: '蚂蚁百灵', key: '', baseUrl: 'https://api.ant-ling.com/v1', models: [], syncing: false },
  { id: 'ollama', name: 'Ollama (本地)', key: 'ollama', baseUrl: 'http://localhost:11434/v1', models: [], syncing: false },

  { id: 'minimax', name: 'MiniMax', key: '', baseUrl: 'https://api.minimax.chat/v1', models: [], syncing: false },
  { id: 'baidu', name: '百度文心', key: '', baseUrl: 'https://qianfan.baidubce.com/v2', models: [], syncing: false },
  { id: 'xinghuo', name: '讯飞星火', key: '', baseUrl: 'https://spark-api.xf-yun.com/v1', models: [], syncing: false },
  { id: 'stepfun', name: '阶跃星辰', key: '', baseUrl: 'https://api.stepfun.com/v1', models: [], syncing: false },
  { id: 'yi', name: '零一万物', key: '', baseUrl: 'https://api.lingyiwanwu.com/v1', models: [], syncing: false },
  { id: 'baichuan', name: '百川智能', key: '', baseUrl: 'https://api.baichuan-ai.com/v1', models: [], syncing: false },
  { id: 'groq', name: 'Groq (极速推理)', key: '', baseUrl: 'https://api.groq.com/openai/v1', models: [], syncing: false },
  { id: 'together', name: 'Together AI', key: '', baseUrl: 'https://api.together.xyz/v1', models: [], syncing: false },
  { id: 'anthropic', name: 'Anthropic Claude', key: '', baseUrl: 'https://api.anthropic.com/v1', models: [], syncing: false },
  { id: 'gemini', name: 'Google Gemini', key: '', baseUrl: 'https://generativelanguage.googleapis.com/v1beta', models: [], syncing: false },
  { id: 'custom', name: '自定义提供商', key: '', baseUrl: '', models: [], syncing: false },
])

// Custom model input per provider
const customModelInputs = ref({})
const activeTab = ref('providers')
const saved = ref(false)
const expandedProvider = ref(null)

// All synced models across providers
const allModels = computed(() => {
  const list = []
  for (const p of providers.value) {
    if (p.models && p.models.length > 0) {
      for (const m of p.models) {
        list.push({ id: m, provider: p.name, providerId: p.id })
      }
    }
  }
  return list
})

// Provider ID → 后端环境变量名映射（与 PROVIDER_ENV_MAP_SHARED 一致）
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
    xiaomi: 'XIAOMI_API_KEY', ollama: null,
    'ant-ling': 'ANT_LING_API_KEY', minimax: 'MINIMAX_API_KEY',
    baidu: 'BAIDU_API_KEY', xinghuo: 'XINGHUO_API_KEY',
    stepfun: 'STEPFUN_API_KEY', groq: 'GROQ_API_KEY',
    together: 'TOGETHER_API_KEY',
  }
  return map[providerId] || providerId.toUpperCase() + '_API_KEY'
}

async function syncModels(p) {
  if (!p.key && p.id !== 'ollama') {
    alert('请先填写 API Key')
    return
  }
  if (!p.baseUrl) {
    alert('请先填写 Base URL')
    return
  }
  p.syncing = true
  try {
    // For Ollama, use local discover
    if (p.id === 'ollama') {
      const resp = await fetch('/api/model/discover', { method: 'POST' })
      const data = await resp.json()
      if (data.ok) {
        p.models = data.models
        saveProvidersToStorage()
      } else {
        alert('同步失败: ' + (data.error || 'Ollama 未运行'))
      }
      return
    }
    // 优先用 provider_id 让后端自动解析 key/url，也支持自定义 base_url 覆盖
    const body = { provider_id: p.id }
    if (p.baseUrl && p.baseUrl !== p.defaultBaseUrl) body.base_url = p.baseUrl
    if (p.key && p.key !== '●●●●●●●●') body.api_key = p.key
    const resp = await fetch('/api/provider/sync-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    const data = await resp.json()
    if (data.ok && data.models && data.models.length > 0) {
      // 同步成功：保留用户手动添加的模型（以同步结果为基础，去重合并）
      const synced = new Set(data.models)
      const manual = (p.models || []).filter(m => !synced.has(m))
      p.models = [...data.models, ...manual]
      saveProvidersToStorage()
      if (manual.length > 0) {
        alert('✅ 已同步 ' + data.models.length + ' 个模型，保留 ' + manual.length + ' 个手动添加的模型')
      }
    } else {
      // API 返回空或失败：保留现有模型，提示用户手动添加
      if (data.error) {
        alert('⚠️ 同步失败: ' + data.error + '\n\n已保留现有 ' + (p.models||[]).length + ' 个模型，你仍可以手动添加模型。')
      } else {
        alert('⚠️ 该接口未返回模型列表\n\n请手动添加模型名称，或联系厂商确认 /models 端点是否可用。')
      }
    }
  } catch (e) {
    // 网络错误：不清除现有模型
    alert('⚠️ 同步请求失败: ' + e.message + '\n\n已保留现有 ' + (p.models||[]).length + ' 个模型。')
  } finally {
    p.syncing = false
  }
}

function addCustomModel(p) {
  const input = customModelInputs.value[p.id] || ''
  const modelId = input.trim()
  if (!modelId) return
  if (!p.models) p.models = []
  if (!p.models.includes(modelId)) {
    p.models.push(modelId)
    saveProvidersToStorage()
  }
  customModelInputs.value[p.id] = ''
}

function removeModel(p, modelId) {
  p.models = p.models.filter(m => m !== modelId)
  saveProvidersToStorage()
}

async function deleteProvider(p) {
  if (!confirm(`确定清除 ${p.name} 的 API Key 和模型配置？`)) return
  // Clear backend env var
  const envKey = getEnvKey(p.id)
  try {
    await fetch('/api/env', {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: envKey })
    })
  } catch(e) { /* backend may not have this key */ }
  // Clear local state
  p.key = ''
  p.models = []
  saveProvidersToStorage()
  saved.value = true
  setTimeout(() => saved.value = false, 2000)
}

function clearAllSettings() {
  if (!confirm('清除所有本地配置？\n\n这将清除：\n- 所有提供商 API Key 和模型列表\n- 当前模型选择\n- 微信登录状态\n- 试用 Token\n\n聊天记录不受影响。')) return
  const keys = []
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i)
    if (k && k.startsWith('vermes-') && k !== 'vermes-sessions' && !k.startsWith('vermes-msgs-')) {
      keys.push(k)
    }
  }
  for (const k of keys) localStorage.removeItem(k)
  // Also reset provider form state
  for (const p of providers.value) {
    p.key = ''
    p.models = []
  }
  saved.value = true
  setTimeout(() => saved.value = false, 2000)
}

async function setCurrentModel(p, modelId) {
  // Normalize provider name for backend
  const providerMap = {
    'openai': 'openai', 'deepseek': 'deepseek', 'qwen': 'qwen',
    'openrouter': 'openrouter', 'vbit': 'vbit', 'ollama': 'ollama',
    'xiaomi': 'xiaomi',
    '通义千问': 'qwen', 'DeepSeek': 'deepseek', 'OpenRouter': 'openrouter',
    'OpenAI': 'openai', 'vbit.top': 'vbit', 'Ollama (本地)': 'ollama',
    '小米 MiMo': 'xiaomi',
  }
  const provider = providerMap[p.id] || p.id

  try {
    // 1. 同步到后端 config.yaml
    const resp = await fetch('/api/model/set', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'main', provider, model: modelId })
    })
    const data = await resp.json()
    if (!data.ok) {
      alert('设置失败: ' + (data.detail || JSON.stringify(data)))
      return
    }

    // 2. 同步到前端状态
    localStorage.setItem('vermes-current-model', modelId)
    localStorage.setItem('vermes-current-provider', p.name)

    // 3. 如果当前 Chat 页面有 store，也刷新它
    const event = new CustomEvent('model-changed', {
      detail: { model: modelId, provider: p.name }
    })
    window.dispatchEvent(event)

    saved.value = true
    setTimeout(() => saved.value = false, 2000)
  } catch (e) {
    alert('设置失败: ' + e.message)
  }
}

function saveProvidersToStorage() {
  // 只要用户展开过这个厂商（有key/有模型/baseUrl被改过），就保存下来
  const data = providers.value
    .filter(p =>
      (p.key && p.key !== '●●●●●●●●' && p.key.trim() !== '') ||
      (p.models && p.models.length > 0) ||
      (p.baseUrl && p.baseUrl !== getDefaultBaseUrl(p.id))
    )
    .map(p => ({
      id: p.id, name: p.name,
      key: (p.key && p.key !== '●●●●●●●●') ? '***saved***' : '',
      baseUrl: p.baseUrl,
      models: p.models || []
    }))
  localStorage.setItem('vermes-providers', JSON.stringify(data))
}

function getDefaultBaseUrl(id) {
  const defaults = {
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
    anthropic: 'https://api.anthropic.com/v1',
    gemini: 'https://generativelanguage.googleapis.com/v1beta',
  }
  return defaults[id] || ''
}

async function save() {
  saveProvidersToStorage()
  let firstRealKey = null
  // Sync API keys + base_url to backend — await each to avoid race conditions
  for (const p of providers.value) {
    // Save API key
    if (p.key && p.key !== '●●●●●●●●' && p.id !== 'ollama') {
      const envKey = getEnvKey(p.id)
      try {
        await fetch('/api/env', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: envKey, value: p.key })
        })
      } catch(e) { console.warn('Save env failed:', e) }
      // Find first model to auto-switch if not already on vbit trial
      if (!firstRealKey && p.models && p.models.length > 0 && p.id !== 'vbit') {
        firstRealKey = { id: p.id, name: p.name, model: p.models[0] }
      }
    }
    // Save base_url to config.yaml for ALL providers (supports custom URLs like token-plan)
    if (p.baseUrl) {
      try {
        await fetch('/api/provider/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_id: p.id, base_url: p.baseUrl, api_key: p.key && p.key !== '●●●●●●●●' ? p.key : '' })
        })
      } catch(e) { console.warn('Save base_url failed:', e) }
    }
  }
  // Auto-switch to first real provider that has models synced
  if (firstRealKey) {
    const currentProvider = localStorage.getItem('vermes-current-provider')
    // Only auto-switch if currently on vbit trial or no provider set
    if (!currentProvider || currentProvider === 'vbit.top') {
      localStorage.setItem('vermes-current-model', firstRealKey.model)
      localStorage.setItem('vermes-current-provider', firstRealKey.name)
      const event = new CustomEvent('model-changed', {
        detail: { model: firstRealKey.model, provider: firstRealKey.name }
      })
      window.dispatchEvent(event)
    }
  }
  saved.value = true
  setTimeout(() => saved.value = false, 2000)
}

function back() { router.push('/') }

function toggleProvider(id) {
  expandedProvider.value = expandedProvider.value === id ? null : id
}

onMounted(() => {
  // 加载用户已保存的配置
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
          // 用户手动添加的厂商不在默认列表中，追加进去
          providers.value.push({
            id: p.id, name: p.name,
            key: p.key === '***saved***' ? '●●●●●●●●' : (p.key || ''),
            baseUrl: p.baseUrl || '',
            models: p.models || [], syncing: false
          })
        }
      }
    } catch(e) {}
  }
  // 不再自动同步、不再填充模板模型 —— 用户自己点同步或手动添加
  // Listen for trial token
  const onTrial = (e) => {
    const { token } = e.detail
    if (!token) return
    const vbit = providers.value.find(p => p.id === 'vbit')
    if (vbit) {
      vbit.key = token
      saveProvidersToStorage()
    }
  }
  window.addEventListener('trial-token', onTrial)
})
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

        <!-- 推荐提示 -->
        <div class="bg-blue-50 dark:bg-blue-900/30 border border-blue-200 dark:border-blue-800 rounded-xl p-4 flex items-start gap-3 mb-3">
          <span class="text-xl">🎯</span>
          <div class="flex-1">
            <div class="font-medium text-blue-700 dark:text-blue-300 text-sm">小白用户首选：DeepSeek（高性价比）</div>
            <div class="text-xs text-blue-600 dark:text-blue-400 mt-1">注册即送额度，价格极低，中文理解强，是入门 AI 对话的最佳选择。</div>
            <a href="https://platform.deepseek.com/" target="_blank" class="inline-block mt-2 text-xs text-blue-500 hover:text-blue-600 font-medium">→ 去 DeepSeek 官网注册 ↗</a>
          </div>
        </div>
        <div class="bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-800 rounded-xl p-4 flex items-start gap-3 mb-3">
          <span class="text-xl">🆓</span>
          <div class="flex-1">
            <div class="font-medium text-green-700 dark:text-green-300 text-sm">推荐：OpenRouter 免费模型</div>
            <div class="text-xs text-green-600 dark:text-green-400 mt-1">无需付费，注册 OpenRouter 即可使用 owl-alpha、tencent/hy3-preview 等免费模型。</div>
            <a href="https://openrouter.ai/" target="_blank" class="inline-block mt-2 text-xs text-green-500 hover:text-green-600 font-medium">→ 去 openrouter.ai 注册 ↗</a>
          </div>
        </div>

        <div v-for="p in providers" :key="p.id" class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
          <!-- Provider header (clickable) -->
          <button @click="toggleProvider(p.id)" class="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 dark:hover:bg-gray-750 transition">
            <div class="flex items-center gap-3">
              <div class="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold" :class="p.key ? 'bg-green-100 dark:bg-green-900 text-green-600 dark:text-green-400' : 'bg-gray-100 dark:bg-gray-700 text-gray-400'">
                {{ p.name.charAt(0) }}
              </div>
              <div class="text-left">
                <div class="font-medium text-gray-800 dark:text-gray-200 text-sm">{{ p.name }}</div>
                <div class="text-xs text-gray-400">{{ p.models.length }} 个模型 · {{ p.key ? '已配置' : '未配置' }}</div>
              </div>
            </div>
            <svg class="w-4 h-4 text-gray-400 transition-transform" :class="expandedProvider === p.id ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/></svg>
          </button>

          <!-- Expanded content -->
          <div v-if="expandedProvider === p.id" class="px-4 pb-4 space-y-3 border-t border-gray-100 dark:border-gray-700">
            <div class="pt-3">
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">API Key</label>
              <input v-model="p.key" :type="p.id === 'ollama' ? 'text' : 'password'" :placeholder="p.id === 'ollama' ? 'ollama' : 'sk-...'" class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
            </div>
            <div>
              <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Base URL</label>
              <input v-model="p.baseUrl" type="text" class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
            </div>

            <!-- Sync button -->
            <div class="flex gap-2">
              <button @click="syncModels(p)" :disabled="p.syncing" class="px-4 py-1.5 bg-green-500 hover:bg-green-600 disabled:bg-green-300 text-white rounded-lg text-xs font-medium transition flex items-center gap-1">
                <svg v-if="p.syncing" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path></svg>
                {{ p.syncing ? '同步中...' : '🔄 同步模型' }}
              </button>
              <button @click="save()" class="px-4 py-1.5 bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 text-gray-700 dark:text-gray-200 rounded-lg text-xs font-medium transition">
                💾 保存配置
              </button>
              <button @click="deleteProvider(p)" class="px-4 py-1.5 bg-red-50 dark:bg-red-900/30 hover:bg-red-100 dark:hover:bg-red-900/50 text-red-500 rounded-lg text-xs font-medium transition">
                🗑 清除配置
              </button>
            </div>

            <!-- Synced models list -->
            <div v-if="p.models.length > 0" class="space-y-1">
              <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">可用模型</div>
              <div v-for="m in p.models" :key="m" class="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-gray-700 rounded-lg">
                <span class="text-sm text-gray-700 dark:text-gray-300">{{ m }}</span>
                <div class="flex items-center gap-2">
                  <button @click="setCurrentModel(p, m)" class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 rounded hover:bg-green-200 dark:hover:bg-green-800/60 transition font-medium">
                    ✓ 设为当前
                  </button>
                  <button @click="removeModel(p, m)" class="text-gray-400 hover:text-red-500 text-xs">✕</button>
                </div>
              </div>
            </div>

            <!-- Custom model input -->
            <div class="flex gap-2">
              <input v-model="customModelInputs[p.id]" @keyup.enter="addCustomModel(p)" placeholder="手动输入模型名..." class="flex-1 px-3 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
              <button @click="addCustomModel(p)" class="px-3 py-1.5 bg-blue-500 hover:bg-blue-600 text-white rounded-lg text-xs font-medium transition">+ 添加</button>
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
