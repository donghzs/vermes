<script setup>
import { ref, onMounted } from 'vue'
import api from '../services/api.js'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
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

async function loadServers() {
  loading.value = true
  try {
    const data = await api.mcpListServers()
    servers.value = Object.entries(data.servers || {}).map(([name, cfg]) => ({
      name,
      command: cfg.command || '',
      args: cfg.args || [],
      env: cfg.env || {},
    }))
  } catch (e) {
    console.error('Failed to load MCP servers:', e)
  } finally {
    loading.value = false
  }
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

onMounted(() => {
  loadServers()
})
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
      <button @click="showAddForm = !showAddForm"
              class="text-xs px-2 py-1 rounded-lg bg-green-500 text-white hover:bg-green-600">
        {{ showAddForm ? '取消' : '+ 添加' }}
      </button>
    </div>

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
      <div v-for="srv in servers" :key="srv.name"
           class="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-lg px-3 py-2">
        <div class="flex items-center gap-2">
          <span class="text-sm">🔌</span>
          <div class="flex-1 min-w-0">
            <div class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ srv.name }}</div>
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
