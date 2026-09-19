/**
 * MCP 指挥中心 — ⑤ 统一入口页（C4）。
 * 整合：已安装 server 管理 + 目录一键安装 + 安全校验 + 调用监控 + 专家目录。
 */
<script setup>
import { ref, onMounted } from 'vue'
import api from '../services/api.js'
import MCPManager from './MCPManager.vue'
import ExpertCatalog from './ExpertCatalog.vue'
import StateBlock from './StateBlock.vue'
import { useBrickEvents } from '../utils/brick-events'

const activeSection = ref('servers') // servers | catalog | security | experts

const catalog = ref([])
const catalogLoading = ref(false)
const installing = ref('')
const securityRules = ref(null)
const overview = ref(null)

async function loadOverview() {
  try {
    overview.value = await api.mcpStats()
  } catch {
    overview.value = null
  }
}

async function loadCatalog() {
  catalogLoading.value = true
  try {
    const data = await api.mcpCatalog()
    catalog.value = data?.catalog || []
  } catch {
    catalog.value = []
  } finally {
    catalogLoading.value = false
  }
}

async function loadSecurity() {
  try {
    securityRules.value = await api.mcpSecurityRules()
  } catch {
    securityRules.value = null
  }
}

async function installFromCatalog(item) {
  if (!item?.name) return
  installing.value = item.name
  try {
    const envValues = {}
    for (const a of (item.auth?.env || [])) {
      if (a.required && !envValues[a.name]) {
        const v = window.prompt(a.prompt || a.name)
        if (!v) throw new Error(`缺少 ${a.name}`)
        envValues[a.name] = v
      }
    }
    await api.mcpInstallFromCatalog(item.name, envValues)
    await loadCatalog()
  } catch (e) {
    console.error('[MCP Center] install failed', e)
  } finally {
    installing.value = ''
  }
}

function refreshAll() {
  loadOverview()
  loadCatalog()
  loadSecurity()
}

onMounted(refreshAll)
const { onEvent } = useBrickEvents()
onEvent('mcp.changed', refreshAll)

const sections = [
  { id: 'servers', label: '服务与调用', icon: '🔌' },
  { id: 'catalog', label: '目录安装', icon: '📦' },
  { id: 'security', label: '安全校验', icon: '🔒' },
  { id: 'experts', label: '专家目录', icon: '🧠' },
]
</script>

<template>
  <div class="min-h-full bg-gray-50 dark:bg-gray-950">
    <div class="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <!-- 指挥中心头 -->
      <div class="flex items-start justify-between gap-3">
        <div>
          <h1 class="text-lg font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
            <span>🎛️</span> MCP 指挥中心
          </h1>
          <p class="text-xs text-gray-500 dark:text-gray-400 mt-1">
            单一入口：管理 MCP server · 目录安装 · 安全校验 · 调用监控 · 专家目录
          </p>
        </div>
        <button
          class="text-xs px-2.5 py-1.5 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200"
          @click="refreshAll"
        >⟳ 刷新</button>
      </div>

      <!-- 调用监控总览条 -->
      <div
        v-if="overview?.summary?.calls"
        class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 px-4 py-3 flex flex-wrap items-center gap-4 text-xs"
      >
        <span class="text-gray-500">总调用 <b class="text-gray-800 dark:text-gray-100">{{ overview.summary.calls }}</b></span>
        <span class="text-gray-500">失败 <b :class="overview.summary.errors ? 'text-red-500' : 'text-gray-800 dark:text-gray-100'">{{ overview.summary.errors || 0 }}</b></span>
        <span v-if="overview.summary.interrupts" class="text-gray-500">
          中断 <b class="text-amber-600">{{ overview.summary.interrupts }}</b>
        </span>
        <span class="text-gray-500">
          成功率 <b class="text-gray-800 dark:text-gray-100">{{ Math.round((overview.summary.success_rate || 0) * 100) }}%</b>
        </span>
        <span class="ml-auto text-gray-400">{{ overview.count || 0 }} 个工具有记录</span>
      </div>
      <StateBlock
        v-else-if="overview && !overview.summary?.calls"
        state="empty" compact icon="📊" text="暂无调用记录"
        detail="统计为进程内内存，后端重启后清零"
      />

      <!-- 分区导航 -->
      <div class="flex flex-wrap gap-2">
        <button
          v-for="s in sections"
          :key="s.id"
          class="text-xs px-3 py-1.5 rounded-full border transition"
          :class="activeSection === s.id
            ? 'bg-emerald-600 border-emerald-600 text-white'
            : 'bg-white dark:bg-gray-900 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-emerald-400'"
          @click="activeSection = s.id"
        >
          {{ s.icon }} {{ s.label }}
        </button>
      </div>

      <!-- 服务与调用 -->
      <div v-show="activeSection === 'servers'" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
        <MCPManager />
      </div>

      <!-- 目录安装 -->
      <div v-show="activeSection === 'catalog'" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 space-y-3">
        <h2 class="text-sm font-medium text-gray-800 dark:text-gray-200">MCP 目录（一键安装）</h2>
        <StateBlock v-if="catalogLoading" state="loading" text="加载目录中…" />
        <StateBlock v-else-if="!catalog.length" state="empty" icon="📦" text="目录为空" />
        <div v-else class="grid gap-3 sm:grid-cols-2">
          <div
            v-for="item in catalog"
            :key="item.name"
            class="rounded-lg border border-gray-100 dark:border-gray-700 p-3 flex flex-col gap-2"
          >
            <div class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ item.name }}</div>
            <div class="text-xs text-gray-500 line-clamp-2">{{ item.description || item.summary || '' }}</div>
            <button
              v-if="!item.installed"
              class="mt-auto text-xs px-3 py-1.5 rounded-lg bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50"
              :disabled="installing === item.name"
              @click="installFromCatalog(item)"
            >{{ installing === item.name ? '安装中…' : '+ 安装' }}</button>
            <span v-else class="mt-auto text-xs text-emerald-600">已安装</span>
          </div>
        </div>
      </div>

      <!-- 安全校验 -->
      <div v-show="activeSection === 'security'" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4 space-y-3">
        <h2 class="text-sm font-medium text-gray-800 dark:text-gray-200">🔒 MCP 安全校验</h2>
        <p class="text-xs text-gray-500">所有 MCP 服务器在保存和启动时都会经过安全校验，拦截已知的后门/外泄/持久化攻击模式。</p>
        <StateBlock v-if="!securityRules" state="empty" compact text="暂无安全规则数据" />
        <div v-else class="space-y-2">
          <div
            v-for="rule in (securityRules.checks || [])"
            :key="rule.id"
            class="flex items-start gap-2 text-xs text-gray-600 dark:text-gray-300"
          >
            <span class="text-emerald-500 mt-0.5">✓</span>
            <div>
              <div class="font-medium">{{ rule.name || rule.id }}</div>
              <div class="text-gray-400">{{ rule.description || '' }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- 专家目录 -->
      <div v-show="activeSection === 'experts'" class="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
        <ExpertCatalog />
      </div>
    </div>
  </div>
</template>
