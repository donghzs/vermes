<!--
  ScholarForge ProjectList — 项目列表与创建
  独立组件，从 Writer.vue 拆分
-->
<template>
  <div class="flex-1 overflow-y-auto bg-gradient-to-br from-gray-50 to-green-50 dark:from-gray-900 dark:to-gray-800">
    <div class="max-w-5xl mx-auto px-8 py-8">
      <!-- 顶部欢迎 -->
      <div class="flex items-center justify-between mb-6">
        <div>
          <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-1">📚 ScholarForge 论文写作</h1>
          <p class="text-sm text-gray-500 dark:text-gray-400">每个项目都是独立的工作空间 · 独立上下文与进度</p>
        </div>
        <button @click="showCreateForm = true"
          class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium flex items-center gap-2">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
          新建项目
        </button>
      </div>

      <!-- 创建表单 -->
      <div v-if="showCreateForm" class="bg-white dark:bg-gray-800 rounded-xl shadow-sm p-6 mb-6">
        <h2 class="text-base font-semibold text-gray-800 dark:text-gray-100 mb-4">创建新论文项目</h2>
        <label class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 block">论文题目</label>
        <input v-model="formTitle" placeholder="例：基于深度学习的图像识别算法研究"
          class="w-full px-4 py-2.5 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm mb-4 focus:outline-none focus:border-green-500 focus:ring-2 focus:ring-green-500/20 dark:text-gray-100"/>
        <label class="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2 block">论文类型</label>
        <div class="grid grid-cols-4 gap-2 mb-5">
          <button v-for="t in paperTypes" :key="t.id" @click="formType = t.name; formWords = t.defaultWords"
            :class="['p-3 border rounded-lg text-left transition-all', formType === t.name ? 'border-green-500 bg-green-50 dark:bg-green-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-gray-300']">
            <div class="text-xl mb-1">{{ t.icon }}</div>
            <div class="text-xs font-medium text-gray-800 dark:text-gray-200">{{ t.name }}</div>
            <div class="text-[10px] text-gray-500 mt-0.5">{{ t.desc }}</div>
          </button>
        </div>
        <div class="flex items-center gap-2">
          <button @click="doCreate" :disabled="!formTitle.trim()"
            class="px-5 py-2.5 bg-gradient-to-r from-green-600 to-emerald-500 hover:from-green-500 hover:to-emerald-400 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-lg text-sm font-semibold">
            🚀 创建并开始
          </button>
          <button @click="showCreateForm = false" class="px-4 py-2.5 text-gray-600 hover:text-gray-800 text-sm">取消</button>
        </div>
        <div class="mt-4 pt-4 border-t border-gray-100 dark:border-gray-700">
          <span class="text-xs text-gray-400">没思路？试试：</span>
          <button v-for="(ex, i) in exampleTitles" :key="i" @click="formTitle = ex"
            class="text-xs text-blue-600 hover:text-blue-700 mx-1 hover:underline">{{ ex }}</button>
        </div>
      </div>

      <!-- 项目列表 -->
      <div>
        <div class="flex items-center justify-between mb-3">
          <h2 class="text-sm font-semibold text-gray-700 dark:text-gray-200">所有论文项目 · {{ projects.length }} 个</h2>
          <span v-if="!showCreateForm && projects.length > 0" @click="showCreateForm = true"
            class="text-xs text-green-600 hover:text-green-700 cursor-pointer">+ 新建项目</span>
        </div>
        <div v-if="loading" class="bg-white dark:bg-gray-800 rounded-xl p-12 text-center">
          <div class="w-8 h-8 border-2 border-green-500/30 border-t-green-500 rounded-full animate-spin mx-auto"></div>
          <p class="text-sm text-gray-400 mt-3">加载中...</p>
        </div>
        <div v-else-if="!projects.length" class="bg-white dark:bg-gray-800 rounded-xl p-12 text-center">
          <div class="text-5xl mb-3">📝</div>
          <p class="text-sm text-gray-500 dark:text-gray-400 mb-4">还没有任何论文项目</p>
          <button @click="showCreateForm = true"
            class="px-5 py-2.5 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium">
            创建第一个项目
          </button>
        </div>
        <div v-else class="grid grid-cols-2 gap-3">
          <div v-for="p in projects" :key="p.id"
            @click="selectProject(p)"
            class="bg-white dark:bg-gray-800 rounded-xl p-4 hover:shadow-lg transition-all cursor-pointer border border-transparent hover:border-green-500 group">
            <div class="flex items-start justify-between mb-2">
              <span class="text-2xl">{{ typeIcon(p.paper_type) }}</span>
              <button @click.stop="doDelete(p)"
                class="text-gray-300 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity" title="删除项目">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V5a2 2 0 012-2h2a2 2 0 012 2v2"/></svg>
              </button>
            </div>
            <h3 class="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-1 line-clamp-2 leading-snug">{{ p.title }}</h3>
            <div class="flex items-center gap-2 text-[10px] text-gray-500 mb-3">
              <span class="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">{{ p.paper_type }}</span>
              <span>{{ formatRelativeTime(p.updated_at) }}</span>
            </div>
            <!-- 进度条 -->
            <div class="mb-2">
              <div class="flex justify-between text-[10px] text-gray-500 mb-1">
                <span>{{ p.total_words || 0 }} / {{ p.target_words }} 字</span>
                <span>{{ p.target_words ? Math.round(((p.total_words || 0) / p.target_words) * 100) : 0 }}%</span>
              </div>
              <div class="w-full h-1.5 bg-gray-100 dark:bg-gray-700 rounded-full overflow-hidden">
                <div class="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full"
                  :style="{ width: Math.min(((p.total_words || 0) / (p.target_words || 1)) * 100, 100) + '%' }"></div>
              </div>
            </div>
            <div class="flex items-center gap-3 text-[10px] text-gray-400">
              <span>📚 {{ p.literature_count || 0 }} 文献</span>
              <span>💬 {{ p.message_count || 0 }} 对话</span>
              <span>📑 {{ p.section_count || 0 }} 章节</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const emit = defineEmits(['selectProject'])

const projects = ref([])
const loading = ref(true)
const showCreateForm = ref(false)
const formTitle = ref('')
const formType = ref('本科论文')
const formWords = ref(10000)

const paperTypes = [
  { id: 'undergrad', name: '本科论文', icon: '🎓', desc: '8000-15000字', defaultWords: 10000 },
  { id: 'course', name: '课程论文', icon: '📋', desc: '3000-6000字', defaultWords: 4000 },
  { id: 'master', name: '硕士论文', icon: '📚', desc: '3-5万字', defaultWords: 30000 },
  { id: 'phd', name: '博士论文', icon: '🔬', desc: '8-15万字', defaultWords: 80000 },
  { id: 'journal', name: '期刊论文', icon: '📰', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'conference', name: '会议论文', icon: '🎤', desc: '4-8页', defaultWords: 5000 },
  { id: 'review', name: '综述论文', icon: '📖', desc: '1-2万字', defaultWords: 15000 },
  { id: 'proposal', name: '开题报告', icon: '📝', desc: '5000-8000字', defaultWords: 6000 },
  { id: 'survey', name: '调研报告', icon: '🔍', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'experiment', name: '实验报告', icon: '🧪', desc: '3000-6000字', defaultWords: 5000 },
  { id: 'case_study', name: '案例分析', icon: '💼', desc: '5000-10000字', defaultWords: 8000 },
  { id: 'graduation_project', name: '毕业设计', icon: '🎯', desc: '10000-20000字', defaultWords: 15000 },
]

const exampleTitles = [
  '基于深度学习的图像识别研究',
  '大语言模型在教育中的应用',
  '区块链与隐私保护机制综述',
]

const loadProjects = async () => {
  try {
    const r = await fetch('/api/scholar/projects')
    if (r.ok) {
      const d = await r.json()
      projects.value = d.projects || []
    }
  } catch (e) { console.error('load projects', e) }
  finally { loading.value = false }
}

const doCreate = async () => {
  if (!formTitle.value?.trim()) return
  try {
    const r = await fetch('/api/scholar/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: formTitle.value.trim(),
        paper_type: formType.value,
        target_words: formWords.value,
      }),
    })
    if (r.ok) {
      const proj = await r.json()
      showCreateForm.value = false
      formTitle.value = ''
      await loadProjects()
      emit('selectProject', proj)
    }
  } catch (e) { console.error('create project', e) }
}

const selectProject = (p) => {
  emit('selectProject', p)
}

const doDelete = async (p) => {
  if (!confirm(`确定删除「${p.title}」？该操作不可恢复。`)) return
  try {
    await fetch(`/api/scholar/projects/${p.id}`, { method: 'DELETE' })
    await loadProjects()
  } catch (e) { console.error('delete', e) }
}

const typeIcon = (type) => {
  const m = { '本科论文': '🎓', '硕士论文': '📚', '博士论文': '🔬', '期刊论文': '📰', '会议论文': '🎤', '综述论文': '📖', '开题报告': '📝' }
  return m[type] || '📄'
}

const formatRelativeTime = (ts) => {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return Math.floor(diff / 60) + '分钟前'
  if (diff < 86400) return Math.floor(diff / 3600) + '小时前'
  if (diff < 604800) return Math.floor(diff / 86400) + '天前'
  return new Date(ts * 1000).toLocaleDateString()
}

onMounted(() => { loadProjects() })
</script>

<style scoped>
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
