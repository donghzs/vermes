<script setup>
import { ref, onErrorCaptured } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const error = ref(null)
const hasError = ref(false)

onErrorCaptured((err, instance, info) => {
  console.error('[ErrorBoundary]', err, info)
  error.value = {
    message: err?.message || String(err),
    stack: err?.stack || '',
    info: info || ''
  }
  hasError.value = true
  // 阻止错误继续冒泡到浏览器
  return false
})

function reload() {
  router.go(0)
}

function dismiss() {
  hasError.value = false
  error.value = null
}
</script>

<template>
  <div v-if="hasError" class="fixed inset-0 z-[9999] flex items-center justify-center bg-gray-900/80 backdrop-blur-sm p-6">
    <div class="max-w-lg w-full bg-white dark:bg-gray-800 rounded-2xl shadow-2xl border border-red-200 dark:border-red-900/30 overflow-hidden">
      <!-- 头部 -->
      <div class="px-6 py-4 border-b border-red-100 dark:border-red-900/20 bg-red-50 dark:bg-red-900/20">
        <div class="flex items-center gap-3">
          <span class="text-2xl">⚠️</span>
          <div>
            <h2 class="font-semibold text-red-700 dark:text-red-300">出了点问题</h2>
            <p class="text-sm text-red-500 dark:text-red-400">应用遇到了一个意外错误，刷新通常能解决。</p>
          </div>
        </div>
      </div>

      <!-- 错误详情 -->
      <div class="px-6 py-4">
        <p class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-2">{{ error?.message }}</p>
        <details v-if="error?.info" class="mb-2">
          <summary class="text-xs text-gray-400 cursor-pointer hover:text-gray-500">组件信息</summary>
          <pre class="mt-1 text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900 rounded p-2 overflow-auto max-h-24">{{ error.info }}</pre>
        </details>
        <details v-if="error?.stack">
          <summary class="text-xs text-gray-400 cursor-pointer hover:text-gray-500">堆栈跟踪</summary>
          <pre class="mt-1 text-xs text-gray-400 dark:text-gray-500 bg-gray-50 dark:bg-gray-900 rounded p-2 overflow-auto max-h-40 font-mono">{{ error.stack }}</pre>
        </details>
      </div>

      <!-- 按钮 -->
      <div class="px-6 py-4 border-t border-gray-100 dark:border-gray-700 flex gap-3">
        <button
          @click="reload"
          class="flex-1 px-4 py-2 bg-green-500 hover:bg-green-600 text-white rounded-lg text-sm font-medium transition-colors"
        >
          🔄 刷新页面
        </button>
        <button
          @click="dismiss"
          class="px-4 py-2 text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
        >
          忽略
        </button>
      </div>
    </div>
  </div>
  <slot v-else />
</template>
