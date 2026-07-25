<script setup>
// Uploader：上传入口（P0c-3）。
//  - PDF 文献：走既有后端 POST /api/scholar/projects/{pid}/literature/upload-pdf（抽取文本→建文献记录）。
//  - 文本导入：本地读文件文本，注入指定工具的文本字段，跳到工具箱预填。
// 全部复用既有端点/invoke，无新增后端逻辑（符合「A 面板统一收敛」纪律）。
import { ref, computed } from 'vue'
import { useScholarStore } from '../../stores/scholar'

const scholar = useScholarStore()

const mode = ref('pdf') // 'pdf' | 'text'
const pdfFile = ref(null)
const uploading = ref(false)
const pdfResult = ref('')
const pdfError = ref('')

const textFile = ref(null)
const textContent = ref('')
const target = ref('scholarforge_review::draft')
const importing = ref(false)
const importMsg = ref('')

// 文本可导入的目标（工具::字段），覆盖常见文本型参数
const TEXT_TARGETS = [
  { value: 'scholarforge_review::draft', label: '审阅草稿 review · draft' },
  { value: 'scholarforge_score::content', label: '评分 score · content' },
  { value: 'scholarforge_polish::text', label: '润色 polish · text' },
  { value: 'scholarforge_deaigc::text', label: '去AI痕迹 deaigc · text' },
  { value: 'scholarforge_replace_citations::draft', label: '替换引用 replace_citations · draft' },
  { value: 'scholarforge_review_claims::paper_text', label: '主张-证据 review_claims · paper_text' },
  { value: 'scholarforge_format_refs::papers', label: '格式化引用 format_refs · papers' },
  { value: 'scholarforge_verify_citations::papers', label: '验证引用 verify_citations · papers' },
  { value: 'scholarforge_save_literature_cards::papers', label: '文献卡片 save_literature_cards · papers' },
]

const hasProject = computed(() => !!scholar.currentProjectId)

function onPdfChange(e) {
  pdfFile.value = e.target.files?.[0] || null
  pdfResult.value = ''
  pdfError.value = ''
}

async function uploadPdf() {
  if (!hasProject.value) {
    pdfError.value = '请先在顶部选择一个项目。'
    return
  }
  if (!pdfFile.value) {
    pdfError.value = '请选择 PDF 文件。'
    return
  }
  uploading.value = true
  pdfError.value = ''
  pdfResult.value = ''
  try {
    const fd = new FormData()
    fd.append('file', pdfFile.value)
    const resp = await fetch(
      `/api/scholar/projects/${scholar.currentProjectId}/literature/upload-pdf`,
      { method: 'POST', body: fd },
    )
    const data = await resp.json().catch(() => ({}))
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
    pdfResult.value = `✅ 已导入文献「${data.title}」（${data.text_length} 字符）→ 文献 #${data.id}`
  } catch (e) {
    pdfError.value = `PDF 上传失败：${e.message}`
  } finally {
    uploading.value = false
  }
}

function onTextChange(e) {
  const f = e.target.files?.[0] || null
  textFile.value = f
  textContent.value = ''
  importMsg.value = ''
  if (!f) return
  f.text()
    .then((t) => {
      textContent.value = t
    })
    .catch((err) => {
      importMsg.value = `读取失败：${err.message}`
    })
}

function importText() {
  if (!textContent.value) {
    importMsg.value = '文件为空或尚未读取。'
    return
  }
  const [tool, field] = target.value.split('::')
  importing.value = true
  importMsg.value = ''
  try {
    scholar.prefillTool(tool, field, textContent.value)
    importMsg.value = `已跳转工具箱并预填 ${tool.split('_').pop()} · ${field}（${textContent.value.length} 字符），可编辑后运行。`
  } catch (e) {
    importMsg.value = `导入失败：${e.message}`
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <div class="p-4 space-y-4">
    <div class="inline-flex rounded-lg border border-gray-300 dark:border-gray-600 overflow-hidden">
      <button
        :class="[
          'px-3 py-1.5 text-sm',
          mode === 'pdf'
            ? 'bg-blue-600 text-white font-medium'
            : 'bg-white dark:bg-gray-800 text-gray-500',
        ]"
        @click="mode = 'pdf'"
      >
        📄 PDF 文献
      </button>
      <button
        :class="[
          'px-3 py-1.5 text-sm',
          mode === 'text'
            ? 'bg-blue-600 text-white font-medium'
            : 'bg-white dark:bg-gray-800 text-gray-500',
        ]"
        @click="mode = 'text'"
      >
        📝 文本导入
      </button>
    </div>

    <!-- PDF 上传 -->
    <div v-if="mode === 'pdf'" class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 space-y-3">
      <p class="text-sm text-gray-500">
        上传 PDF 论文，后端用 PyMuPDF 抽取标题/作者/摘要/全文，并自动建成一条文献记录（归属当前项目）。
      </p>
      <p v-if="!hasProject" class="text-sm text-amber-600">⚠️ 请先在顶部选择一个项目。</p>
      <input
        type="file"
        accept="application/pdf,.pdf"
        class="block text-sm"
        @change="onPdfChange"
      />
      <button
        class="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition disabled:opacity-50"
        :disabled="!pdfFile || uploading"
        @click="uploadPdf"
      >
        {{ uploading ? '上传中…' : '上传并建文献' }}
      </button>
      <p v-if="pdfResult" class="text-sm text-emerald-600">{{ pdfResult }}</p>
      <p v-if="pdfError" class="text-sm text-red-500">{{ pdfError }}</p>
    </div>

    <!-- 文本导入 -->
    <div v-else class="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 space-y-3">
      <p class="text-sm text-gray-500">
        读取本地文本文件（.txt/.md/.bib/.ris/.csv/.tex），注入指定工具的文本字段，跳到工具箱预填。
      </p>
      <input
        type="file"
        accept=".txt,.md,.bib,.ris,.csv,.tex,text/plain"
        class="block text-sm"
        @change="onTextChange"
      />
      <label class="block text-xs font-medium text-gray-600 dark:text-gray-300">导入到</label>
      <select
        v-model="target"
        class="w-full rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-3 py-2 text-sm"
      >
        <option v-for="t in TEXT_TARGETS" :key="t.value" :value="t.value">{{ t.label }}</option>
      </select>
      <p v-if="textContent" class="text-xs text-gray-400">
        已读取 {{ textContent.length }} 字符
      </p>
      <button
        class="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white text-sm font-medium transition disabled:opacity-50"
        :disabled="!textContent || importing"
        @click="importText"
      >
        填入工具箱
      </button>
      <p v-if="importMsg" class="text-sm text-emerald-600">{{ importMsg }}</p>
    </div>
  </div>
</template>
