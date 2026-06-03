<script setup>
import { ref } from 'vue'

const emit = defineEmits(['close'])
const activeSection = ref('start')

const sections = [
  { id: 'start', icon: '🚀', title: '快速开始' },
  { id: 'features', icon: '✨', title: '核心功能' },
  { id: 'models', icon: '🤖', title: '模型配置' },
  { id: 'faq', icon: '❓', title: '常见问题' },
  { id: 'shortcuts', icon: '⌨️', title: '快捷操作' },
]

const features = [
  { icon: '💬', title: '智能对话', desc: '多轮对话自动记住上下文，支持 Markdown 渲染、代码高亮' },
  { icon: '🖼️', title: '图片识别', desc: '拖拽上传图片，支持多图识别，AI 自动分析内容' },
  { icon: '📎', title: '文件处理', desc: 'PDF/Word/Excel 自动提取文字，AI 读取文件内容回答问题' },
  { icon: '🧠', title: 'AI Agent', desc: '内置工具调用能力：搜索网络、执行代码、读写文件、浏览网页' },
  { icon: '📚', title: '会话管理', desc: '多会话切换、历史搜索、导出 Markdown' },
  { icon: '🔄', title: '自动更新', desc: '有新版本自动提示，一键升级' },
]

const faqs = [
  { q: '白屏 / 打不开？', a: 'Windows: 以管理员身份运行 | macOS: 系统设置 → 隐私与安全性 → 仍要打开' },
  { q: '对话没有回复？', a: '检查网络 → 检查 API Key → 检查额度 → 查看设置页模型状态' },
  { q: '图片识别不工作？', a: '确保使用支持视觉的模型（DeepSeek Chat、GPT-4o、Claude Sonnet）' },
  { q: '额度用完了？', a: '微信扫码续杯（500次/天）| 配置自己的 API Key | 明天再来' },
  { q: '如何卸载？', a: 'Windows: 控制面板卸载 | macOS: 删除 Applications 中的 Vermes.app' },
]

const shortcuts = [
  { key: 'Enter', desc: '发送消息' },
  { key: 'Shift + Enter', desc: '换行' },
  { key: '⌘/Ctrl + K', desc: '聚焦输入框' },
  { key: '⌘/Ctrl + N', desc: '新建会话' },
  { key: '⌘/Ctrl + B', desc: '切换侧边栏' },
  { key: '⌘/Ctrl + ,', desc: '打开设置' },
  { key: 'Escape', desc: '停止生成' },
  { key: '拖拽文件', desc: '上传图片或文件' },
]

const quickStarts = [
  { icon: '📧', text: '帮我写一封邮件' },
  { icon: '📊', text: '分析这个 Excel 文件' },
  { icon: '📝', text: '帮我写一篇公众号文章' },
  { icon: '💻', text: '写一段 Python 代码' },
  { icon: '🔍', text: '搜索最新的 AI 新闻' },
  { icon: '🌐', text: '翻译这段英文' },
]
</script>

<template>
  <div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" @click.self="emit('close')">
    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden">
      
      <!-- 头部 -->
      <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 bg-green-500 rounded-xl flex items-center justify-center text-white font-bold text-lg">V</div>
          <div>
            <h2 class="text-lg font-bold text-gray-800 dark:text-gray-100">Vermes 使用指南</h2>
            <p class="text-xs text-gray-500 dark:text-gray-400">开箱即用的桌面 AI 助手</p>
          </div>
        </div>
        <button @click="emit('close')" class="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition">
          <svg class="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/>
          </svg>
        </button>
      </div>

      <div class="flex flex-1 overflow-hidden">
        <!-- 左侧导航 -->
        <div class="w-40 border-r border-gray-200 dark:border-gray-700 p-3 space-y-1 overflow-y-auto">
          <button v-for="s in sections" :key="s.id"
            @click="activeSection = s.id"
            class="w-full text-left px-3 py-2 rounded-lg text-sm transition flex items-center gap-2"
            :class="activeSection === s.id ? 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 font-medium' : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'">
            <span>{{ s.icon }}</span>
            <span>{{ s.title }}</span>
          </button>
        </div>

        <!-- 右侧内容 -->
        <div class="flex-1 overflow-y-auto p-6">
          
          <!-- 快速开始 -->
          <div v-if="activeSection === 'start'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🚀 快速开始</h3>
            
            <div class="bg-green-50 dark:bg-green-900/20 rounded-xl p-4 mb-6 border border-green-200 dark:border-green-800">
              <p class="text-sm text-green-700 dark:text-green-300 font-medium mb-2">三步开始对话：</p>
              <div class="space-y-1 text-sm text-green-600 dark:text-green-400">
                <p>1️⃣ 打开 Vermes</p>
                <p>2️⃣ 在输入框输入问题</p>
                <p>3️⃣ 按 Enter 发送</p>
              </div>
            </div>

            <p class="text-sm text-gray-600 dark:text-gray-400 mb-3">试试这些：</p>
            <div class="grid grid-cols-2 gap-2">
              <div v-for="item in quickStarts" :key="item.text"
                class="p-3 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <span class="text-lg mr-2">{{ item.icon }}</span>
                <span class="text-sm text-gray-700 dark:text-gray-300">"{{ item.text }}"</span>
              </div>
            </div>

            <div class="mt-6 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-xl border border-blue-200 dark:border-blue-800">
              <p class="text-sm text-blue-700 dark:text-blue-300">
                💡 <strong>免费体验</strong>：无需注册，每天 100 次对话。额度用完可微信扫码续杯或配置自己的 API Key。
              </p>
            </div>
          </div>

          <!-- 核心功能 -->
          <div v-if="activeSection === 'features'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">✨ 核心功能</h3>
            <div class="space-y-3">
              <div v-for="f in features" :key="f.title"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <div class="flex items-start gap-3">
                  <span class="text-2xl">{{ f.icon }}</span>
                  <div>
                    <h4 class="font-medium text-gray-800 dark:text-gray-200">{{ f.title }}</h4>
                    <p class="text-sm text-gray-500 dark:text-gray-400 mt-1">{{ f.desc }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- 模型配置 -->
          <div v-if="activeSection === 'models'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">🤖 模型配置</h3>
            
            <div class="space-y-4">
              <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">免费使用</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400">直接打开就能用，每天 100 次免费对话，无需任何配置。</p>
              </div>

              <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">微信扫码续杯</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400">关注公众号，每日额度提升至 500 次。</p>
              </div>

              <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">配置自己的 API Key</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400 mb-3">支持 8+ 模型提供商：</p>
                <div class="flex flex-wrap gap-2">
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">DeepSeek</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">小米 MiMo</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">通义千问</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">OpenAI</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">Anthropic</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">Gemini</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">OpenRouter</span>
                  <span class="px-2 py-1 bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 rounded text-xs">Ollama</span>
                </div>
                <p class="text-sm text-gray-500 dark:text-gray-400 mt-3">
                  点击 ⚙️ 设置 → 输入 API Key → 同步模型 → 设为当前
                </p>
              </div>

              <div class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">输出上限 (max_tokens)</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400">在设置页顶部可配置。不设置 = 让模型自己决定（推荐）。</p>
              </div>
            </div>
          </div>

          <!-- 常见问题 -->
          <div v-if="activeSection === 'faq'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">❓ 常见问题</h3>
            <div class="space-y-3">
              <div v-for="f in faqs" :key="f.q"
                class="p-4 bg-gray-50 dark:bg-gray-700 rounded-xl border border-gray-200 dark:border-gray-600">
                <h4 class="font-medium text-gray-800 dark:text-gray-200 mb-2">{{ f.q }}</h4>
                <p class="text-sm text-gray-500 dark:text-gray-400">{{ f.a }}</p>
              </div>
            </div>
          </div>

          <!-- 快捷操作 -->
          <div v-if="activeSection === 'shortcuts'">
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-4">⌨️ 快捷操作</h3>
            <div class="space-y-2">
              <div v-for="s in shortcuts" :key="s.key"
                class="flex items-center justify-between p-3 bg-gray-50 dark:bg-gray-700 rounded-lg border border-gray-200 dark:border-gray-600">
                <span class="text-sm text-gray-700 dark:text-gray-300">{{ s.desc }}</span>
                <kbd class="px-2 py-1 bg-gray-200 dark:bg-gray-600 rounded text-xs font-mono text-gray-600 dark:text-gray-300">{{ s.key }}</kbd>
              </div>
            </div>

            <div class="mt-6 p-4 bg-purple-50 dark:bg-purple-900/20 rounded-xl border border-purple-200 dark:border-purple-800">
              <h4 class="font-medium text-purple-800 dark:text-purple-200 mb-2">💡 小技巧</h4>
              <ul class="text-sm text-purple-600 dark:text-purple-400 space-y-1">
                <li>• 拖拽图片到输入框直接识别</li>
                <li>• 拖拽文件自动提取内容</li>
                <li>• 支持多轮对话记住上下文</li>
                <li>• 左侧边栏管理多个会话</li>
                <li>• 点击消息右上角可复制</li>
              </ul>
            </div>
          </div>

        </div>
      </div>

      <!-- 底部 -->
      <div class="px-6 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <div class="flex items-center justify-between">
          <p class="text-xs text-gray-400 dark:text-gray-500">
            Vermes AI Agent © Vbit.top
          </p>
          <a href="https://vbit.top" target="_blank" class="text-xs text-green-500 hover:text-green-600 transition">
            vbit.top ↗
          </a>
        </div>
      </div>
    </div>
  </div>
</template>
