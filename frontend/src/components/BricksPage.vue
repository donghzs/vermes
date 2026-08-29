<template>
  <div class="h-full flex flex-col bg-gray-900 text-white">
    <!-- 顶部 -->
    <div class="px-6 py-4 border-b border-gray-700 flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3">
        <span class="text-2xl">🧱</span>
        <h1 class="text-xl font-bold">积木市场</h1>
        <span class="text-sm text-gray-400">
          {{ loading ? '加载中…' : `${filtered.length} / ${total} 个` }}
        </span>
      </div>
      <button
        @click="load(true)"
        :disabled="loading"
        class="px-3 py-1.5 text-sm rounded-lg bg-gray-700 hover:bg-gray-600 disabled:opacity-50 transition"
      >
        {{ loading ? '刷新中…' : '🔄 刷新' }}
      </button>
    </div>

    <!-- 过滤器：类型 / 关键词 / 仅已装 -->
    <div class="px-6 py-3 border-b border-gray-800 flex flex-wrap items-center gap-2">
      <button
        v-for="t in TYPE_FILTERS"
        :key="t.key"
        @click="typeFilter = t.key"
        class="px-3 py-1 text-xs rounded-full transition"
        :class="typeFilter === t.key
          ? 'bg-blue-600 text-white'
          : 'bg-gray-700 text-gray-300 hover:bg-gray-600'"
      >{{ t.label }}</button>

      <span class="w-px h-5 bg-gray-700 mx-1"></span>

      <input
        v-model="query"
        placeholder="搜索名称 / id / 描述…"
        class="px-3 py-1 text-sm rounded-lg bg-gray-800 border border-gray-700 text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-64"
      />
      <label class="flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer select-none">
        <input type="checkbox" v-model="installedOnly" class="accent-blue-500" /> 仅已装
      </label>
    </div>

    <!-- 内容 -->
    <div class="flex-1 overflow-y-auto p-6">
      <div v-if="loading && bricks.length === 0" class="text-center text-gray-400 py-12">
        加载中…
      </div>

      <div v-else-if="error" class="text-center text-gray-400 py-12">
        <p class="text-lg">⚠️ {{ error }}</p>
        <p class="text-sm mt-2">后端 bricks 端点未就绪时，需重新打包 DMG 才生效（工程约束 #1）</p>
      </div>

      <div v-else-if="filtered.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">🧱 无匹配的积木</p>
        <p class="text-sm mt-2">换个筛选条件或关键词试试</p>
      </div>

      <div v-else class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <BrickCard
          v-for="b in filtered"
          :key="b.id"
          :brick="b"
          :busy="busyId === b.id"
          @install="onInstall"
          @uninstall="onUninstall"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api'
import { toast } from '../utils/toast'
import BrickCard from './BrickCard.vue'

// P1-3：四态合一入口。旧 /module-store、/skill-market 已重定向到本页。
const TYPE_FILTERS = [
  { key: '', label: '全部' },
  { key: 'skill', label: '🧩 技能' },
  { key: 'tool', label: '🛠 工具' },
  { key: 'module', label: '📦 模块' },
  { key: 'software', label: '🖥 软件' },
]

const bricks = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')
const query = ref('')
const typeFilter = ref('')
const installedOnly = ref(false)
const busyId = ref('')

async function load(refresh = false) {
  loading.value = true
  error.value = ''
  try {
    // api.get 经 services/api.js 的 request() 自动带 session token（桌面/在线两种模式）
    const data = await api.get('/v1/bricks' + (refresh ? '?refresh=true' : ''))
    bricks.value = data.bricks || []
    total.value = data.total ?? bricks.value.length
  } catch (e) {
    error.value = '积木列表加载失败：' + (e?.message || e)
  } finally {
    loading.value = false
  }
}

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  return bricks.value.filter((b) => {
    if (typeFilter.value && b.type !== typeFilter.value) return false
    if (installedOnly.value && b.install_state !== 'installed') return false
    if (q && !`${b.id} ${b.name} ${b.description || ''}`.toLowerCase().includes(q)) return false
    return true
  })
})

// P1-4：装完即用的三态反馈 —— 可用 / 本体未就绪（给指引）/ 未探测到工具。
// 不谎报成功：probe 说没注册工具，就如实告诉用户，并给下一步动作。
function reportProbe(probe, brick) {
  if (!probe) return
  if (probe.available) {
    const n = probe.tools_registered
    toast.success(n > 0 ? `✅ 对话中已可用（${n} 个工具已注册）` : '✅ 对话中已可用')
    return
  }
  if (probe.backend_hint) {
    // software 两步安装的第二步（本体）不由 Vermes 代管，只能引导；停留久一点让用户读完
    toast.warning(probe.backend_hint, 8000)
    return
  }
  toast.warning(
    `已安装，但未探测到已注册工具${brick.type === 'module' ? '（热重载可能失败）' : ''}` +
      '，可点「🔄 刷新」重试或重启应用',
    6000,
  )
}

async function onInstall(brick) {
  busyId.value = brick.id
  try {
    const r = await api.post(`/v1/bricks/${encodeURIComponent(brick.id)}/install`, {})
    if (r?.ok) {
      toast.success(r.message || `已安装 ${brick.name}`)
      reportProbe(r.probe, brick)
    } else {
      toast.error(r?.message || '安装失败')
    }
    await load(true)
  } catch (e) {
    toast.error('安装请求失败：' + (e?.message || e))
  } finally {
    busyId.value = ''
  }
}

async function onUninstall(brick) {
  busyId.value = brick.id
  try {
    const r = await api.post(`/v1/bricks/${encodeURIComponent(brick.id)}/uninstall`, {})
    if (r?.ok) toast.success(r.message || `已卸载 ${brick.name}`)
    else toast.error(r?.message || '卸载失败')
    await load(true)
  } catch (e) {
    toast.error('卸载请求失败：' + (e?.message || e))
  } finally {
    busyId.value = ''
  }
}

onMounted(() => load())
</script>
