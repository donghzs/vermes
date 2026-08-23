<template>
  <div class="h-full flex flex-col bg-gray-900 text-white">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-700 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🏪</span>
        <h1 class="text-xl font-bold">模块商店</h1>
        <span class="text-sm text-gray-400" v-if="modules.length > 0">
          {{ modules.length }} 个模块可用
        </span>
      </div>
      <button
        @click="refresh"
        :disabled="loading"
        class="px-3 py-1.5 text-sm rounded-lg bg-gray-700 hover:bg-gray-600 disabled:opacity-50 transition"
      >
        {{ loading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </div>

    <!-- 模块卡片 -->
    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="loading && modules.length === 0" class="text-center text-gray-400 py-12">
        加载中…
      </div>

      <div v-else-if="modules.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">📦 暂无可用模块</p>
        <p class="text-sm mt-2">模块目录为空或加载失败</p>
      </div>

      <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div
          v-for="mod in modules"
          :key="mod.name"
          class="bg-gray-800 rounded-xl p-5 border border-gray-700 hover:border-gray-500 transition"
        >
          <!-- 标题行 -->
          <div class="flex items-start justify-between mb-3">
            <div>
              <div class="flex items-center gap-2">
                <h3 class="text-lg font-semibold">{{ mod.display_name }}</h3>
                <span v-if="mod.recommended" class="text-xs px-2 py-0.5 rounded-full bg-green-900 text-green-300">
                  推荐
                </span>
              </div>
              <p class="text-sm text-gray-400 mt-0.5">
                {{ mod.name }} · v{{ mod.version }} · {{ mod.size_code ? Math.round(mod.size_code / 1024) + 'KB' : '' }}
              </p>
            </div>
            <span
              class="text-xs px-2 py-1 rounded-full"
              :class="mod.installed ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-300'"
            >
              {{ mod.installed ? '✅ 已安装' : '⬇ 可安装' }}
            </span>
          </div>

          <!-- 描述 -->
          <p class="text-sm text-gray-300 mb-3" v-if="mod.description">
            {{ mod.description }}
          </p>

          <!-- 工具标签 -->
          <div class="flex flex-wrap gap-1.5 mb-4" v-if="mod.provides_tools && mod.provides_tools.length > 0">
            <span
              v-for="tool in mod.provides_tools.slice(0, 6)"
              :key="tool"
              class="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300"
            >
              {{ tool }}
            </span>
            <span v-if="mod.provides_tools.length > 6" class="text-xs text-gray-500 self-center">
              +{{ mod.provides_tools.length - 6 }} 更多
            </span>
          </div>

          <!-- 关键词 -->
          <div class="flex flex-wrap gap-1.5 mb-4" v-if="mod.keywords && mod.keywords.length > 0">
            <span
              v-for="kw in mod.keywords.slice(0, 5)"
              :key="kw"
              class="text-xs px-2 py-0.5 rounded bg-gray-700/50 text-gray-400"
            >
              #{{ kw }}
            </span>
          </div>

          <!-- 操作按钮 -->
          <div class="flex gap-2">
            <button
              v-if="!mod.installed"
              @click="installModule(mod)"
              :disabled="installing === mod.name"
              class="flex-1 px-4 py-2 text-sm rounded-lg bg-green-600 hover:bg-green-500 disabled:opacity-50 transition font-medium"
            >
              {{ installing === mod.name ? '安装中…' : '⬇ 安装' }}
            </button>
            <button
              v-else
              @click="uninstallModule(mod)"
              :disabled="installing === mod.name"
              class="flex-1 px-4 py-2 text-sm rounded-lg bg-red-700 hover:bg-red-600 disabled:opacity-50 transition"
            >
              {{ installing === mod.name ? '卸载中…' : '🗑 卸载' }}
            </button>
            <a
              v-if="mod.homepage"
              :href="mod.homepage"
              target="_blank"
              class="px-3 py-2 text-sm rounded-lg bg-gray-700 hover:bg-gray-600 transition"
            >
              📖
            </a>
          </div>
        </div>
      </div>
    </div>

    <!-- 消息提示 -->
    <div
      v-if="message.text"
      class="fixed bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg shadow-lg text-sm"
      :class="message.type === 'error' ? 'bg-red-600' : message.type === 'success' ? 'bg-green-600' : 'bg-gray-700'"
    >
      {{ message.text }}
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const modules = ref([])
const loading = ref(false)
const installing = ref('')
const message = ref({ text: '', type: '' })

function showMessage(text, type = 'info') {
  message.value = { text, type }
  setTimeout(() => { message.value = { text: '', type: '' } }, 3000)
}

async function fetchCatalog() {
  loading.value = true
  try {
    const res = await fetch('/api/v1/modules/market')
    const data = await res.json()
    if (data.error) {
      showMessage(`目录加载失败: ${data.error}`, 'error')
    }
    modules.value = data.modules || []
  } catch (e) {
    showMessage(`请求失败: ${e.message}`, 'error')
  } finally {
    loading.value = false
  }
}

async function installModule(mod) {
  installing.value = mod.name
  showMessage(`正在安装 ${mod.display_name}…`)
  try {
    const res = await fetch('/api/v1/modules/market/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: mod.id || mod.name }),
    })
    const data = await res.json()
    if (data.ok) {
      showMessage(`✅ ${mod.display_name} 安装成功`, 'success')
      mod.installed = true
    } else {
      showMessage(`安装失败: ${data.detail || data.error}`, 'error')
    }
  } catch (e) {
    showMessage(`请求失败: ${e.message}`, 'error')
  } finally {
    installing.value = ''
  }
}

async function uninstallModule(mod) {
  installing.value = mod.name
  showMessage(`正在卸载 ${mod.display_name}…`)
  try {
    const res = await fetch('/api/v1/modules/market/uninstall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: mod.id || mod.name }),
    })
    const data = await res.json()
    if (data.ok) {
      showMessage(`✅ ${mod.display_name} 已卸载`, 'success')
      mod.installed = false
    } else {
      showMessage(`卸载失败: ${data.detail || data.error}`, 'error')
    }
  } catch (e) {
    showMessage(`请求失败: ${e.message}`, 'error')
  } finally {
    installing.value = ''
  }
}

function refresh() {
  fetchCatalog()
}

onMounted(() => {
  fetchCatalog()
})
</script>
