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

    <!-- P4-1 待审核 tab -->
    <div class="px-6 py-3 border-b border-gray-800 flex items-center gap-2">
      <button
        @click="switchView('market')"
        class="px-3 py-1 text-sm rounded-full transition"
        :class="view === 'market'
          ? 'bg-blue-600 text-white'
          : 'bg-gray-700 text-gray-300 hover:bg-gray-600'"
      >🧱 积木市场</button>
      <button
        @click="switchView('pending')"
        class="px-3 py-1 text-sm rounded-full transition relative"
        :class="view === 'pending'
          ? 'bg-amber-600 text-white'
          : 'bg-gray-700 text-gray-300 hover:bg-gray-600'"
      >⏳ 待审核
        <span v-if="pendingCount > 0" class="ml-1 px-1.5 py-0.5 text-xs rounded-full bg-red-500 text-white">{{ pendingCount }}</span>
      </button>
    </div>

    <!-- 过滤器：类型 / 关键词 / 仅已装（仅市场视图）-->
    <div v-if="view === 'market'" class="px-6 py-3 border-b border-gray-800 flex flex-wrap items-center gap-2">
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

      <div v-else-if="view === 'market' && filtered.length === 0" class="text-center text-gray-400 py-12">
        <p class="text-lg">🧱 无匹配的积木</p>
        <p class="text-sm mt-2">换个筛选条件或关键词试试</p>
      </div>

      <div v-else-if="view === 'market'" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <BrickCard
          v-for="b in filtered"
          :key="b.id"
          :brick="b"
          :busy="busyId === b.id"
          @install="onInstall"
          @uninstall="onUninstall"
        />
      </div>

      <!-- P4-1 待审核列表 -->
      <div v-else-if="view === 'pending'" class="space-y-3">
        <div v-if="pendingLoading" class="text-center text-gray-400 py-12">加载中…</div>
        <div v-else-if="pendingReviews.length === 0" class="text-center text-gray-400 py-12">
          <p class="text-lg">✅ 暂无待审核的 brick</p>
          <p class="text-sm mt-2">开发者提交的 brick 会出现在这里，由你审核上架</p>
        </div>
        <div
          v-for="rev in pendingReviews"
          :key="rev.brick_id"
          class="bg-gray-800 border border-gray-700 rounded-xl p-4"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <h3 class="text-base font-semibold truncate">{{ rev.brick_id }}</h3>
              <p class="text-xs text-gray-400 mt-0.5">
                提交者：{{ rev.submitted_by || '未知' }} ·
                {{ rev.submitted_at ? new Date(rev.submitted_at * 1000).toLocaleString() : '' }}
              </p>
              <div v-if="rev.metadata && Object.keys(rev.metadata).length" class="mt-2 text-xs text-gray-300 space-y-0.5">
                <p v-if="rev.metadata.version">版本：{{ rev.metadata.version }}</p>
                <p v-if="rev.metadata.vermes_min">要求 Vermes ≥ {{ rev.metadata.vermes_min }}</p>
                <p v-if="rev.metadata.dependencies && rev.metadata.dependencies.length">依赖：{{ rev.metadata.dependencies.join(', ') }}</p>
                <p v-if="rev.metadata.description" class="line-clamp-2">描述：{{ rev.metadata.description }}</p>
              </div>
            </div>
            <div class="flex gap-2 shrink-0">
              <button
                @click="onApprove(rev)"
                :disabled="busyReview === rev.brick_id"
                class="px-3 py-1.5 text-sm rounded-lg bg-green-600 hover:bg-green-500 disabled:opacity-50 transition"
              >✅ 通过</button>
              <button
                @click="onReject(rev)"
                :disabled="busyReview === rev.brick_id"
                class="px-3 py-1.5 text-sm rounded-lg bg-red-600 hover:bg-red-500 disabled:opacity-50 transition"
              >⛔ 拒绝</button>
            </div>
          </div>
        </div>
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

// P4-1 待审核视图
const view = ref('market')
const pendingReviews = ref([])
const pendingLoading = ref(false)
const busyReview = ref('')
const pendingCount = computed(() => pendingReviews.value.length)

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

async function loadPending() {
  pendingLoading.value = true
  try {
    const data = await api.get('/v1/bricks/reviews?status=submitted')
    pendingReviews.value = data.reviews || []
  } catch (e) {
    toast.error('待审核列表加载失败：' + (e?.message || e))
    pendingReviews.value = []
  } finally {
    pendingLoading.value = false
  }
}

function switchView(v) {
  view.value = v
  if (v === 'pending') loadPending()
}

async function onApprove(rev) {
  busyReview.value = rev.brick_id
  try {
    const r = await api.post(`/v1/bricks/${encodeURIComponent(rev.brick_id)}/review`, { decision: 'approve', reviewer: 'admin' })
    if (r?.ok) toast.success(`✅ 已通过 ${rev.brick_id}`)
    else toast.error(r?.message || '审核失败')
    await loadPending()
  } catch (e) {
    toast.error('审核请求失败：' + (e?.message || e))
  } finally {
    busyReview.value = ''
  }
}

async function onReject(rev) {
  busyReview.value = rev.brick_id
  try {
    const r = await api.post(`/v1/bricks/${encodeURIComponent(rev.brick_id)}/review`, { decision: 'reject', note: '审核未通过' })
    if (r?.ok) toast.success(`⛔ 已拒绝 ${rev.brick_id}`)
    else toast.error(r?.message || '审核失败')
    await loadPending()
  } catch (e) {
    toast.error('审核请求失败：' + (e?.message || e))
  } finally {
    busyReview.value = ''
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
