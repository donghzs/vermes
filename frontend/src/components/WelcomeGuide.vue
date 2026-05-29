<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'
import { useRouter } from 'vue-router'
import { toast } from '../utils/toast'

const chat = useChatStore()
const router = useRouter()

// 引导步骤
const currentStep = ref(1)
const selectedMode = ref(null) // 'wechat' | 'free' | 'apikey' | 'local'

// 选择使用方式
function selectMode(mode) {
  selectedMode.value = mode
  currentStep.value = 2
}

// 完成引导，进入聊天
function finishGuide() {
  currentStep.value = 3
}

// 跳转到设置页
function goSettings() {
  router.push('/settings')
}

// 快速发送第一条消息
function quickSend(msg) {
  chat.sendMessage(msg, [])
}

// 快速开始建议
const quickStarts = [
  { icon: '📧', text: '帮我写一封邮件' },
  { icon: '📊', text: '分析这个Excel文件' },
  { icon: '📝', text: '帮我写一篇公众号文章' },
  { icon: '💻', text: '写一段Python代码' },
]
</script>

<template>
  <!-- 空会话时显示引导 -->
  <div v-if="chat.filteredMessages.length === 0" class="flex-1 flex flex-col items-center justify-center px-8 py-16 bg-gray-50 dark:bg-gray-900">
    
    <!-- Step 1: 选择使用方式 -->
    <div v-if="currentStep === 1" class="w-full max-w-md">
      <!-- Logo -->
      <div class="text-center mb-8">
        <div class="w-20 h-20 bg-green-500 rounded-3xl flex items-center justify-center text-white text-4xl font-bold mx-auto mb-4 shadow-lg">V</div>
        <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-2">欢迎使用 Vermes</h1>
        <p class="text-gray-500 dark:text-gray-400">选择一种方式开始</p>
      </div>

      <!-- 选择卡片 -->
      <div class="space-y-3">
        <!-- 微信扫码登录 -->
        <button @click="selectMode('wechat')" 
                class="w-full p-4 bg-white dark:bg-gray-800 rounded-2xl border-2 border-green-200 dark:border-green-800 hover:border-green-400 dark:hover:border-green-600 transition shadow-sm text-left">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 bg-green-100 dark:bg-green-900/50 rounded-xl flex items-center justify-center text-2xl">💬</div>
            <div class="flex-1">
              <div class="font-semibold text-gray-800 dark:text-gray-200">微信扫码登录</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">最简单，每天免费500积分</div>
            </div>
            <span class="text-xs bg-green-100 dark:bg-green-900/50 text-green-600 dark:text-green-400 px-2 py-1 rounded-full font-medium">推荐</span>
          </div>
        </button>

        <!-- 免费体验 -->
        <button @click="selectMode('free')"
                class="w-full p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 transition text-left">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 bg-blue-100 dark:bg-blue-900/50 rounded-xl flex items-center justify-center text-2xl">🆓</div>
            <div class="flex-1">
              <div class="font-semibold text-gray-800 dark:text-gray-200">免费体验</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">无需注册，直接使用</div>
            </div>
          </div>
        </button>

        <!-- 配置API Key -->
        <button @click="selectMode('apikey')"
                class="w-full p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 transition text-left">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 bg-purple-100 dark:bg-purple-900/50 rounded-xl flex items-center justify-center text-2xl">🔑</div>
            <div class="flex-1">
              <div class="font-semibold text-gray-800 dark:text-gray-200">配置自己的 API Key</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">使用 DeepSeek、OpenAI 等</div>
            </div>
          </div>
        </button>

        <!-- 本地模型 -->
        <button @click="selectMode('local')"
                class="w-full p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-gray-300 dark:hover:border-gray-600 transition text-left">
          <div class="flex items-center gap-4">
            <div class="w-12 h-12 bg-gray-100 dark:bg-gray-700 rounded-xl flex items-center justify-center text-2xl">💻</div>
            <div class="flex-1">
              <div class="font-semibold text-gray-800 dark:text-gray-200">使用本地模型</div>
              <div class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">完全免费，数据不离开电脑</div>
            </div>
          </div>
        </button>
      </div>
    </div>

    <!-- Step 2: 根据选择引导配置 -->
    <div v-else-if="currentStep === 2" class="w-full max-w-md">
      <!-- 微信扫码 -->
      <div v-if="selectedMode === 'wechat'" class="text-center">
        <div class="text-6xl mb-6">💬</div>
        <h2 class="text-xl font-bold text-gray-800 dark:text-gray-100 mb-3">微信扫码登录</h2>
        <p class="text-gray-500 dark:text-gray-400 mb-6">点击右上角的微信登录按钮，扫码即可</p>
        <div class="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 mb-6">
          <div class="text-sm text-gray-600 dark:text-gray-300 space-y-2">
            <p>1. 点击顶部栏的 👤 头像</p>
            <p>2. 用微信扫描二维码</p>
            <p>3. 授权后自动登录</p>
          </div>
        </div>
        <button @click="finishGuide()" class="px-6 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl font-medium transition">
          我已登录，继续 →
        </button>
      </div>

      <!-- 免费体验 -->
      <div v-else-if="selectedMode === 'free'" class="text-center">
        <div class="text-6xl mb-6">🆓</div>
        <h2 class="text-xl font-bold text-gray-800 dark:text-gray-100 mb-3">免费体验</h2>
        <p class="text-gray-500 dark:text-gray-400 mb-6">无需任何配置，直接开始对话</p>
        <div class="bg-green-50 dark:bg-green-900/30 rounded-2xl p-6 border border-green-200 dark:border-green-800 mb-6">
          <div class="text-sm text-green-700 dark:text-green-300 space-y-2">
            <p>✅ 每天 100 次免费调用</p>
            <p>✅ 支持基础对话功能</p>
            <p>✅ 无需注册账号</p>
          </div>
        </div>
        <button @click="finishGuide()" class="px-6 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl font-medium transition">
          开始体验 →
        </button>
      </div>

      <!-- 配置API Key -->
      <div v-else-if="selectedMode === 'apikey'" class="text-center">
        <div class="text-6xl mb-6">🔑</div>
        <h2 class="text-xl font-bold text-gray-800 dark:text-gray-100 mb-3">配置 API Key</h2>
        <p class="text-gray-500 dark:text-gray-400 mb-6">前往设置页配置你的模型提供商</p>
        <div class="bg-blue-50 dark:bg-blue-900/30 rounded-2xl p-6 border border-blue-200 dark:border-blue-800 mb-6">
          <div class="text-sm text-blue-700 dark:text-blue-300 space-y-2">
            <p>🎯 <strong>推荐 DeepSeek</strong> — 国产高性价比</p>
            <p>📝 注册后复制 API Key</p>
            <p>⚙️ 在设置页粘贴即可</p>
          </div>
        </div>
        <button @click="goSettings()" class="px-6 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl font-medium transition mb-3">
          前往设置页 →
        </button>
        <button @click="finishGuide()" class="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">
          稍后再配置
        </button>
      </div>

      <!-- 本地模型 -->
      <div v-else-if="selectedMode === 'local'" class="text-center">
        <div class="text-6xl mb-6">💻</div>
        <h2 class="text-xl font-bold text-gray-800 dark:text-gray-100 mb-3">使用本地模型</h2>
        <p class="text-gray-500 dark:text-gray-400 mb-6">完全免费，数据不离开你的电脑</p>
        <div class="bg-gray-100 dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 mb-6">
          <div class="text-sm text-gray-700 dark:text-gray-300 space-y-3 text-left">
            <p class="font-medium">安装 Ollama：</p>
            <div class="bg-gray-200 dark:bg-gray-700 rounded-lg p-3 font-mono text-xs">
              curl -fsSL https://ollama.ai/install.sh | sh
            </div>
            <p class="font-medium mt-3">下载模型：</p>
            <div class="bg-gray-200 dark:bg-gray-700 rounded-lg p-3 font-mono text-xs">
              ollama pull qwen2.5:7b
            </div>
          </div>
        </div>
        <button @click="goSettings()" class="px-6 py-3 bg-green-500 hover:bg-green-600 text-white rounded-xl font-medium transition mb-3">
          前往设置页检测 →
        </button>
        <button @click="finishGuide()" class="text-sm text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition">
          我已安装，继续
        </button>
      </div>
    </div>

    <!-- Step 3: 开始对话 -->
    <div v-else-if="currentStep === 3" class="w-full max-w-md text-center">
      <div class="text-6xl mb-6">🎊</div>
      <h2 class="text-xl font-bold text-gray-800 dark:text-gray-100 mb-3">一切就绪！</h2>
      <p class="text-gray-500 dark:text-gray-400 mb-8">试试对我说：</p>

      <!-- 快速开始 -->
      <div class="space-y-3 mb-8">
        <button v-for="item in quickStarts" :key="item.text"
                @click="quickSend(item.text)"
                class="w-full p-4 bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 hover:border-green-400 dark:hover:border-green-600 transition text-left">
          <div class="flex items-center gap-3">
            <span class="text-xl">{{ item.icon }}</span>
            <span class="text-gray-700 dark:text-gray-300">"{{ item.text }}"</span>
          </div>
        </button>
      </div>

      <!-- 底部说明 -->
      <p class="text-xs text-gray-400 dark:text-gray-500">
        随时可以输入任何问题，我会尽力帮助你
      </p>
    </div>

    <!-- 底部通用说明 -->
    <div class="mt-8 text-center">
      <p class="text-xs text-gray-400 dark:text-gray-500">
        Vermes · 本地 AI Agent · 数据仅在本地处理
      </p>
    </div>
  </div>
</template>
