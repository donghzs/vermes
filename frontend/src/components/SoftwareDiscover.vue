<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'

// ── 子 tab：已装 / 发现 ──
const subTab = ref('installed')

// ── 已装适配器 ──
const installed = ref([])
const installedLoading = ref(false)

async function loadInstalled() {
  installedLoading.value = true
  try {
    const data = await api.get('/adapters/installed')
    installed.value = Array.isArray(data.adapters) ? data.adapters : []
  } catch (e) {
    console.error('Failed to load installed adapters:', e)
  } finally {
    installedLoading.value = false
  }
}

// ── 发现推荐 ──
const intentQuery = ref('')
const recommendations = ref([])
const recLoading = ref(false)
const recError = ref('')
const installingSoftware = ref('')
const installMsg = ref('')
const installMsgOk = ref(true)

async function searchRecommendations() {
  recLoading.value = true
  recError.value = ''
  installMsg.value = ''
  try {
    const q = intentQuery.value.trim() || '常用工具'
    const data = await api.get(`/adapters/recommend?intent=${encodeURIComponent(q)}&limit=24`)
    if (data && data.error) {
      recError.value = data.error
      recommendations.value = []
    } else {
      recommendations.value = Array.isArray(data.recommendations) ? data.recommendations : []
    }
  } catch (e) {
    recError.value = e.message || String(e)
    recommendations.value = []
  } finally {
    recLoading.value = false
  }
}

async function installAdapter(rec) {
  installingSoftware.value = rec.software
  installMsg.value = ''
  try {
    const data = await api.post('/adapters/install', {
      software: rec.software,
      adapter_install: rec.adapter_install,
      backend_hint: rec.backend_hint,
    })
    if (data && data.ok) {
      installMsg.value = `✅ ${rec.software} 适配器已安装${data.tools_registered >= 0 ? `（注册 ${data.tools_registered} 个工具）` : ''}`
      installMsgOk.value = true
      // 刷新已装列表
      await loadInstalled()
      // 从推荐列表移除
      recommendations.value = recommendations.value.filter(r => r.software !== rec.software)
    } else {
      installMsg.value = `❌ 安装失败: ${(data && data.error) || '未知错误'}`
      installMsgOk.value = false
    }
  } catch (e) {
    installMsg.value = `❌ 请求失败: ${e.message}`
    installMsgOk.value = false
  } finally {
    installingSoftware.value = ''
  }
}

function domainIcon(domain) {
  const icons = {
    '3d': '🧊', 'ai': '🤖', 'audio': '🎵', 'automation': '⚡',
    'communication': '💬', 'database': '🗄️', 'data-science': '📊',
    'design': '🎨', 'devops': '🔧', 'devtools': '🛠️', 'knowledge': '📖',
    'mobile': '📱', 'music': '🎶', 'office': '📝', 'productivity': '📋',
    'video': '🎬', 'web': '🌐',
  }
  return icons[domain] || '📦'
}

function scoreLabel(score) {
  if (score >= 0.7) return '高'
  if (score >= 0.3) return '中'
  return '低'
}

onMounted(() => {
  loadInstalled()
})
</script>

<template>
  <div class="space-y-3">
    <!-- Header + sub tabs -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">📦</span>
        <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">软件积木</h3>
        <div class="flex items-center gap-1 ml-1">
          <button @click="subTab = 'installed'; loadInstalled()"
                  :class="subTab === 'installed' ? 'text-gray-800 dark:text-gray-100 border-b-2 border-green-500' : 'text-gray-400'"
                  class="text-xs px-1 pb-0.5">已装 ({{ installed.length }})</button>
          <button @click="subTab = 'discover'; searchRecommendations()"
                  :class="subTab === 'discover' ? 'text-gray-800 dark:text-gray-100 border-b-2 border-green-500' : 'text-gray-400'"
                  class="text-xs px-1 pb-0.5">发现</button>
        </div>
      </div>
    </div>

    <!-- 已装 tab -->
    <template v-if="subTab === 'installed'">
      <div v-if="installedLoading" class="text-center py-6 text-xs text-gray-400 animate-pulse">加载中…</div>
      <div v-else-if="installed.length === 0" class="text-center py-6 text-xs text-gray-400">
        <div class="text-2xl mb-1">📦</div>
        <div>暂无已装软件积木</div>
        <div class="text-[10px] mt-1">点击「发现」安装 CLI-Anything 适配器</div>
      </div>
      <div v-else class="space-y-1.5 max-h-64 overflow-y-auto">
        <div v-for="adapter in installed" :key="adapter.software"
             class="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-800">
          <span class="text-base">{{ domainIcon('') }}</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ adapter.software }}</div>
            <div class="text-[10px] text-gray-400">
              {{ adapter.tools_registered >= 0 ? `${adapter.tools_registered} 个工具` : '加载失败' }}
            </div>
          </div>
          <span class="text-[9px] px-1.5 py-0.5 rounded-full bg-green-100 dark:bg-green-900/40 text-green-600">已装</span>
        </div>
      </div>
    </template>

    <!-- 发现 tab -->
    <template v-else>
      <div class="flex gap-1.5">
        <input v-model="intentQuery" @keyup.enter="searchRecommendations" type="text"
               placeholder="输入需求，如 3D建模 / 视频编辑 / 数据库"
               class="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-green-500" />
        <button @click="searchRecommendations"
                class="text-xs px-3 py-1.5 rounded-lg bg-green-500 text-white hover:bg-green-600">搜索</button>
      </div>

      <div v-if="installMsg" :class="installMsgOk ? 'text-green-600' : 'text-red-500'" class="text-[11px] px-1">{{ installMsg }}</div>

      <div class="space-y-1.5 max-h-72 overflow-y-auto">
        <div v-for="rec in recommendations" :key="rec.software"
             class="flex items-start gap-2 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-800">
          <span class="text-base flex-shrink-0 mt-0.5">{{ domainIcon(rec.domain) }}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-xs font-medium text-gray-700 dark:text-gray-200 truncate">{{ rec.software }}</span>
              <span class="text-[9px] px-1 py-0.5 rounded bg-gray-200 dark:bg-gray-700 text-gray-500">{{ rec.domain }}</span>
              <span class="text-[9px] px-1 py-0.5 rounded"
                    :class="rec.score >= 0.7 ? 'bg-green-100 dark:bg-green-900/40 text-green-600' : 'bg-gray-100 dark:bg-gray-700 text-gray-400'">
                匹配{{ scoreLabel(rec.score) }}
              </span>
            </div>
            <div v-if="rec.reason" class="text-[10px] text-gray-400 mt-0.5">{{ rec.reason }}</div>
            <div v-if="rec.backend_hint" class="text-[10px] text-orange-400 mt-0.5">⚠ 需要: {{ rec.backend_hint }}</div>
          </div>
          <button @click="installAdapter(rec)"
                  :disabled="installingSoftware === rec.software"
                  class="text-[11px] px-2 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-50 flex-shrink-0">
            {{ installingSoftware === rec.software ? '安装中…' : '安装' }}
          </button>
        </div>

        <div v-if="recLoading" class="text-center py-4 text-xs text-gray-400 animate-pulse">搜索中…</div>
        <div v-else-if="recError" class="text-center py-4 text-xs text-red-400 px-2">⚠️ {{ recError }}</div>
        <div v-else-if="recommendations.length === 0 && !recLoading" class="text-center py-6 text-xs text-gray-400">
          <div class="text-2xl mb-1">🔍</div>
          <div>输入你的需求，发现可安装的软件积木</div>
          <div class="text-[10px] mt-1">如"3D建模""视频编辑""数据库管理"</div>
        </div>
      </div>
    </template>
  </div>
</template>
