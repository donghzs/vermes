<script setup>
import { ref, onMounted, computed } from 'vue'
import api from '../services/api.js'

const skills = ref([])
const toolsets = ref([])
const loading = ref(false)
const showToolsets = ref(false)

// tab: 'installed' | 'market'
const tab = ref('installed')

// market state
const marketQuery = ref('')
const marketSource = ref('all')
const marketItems = ref([])
const marketLoading = ref(false)
const marketError = ref('')
const marketTotal = ref(0)
const installingName = ref('')
const marketMsg = ref('')
const marketMsgOk = ref(true)

const sourceOptions = [
  { id: 'all', label: '全部' },
  { id: 'official', label: '官方' },
  { id: 'clawhub', label: 'QClaw' },
  { id: 'github', label: 'GitHub' },
  { id: 'skillhub', label: 'Skillhub' },
  { id: 'lobehub', label: 'LobeHub' },
]

const enabledCount = computed(() => skills.value.filter(s => s.enabled).length)

async function loadSkills() {
  loading.value = true
  try {
    const data = await api.getSkills()
    skills.value = Array.isArray(data) ? data : []
  } catch (e) {
    console.error('Failed to load skills:', e)
  } finally {
    loading.value = false
  }
}

async function loadToolsets() {
  try {
    const data = await api.getToolsets()
    toolsets.value = Array.isArray(data) ? data : []
    showToolsets.value = true
  } catch (e) {
    console.error('Failed to load toolsets:', e)
  }
}

async function toggleSkill(name, enabled) {
  try {
    await api.toggleSkill(name, enabled)
    const skill = skills.value.find(s => s.name === name)
    if (skill) skill.enabled = enabled
  } catch (e) {
    const skill = skills.value.find(s => s.name === name)
    if (skill) skill.enabled = !enabled
    alert('切换失败: ' + e.message)
  }
}

function isInstalled(name) {
  return skills.value.some(s => s.name === name)
}

function skillIcon(source) {
  const icons = { builtin: '🔷', trusted: '🟢', community: '🟡', hub: '🟣', official: '⭐', clawhub: '🟣', github: '🐙', skillhub: '🔶', lobehub: '🟠' }
  return icons[source] || '📄'
}

function trustLabel(item) {
  if (item.source === 'official') return '官方'
  return item.trust_level || item.trust || item.source || ''
}

async function searchMarket() {
  marketLoading.value = true
  marketError.value = ''
  marketMsg.value = ''
  try {
    const data = await api.searchSkills(marketQuery.value.trim(), marketSource.value, 24)
    if (data && data.error) {
      marketError.value = data.error
      marketItems.value = []
      marketTotal.value = 0
    } else {
      marketItems.value = (data && data.items) || []
      marketTotal.value = (data && data.total) || marketItems.value.length
    }
  } catch (e) {
    marketError.value = e.message || String(e)
    marketItems.value = []
    marketTotal.value = 0
  } finally {
    marketLoading.value = false
  }
}

async function installMarket(item) {
  installingName.value = item.name
  marketMsg.value = ''
  try {
    const res = await api.installSkill({ identifier: item.identifier, name: item.name })
    if (res && res.ok) {
      marketMsg.value = `已安装「${item.name}」`
      marketMsgOk.value = true
      await loadSkills()
    } else {
      marketMsg.value = (res && res.message) || '安装失败'
      marketMsgOk.value = false
    }
  } catch (e) {
    marketMsg.value = e.message || String(e)
    marketMsgOk.value = false
  } finally {
    installingName.value = ''
  }
}

async function uninstallMarket(item) {
  installingName.value = item.name
  marketMsg.value = ''
  try {
    const res = await api.uninstallSkill(item.name)
    if (res && res.ok) {
      marketMsg.value = `已卸载「${item.name}」`
      marketMsgOk.value = true
      await loadSkills()
    } else {
      marketMsg.value = (res && res.message) || '卸载失败'
      marketMsgOk.value = false
    }
  } catch (e) {
    marketMsg.value = e.message || String(e)
    marketMsgOk.value = false
  } finally {
    installingName.value = ''
  }
}

onMounted(() => {
  loadSkills()
})
</script>

<template>
  <div class="space-y-3">
    <!-- Header + tabs -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">🧩</span>
        <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">技能</h3>
        <div class="flex items-center gap-1 ml-1">
          <button @click="tab = 'installed'"
                  :class="tab === 'installed' ? 'text-gray-800 dark:text-gray-100 border-b-2 border-blue-500' : 'text-gray-400'"
                  class="text-xs px-1 pb-0.5">已安装 ({{ enabledCount }}/{{ skills.length }})</button>
          <button @click="tab = 'market'"
                  :class="tab === 'market' ? 'text-gray-800 dark:text-gray-100 border-b-2 border-blue-500' : 'text-gray-400'"
                  class="text-xs px-1 pb-0.5">发现</button>
        </div>
      </div>
      <button v-if="tab === 'installed'" @click="loadToolsets" class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
        📦 工具集
      </button>
    </div>

    <!-- Installed tab -->
    <template v-if="tab === 'installed'">
      <div v-if="showToolsets" class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-1.5">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-medium text-gray-600 dark:text-gray-400">📦 工具集</span>
          <button @click="showToolsets = false" class="text-gray-400 hover:text-gray-600 text-xs">✕</button>
        </div>
        <div v-for="ts in toolsets" :key="ts.name"
             class="flex items-center gap-2 text-xs py-1">
          <span class="text-gray-400">{{ ts.enabled ? '✅' : '⬜' }}</span>
          <div class="flex-1 min-w-0">
            <span class="text-gray-700 dark:text-gray-300">{{ ts.label || ts.name }}</span>
            <span v-if="ts.configured === false" class="text-[10px] text-orange-400 ml-1">未配置</span>
          </div>
          <div class="flex flex-wrap gap-0.5 max-w-[50%]">
            <span v-for="tool in (ts.tools || []).slice(0, 4)" :key="tool"
                  class="text-[9px] px-1 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-400 rounded truncate max-w-[80px]">
              {{ tool }}
            </span>
            <span v-if="(ts.tools || []).length > 4" class="text-[9px] text-gray-400">+{{ ts.tools.length - 4 }}</span>
          </div>
        </div>
      </div>

      <div class="space-y-1 max-h-64 overflow-y-auto">
        <div v-for="skill in skills" :key="skill.name"
             class="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-750">
          <span class="text-sm flex-shrink-0">{{ skillIcon(skill.source) }}</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate">{{ skill.name }}</div>
            <div class="text-[10px] text-gray-400 truncate">{{ skill.description || skill.source || '' }}</div>
          </div>
          <button @click="toggleSkill(skill.name, !skill.enabled)"
                  class="relative inline-flex h-4 w-7 items-center rounded-full transition-colors flex-shrink-0"
                  :class="skill.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'">
            <span class="inline-block h-3 w-3 transform rounded-full bg-white transition-transform"
                  :class="skill.enabled ? 'translate-x-3.5' : 'translate-x-0.5'"></span>
          </button>
        </div>
        <div v-if="!loading && skills.length === 0" class="text-center py-6 text-xs text-gray-400">
          <div class="text-2xl mb-1">🧩</div>
          <div>暂无已安装技能</div>
        </div>
        <div v-if="loading" class="text-center py-3 text-xs text-gray-400 animate-pulse">加载中...</div>
      </div>
    </template>

    <!-- Market tab -->
    <template v-else>
      <div class="flex gap-1.5">
        <input v-model="marketQuery" @keyup.enter="searchMarket" type="text" placeholder="搜索技能，如 论文 / 网页 / 翻译"
               class="flex-1 text-xs px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500" />
        <button @click="searchMarket"
                class="text-xs px-3 py-1.5 rounded-lg bg-blue-500 text-white hover:bg-blue-600">搜索</button>
      </div>
      <div class="flex flex-wrap gap-1">
        <button v-for="opt in sourceOptions" :key="opt.id" @click="marketSource = opt.id; searchMarket()"
                :class="marketSource === opt.id ? 'bg-blue-500 text-white' : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400'"
                class="text-[10px] px-2 py-0.5 rounded-full">{{ opt.label }}</button>
      </div>

      <div v-if="marketMsg" :class="marketMsgOk ? 'text-green-600' : 'text-red-500'" class="text-[11px] px-1">{{ marketMsg }}</div>

      <div class="space-y-1.5 max-h-72 overflow-y-auto">
        <div v-for="item in marketItems" :key="item.identifier || item.name"
             class="flex items-start gap-2 px-2 py-2 rounded-lg bg-gray-50 dark:bg-gray-800">
          <span class="text-base flex-shrink-0 mt-0.5">{{ skillIcon(item.source) }}</span>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-xs font-medium text-gray-700 dark:text-gray-200 truncate">{{ item.name }}</span>
              <span class="text-[9px] px-1 py-0.5 rounded bg-gray-200 dark:bg-gray-700 text-gray-500 dark:text-gray-400">{{ trustLabel(item) }}</span>
            </div>
            <div class="text-[10px] text-gray-400 truncate">{{ item.description || '' }}</div>
            <div v-if="item.tags && item.tags.length" class="flex flex-wrap gap-1 mt-1">
              <span v-for="t in item.tags.slice(0,4)" :key="t" class="text-[9px] px-1 py-0.5 rounded bg-blue-50 dark:bg-blue-900/30 text-blue-500">{{ t }}</span>
            </div>
          </div>
          <button v-if="!isInstalled(item.name)" @click="installMarket(item)"
                  :disabled="installingName === item.name"
                  class="text-[11px] px-2 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-50 flex-shrink-0">
            {{ installingName === item.name ? '安装中…' : '安装' }}
          </button>
          <button v-else @click="uninstallMarket(item)"
                  :disabled="installingName === item.name"
                  class="text-[11px] px-2 py-1 rounded-lg bg-gray-200 dark:bg-gray-700 text-gray-500 hover:text-red-500 disabled:opacity-50 flex-shrink-0">
            {{ installingName === item.name ? '…' : '卸载' }}
          </button>
        </div>

        <div v-if="marketLoading" class="text-center py-4 text-xs text-gray-400 animate-pulse">搜索中…</div>
        <div v-else-if="marketError" class="text-center py-4 text-xs text-red-400 px-2">⚠️ {{ marketError }}</div>
        <div v-else-if="marketItems.length === 0" class="text-center py-6 text-xs text-gray-400">
          <div class="text-2xl mb-1">🔍</div>
          <div>输入关键词搜索技能，或从 QClaw / GitHub 等来源发现</div>
        </div>
      </div>
    </template>
  </div>
</template>
