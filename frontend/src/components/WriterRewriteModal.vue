<template>
  <Teleport to="body">
    <div v-if="visible" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/50" @click.self="$emit('close')">
      <div class="bg-white dark:bg-gray-800 rounded-2xl shadow-2xl w-full max-w-lg mx-4 overflow-hidden">
        <div class="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h3 class="text-lg font-semibold text-gray-800 dark:text-gray-100">修改：{{ targetTitle }}</h3>
          <button @click="$emit('close')" class="p-1 hover:bg-gray-100 dark:hover:bg-gray-700 rounded text-gray-400">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="p-6 space-y-4">
          <div>
            <label class="block text-xs text-gray-500 mb-2">修改模式</label>
            <div class="grid grid-cols-4 gap-2">
              <button v-for="m in REWRITE_MODES" :key="m.key" @click="mode = m.key"
                :class="['px-3 py-2 rounded-xl text-sm font-medium text-center transition-all',
                  mode === m.key 
                    ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 ring-2 ring-green-500/30' 
                    : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600']">
                <span class="block text-lg">{{ m.icon }}</span>
                <span class="text-xs">{{ m.label }}</span>
              </button>
            </div>
          </div>
          <div>
            <label class="block text-xs text-gray-500 mb-1">额外要求（可选）</label>
            <input v-model="instruction" placeholder="如：增加数据支撑 / 更口语化 / 加入案例..."
              class="w-full px-3 py-2 bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-xl text-sm focus:outline-none focus:border-green-500 dark:text-gray-100"
              @keydown.enter="submit" :disabled="loading" />
          </div>
        </div>
        <div class="px-6 py-4 bg-gray-50 dark:bg-gray-750 border-t border-gray-200 dark:border-gray-700 flex justify-end gap-2">
          <button @click="$emit('close')" class="px-4 py-2 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 rounded-xl">取消</button>
          <button @click="submit" :disabled="loading"
            class="px-5 py-2 bg-green-600 hover:bg-green-700 disabled:opacity-50 text-white rounded-xl text-sm font-medium flex items-center gap-2">
            <span v-if="loading" class="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin"></span>
            {{ loading ? '修改中...' : '开始修改' }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref } from 'vue'

const props = defineProps({
  visible: Boolean,
  targetTitle: { type: String, default: '未命名章节' },
  targetKey: { type: String, default: '' },
  projectId: Number,
  sectionContents: Object,
  activeSection: String,
  outline: Array,
})

const emit = defineEmits(['close', 'rewrite-done'])

const mode = ref('polish')
const instruction = ref('')
const loading = ref(false)

const REWRITE_MODES = [
  { key: 'polish', icon: '✨', label: '润色' },
  { key: 'expand', icon: '📖', label: '扩写' },
  { key: 'shorten', icon: '✂️', label: '精简' },
  { key: 'restructure', icon: '🔀', label: '重组' },
  { key: 'add_data', icon: '📊', label: '加数据' },
  { key: 'academic', icon: '🎓', label: '学术化' },
  { key: 'plain', icon: '💬', label: '通俗化' },
]

const submit = async () => {
  if (!props.targetKey || !props.projectId || loading.value) return
  loading.value = true
  const sectionKey = props.targetKey
  try {
    const r = await fetch(`/api/scholar/projects/${props.projectId}/rewrite-section`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section_key: sectionKey,
        mode: mode.value,
        instruction: instruction.value,
      }),
    })
    if (!r.ok) throw new Error(await r.text())
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() || ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try {
          const evt = JSON.parse(line.slice(6))
          if (evt.type === 'rewrite_done') {
            emit('rewrite-done', { sectionKey, text: evt.text })
            mode.value = 'polish'
            instruction.value = ''
            emit('close')
          } else if (evt.type === 'error') {
            throw new Error(evt.message)
          }
        } catch (e) { if (e.message && !e.message.includes('JSON')) throw e }
      }
    }
  } catch (e) {
    alert('修改失败: ' + e.message)
  } finally {
    loading.value = false
  }
}
</script>
