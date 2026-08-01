<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import api from '../services/api'
import { useMemoryFlagsStore } from '../stores/memoryFlags'
import { showToast } from '../utils/toast'

const flagsStore = useMemoryFlagsStore()

const memories = ref([])
const total = ref(0)
const offset = ref(0)
const limit = ref(50)
const loading = ref(false)

const searchQuery = ref('')
const filterTag = ref('')
const filterSource = ref('')
const selectedMemory = ref(null)
const detailLoading = ref(false)

const tagColors = {
  preference: 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
  reference: 'bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300',
  ephemeral: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
  decision: 'bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300',
  volatile: 'bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-300',
}

const sourceLabels = {
  skill: '技能',
  note: '笔记',
  l1_auto: '自动抽取',
  compression: '交割',
  task_plan: '任务',
  recall: '迁移',
  usage: '使用',
}

const layerLabels = {
  note: 'L1 笔记',
  procedural: 'L2 过程',
  episodic: 'L3 情节',
  reference: 'L4 参考',
}

async function fetchMemories() {
  loading.value = true
  try {
    const params = {}
    if (searchQuery.value) params.query = searchQuery.value
    if (filterTag.value) params.lifecycle_tag = filterTag.value
    if (filterSource.value) params.source = filterSource.value
    params.limit = limit.value
    params.offset = offset.value
    const data = await api.listMemories(params)
    if (data && data.ok) {
      memories.value = data.memories || []
      total.value = data.total || 0
    } else {
      memories.value = []
      total.value = 0
    }
  } catch (e) {
    memories.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

async function openDetail(memoryId) {
  detailLoading.value = true
  try {
    const data = await api.getMemoryDetail(memoryId)
    if (data && data.ok) {
      selectedMemory.value = data.memory
    } else {
      showToast(data?.error || '加载详情失败', 'error')
    }
  } catch (e) {
    showToast('加载详情失败', 'error')
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selectedMemory.value = null
}

async function restoreMemory(memoryId) {
  // 查找指向该记忆的 resolved demote flag
  const flag = flagsStore.resolvedFlags.find(
    f => String(f.memory_id) === String(memoryId) && f.resolution === 'demote'
  )
  if (flag) {
    await flagsStore.restoreFlag(flag.id)
    // 刷新记忆列表（lifecycle_tag 已恢复为 reference）
    fetchMemories()
  } else {
    showToast('该记忆没有可恢复的降级记录', 'error')
  }
}

const pages = computed(() => Math.ceil(total.value / limit.value) || 1)
const currentPage = computed(() => Math.floor(offset.value / limit.value) + 1)

watch([searchQuery, filterTag, filterSource], () => {
  offset.value = 0
  fetchMemories()
})

onMounted(() => {
  fetchMemories()
  // P2-13 fix: 不依赖 MemoryFlags.vue 先挂载，自己拉 resolved
  flagsStore.fetchResolved()
})
</script>

<template>
  <div class="p-4">
    <!-- 搜索与过滤 -->
    <div class="flex gap-3 mb-4 items-center flex-wrap">
      <input
        v-model="searchQuery"
        type="text"
        placeholder="搜索记忆…"
        class="flex-1 min-w-[200px] px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-sm text-gray-700 dark:text-gray-200 focus:outline-none focus:ring-2 focus:ring-green-400"
      />
      <select v-model="filterTag"
              class="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-sm text-gray-700 dark:text-gray-200">
        <option value="">全部标签</option>
        <option value="preference">偏好</option>
        <option value="reference">参考</option>
        <option value="ephemeral">临时</option>
        <option value="decision">决策</option>
        <option value="volatile">易失</option>
      </select>
      <select v-model="filterSource"
              class="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-sm text-gray-700 dark:text-gray-200">
        <option value="">全部来源</option>
        <option value="skill">技能</option>
        <option value="note">笔记</option>
        <option value="l1_auto">自动抽取</option>
        <option value="compression">交割</option>
      </select>
      <span class="text-xs text-gray-400">共 {{ total }} 条</span>
    </div>

    <!-- 记忆列表 -->
    <div v-if="loading" class="text-center py-8 text-gray-400">加载中…</div>
    <div v-else-if="memories.length === 0" class="text-center py-8 text-gray-400">无记忆</div>
    <div v-else class="space-y-2">
      <div v-for="m in memories" :key="m.id"
           @click="openDetail(m.id)"
           class="flex items-center gap-3 px-3 py-2.5 rounded-lg border border-gray-100 dark:border-gray-700 bg-white dark:bg-gray-800 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-750 transition-colors">
        <span class="flex-shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium"
              :class="tagColors[m.lifecycle_tag] || 'bg-gray-100 text-gray-500'">
          {{ m.lifecycle_tag }}
        </span>
        <span class="flex-shrink-0 text-[10px] text-gray-400">{{ sourceLabels[m.source] || m.source }}</span>
        <span class="flex-1 min-w-0 text-sm text-gray-700 dark:text-gray-300 truncate">{{ m.content_preview }}</span>
        <span class="flex-shrink-0 text-xs text-gray-400">{{ m.access_count }}次</span>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="pages > 1" class="flex justify-center gap-2 mt-4">
      <button @click="offset = Math.max(0, offset - limit); fetchMemories()"
              :disabled="offset === 0"
              class="px-3 py-1 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-50">
        上一页
      </button>
      <span class="text-sm text-gray-500">{{ currentPage }} / {{ pages }}</span>
      <button @click="offset += limit; fetchMemories()"
              :disabled="currentPage >= pages"
              class="px-3 py-1 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 disabled:opacity-50">
        下一页
      </button>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="selectedMemory"
         class="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
         @click.self="closeDetail">
      <div class="w-[520px] max-w-[94vw] bg-white dark:bg-gray-800 rounded-xl shadow-xl p-5 overflow-y-auto max-h-[80vh]">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-base font-semibold text-gray-700 dark:text-gray-200">记忆详情 #{{ selectedMemory.id }}</h3>
          <button @click="closeDetail" class="text-gray-400 hover:text-gray-600">✕</button>
        </div>

        <div class="space-y-3 text-sm">
          <div class="flex gap-2">
            <span class="px-2 py-0.5 rounded text-[11px] font-medium"
                  :class="tagColors[selectedMemory.lifecycle_tag] || ''">
              {{ selectedMemory.lifecycle_tag }}
            </span>
            <span class="px-2 py-0.5 rounded text-[11px] bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
              {{ layerLabels[selectedMemory.layer] || selectedMemory.layer }}
            </span>
            <span class="px-2 py-0.5 rounded text-[11px] bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
              {{ sourceLabels[selectedMemory.source] || selectedMemory.source }}
            </span>
          </div>

          <div>
            <span class="text-gray-400 text-xs">类型</span>
            <p class="text-gray-700 dark:text-gray-300">{{ selectedMemory.type }}</p>
          </div>

          <div>
            <span class="text-gray-400 text-xs">内容</span>
            <p class="text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words">{{ selectedMemory.content }}</p>
          </div>

          <div>
            <span class="text-gray-400 text-xs">指针</span>
            <p class="text-gray-500 dark:text-gray-400">{{ selectedMemory.pointer }}</p>
          </div>

          <div class="flex gap-4">
            <span class="text-gray-400 text-xs">访问 {{ selectedMemory.access_count }} 次</span>
            <span class="text-gray-400 text-xs">更新 {{ selectedMemory.updated_at }}</span>
          </div>

          <!-- 恢复按钮（仅 ephemeral 且有可恢复 flag 时显示） -->
          <button v-if="selectedMemory.lifecycle_tag === 'ephemeral'"
                  @click="restoreMemory(selectedMemory.id)"
                  class="mt-2 px-4 py-2 rounded-lg bg-green-50 dark:bg-green-900 text-green-600 dark:text-green-300 hover:bg-green-100 text-sm font-medium">
            ↩ 恢复权重（从 ephemeral → reference）
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
