<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4" @click.self="$emit('close')">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <div class="flex items-center gap-2">
            <span class="text-xl">📖</span>
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100">引用替换占位符</h3>
          </div>
          <button @click="$emit('close')" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600" aria-label="关闭">✕</button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          <p class="text-sm text-gray-500 dark:text-gray-400">
            将正文中的 <code class="px-1 bg-gray-100 dark:bg-gray-700 rounded text-[11px]">[n]</code> 引用占位符自动替换为真实学术文献。系统会搜索 Semantic Scholar 等数据库，按语义匹配度自动填充。
          </p>

          <!-- 论文主题 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">论文主题（用于搜索匹配）</label>
            <input v-model="topic" type="text" placeholder="例：户外建构游戏对幼儿合作能力的影响"
              class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100" />
          </div>

          <!-- 关键词 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">关键词（逗号分隔，可选）</label>
            <input v-model="keywordsInput" type="text" placeholder="例：建构游戏, 合作能力, 维果茨基"
              class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100" />
          </div>

          <!-- 草稿输入 -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="text-xs font-medium text-gray-700 dark:text-gray-200">论文草稿</label>
              <button @click="loadFromProject" :disabled="loading" class="text-[10px] text-green-600 hover:text-green-700 disabled:opacity-40">
                ← 从当前论文载入
              </button>
            </div>
            <textarea v-model="draft" rows="12" placeholder="粘贴含 [1][2][3] 占位符的论文正文..."
              class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-mono focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100 resize-y"></textarea>
          </div>

          <!-- 引用格式 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">引用格式</label>
            <select v-model="citationStyle" class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100">
              <option value="gbt7714">GB/T 7714-2015</option>
              <option value="apa">APA 7th</option>
              <option value="mla">MLA 9th</option>
              <option value="ieee">IEEE</option>
              <option value="chicago">Chicago 17th</option>
              <option value="vancouver">Vancouver</option>
            </select>
          </div>

          <!-- 置信度阈值 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">
              匹配阈值：{{ (confidenceThreshold * 100).toFixed(0) }}%（低于此值保留占位符）
            </label>
            <input v-model.number="confidenceThreshold" type="range" min="0.1" max="0.9" step="0.05" class="w-full" />
          </div>

          <!-- 结果 -->
          <div v-if="result" class="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden">
            <div class="bg-gray-50 dark:bg-gray-700/50 px-3 py-2 text-xs font-medium text-gray-700 dark:text-gray-200 flex items-center justify-between">
              <span>✅ 替换结果</span>
              <button @click="copyResult" class="text-[10px] text-green-600 hover:text-green-700">复制</button>
            </div>
            <pre class="p-3 text-[11px] text-gray-700 dark:text-gray-200 whitespace-pre-wrap max-h-60 overflow-y-auto leading-relaxed">{{ result }}</pre>
          </div>

          <!-- 错误 -->
          <div v-if="error" class="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-300">
            {{ error }}
          </div>
        </div>

        <!-- Footer -->
        <div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-gray-200 dark:border-gray-700">
          <button @click="$emit('close')" class="px-4 py-2 text-sm text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg">取消</button>
          <button @click="runReplace" :disabled="loading || !draft.trim()" class="px-5 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-40 text-white rounded-lg text-sm font-medium flex items-center gap-2">
            <span v-if="loading" class="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            {{ loading ? '替换中...' : '🚀 开始替换' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
  projectId: { type: [Number, String], default: null },
  buildFullPaper: { type: Function, default: null },
})
const emit = defineEmits(['close'])

const topic = ref('')
const keywordsInput = ref('')
const draft = ref('')
const citationStyle = ref('gbt7714')
const confidenceThreshold = ref(0.65)
const loading = ref(false)
const result = ref('')
const error = ref('')

async function loadFromProject() {
  if (!props.buildFullPaper) return
  try {
    const full = await props.buildFullPaper()
    if (full) draft.value = full
  } catch (e) {
    console.error('load from project failed', e)
  }
}

async function runReplace() {
  if (!draft.value.trim()) return
  loading.value = true
  error.value = ''
  result.value = ''
  try {
    const resp = await fetch('/api/scholar/replace-citations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        draft: draft.value,
        topic: topic.value,
        keywords: keywordsInput.value.split(',').map(k => k.trim()).filter(Boolean),
        paper_type: '本科论文',
        confidence_threshold: confidenceThreshold.value,
        citation_style: citationStyle.value,
      }),
    })
    const data = await resp.json()
    if (!resp.ok) {
      error.value = data.detail || '替换失败'
      return
    }
    result.value = data.result || ''
  } catch (e) {
    error.value = '网络错误：' + e.message
  } finally {
    loading.value = false
  }
}

async function copyResult() {
  if (!result.value) return
  try {
    await navigator.clipboard.writeText(result.value)
  } catch (e) {
    console.error('copy failed', e)
  }
}
</script>
