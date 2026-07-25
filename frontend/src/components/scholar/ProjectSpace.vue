<script setup>
// 项目空间（P0c-2）：项目列表 / 新建 / 重命名 / 删除 / 选中。
// 接 blueprint.py 既有 REST：GET/POST/PATCH/DELETE /api/scholar/projects[/{pid}]。
// 删除有二次确认；选中项目写入 scholar.currentProjectId 供 SchemaForm 自动注入。
import { ref, reactive } from 'vue'
import { useScholarStore } from '../../stores/scholar'

const scholar = useScholarStore()

const showCreate = ref(false)
const busy = ref(false)
const error = ref('')
const confirmDeleteId = ref(null)
const editingId = ref(null)
const editingTitle = ref('')

const createForm = reactive({
  title: '',
  paper_type: '本科论文',
  target_words: 8000,
})

const PAPER_TYPES = ['本科论文', '硕士论文', '博士论文', '期刊论文', '课程论文']

async function onCreate() {
  error.value = ''
  if (!createForm.title.trim()) {
    error.value = '项目标题不能为空'
    return
  }
  busy.value = true
  try {
    await scholar.createProject({
      title: createForm.title.trim(),
      paper_type: createForm.paper_type,
      target_words: Number(createForm.target_words) || 8000,
    })
    createForm.title = ''
    showCreate.value = false
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

async function onDelete(pid) {
  error.value = ''
  busy.value = true
  try {
    await scholar.removeProject(pid)
    confirmDeleteId.value = null
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

function startRename(p) {
  editingId.value = p.id
  editingTitle.value = p.title
}

async function onRename(pid) {
  error.value = ''
  const title = editingTitle.value.trim()
  if (!title) {
    editingId.value = null
    return
  }
  busy.value = true
  try {
    await scholar.updateProject(pid, { title })
    editingId.value = null
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="p-4 space-y-4">
    <div class="flex items-center justify-between">
      <p class="text-sm text-gray-500">
        选中的项目会作为
        <code class="px-1 bg-gray-100 dark:bg-gray-700 rounded">project_id</code>
        自动注入到工具调用中。
      </p>
      <button
        @click="showCreate = !showCreate"
        class="px-3 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition shrink-0"
      >
        {{ showCreate ? '收起' : '＋ 新建项目' }}
      </button>
    </div>

    <p v-if="error" class="text-sm text-red-500">{{ error }}</p>

    <!-- 新建表单 -->
    <form
      v-if="showCreate"
      @submit.prevent="onCreate"
      class="rounded-lg border border-blue-300 dark:border-blue-700 bg-blue-50/40 dark:bg-blue-900/10 p-4 space-y-3"
    >
      <div class="flex flex-col gap-1">
        <label class="text-xs font-medium text-gray-600 dark:text-gray-300">项目标题 <span class="text-red-500">*</span></label>
        <input
          v-model="createForm.title"
          type="text"
          placeholder="例如：幼儿园户外自主游戏对大班幼儿社会性发展的影响研究"
          class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
        />
      </div>
      <div class="grid grid-cols-2 gap-3">
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-600 dark:text-gray-300">论文类型</label>
          <select
            v-model="createForm.paper_type"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
          >
            <option v-for="t in PAPER_TYPES" :key="t" :value="t">{{ t }}</option>
          </select>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs font-medium text-gray-600 dark:text-gray-300">目标字数</label>
          <input
            v-model="createForm.target_words"
            type="number"
            min="1000"
            step="1000"
            class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
          />
        </div>
      </div>
      <button
        type="submit"
        :disabled="busy"
        class="px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white text-sm font-medium transition"
      >
        创建
      </button>
    </form>

    <!-- 项目列表 -->
    <div v-if="scholar.projectsLoaded && !scholar.projects.length" class="text-sm text-gray-400 py-8 text-center">
      还没有论文项目，点「＋ 新建项目」开始。
    </div>

    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <div
        v-for="p in scholar.projects"
        :key="p.id"
        :class="[
          'rounded-lg border p-3 transition cursor-pointer',
          scholar.currentProjectId === p.id
            ? 'border-blue-500 bg-blue-50/60 dark:bg-blue-900/20 ring-1 ring-blue-400'
            : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-blue-300',
        ]"
        @click="scholar.currentProjectId = p.id"
      >
        <div class="flex items-start justify-between gap-2">
          <div class="min-w-0">
            <template v-if="editingId === p.id">
              <input
                v-model="editingTitle"
                @click.stop
                @keyup.enter="onRename(p.id)"
                @keyup.esc="editingId = null"
                class="w-full rounded border border-blue-400 bg-white dark:bg-gray-900 px-2 py-1 text-sm"
              />
            </template>
            <template v-else>
              <p class="text-sm font-medium truncate">
                <span v-if="scholar.currentProjectId === p.id" class="text-blue-500">✓</span>
                #{{ p.id }} {{ p.title }}
              </p>
            </template>
            <p class="mt-1 text-xs text-gray-400">
              {{ p.paper_type || '论文' }}<span v-if="p.target_words"> · 目标 {{ p.target_words }} 字</span>
            </p>
          </div>
          <div class="flex items-center gap-1 shrink-0" @click.stop>
            <template v-if="editingId === p.id">
              <button class="text-xs text-blue-600 hover:underline" @click="onRename(p.id)">保存</button>
              <button class="text-xs text-gray-400 hover:underline" @click="editingId = null">取消</button>
            </template>
            <template v-else-if="confirmDeleteId === p.id">
              <span class="text-xs text-red-500">确认删除？</span>
              <button class="text-xs text-red-600 font-medium hover:underline" @click="onDelete(p.id)">删除</button>
              <button class="text-xs text-gray-400 hover:underline" @click="confirmDeleteId = null">取消</button>
            </template>
            <template v-else>
              <button class="text-xs text-gray-400 hover:text-blue-500" title="重命名" @click="startRename(p)">✏️</button>
              <button class="text-xs text-gray-400 hover:text-red-500" title="删除" @click="confirmDeleteId = p.id">🗑️</button>
            </template>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
