<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4" @click.self="$emit('close')">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col">
        <!-- Header -->
        <div class="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
          <div class="flex items-center gap-2">
            <span class="text-xl">🎨</span>
            <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100">风格学习</h3>
          </div>
          <button @click="$emit('close')" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400 hover:text-gray-600" aria-label="关闭">✕</button>
        </div>

        <!-- Body -->
        <div class="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          <div class="p-3 bg-green-50 dark:bg-green-900/15 border border-green-200 dark:border-green-800 rounded-lg text-sm text-green-700 dark:text-green-300 leading-relaxed">
            💡 <b>仿写你的声音</b>，而非降 AI 率。上传 2-5 篇你自己的过往论文/草稿，系统会提取你的写作风格指纹（术语、句式、过渡词、引用习惯、章节结构），后续用 ScholarForge 写作时会自动匹配。
          </div>

          <!-- 用户标识 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">风格标识（用于区分不同用户，默认 default）</label>
            <input v-model="userId" type="text" placeholder="default"
              class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100" />
          </div>

          <!-- 来源描述 -->
          <div>
            <label class="block text-xs font-medium text-gray-700 dark:text-gray-200 mb-1">来源描述（可选）</label>
            <input v-model="description" type="text" placeholder="例：我的硕士论文 / 已发表期刊文章"
              class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100" />
          </div>

          <!-- 示例文本 -->
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="text-xs font-medium text-gray-700 dark:text-gray-200">示例论文（每篇 ≥500 字效果最佳）</label>
              <span class="text-[10px] text-gray-400">{{ examples.length }} 篇</span>
            </div>
            <div v-for="(ex, i) in examples" :key="i" class="mb-2 relative">
              <textarea v-model="examples[i]" rows="6" :placeholder="`示例论文 ${i + 1}：粘贴你的论文全文...`"
                class="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm font-mono focus:ring-2 focus:ring-green-500 focus:border-transparent dark:bg-gray-700 dark:text-gray-100 resize-y"></textarea>
              <button @click="examples.splice(i, 1)" class="absolute top-2 right-2 text-gray-400 hover:text-red-500 text-xs">✕</button>
            </div>
            <button @click="examples.push('')" class="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 hover:border-green-500 hover:text-green-600 transition-colors">
              + 添加示例
            </button>
          </div>

          <!-- 结果 -->
          <div v-if="result" class="border border-green-200 dark:border-green-800 rounded-lg overflow-hidden">
            <div class="bg-green-50 dark:bg-green-900/15 px-3 py-2 text-xs font-medium text-green-700 dark:text-green-300">
              ✅ 风格学习完成
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
          <button @click="runLearn" :disabled="loading || validExamples.length === 0" class="px-5 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-40 text-white rounded-lg text-sm font-medium flex items-center gap-2">
            <span v-if="loading" class="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            {{ loading ? '学习中...' : '🎨 学习风格' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  visible: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

const userId = ref('default')
const description = ref('')
const examples = ref([''])
const loading = ref(false)
const result = ref('')
const error = ref('')

const validExamples = computed(() => examples.value.filter(e => e && e.trim().length >= 100))

async function runLearn() {
  if (validExamples.value.length === 0) return
  loading.value = true
  error.value = ''
  result.value = ''
  try {
    const resp = await fetch('/api/scholar/learn-style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        examples: validExamples.value,
        user_id: userId.value || 'default',
        description: description.value,
      }),
    })
    const data = await resp.json()
    if (!resp.ok) {
      error.value = data.detail || '学习失败'
      return
    }
    result.value = data.result || ''
  } catch (e) {
    error.value = '网络错误：' + e.message
  } finally {
    loading.value = false
  }
}
</script>
