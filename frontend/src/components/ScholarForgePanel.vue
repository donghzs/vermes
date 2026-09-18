<script setup>
// ScholarForge 论文写作面板（A 入口）：容器 + 顶部项目选择器 + Tab。
//
// Tab 由 useScholarStore.activeTab 驱动：FlowGuide「打开工具」/ Uploader「填入工具箱」
// 会经 store 切回「工具箱」并预选工具，实现跨子组件联动。
//
// 与对话式(C) 共享同一后端引擎：工具箱经 invokeTool → POST /api/tools/invoke →
// handler 内部含 run_quality_gate，故质量护栏对两种入口行为一致（P0b 已封堵缺口）。
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useScholarStore } from '../stores/scholar'
import ToolBox from './scholar/ToolBox.vue'
import ProjectSpace from './scholar/ProjectSpace.vue'
import QualityView from './scholar/QualityView.vue'
import FlowGuide from './scholar/FlowGuide.vue'
import Uploader from './scholar/Uploader.vue'

const router = useRouter()
const scholar = useScholarStore()

function goChat() { router.push('/') }

const TABS = [
  { key: 'tools', label: '🧰 工具箱' },
  { key: 'projects', label: '🗂️ 项目空间' },
  { key: 'quality', label: '🛡️ 质量视图' },
  { key: 'guide', label: '🧭 写作引导' },
  { key: 'upload', label: '📥 上传' },
]

// 记住用户上次用的 Tab。背景（2026-09-16「非技术用户上手」专项）：
// 原先每次进来都固定落在「工具箱」——28 个工具卡全是 scholarforge_write /
// section_type / p_value 这类术语，第一次来的人不知道先点哪个，会直接判定「用不了」。
const TAB_KEY = 'vermes-scholar-last-tab'

// 无项目时的上手提示条，用户可关掉（关掉后本次会话不再出现）
const hintDismissed = ref(false)
const projectHintDismissed = ref(false)

function switchTab(key) {
  scholar.activeTab = key
  try { localStorage.setItem(TAB_KEY, key) } catch {}
}

function goSettings() { router.push('/settings') }

// 下拉框选项目：显式经 store，确保后端激活项目被同步（v-model 直接赋值会漏掉这一步）
function onProjectChange(e) {
  const raw = e.target.value
  if (raw === '') { scholar.selectProject(null); return }
  const pid = Number(raw)
  scholar.selectProject(Number.isInteger(pid) && pid > 0 ? pid : null)
}

// ── 环境自检（2026-09-16 非技术用户上手专项）──────────────────────────
// 原则：动手之前就告诉用户缺什么，而不是等他点了工具、在角落里冒一行红字才明白。
// 复用后端已有的 GET /api/onboarding（blueprints/config.py get_onboarding）：
// 它检查 model / provider / api_key 三者是否齐备 —— 不重复实现，口径与首页一致。
const envChecked = ref(false)
const envMissing = ref([])
const envDismissed = ref(false)

const MISSING_LABEL = {
  model: '还没选模型',
  provider: '还没选服务商',
  api_key: 'API Key 还没填',
}
const envMissingText = computed(
  () => envMissing.value.map(m => MISSING_LABEL[m] || m).join('、')
)
const envBlocked = computed(
  () => envChecked.value && !envDismissed.value && envMissing.value.length > 0
)

async function checkEnv() {
  try {
    const resp = await fetch('/api/onboarding')
    if (!resp.ok) {
      envChecked.value = true
      return // 老后端没有该端点：静默降级，不显示横幅、不打扰
    }
    const d = await resp.json()
    envMissing.value = Array.isArray(d.missing) ? d.missing : []
  } catch {
    /* 后端不可达：不阻断面板本身 */
  }
  envChecked.value = true
}

// 有项目但没选中：写回类成果会落进后端隐藏的默认兜底项目，用户回项目空间找不到。
const projectNotPicked = computed(
  () => scholar.projectsLoaded && scholar.projects.length > 0 &&
        scholar.currentProjectId == null && !projectHintDismissed.value
)

onMounted(async () => {
  await Promise.all([scholar.loadProjects(), checkEnv()])
  let saved = null
  try { saved = localStorage.getItem(TAB_KEY) } catch {}
  if (saved && TABS.some(t => t.key === saved)) {
    // 老用户：回到他上次待的地方，不打扰
    scholar.activeTab = saved
  } else {
    // 第一次来：没有项目就直接落到「写作引导」——先讲清楚要干什么，再进工具箱
    scholar.activeTab = scholar.projects.length === 0 ? 'guide' : 'tools'
  }
})
</script>

<template>
  <div class="flex flex-col h-full bg-gray-50 dark:bg-gray-900">
    <header class="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shrink-0 flex-wrap">
      <button
        @click="goChat"
        class="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition text-gray-500"
        title="返回会话"
      >
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 19l-7-7 7-7"/>
        </svg>
      </button>
      <h1 class="text-base font-semibold flex items-center gap-2">
        <span>📝</span> 论文写作
      </h1>

      <!-- Tab 切换 -->
      <nav class="flex items-center gap-1 ml-2 flex-wrap">
        <button
          v-for="t in TABS"
          :key="t.key"
          :class="[
            'px-3 py-1.5 rounded-lg text-sm transition',
            scholar.activeTab === t.key
              ? 'bg-blue-600 text-white font-medium'
              : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700',
          ]"
          @click="switchTab(t.key)"
        >
          {{ t.label }}
        </button>
      </nav>

      <div class="ml-auto flex items-center gap-2">
        <label class="text-xs text-gray-500">当前项目</label>
        <!-- 不用 v-model：直接改 currentProjectId 会绕过 store 的 selectProject()，
             后端「激活项目」不会被更新，写回类成果仍会落进隐藏的默认兜底项目。
             @change 显式走 selectProject（内含种入后端 + 跨会话记忆）。
             另：:value="null" 会被 Vue 视为「移除属性」，option.value 退化成中文文本，
             故这里用静态空串 ''，在 handler 里映射回 null。 -->
        <select
          :value="scholar.currentProjectId ?? ''"
          @change="onProjectChange"
          class="rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-2 py-1.5 text-sm"
        >
          <option value="">未选择项目（成果可能散落）</option>
          <option v-for="p in scholar.projects" :key="p.id" :value="p.id">
            #{{ p.id }} {{ p.title }}
          </option>
        </select>
      </div>
    </header>

    <!-- 环境自检（2026-09-16 非技术用户上手专项）
         背景：模型没配好时，工具箱 28 个工具点下去必然报错，而原先只有报错那一刻
         才在角落冒一行红字 —— 用户会判定「软件坏了」，而不是「我还没配模型」。
         这里在进页面时就显式说明缺什么，并给一键直达。 -->
    <div
      v-if="envBlocked"
      class="shrink-0 flex items-center gap-3 px-4 py-2.5 flex-wrap
             bg-rose-50 dark:bg-rose-900/25 border-b border-rose-200 dark:border-rose-800
             text-sm text-rose-800 dark:text-rose-200"
    >
      <span>⚠️ <b>还差一步才能开始</b> —— {{ envMissingText }}。论文工具需要调用大模型，配好之后 28 个工具才跑得动。</span>
      <span class="ml-auto flex items-center gap-2">
        <button
          @click="goSettings"
          class="px-3 py-1 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-medium transition"
        >⚙️ 去设置</button>
        <button
          @click="switchTab('guide')"
          class="px-3 py-1 rounded-lg border border-rose-300 dark:border-rose-700 hover:bg-rose-100 dark:hover:bg-rose-900/40 text-xs transition"
        >先看看流程</button>
        <button
          @click="envDismissed = true"
          class="px-2 py-1 text-xs text-rose-600 dark:text-rose-400 hover:underline"
        >知道了</button>
      </span>
    </div>

    <!-- 有项目但没选中：写回类成果会落进后端隐藏的默认兜底项目 -->
    <div
      v-if="!envBlocked && projectNotPicked"
      class="shrink-0 flex items-center gap-3 px-4 py-2.5 flex-wrap
             bg-amber-50 dark:bg-amber-900/25 border-b border-amber-200 dark:border-amber-800
             text-sm text-amber-800 dark:text-amber-200"
    >
      <span>📌 <b>还没选项目</b> —— 现在点写回类工具，成果会落进一个隐藏的默认项目，你在「项目空间」里翻不到。</span>
      <span class="ml-auto flex items-center gap-2">
        <button
          @click="switchTab('projects')"
          class="px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-medium transition"
        >去选一个</button>
        <button
          @click="projectHintDismissed = true"
          class="px-2 py-1 text-xs text-amber-600 dark:text-amber-400 hover:underline"
        >知道了</button>
      </span>
    </div>

    <!-- 无项目时的上手提示（2026-09-16 非技术用户上手专项）
         背景：此前没有任何项目也能直接点工具，而写回类工具在后端会**静默写进隐藏的
         「默认兜底项目」**——用户回「项目空间」找不到自己的成果，只会以为「点了没用」。
         这里把「先建项目」摆在最显眼处，并提供直达按钮。 -->
    <div
      v-if="scholar.projectsLoaded && scholar.projects.length === 0 && !hintDismissed"
      class="shrink-0 flex items-center gap-3 px-4 py-2.5 flex-wrap
             bg-amber-50 dark:bg-amber-900/25 border-b border-amber-200 dark:border-amber-800
             text-sm text-amber-800 dark:text-amber-200"
    >
      <span>📌 <b>还没有论文项目</b> —— 先建一个，之后的文献、写作、质量检查都会归到它下面，成果不会散落。</span>
      <span class="ml-auto flex items-center gap-2">
        <button
          @click="switchTab('projects')"
          class="px-3 py-1 rounded-lg bg-amber-600 hover:bg-amber-700 text-white text-xs font-medium transition"
        >＋ 新建项目</button>
        <button
          @click="switchTab('guide')"
          class="px-3 py-1 rounded-lg border border-amber-300 dark:border-amber-700 hover:bg-amber-100 dark:hover:bg-amber-900/40 text-xs transition"
        >看看写作流程</button>
        <button
          @click="hintDismissed = true"
          class="px-2 py-1 text-xs text-amber-600 dark:text-amber-400 hover:underline"
        >知道了</button>
      </span>
    </div>

    <main class="flex-1 overflow-y-auto">
      <ToolBox v-if="scholar.activeTab === 'tools'" />
      <ProjectSpace v-else-if="scholar.activeTab === 'projects'" />
      <QualityView v-else-if="scholar.activeTab === 'quality'" />
      <FlowGuide v-else-if="scholar.activeTab === 'guide'" />
      <Uploader v-else-if="scholar.activeTab === 'upload'" />
    </main>
  </div>
</template>
