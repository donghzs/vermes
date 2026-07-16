<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="$emit('close')">
      <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-[600px] max-h-[80vh] flex flex-col">
        <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 class="text-sm font-semibold text-gray-800 dark:text-gray-100">导出论文</h3>
          <button @click="$emit('close')" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="p-4 flex-1 overflow-y-auto">
          <div class="grid grid-cols-3 gap-3 mb-4">
            <button v-for="fmt in exportFormats" :key="fmt.id" @click="format = fmt.id"
              :class="['p-3 border rounded-lg text-left transition-all', format === fmt.id ? 'border-green-500 bg-green-50 dark:bg-green-900/20' : 'border-gray-200 dark:border-gray-700 hover:border-gray-300']">
              <div class="text-2xl mb-1">{{ fmt.icon }}</div>
              <div class="text-xs font-medium text-gray-800 dark:text-gray-200">{{ fmt.name }}</div>
              <div class="text-[10px] text-gray-500 mt-0.5">{{ fmt.desc }}</div>
            </button>
          </div>
          <div class="space-y-3">
            <div>
              <label class="text-xs text-gray-500 mb-1 block">文件名</label>
              <input v-model="filename" class="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm dark:text-gray-100"/>
            </div>
            <div v-if="format === 'latex'">
              <label class="text-xs text-gray-500 mb-1 block">LaTeX 模板（{{ latexTemplates.length }} 个期刊/会议）</label>
              <select v-model="latexTemplate" class="w-full px-3 py-2 bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg text-sm dark:text-gray-100">
                <optgroup label="📰 国际期刊">
                  <option v-for="t in latexTemplates.filter(t=>t.category==='journal')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                </optgroup>
                <optgroup label="🎤 国际会议">
                  <option v-for="t in latexTemplates.filter(t=>t.category==='conference')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                </optgroup>
                <optgroup label="🇨🇳 国内期刊（国标）">
                  <option v-for="t in latexTemplates.filter(t=>t.category==='chinese')" :key="t.id" :value="t.id">{{ t.name }} — {{ t.desc }}</option>
                </optgroup>
              </select>
            </div>
          </div>
        </div>
        <div class="px-4 py-3 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
          <button @click="$emit('close')" class="px-4 py-2 text-xs text-gray-600 hover:text-gray-800">取消</button>
          <button @click="doExport" class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-xs font-medium">导出</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { toast } from '../utils/toast'

const props = defineProps({
  visible: Boolean,
  projectTitle: { type: String, default: '未命名论文' },
  projectId: Number,
  buildFullPaper: { type: Function, required: true },
  saveCurrentSection: { type: Function, required: true },
  activeSection: String,
  currentContent: String,
  sectionContents: Object
})

const emit = defineEmits(['close', 'export-start'])

const format = ref('pdf')
const filename = computed(() => `${props.projectTitle || '未命名论文'}.${format.value}`)
const latexTemplate = ref('ieee')

const latexTemplates = [
  { id: 'ieee', name: 'IEEEtran', desc: 'IEEE 期刊/会议', category: 'journal' },
  { id: 'springer-svjour', name: 'Springer SVJour', desc: 'Springer 期刊模板', category: 'journal' },
  { id: 'elsevier-elsarticle', name: 'Elsevier Elsarticle', desc: 'Elsevier 期刊', category: 'journal' },
  { id: 'nature', name: 'Nature', desc: 'Nature 期刊', category: 'journal' },
  { id: 'science', name: 'Science', desc: 'Science 期刊', category: 'journal' },
  { id: 'apa', name: 'APA 6th', desc: '心理学/社会科学', category: 'journal' },
  { id: 'acm-sigconf', name: 'ACM SigConf', desc: 'ACM 会议标准', category: 'conference' },
  { id: 'mlr', name: 'MLR/JMLR', desc: '机器学习会议/期刊', category: 'conference' },
  { id: 'neurips', name: 'NeurIPS', desc: 'NeurIPS 会议', category: 'conference' },
  { id: 'icml', name: 'ICML', desc: 'ICML 会议', category: 'conference' },
  { id: 'cvpr', name: 'CVPR/ICCV', desc: '计算机视觉会议', category: 'conference' },
  { id: 'iclr', name: 'ICLR', desc: '国际学习表征会议', category: 'conference' },
  { id: 'acl', name: 'ACL', desc: '计算语言学年会', category: 'conference' },
  { id: 'aaai', name: 'AAAI', desc: '人工智能促进会', category: 'conference' },
  { id: 'gbt7714', name: 'GB/T 7714', desc: '中国学术期刊通用', category: 'chinese' },
  { id: 'acta-physica', name: '物理学报', desc: '中国物理学会', category: 'chinese' },
  { id: 'jcs', name: '计算机学报', desc: '中国计算机学会', category: 'chinese' },
  { id: 'jsi', name: '软件学报', desc: '中国计算机学会', category: 'chinese' },
]

const exportFormats = [
  { id: 'pdf', name: 'PDF 文档', icon: '📄', desc: '适合提交和打印' },
  { id: 'latex', name: 'LaTeX', icon: '📐', desc: '学术标准格式' },
  { id: 'word', name: 'Word', icon: '📝', desc: '便于后续编辑' },
  { id: 'markdown', name: 'Markdown', icon: '⬇️', desc: '保留源格式' },
  { id: 'bibtex', name: 'BibTeX', icon: '📚', desc: '仅导出参考文献' }
]

const doExport = async () => {
  const fmt = format.value
  emit('export-start', { format: fmt })
  emit('close')

  try {
    const pid = props.projectId || 0
    if (props.activeSection && props.currentContent !== (props.sectionContents || {})[props.activeSection]) {
      await props.saveCurrentSection()
    }
    const params = new URLSearchParams({ format: fmt, template: latexTemplate.value })
    const resp = await fetch(`/api/scholar/projects/${pid}/export?${params}`, {
      headers: { 'x-client-id': localStorage.getItem('scholarClientId') || '' }
    })
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: '导出失败' }))
      toast.error(err.detail || '导出失败')
      return
    }
    const ct = resp.headers.get('Content-Type') || ''
    if (ct.includes('application/json')) {
      const data = await resp.json()
      if (fmt === 'bibtex' || fmt === 'markdown') {
        const blob = new Blob([data.content], { type: 'text/plain;charset=utf-8' })
        downloadBlob(blob, filename.value)
      }
    } else {
      const blob = await resp.blob()
      downloadBlob(blob, filename.value)
    }
  } catch (e) {
    toast.error('导出失败: ' + e.message)
  }
}

const downloadBlob = (blob, name) => {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

watch(() => props.visible, (v) => { if (v) format.value = 'pdf' })
</script>
