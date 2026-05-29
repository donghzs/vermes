<script setup>
import { ref } from 'vue'

const props = defineProps({
  provider: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  showSave: { type: Boolean, default: true },
  showBaseUrl: { type: Boolean, default: true },
  customDescription: { type: String, default: '' }
})

const emit = defineEmits(['toggle', 'sync', 'save', 'setModel'])

function onToggle() { emit('toggle', props.provider.id) }
function onSync() { emit('sync', props.provider) }
function onSave() { emit('save') }
function onSetModel(model) { emit('setModel', props.provider, model) }

// Provider metadata
const PROVIDER_META = {
  vbit: { icon: 'V', color: 'green', name: 'Vbit 免费体验', desc: '✅ 无需注册 · ✅ 无需 API Key · ✅ 开箱即用', showSave: false, showBaseUrl: false },
  deepseek: { icon: 'D', color: 'blue', name: '🚀 DeepSeek', desc: '国产高性价比，注册即送额度' },
  ollama: { icon: '💻', color: 'purple', name: '💻 本地模型', desc: '完全免费，数据不离开你的电脑', showSave: false, customDesc: '支持 Ollama、vMLX 等本地推理引擎。\n安装 Ollama 后点击「同步模型」自动检测已安装的模型。' },
  xiaomi: { icon: '小', color: 'orange', name: '小米 MiMo', desc: '小米自研大模型' },
  qwen: { icon: '千', color: 'indigo', name: '通义千问', desc: '阿里出品，中文理解能力强' },
  baidu: { icon: '百', color: 'blue', name: '百度文心', desc: '百度生态，知识增强' },
  xinghuo: { icon: '星', color: 'blue', name: '讯飞星火', desc: '语音识别见长' },
  minimax: { icon: 'M', color: 'teal', name: 'MiniMax', desc: '多模态能力' },
  'ant-ling': { icon: '蚂', color: 'blue', name: '蚂蚁百灵', desc: '蚂蚁集团' },
  stepfun: { icon: '阶', color: 'green', name: '阶跃星辰', desc: '长文本处理' },
  yi: { icon: '零', color: 'purple', name: '零一万物', desc: 'Yi 系列' },
  baichuan: { icon: '百', color: 'orange', name: '百川智能', desc: '开源+商用' },
  openai: { icon: 'O', color: 'gray', name: 'OpenAI', desc: 'GPT 系列' },
  anthropic: { icon: 'A', color: 'orange', name: 'Anthropic', desc: 'Claude 系列' },
  google: { icon: 'G', color: 'blue', name: 'Google', desc: 'Gemini 系列' },
  mistral: { icon: 'M', color: 'indigo', name: 'Mistral', desc: '欧洲开源' },
  groq: { icon: 'G', color: 'red', name: 'Groq', desc: '极速推理' },
  openrouter: { icon: 'R', color: 'purple', name: 'OpenRouter', desc: '统一路由' },
  custom: { icon: '+', color: 'gray', name: '自定义', desc: '任意 OpenAI 兼容接口' }
}

const meta = PROVIDER_META[props.provider.id] || { icon: '?', color: 'gray', name: props.provider.name, desc: '' }
const colorClasses = {
  green: 'bg-green-100 dark:bg-green-900 text-green-600 dark:text-green-400',
  blue: 'bg-blue-100 dark:bg-blue-900 text-blue-600 dark:text-blue-400',
  purple: 'bg-purple-100 dark:bg-purple-900 text-purple-600 dark:text-purple-400',
  orange: 'bg-orange-100 dark:bg-orange-900 text-orange-600 dark:text-orange-400',
  indigo: 'bg-indigo-100 dark:bg-indigo-900 text-indigo-600 dark:text-indigo-400',
  teal: 'bg-teal-100 dark:bg-teal-900 text-teal-600 dark:text-teal-400',
  red: 'bg-red-100 dark:bg-red-900 text-red-600 dark:text-red-400',
  gray: 'bg-gray-100 dark:bg-gray-700 text-gray-400'
}
</script>

<template>
  <div class="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden">
    <!-- Header -->
    <button @click="onToggle" class="w-full px-4 py-3 flex items-center justify-between hover:bg-gray-50 dark:hover:bg-gray-750 transition">
      <div class="flex items-center gap-3">
        <div class="w-10 h-10 rounded-xl flex items-center justify-center text-sm font-bold" :class="colorClasses[meta.color]">
          {{ meta.icon }}
        </div>
        <div class="text-left">
          <div class="font-medium text-gray-800 dark:text-gray-200">{{ meta.name }}</div>
          <div class="text-xs text-gray-500 dark:text-gray-400">{{ meta.desc }}</div>
        </div>
      </div>
      <svg class="w-4 h-4 text-gray-400 transition-transform" :class="expanded ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
      </svg>
    </button>

    <!-- Expanded Content -->
    <div v-if="expanded" class="px-4 pb-4 space-y-3 border-t border-gray-100 dark:border-gray-700">
      <!-- Custom Description (for Ollama etc) -->
      <div v-if="customDescription || meta.customDesc" class="pt-3 text-xs text-gray-500 dark:text-gray-400 whitespace-pre-line">
        {{ customDescription || meta.customDesc }}
      </div>

      <!-- API Key -->
      <div v-if="showSave" class="pt-3">
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">API Key</label>
        <input v-model="provider.key" type="password" placeholder="sk-..." 
               class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
      </div>

      <!-- Base URL -->
      <div v-if="showBaseUrl && meta.showBaseUrl !== false">
        <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Base URL</label>
        <input v-model="provider.baseUrl" type="text" 
               class="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-700 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-500" />
      </div>

      <!-- Actions -->
      <div class="flex gap-2">
        <button @click="onSync" :disabled="provider.syncing" 
                class="px-4 py-1.5 bg-green-500 hover:bg-green-600 disabled:bg-green-300 text-white rounded-lg text-xs font-medium transition flex items-center gap-1">
          <svg v-if="provider.syncing" class="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"></path>
          </svg>
          {{ provider.syncing ? '同步中...' : '🔄 同步模型' }}
        </button>
        <button v-if="showSave && meta.showSave !== false" @click="onSave" 
                class="px-4 py-1.5 bg-gray-200 dark:bg-gray-600 hover:bg-gray-300 dark:hover:bg-gray-500 text-gray-700 dark:text-gray-200 rounded-lg text-xs font-medium transition">
          💾 保存
        </button>
      </div>

      <!-- Models List -->
      <div v-if="provider.models.length > 0" class="space-y-1">
        <div class="text-xs text-gray-500 dark:text-gray-400 font-medium">可用模型</div>
        <div v-for="m in provider.models" :key="m" 
             class="flex items-center justify-between px-3 py-1.5 bg-gray-50 dark:bg-gray-700 rounded-lg">
          <span class="text-sm text-gray-700 dark:text-gray-300">{{ m }}</span>
          <button @click="onSetModel(m)" 
                  class="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 rounded hover:bg-green-200 dark:hover:bg-green-800/60 transition font-medium">
            ✓ 设为当前
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
