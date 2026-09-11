<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
import { useBrickEvents } from '../utils/brick-events'
// 2.4.9 桌面端体验专项 B1：统一状态块（替代散落的"加载中/暂无/失败"写法）
import StateBlock from './StateBlock.vue'
const { confirm } = useConfirm()

const servers = ref([])
const loading = ref(false)
const showAddForm = ref(false)
const testing = ref(null)
const testResult = ref({})

// Form state
const newName = ref('')
const newCommand = ref('')
const newArgs = ref('')
const newEnv = ref('')

// ⑤ MCP 指挥中心：调用监控
//
// 后端返回的是 per-tool 粒度（"server/tool" 为 key），UI 按 server 聚合展示。
// 统计是**进程内内存**，后端重启即清零 —— 所以"有 server 但无调用"是正常
// 状态（尚未被调用），UI 必须按「暂无记录」而非「出错」来解释。
const callStats = ref(null)

async function loadServers() {
  loading.value = true
  try {
    const data = await api.mcpListServers()
    servers.value = Object.entries(data.servers || {}).map(([name, cfg]) => ({
      name,
      command: cfg.command || '',
      args: cfg.args || [],
      env: cfg.env || {},
      enabled: cfg.enabled !== false,
    }))
  } catch (e) {
    console.error('Failed to load MCP servers:', e)
  } finally {
    loading.value = false
  }
}

// fail-open：统计端点不可得（MCP 未启用 / 后端较旧）时静默降级，不阻塞主体
async function loadCallStats() {
  try {
    callStats.value = await api.mcpStats()
  } catch (e) {
    callStats.value = null
  }
}

function refreshAll() {
  loadServers()
  loadCallStats()
}

// per-tool → per-server 聚合（含派生字段 avg_ms / rate）
// 三态：calls / errors / interrupts —— interrupts 是 ⑤ P2 观察后加的维度
// （用户主动中断的次数，既非成功也非失败，UI 单独展示）。
const statsByServer = computed(() => {
  const m = {}
  for (const t of (callStats.value?.tools || [])) {
    const s = m[t.server] || (m[t.server] = { calls: 0, errors: 0, interrupts: 0, total_ms: 0, max_ms: 0, tools: 0 })
    s.calls += t.calls || 0
    s.errors += t.errors || 0
    s.interrupts += t.interrupts || 0
    s.total_ms += t.total_ms || 0
    s.max_ms = Math.max(s.max_ms, t.max_ms || 0)
    s.tools += 1
  }
  for (const s of Object.values(m)) {
    s.avg_ms = s.calls ? Math.round(s.total_ms / s.calls) : 0
    // 三态：成功率分母排除 interrupts（与后端 _record_mcp_call 语义一致）
    s.rate = s.calls ? Math.round((s.calls - s.errors - s.interrupts) / s.calls * 100) : null
  }
  return m
})

const statsSummary = computed(() => callStats.value?.summary || null)
const hasAnyCall = computed(() => (statsSummary.value?.calls || 0) > 0)

function fmtMs(ms) {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

async function addServer() {
  if (!newName.value.trim() || !newCommand.value.trim()) return
  try {
    const args = newArgs.value.trim() ? newArgs.value.split(/\s+/) : []
    let env = {}
    if (newEnv.value.trim()) {
      for (const line of newEnv.value.split('\n')) {
        const idx = line.indexOf('=')
        if (idx > 0) env[line.substring(0, idx).trim()] = line.substring(idx + 1).trim()
      }
    }
    await api.mcpAddServer(newName.value.trim(), newCommand.value.trim(), args, env)
    await loadServers()
    showAddForm.value = false
    newName.value = ''
    newCommand.value = ''
    newArgs.value = ''
    newEnv.value = ''
  } catch (e) {
    toast.error('添加失败: ' + e.message)
  }
}

async function removeServer(name) {
  if (!await confirm({ title: '删除 MCP Server', message: `确定删除 MCP server "${name}" 吗？`, confirmText: '删除', danger: true })) return
  try {
    await api.mcpRemoveServer(name)
    servers.value = servers.value.filter(s => s.name !== name)
  } catch (e) {
    toast.error('删除失败: ' + e.message)
  }
}

async function testServer(name) {
  testing.value = name
  testResult.value = { ...testResult.value, [name]: { loading: true } }
  try {
    const data = await api.mcpTestServer(name)
    testResult.value[name] = data
  } catch (e) {
    testResult.value[name] = { ok: false, error: e.message }
  } finally {
    testing.value = null
  }
}

async function toggleServer(srv) {
  try {
    const target = !srv.enabled
    await api.mcpSetEnabled(srv.name, target)
    srv.enabled = target
    toast.success(`${srv.name} 已${target ? '启用' : '禁用'}`)
  } catch (e) {
    toast.error('切换失败: ' + e.message)
  }
}

onMounted(() => {
  refreshAll()
})

// 动态刷新：MCP 工具变更后自动重新加载
const { onEvent } = useBrickEvents()
onEvent('mcp.changed', () => refreshAll())
onEvent('tool.registered', () => refreshAll())
onEvent('tool.deregistered', () => refreshAll())
</script>

<template>
  <div class="space-y-3">
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div class="flex items-center gap-2">
        <span class="text-lg">🔌</span>
        <h3 class="text-sm font-medium text-gray-700 dark:text-gray-300">MCP 服务</h3>
        <span class="text-xs text-gray-400">({{ servers.length }} 个)</span>
      </div>
      <div class="flex items-center gap-1">
        <button @click="refreshAll()" title="刷新列表与调用统计"
                class="text-xs px-2 py-1 rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">
          ⟳
        </button>
        <button @click="showAddForm = !showAddForm"
                class="text-xs px-2 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600">
          {{ showAddForm ? '取消' : '+ 添加' }}
        </button>
      </div>
    </div>

    <!-- ⑤ 调用监控总览 -->
    <div v-if="hasAnyCall"
         class="flex items-center gap-3 text-[10px] px-2 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-800/50">
      <span class="text-gray-500 dark:text-gray-400">
        调用 <b class="text-gray-700 dark:text-gray-200">{{ statsSummary.calls }}</b>
      </span>
      <span class="text-gray-500 dark:text-gray-400">
        失败 <b :class="statsSummary.errors ? 'text-red-500' : 'text-gray-700 dark:text-gray-200'">{{ statsSummary.errors }}</b>
      </span>
      <!-- 三态：用户主动中断独立展示（既非成功也非失败） -->
      <span v-if="statsSummary.interrupts" class="text-gray-500 dark:text-gray-400">
        中断 <b class="text-amber-600 dark:text-amber-400">{{ statsSummary.interrupts }}</b>
      </span>
      <span class="text-gray-500 dark:text-gray-400">
        成功率 <b :class="(statsSummary.errors || statsSummary.interrupts) ? 'text-amber-600 dark:text-amber-400' : 'text-green-600 dark:text-green-400'">{{ Math.round(statsSummary.success_rate * 100) }}%</b>
      </span>
      <span class="ml-auto text-gray-400">{{ callStats.count }} 个工具有记录</span>
    </div>
    <!-- 有 server 但零调用属正常（尚未被调用），不是错误态 -->
    <StateBlock v-else-if="callStats && callStats.count === 0 && servers.length"
                state="empty" compact icon="📊" text="暂无调用记录"
                detail="统计为进程内内存，后端重启后清零" />

    <!-- Add form -->
    <div v-if="showAddForm" class="bg-gray-50 dark:bg-gray-800 rounded-lg p-3 space-y-2">
      <input v-model="newName" type="text" placeholder="名称 (如: filesystem)"
             class="w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-green-400" />
      <input v-model="newCommand" type="text" placeholder="命令 (如: npx)"
             class="w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-green-400" />
      <input v-model="newArgs" type="text" placeholder="参数 (空格分隔, 如: -y @modelcontextprotocol/server-filesystem /tmp)"
             class="w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-green-400" />
      <textarea v-model="newEnv" placeholder="环境变量 (每行 KEY=value)" rows="2"
             class="w-full px-3 py-1.5 text-xs rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 focus:outline-none focus:border-green-400"></textarea>
      <button @click="addServer"
              class="w-full py-1.5 text-xs rounded bg-green-500 text-white hover:bg-green-600">
        保存
      </button>
    </div>

    <!-- Server list -->
    <div class="space-y-1.5">
      <StateBlock v-if="loading && servers.length === 0" state="loading" text="加载 MCP 服务…" />
      <StateBlock v-else-if="!loading && servers.length === 0" state="empty" icon="🔌"
                  text="尚未配置 MCP 服务" detail="点击右上角「+」添加，接入外部工具能力">
        <button class="mt-1.5 text-xs px-2.5 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600"
                @click="showAddForm = true">+ 添加服务</button>
      </StateBlock>
      <div v-for="srv in servers" :key="srv.name"
           class="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-lg px-3 py-2">
        <div class="flex items-center gap-2">
          <!-- 3.5 启用/禁用开关 -->
          <button @click="toggleServer(srv)" :title="srv.enabled ? '点击禁用' : '点击启用'"
                  class="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition"
                  :class="srv.enabled ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'">
            <span class="inline-block h-4 w-4 transform rounded-full bg-white transition"
                  :class="srv.enabled ? 'translate-x-4' : 'translate-x-0.5'"></span>
          </button>
          <span class="text-sm">🔌</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ srv.name }}<span v-if="!srv.enabled" class="ml-1 text-[9px] text-gray-400">(已禁用)</span></div>
            <div class="text-[10px] text-gray-400 truncate">
              {{ srv.command }} {{ srv.args?.join(' ') || '' }}
            </div>
          </div>
          <button @click="testServer(srv.name)" :disabled="testing === srv.name"
                  class="text-xs text-blue-500 hover:text-blue-600 disabled:opacity-50">
            {{ testing === srv.name ? '⏳' : '🔌测试' }}
          </button>
          <button @click="removeServer(srv.name)"
                  class="text-xs text-gray-300 hover:text-red-500">🗑</button>
        </div>

        <!-- ⑤ per-server 调用统计 -->
        <div v-if="statsByServer[srv.name]"
             class="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[10px] text-gray-400">
          <span>调用 <b class="text-gray-600 dark:text-gray-300">{{ statsByServer[srv.name].calls }}</b></span>
          <span v-if="statsByServer[srv.name].errors"
                class="text-red-400">失败 {{ statsByServer[srv.name].errors }}</span>
          <!-- 三态：用户主动中断独立展示 -->
          <span v-if="statsByServer[srv.name].interrupts"
                class="text-amber-500">中断 {{ statsByServer[srv.name].interrupts }}</span>
          <span v-if="statsByServer[srv.name].rate !== null"
                :class="(statsByServer[srv.name].errors || statsByServer[srv.name].interrupts) ? 'text-amber-500' : 'text-green-500'">
            成功率 {{ statsByServer[srv.name].rate }}%
          </span>
          <span>均 {{ fmtMs(statsByServer[srv.name].avg_ms) }}</span>
          <span>峰 {{ fmtMs(statsByServer[srv.name].max_ms) }}</span>
          <span class="ml-auto">{{ statsByServer[srv.name].tools }} 个工具</span>
        </div>

        <!-- Test result -->
        <div v-if="testResult[srv.name]" class="mt-1.5 pt-1.5 border-t border-gray-100 dark:border-gray-700">
          <div v-if="testResult[srv.name].loading" class="text-[10px] text-gray-400 animate-pulse">测试中...</div>
          <div v-else>
            <div class="text-[10px]" :class="testResult[srv.name].ok ? 'text-green-500' : 'text-red-500'">
              {{ testResult[srv.name].ok ? '✅ 连接成功' : '❌ ' + (testResult[srv.name].error || testResult[srv.name].message || '连接失败') }}
            </div>
            <div v-if="testResult[srv.name].tools && testResult[srv.name].tools.length > 0"
                 class="mt-1 flex flex-wrap gap-1">
              <span v-for="tool in testResult[srv.name].tools" :key="tool"
                    class="text-[9px] px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 rounded">
                {{ tool }}
              </span>
            </div>
          </div>
        </div>
      </div>
      <!-- Empty -->
      <div v-if="!loading && servers.length === 0" class="text-center py-6 text-xs text-gray-400">
        <div class="text-2xl mb-1">🔌</div>
        <div>未配置 MCP 服务</div>
        <div class="text-[10px] mt-1">点击「+ 添加」配置 MCP server</div>
      </div>
      <div v-if="loading" class="text-center py-3 text-xs text-gray-400 animate-pulse">加载中...</div>
    </div>
  </div>
</template>
