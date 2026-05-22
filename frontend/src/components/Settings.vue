<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import * as api from '../services/api'

const chat = useChatStore()
const router = useRouter()

const providers = ref([
  { id: 'openai', name: 'OpenAI', key: '', baseUrl: 'https://api.openai.com/v1', models: [], syncing: false },
  { id: 'deepseek', name: 'DeepSeek', key: '', baseUrl: 'https://api.deepseek.com', models: [], syncing: false },
  { id: 'qwen', name: '通义千问', key: '', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: [], syncing: false },
  { id: 'openrouter', name: 'OpenRouter', key: '', baseUrl: 'https://openrouter.ai/api/v1', models: [], syncing: false },
  { id: 'vbit', name: 'vbit.top', key: '', baseUrl: 'https://api.vbit.top/v1', models: [], syncing: false },
  { id: 'ollama', name: 'Ollama (本地)', key: 'ollama', baseUrl: 'http://localhost:11434/v1', models: [], syncing: false },
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
    const resp = await fetch('/api/provider/sync-models', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_url: p.baseUrl, api_key: p.key === '●●●●●●●●' ? '' : p.key })
    })
    const data = await resp.json()
    if (data.ok) {
      p.models = data.models
      saveProvidersToStorage()
    } else {
      alert('同步失败: ' + (data.error || '未知错误'))
    }
  } catch (e) {
    alert('同步失败: ' + e.message)
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

function saveProvidersToStorage() {
  const data = providers.value.map(p => ({
    id: p.id, name: p.name,
    key: p.key ? '***saved***' : '',
    baseUrl: p.baseUrl,
    models: p.models || []
  }))
  localStorage.setItem('vermes-providers', JSON.stringify(data))
}

function save() {
  saveProvidersToStorage()
  saved.value = true
  setTimeout(() => saved.value = false, 2000)
}

function back() { router.push('/') }

function toggleProvider(id) {
  expandedProvider.value = expandedProvider.value === id ? null : id
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
          if (p.models) target.models = p.models
        }
      }
    } catch(e) {}
  }
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
          <span class="text-xl">🆓</span>
          <div class="flex-1">
            <div class="font-medium text-blue-700 dark:text-blue-300 text-sm">推荐：OpenRouter 免费模型</div>
            <div class="text-xs text-blue-600 dark:text-blue-400 mt-1">注册 OpenRouter 即可使用 owl-alpha、tencent/hy3-preview 等免费模型，无需付费。</div>
            <a href="https://openrouter.ai/" target="_blank" class="inline-block mt-2 text-xs text-blue-500 hover:text-blue-600 font-medium">→ 去 openrouter.ai 注册 ↗</a>
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
            </div>

            <!-- Synced models list -->
            <div v-if="p.models.length > 0" class="space-y-1">
              <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">可用模型</div>
              <div v-for="m in p.models" :key="m" class="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-gray-700 rounded-lg">
                <span class="text-sm text-gray-700 dark:text-gray-300">{{ m }}</span>
                <button @click="removeModel(p, m)" class="text-gray-400 hover:text-red-500 text-xs">✕</button>
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
      </div>

      <!-- 关于 -->
      <div v-if="activeTab === 'about'" class="max-w-2xl space-y-4">
        <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-6 text-center space-y-3">
          <div class="w-16 h-16 bg-green-500 rounded-2xl flex items-center justify-center text-white text-2xl font-bold mx-auto">V</div>
          <h3 class="text-lg font-bold text-gray-800 dark:text-gray-200">Vermes</h3>
          <p class="text-sm text-gray-500 dark:text-gray-400">AI Agent by vbit.top</p>
          <p class="text-xs text-gray-400">版本 1.0.0 · 基于 Hermes Agent</p>
          <a href="https://vbit.top" target="_blank" class="text-sm text-green-600 dark:text-green-400 hover:underline">访问 vbit.top →</a>
        </div>
      </div>
    </div>
  </div>
</template>
