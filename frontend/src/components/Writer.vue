<template>
  <div class="h-full flex flex-col overflow-hidden bg-gray-900">
    <!-- 顶部工具栏 -->
    <div class="px-4 py-2 border-b border-gray-700 flex items-center gap-1.5 shrink-0">
      <span class="text-[10px] text-gray-500 uppercase tracking-wider mr-2">论文 Agent</span>
      <button
        v-for="a in agents"
        :key="a.name"
        @click="activeAgent = a.name"
        :class="[
          'px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors',
          activeAgent === a.name
            ? 'bg-green-600 text-white shadow'
            : 'text-gray-400 hover:text-gray-200 bg-gray-800'
        ]"
      >{{ a.icon }} {{ a.label }}</button>

      <span class="text-gray-700 mx-1">|</span>
      <button
        @click="runPipeline"
        :disabled="streaming"
        class="px-4 py-1.5 bg-gradient-to-r from-green-600 to-emerald-500 text-white rounded-lg text-xs font-semibold hover:from-green-500 enabled:shadow-lg enabled:shadow-green-500/20 disabled:opacity-40 transition-all"
      >⚡ 全链路写作</button>

      <div class="flex-1" />
      <button @click="$router.push('/')" class="px-2 py-1 text-gray-500 hover:text-gray-300 text-xs">← Vermes</button>
    </div>

    <!-- 主体 -->
    <div class="flex-1 flex overflow-hidden">
      <div class="flex-1 flex flex-col overflow-hidden">
        <!-- 文档编辑区 -->
        <div class="flex-1 overflow-y-auto px-6 py-4">
          <div v-if="!draftText && !streamingText" class="flex flex-col items-center justify-center h-full text-center text-gray-500 gap-4">
            <div class="text-5xl">✍️</div>
            <div class="text-lg text-gray-400">ScholarForge 论文写作</div>
            <div class="text-sm text-gray-600 max-w-md">选择上方 Agent，输入研究方向或关键词开始</div>
            <div class="flex gap-2 mt-2">
              <button
                v-for="q in quickStarts"
                :key="q.agent"
                @click="quickStart(q)"
                class="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg text-sm transition-colors text-left"
              >{{ q.icon }} {{ q.label }}</button>
            </div>
          </div>

          <div v-else class="max-w-3xl mx-auto">
            <div v-if="draftText" class="prose prose-invert prose-sm" v-html="renderedDraft"></div>
            <div v-if="streamingText" class="mt-4 prose prose-invert prose-sm" v-html="renderedStream"></div>
            <div v-if="streaming && !streamingText" class="text-gray-500 italic text-sm animate-pulse mt-4">思考中...</div>
          </div>
        </div>

        <!-- 底部输入栏 -->
        <div class="px-6 py-3 border-t border-gray-800 shrink-0">
          <div class="flex items-end gap-2 max-w-3xl mx-auto">
            <textarea
              v-model="input"
              @keydown.enter.exact.prevent="send"
              :placeholder="inputPlaceholder"
              rows="2"
              class="flex-1 px-4 py-2.5 bg-gray-800 border border-gray-700 rounded-xl text-sm text-gray-100 resize-none focus:outline-none focus:border-green-500 transition-colors placeholder-gray-600"
            ></textarea>
            <button
              @click="send"
              :disabled="!input.trim() || streaming"
              class="px-5 py-2.5 bg-green-600 hover:bg-green-700 text-white rounded-xl text-sm font-medium disabled:opacity-40 shrink-0 transition-colors"
            >{{ streaming ? '⏳' : '发送' }}</button>
          </div>
        </div>
      </div>

      <!-- 右侧面板 -->
      <div class="w-72 border-l border-gray-800 flex flex-col overflow-hidden bg-gray-900/50 shrink-0">
        <div class="px-4 py-3 border-b border-gray-800 text-xs text-gray-500 font-medium">事件日志</div>
        <div class="flex-1 overflow-y-auto px-4 py-2 space-y-1">
          <div v-if="!events.length" class="text-xs text-gray-600 py-4 text-center">等待输入...</div>
          <div v-for="(e, i) in events" :key="i" class="text-xs flex items-start gap-1.5 py-0.5">
            <span class="shrink-0">{{ eventIcon(e.type) }}</span>
            <span class="text-gray-400">{{ e.message }}</span>
          </div>
        </div>

        <div class="px-4 py-3 border-t border-gray-800">
          <div class="text-xs text-gray-500 mb-1">已引用文献</div>
          <div v-if="!citations.length" class="text-xs text-gray-600">暂无</div>
          <div v-for="(c, i) in citations" :key="i" class="text-[11px] text-gray-400 py-0.5 truncate" :title="c.title">
            [{{ i+1 }}] {{ c.title }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const input = ref('')
const activeAgent = ref('literature')
const draftText = ref('')
const streaming = ref(false)
const streamingText = ref('')
const events = ref([])
const citations = ref([])

const agents = ref([
  { name: 'topic', icon: '💡', label: '选题' },
  { name: 'literature', icon: '📚', label: '文献' },
  { name: 'outline', icon: '📋', label: '大纲' },
  { name: 'writing', icon: '✍️', label: '写作' },
  { name: 'refinement', icon: '✨', label: '润色' },
])

const quickStarts = computed(() => [
  { icon: '🔍', label: '检索文献', agent: 'literature', prompt: '请帮我检索关于' },
  { icon: '📋', label: '生成大纲', agent: 'outline', prompt: '请为以下主题生成论文大纲：' },
  { icon: '✍️', label: '开始写作', agent: 'writing', prompt: '请撰写论文的引言部分：' },
])

const inputPlaceholder = computed(() => {
  const map = {
    topic: '描述研究方向，AI 分析可行性...',
    literature: '输入关键词，检索文献并生成综述...',
    outline: '生成论文大纲...',
    writing: '告诉 AI 写哪一节...',
    refinement: '粘贴需要润色的内容...',
  }
  return map[activeAgent.value] || '输入需求...'
})

onMounted(async () => {
  try {
    const resp = await fetch('/api/scholar/agents')
    if (resp.ok) {
      const data = await resp.json()
      if (data.agents?.length) agents.value = data.agents
    }
  } catch (e) { /* keep defaults */ }
})

function quickStart(q) {
  activeAgent.value = q.agent
  input.value = q.prompt
}

function eventIcon(type) {
  return { thinking: '💭', searching: '🔍', reading: '📖', writing: '✍️', done: '✅', error: '❌', stage: '🚀', citation: '📄', content: '' }[type] || '•'
}

const renderedDraft = computed(() => renderMd(draftText.value))
const renderedStream = computed(() => renderMd(streamingText.value))

function renderMd(t) {
  if (!t) return ''
  return t
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre class="bg-gray-950 p-3 rounded-lg overflow-x-auto text-xs my-2"><code>$2</code></pre>')
    .replace(/`([^`]+)`/g, '<code class="bg-gray-800 px-1.5 py-0.5 rounded text-xs">$1</code>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-gray-100">$1</strong>')
    .replace(/^### (.+)$/gm, '<h3 class="text-base font-semibold text-gray-100 mt-4 mb-2">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold text-gray-100 mt-5 mb-2">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-gray-100 mt-6 mb-3">$1</h1>')
    .replace(/^[-*]\s(.+)$/gm, '<li class="ml-5 list-disc text-gray-300">$1</li>')
    .replace(/^\d+\.\s(.+)$/gm, '<li class="ml-5 list-decimal text-gray-300">$1</li>')
    .replace(/\n\n/g, '</p><p class="text-gray-300 my-2 leading-relaxed">')
    .replace(/\n/g, '<br>')
}

async function send() {
  if (!input.value.trim() || streaming.value) return
  const msg = input.value.trim()
  input.value = ''
  streaming.value = true
  streamingText.value = ''
  events.value = []

  try {
    const resp = await fetch('/api/scholar/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, agent: activeAgent.value, pipeline: false }),
    })
    await readSSE(resp.body.getReader(), handleSSE)
  } catch (e) {
    events.value.push({ type: 'error', message: e.message })
  } finally {
    streaming.value = false
    if (streamingText.value) {
      draftText.value += (draftText.value ? '\n\n' : '') + streamingText.value
      streamingText.value = ''
    }
  }
}

async function runPipeline() {
  if (!input.value.trim() || streaming.value) return
  const msg = input.value.trim()
  input.value = ''
  streaming.value = true
  streamingText.value = ''
  events.value = []

  try {
    const resp = await fetch('/api/scholar/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, agent: 'literature', pipeline: true }),
    })
    await readSSE(resp.body.getReader(), handleSSE)
  } catch (e) {
    events.value.push({ type: 'error', message: e.message })
  } finally {
    streaming.value = false
    if (streamingText.value) {
      draftText.value += (draftText.value ? '\n\n' : '') + streamingText.value
      streamingText.value = ''
    }
  }
}

function handleSSE(evt) {
  const msg = evt.message || evt.data?.message || ''
  switch (evt.type) {
    case 'thinking':
    case 'searching':
    case 'reading':
    case 'writing':
      if (msg) events.value.push({ type: evt.type, message: msg })
      break
    case 'done':
      events.value.push({ type: 'done', message: msg || '完成' })
      break
    case 'stage':
      if (evt.pipeline === 'start') {
        const labels = { literature: '📚 文献检索', outline: '📋 大纲生成', writing: '✍️ 逐节写作', refinement: '✨ 润色' }
        events.value.push({ type: 'stage', message: `${labels[evt.stage] || evt.stage} 开始` })
      } else if (evt.pipeline === 'done') {
        events.value.push({ type: 'done', message: `${evt.stage} 完成` })
      }
      break
    case 'citation':
      if (evt.paper) citations.value.push(evt.paper)
      break
    case 'content':
      streamingText.value += evt.text || ''
      break
    case 'error':
      events.value.push({ type: 'error', message: msg || evt.message || '未知错误' })
      break
  }
}

async function readSSE(reader, handler) {
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value)
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const s = line.slice(6).trim()
      if (!s || s === '[DONE]') continue
      try { handler(JSON.parse(s)) } catch (e) { /* skip */ }
    }
  }
}
</script>
