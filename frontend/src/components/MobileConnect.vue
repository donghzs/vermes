<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'

const { confirm } = useConfirm()

// ── 渠道数据 ──
const channelsData = ref(null)
const channelsLoading = ref(false)
const channelExpanded = reactive({})
const channelForms = reactive({})
const channelSaving = ref(false)

// ── 网关状态 ──
const gatewayRunning = ref(false)
const gatewayStarting = ref(false)

async function checkGatewayStatus() {
  try {
    const d = await api.default.get('/status')
    gatewayRunning.value = !!d.gateway_running
  } catch {}
}

async function startGateway() {
  gatewayStarting.value = true
  try {
    if (window.vermes?.restartGateway) {
      await window.vermes.restartGateway()
    }
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 2000))
      await checkGatewayStatus()
      if (gatewayRunning.value) break
    }
  } catch (e) {
    console.error('startGateway:', e)
  } finally {
    gatewayStarting.value = false
  }
}

async function restartGateway() {
  gatewayStarting.value = true
  try {
    if (window.vermes?.restartGateway) {
      await window.vermes.restartGateway()
    }
    gatewayRunning.value = false
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 2000))
      await checkGatewayStatus()
      if (gatewayRunning.value) break
    }
  } catch (e) {
    console.error('restartGateway:', e)
  } finally {
    gatewayStarting.value = false
  }
}

const channelCategories = computed(() => {
  if (!channelsData.value?.grouped) return []
  return Object.entries(channelsData.value.grouped).map(([cat, items]) => ({ cat, items }))
})

async function loadChannels() {
  channelsLoading.value = true
  try {
    const data = await api.default.listGatewayChannels()
    channelsData.value = data
    for (const ch of data.channels || []) {
      channelForms[ch.key] = {}
      for (const f of ch.fields) {
        channelForms[ch.key][f.key] = ''
      }
    }
  } catch (e) {
    console.error('loadChannels:', e)
  } finally {
    channelsLoading.value = false
  }
}

async function saveChannel(platformKey) {
  const form = channelForms[platformKey]
  if (!form) return
  channelSaving.value = true
  try {
    const fields = {}
    for (const [k, v] of Object.entries(form)) {
      if (v && v.trim()) fields[k] = v.trim()
    }
    const result = await api.default.saveGatewayChannel(platformKey, fields)
    if (result.ok) {
      if (channelsData.value) {
        const idx = channelsData.value.channels.findIndex(c => c.key === platformKey)
        if (idx >= 0) channelsData.value.channels[idx] = result.channel
        for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
          const i2 = items.findIndex(c => c.key === platformKey)
          if (i2 >= 0) items[i2] = result.channel
        }
      }
      for (const k of Object.keys(form)) form[k] = ''
      const gw = result.gateway
      if (gw?.ok && gw.state === 'connected') {
        toast.success(`${platformKey} 已连接`)
      } else if (gw?.ok && gw.note) {
        toast.success('凭据已保存，渠道将在数秒内自动连接')
      } else if (gw && !gw.ok) {
        toast.error(`${platformKey} 连接失败: ${gw.error || '未知错误'}`)
      } else {
        toast.success('渠道凭据已保存')
      }
      await checkGatewayStatus()
    }
  } catch (e) {
    toast.error('保存失败: ' + (e.message || e))
  } finally {
    channelSaving.value = false
  }
}

async function clearChannel(platformKey) {
  const ch = channelsData.value?.channels.find(c => c.key === platformKey)
  if (!ch) return
  if (!await confirm({ title: '清除渠道凭据', message: `确认清除 ${ch.label} 的凭据？`, confirmText: '清除', danger: true })) return
  try {
    await api.default.clearGatewayChannel(platformKey)
    if (channelsData.value) {
      const idx = channelsData.value.channels.findIndex(c => c.key === platformKey)
      if (idx >= 0) {
        channelsData.value.channels[idx].configured = false
        channelsData.value.channels[idx].enabled = false
        for (const f of channelsData.value.channels[idx].fields) {
          f.value = ''
          f.has_value = false
        }
      }
      for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
        const i2 = items.findIndex(c => c.key === platformKey)
        if (i2 >= 0) {
          items[i2].configured = false
          items[i2].enabled = false
          for (const f of items[i2].fields) {
            f.value = ''
            f.has_value = false
          }
        }
      }
    }
    toast.success('已清除凭据')
    await checkGatewayStatus()
  } catch (e) {
    toast.error('清除失败: ' + (e.message || e))
  }
}

async function toggleChannel(platformKey) {
  try {
    const result = await api.default.toggleGatewayChannel(platformKey)
    if (result.ok) {
      if (channelsData.value) {
        const ch = channelsData.value.channels.find(c => c.key === platformKey)
        if (ch) ch.enabled = result.enabled
        for (const [cat, items] of Object.entries(channelsData.value.grouped)) {
          const i2 = items.findIndex(c => c.key === platformKey)
          if (i2 >= 0) items[i2].enabled = result.enabled
        }
      }
      toast.success(result.enabled ? '已启用' : '已禁用')
      await checkGatewayStatus()
    }
  } catch (e) {
    toast.error('操作失败: ' + (e.message || e))
  }
}

onMounted(() => {
  loadChannels()
  checkGatewayStatus()
})
</script>

<template>
  <div class="space-y-4">
    <!-- 简介 -->
    <div class="flex items-center gap-2">
      <span class="text-lg">📱</span>
      <h3 class="font-medium text-gray-800 dark:text-gray-200">移动渠道接入</h3>
    </div>
    <p class="text-xs text-gray-500 dark:text-gray-400">将 Agent 接入即时通讯、邮件、智能家居等平台，实现跨渠道对话。填入平台凭据即可启用，支持 25+ 平台。</p>

    <!-- 状态概览 + 启动网关 -->
    <div v-if="channelsData" class="flex items-center gap-4 text-xs flex-wrap">
      <span class="text-gray-500 dark:text-gray-400">已配置 <span class="text-green-600 dark:text-green-400 font-medium">{{ channelsData.configured_count }}</span> / {{ channelsData.total }}</span>
      <span class="text-gray-300 dark:text-gray-600">|</span>
      <span :class="gatewayRunning ? 'text-green-600 dark:text-green-400' : 'text-gray-400'">● 网关{{ gatewayRunning ? '运行中' : '未运行' }}</span>
      <button v-if="!gatewayRunning" @click="startGateway" :disabled="gatewayStarting" class="px-3 py-1 text-xs rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">
        {{ gatewayStarting ? '启动中...' : '▶ 启动网关' }}
      </button>
      <button v-else @click="restartGateway" :disabled="gatewayStarting" class="px-3 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 whitespace-nowrap">
        {{ gatewayStarting ? '重启中...' : '↻ 重启网关' }}
      </button>
    </div>

    <div v-if="channelsLoading" class="text-center text-sm text-gray-400 py-4">
      <div class="animate-spin inline-block w-4 h-4 border-2 border-gray-300 border-t-green-500 rounded-full mr-1"></div> 加载中...
    </div>

    <!-- 按分类分组展示 -->
    <div v-else-if="channelCategories.length > 0" class="space-y-4">
      <div v-for="group in channelCategories" :key="group.cat" class="space-y-2">
        <h4 class="text-xs font-medium text-gray-500 dark:text-gray-400 flex items-center gap-1">
          <span v-if="group.cat === '国内'">🇨🇳</span>
          <span v-else-if="group.cat === '国际'">🌍</span>
          <span v-else>⚙️</span>
          {{ group.cat }}平台（{{ group.items.length }} 个）
        </h4>
        <div class="space-y-2">
          <div v-for="ch in group.items" :key="ch.key" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-700/30 overflow-hidden">
            <!-- 头部：点击展开 -->
            <div @click="channelExpanded[ch.key] = !channelExpanded[ch.key]" class="p-3 flex items-center justify-between cursor-pointer hover:bg-gray-100 dark:hover:bg-gray-700/50 transition">
              <div class="flex items-center gap-2">
                <span class="text-base">{{ ch.icon }}</span>
                <span class="text-sm font-medium text-gray-800 dark:text-gray-200">{{ ch.label }}</span>
                <span v-if="ch.configured" class="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400">已配置</span>
                <span v-if="ch.enabled" class="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400">已启用</span>
              </div>
              <span class="text-gray-400 text-xs">{{ channelExpanded[ch.key] ? '▼' : '▶' }}</span>
            </div>
            <!-- 展开内容 -->
            <div v-if="channelExpanded[ch.key]" class="px-3 pb-3 space-y-3 border-t border-gray-200 dark:border-gray-700">
              <div class="pt-2 space-y-2">
                <div v-for="f in ch.fields" :key="f.key" class="flex gap-2 items-center">
                  <label class="w-36 shrink-0 text-xs text-gray-500 dark:text-gray-400 truncate" :title="f.key">
                    {{ f.label }}
                    <span v-if="!f.required" class="text-gray-400">(可选)</span>
                  </label>
                  <input
                    :value="channelForms[ch.key]?.[f.key] || ''"
                    @input="channelForms[ch.key][f.key] = $event.target.value"
                    :type="f.secret ? 'password' : 'text'"
                    :placeholder="f.has_value ? '已配置（重新输入可覆盖）' : f.placeholder"
                    class="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-300 focus:border-green-400 focus:ring-1 focus:ring-green-400 outline-none font-mono"
                  />
                  <span v-if="f.has_value" class="text-green-500 text-xs whitespace-nowrap">●●●●</span>
                </div>
              </div>
              <div class="flex gap-2 justify-end pt-1">
                <button v-if="ch.configured" @click="toggleChannel(ch.key)" class="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 whitespace-nowrap">{{ ch.enabled ? '禁用' : '启用' }}</button>
                <button v-if="ch.configured" @click="clearChannel(ch.key)" class="px-3 py-1.5 text-sm rounded-lg text-gray-400 hover:text-red-500 border border-gray-300 dark:border-gray-600 whitespace-nowrap">清除</button>
                <button @click="saveChannel(ch.key)" :disabled="channelSaving" class="px-4 py-1.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 disabled:opacity-40 whitespace-nowrap">保存</button>
              </div>
              <!-- 接入教程 -->
              <div class="rounded-lg bg-blue-50/50 dark:bg-blue-900/10 border border-blue-100 dark:border-blue-900/30 p-3 space-y-1">
                <div class="flex items-center gap-1.5">
                  <span class="text-xs">📘</span>
                  <span class="text-xs font-medium text-blue-600 dark:text-blue-400">接入教程</span>
                  <a v-if="ch.apply_url" :href="ch.apply_url" target="_blank" rel="noopener" class="text-[11px] text-blue-500 hover:underline ml-auto">申请入口 ↗</a>
                </div>
                <p class="text-[11px] text-gray-500 dark:text-gray-400 whitespace-pre-wrap leading-relaxed pl-5">{{ ch.tutorial }}</p>
                <p v-if="ch.note" class="text-[10px] text-gray-400 dark:text-gray-500 pl-5 pt-1">💡 {{ ch.note }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="text-center text-sm text-gray-400 py-4">暂无可用渠道</div>
  </div>
</template>
