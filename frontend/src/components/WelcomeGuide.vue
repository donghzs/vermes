<script setup>
import { ref } from 'vue'
import { useChatStore } from '../stores/chat'

const chat = useChatStore()

const steps = [
  {
    icon: '🎯',
    title: '配置你的 AI 模型',
    desc: '选择一个模型厂商，输入你的 API Key',
    actions: [
      { label: '🎁 vbit.top 免费体验（10万token）', cmd: '我想用 vbit.top 免费体验', highlight: true },
      { label: '🌐 获取 OpenRouter 200+大模型（含免费）', cmd: null, highlight: false, url: 'https://openrouter.ai/', tip: '注册后复制 API Key，到「设置」→「提供商」粘贴即可' },
      { label: '💬 DeepSeek（国产低价）', cmd: '我想用 DeepSeek', highlight: false },
      { label: '🏠 本地模型（Ollama/LM Studio）', cmd: '我想用本地模型', highlight: false },
    ]
  },
  {
    icon: '⚡',
    title: '随时呼唤我',
    desc: '想要什么，直接说，我来帮你搞定',
    actions: [
      { label: '"帮我安装翻译技能"', cmd: null, info: true },
      { label: '"换个模型"', cmd: null, info: true },
      { label: '"调暗一点"', cmd: null, info: true },
      { label: '"解释这段代码"', cmd: null, info: true },
    ]
  }
]

// 快速发送一条消息
function quickSend(cmd) {
  if (!cmd) return
  chat.sendMessage(cmd, [])
}
</script>

<template>
  <!-- 空会话时显示引导卡片 -->
  <div v-if="chat.filteredMessages.length === 0" class="flex-1 flex flex-col items-center justify-center px-8 py-16 bg-gray-50 dark:bg-gray-900">

    <!-- Logo + 标题 -->
    <div class="text-center mb-10">
      <div class="text-5xl mb-4">V</div>
      <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-2">欢迎使用 Vermes</h1>
      <p class="text-gray-500 dark:text-gray-400 text-sm">本地 AI Agent · 你的私人智能助手</p>
    </div>

    <!-- 快速操作卡片 -->
    <div class="w-full max-w-xl space-y-6">
      <div v-for="step in steps" :key="step.title" class="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 p-5">
        <div class="flex items-center gap-3 mb-4">
          <span class="text-2xl">{{ step.icon }}</span>
          <div>
            <div class="font-semibold text-gray-800 dark:text-gray-200 text-base">{{ step.title }}</div>
            <div class="text-sm text-gray-500 dark:text-gray-400 mt-0.5">{{ step.desc }}</div>
          </div>
        </div>
        <div class="flex flex-col gap-2">
          <template v-for="action in step.actions" :key="action.label">
            <!-- 高亮主推按钮 -->
            <button
              v-if="action.highlight"
              @click="quickSend(action.cmd)"
              class="w-full text-left px-4 py-3 rounded-xl bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 font-medium text-sm hover:bg-green-100 dark:hover:bg-green-900/50 transition border border-green-200 dark:border-green-800"
            >
              {{ action.label }}
            </button>
            <!-- 提示信息按钮（说明类） -->
            <div
              v-else-if="action.info"
              class="w-full text-left px-4 py-2.5 rounded-xl bg-gray-50 dark:bg-gray-700/50 text-gray-500 dark:text-gray-400 text-sm cursor-default"
              :title="action.label"
            >
              {{ action.label }}
            </div>
            <!-- 带注册链接的按钮（如 OpenRouter） -->
            <button
              v-else-if="action.url"
              @click="action.cmd ? quickSend(action.cmd) : null"
              class="w-full text-left px-4 py-3 rounded-xl bg-blue-50 dark:bg-blue-900/20 text-gray-700 dark:text-blue-300 text-sm hover:bg-blue-100 dark:hover:bg-blue-900/40 transition border border-blue-200 dark:border-blue-800"
            >
              <div class="flex items-center justify-between">
                <span>{{ action.label }}</span>
                <a :href="action.url" target="_blank" class="text-blue-500 hover:text-blue-600 text-xs whitespace-nowrap ml-2" @click.stop>去注册 ↗</a>
              </div>
              <div v-if="action.tip" class="text-xs text-gray-400 dark:text-gray-500 mt-1.5">💡 {{ action.tip }}</div>
            </button>
            <!-- 普通按钮 -->
            <button
              v-else
              @click="quickSend(action.cmd)"
              class="w-full text-left px-4 py-2.5 rounded-xl bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-sm hover:bg-gray-200 dark:hover:bg-gray-600 transition"
            >
              {{ action.label }}
            </button>
          </template>
        </div>
      </div>

      <!-- 底部说明 -->
      <div class="text-center">
        <p class="text-xs text-gray-400 dark:text-gray-500">
          Vermes 不预置任何 API Key · 配置完全由你掌控 · 数据仅在本地处理
        </p>
        <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">
          需要帮助？输入 <code class="bg-gray-100 dark:bg-gray-700 px-1 rounded">/help</code> 或直接描述你的需求
        </p>
      </div>
    </div>

  </div>
</template>
