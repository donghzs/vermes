<template>
  <div v-if="chat.pendingApproval" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm">
    <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl max-w-lg w-full mx-4 overflow-hidden border border-gray-200 dark:border-gray-600">
      <!-- Header -->
      <div class="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center gap-3">
        <span class="text-2xl">⚠️</span>
        <div>
          <h3 class="text-base font-semibold text-gray-900 dark:text-white">工具审批请求</h3>
          <p class="text-xs text-gray-500 dark:text-gray-400">Agent 想执行以下命令</p>
        </div>
      </div>

      <!-- Body -->
      <div class="px-5 py-4 space-y-3">
        <div>
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">命令</div>
          <pre class="text-sm font-mono bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200 p-3 rounded-lg overflow-x-auto whitespace-pre-wrap break-all">{{ chat.pendingApproval.command }}</pre>
        </div>
        <div v-if="chat.pendingApproval.description">
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">说明</div>
          <div class="text-sm text-gray-700 dark:text-gray-300">{{ chat.pendingApproval.description }}</div>
        </div>
        <div v-if="chat.pendingApproval.diff">
          <div class="text-xs text-gray-500 dark:text-gray-400 mb-1">改动预览（diff）</div>
          <pre class="text-xs font-mono bg-gray-100 dark:bg-gray-900 text-gray-800 dark:text-gray-200 p-3 rounded-lg overflow-x-auto whitespace-pre-wrap break-all max-h-64 overflow-y-auto">{{ chat.pendingApproval.diff }}</pre>
        </div>
      </div>

      <!-- Actions -->
      <div class="px-5 py-4 border-t border-gray-200 dark:border-gray-700 flex gap-2 justify-end">
        <button @click="chat.resolveApproval('deny')"
          class="px-4 py-2 text-sm rounded-lg bg-red-500/10 text-red-600 dark:text-red-400 hover:bg-red-500/20 transition font-medium">
          拒绝
        </button>
        <button @click="chat.resolveApproval('once')"
          class="px-4 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 transition">
          仅本次
        </button>
        <button @click="chat.resolveApproval('session')"
          class="px-4 py-2 text-sm rounded-lg bg-green-500/10 text-green-600 dark:text-green-400 hover:bg-green-500/20 transition font-medium">
          本次会话允许
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useChatStore } from '../stores/chat'
const chat = useChatStore()
</script>
