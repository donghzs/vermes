<template>
  <div class="flex h-full min-h-0 w-full bg-slate-50 dark:bg-slate-900 text-slate-700 dark:text-slate-200">

    <!-- 左栏：工作流列表 -->
    <aside class="w-64 shrink-0 flex flex-col border-r border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800">
      <div class="p-3 border-b border-slate-200 dark:border-slate-700">
        <h1 class="text-base font-semibold">工作流编排</h1>
        <p class="text-xs text-slate-400 mt-0.5">可视化 DAG 编辑器</p>
        <div class="flex gap-2 mt-3">
          <button @click="newWorkflow" class="flex-1 text-xs px-2 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white transition">＋ 新建</button>
          <button @click="loadList" class="text-xs px-2 py-1.5 rounded-md bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition" title="刷新">↻</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-1.5">
        <div v-if="!workflows.length" class="text-xs text-slate-400 text-center py-8">还没有工作流<br/>点击「新建」开始</div>
        <button v-for="wf in workflows" :key="wf.name" @click="loadWorkflow(wf.name)"
          class="w-full text-left px-3 py-2 rounded-md border transition text-sm"
          :class="current.name === wf.name ? 'border-blue-400 bg-blue-50 dark:bg-blue-900/30' : 'border-slate-200 dark:border-slate-700 hover:bg-slate-50 dark:hover:bg-slate-700/50'">
          <div class="font-medium truncate">{{ wf.name }}</div>
          <div class="text-[11px] text-slate-400 truncate">{{ wf.description || '无描述' }} · {{ wf.step_count }} 步</div>
        </button>
      </div>
    </aside>

    <!-- 中栏：DAG 画布 -->
    <main class="flex-1 min-w-0 flex flex-col relative">
      <div class="absolute top-3 left-3 z-10 flex items-center gap-1.5 bg-white/90 dark:bg-slate-800/90 backdrop-blur rounded-lg border border-slate-200 dark:border-slate-700 px-2 py-1 shadow-sm">
        <button @click="autoLayout" class="text-xs px-2 py-1 rounded bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition" title="按依赖分层自动排布">⚡ 自动布局</button>
        <div class="w-px h-5 bg-slate-200 dark:bg-slate-600"></div>
        <button @click="zoom = Math.min(2.5, zoom + 0.15)" class="w-7 h-7 rounded bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition text-sm">＋</button>
        <button @click="zoom = Math.max(0.3, zoom - 0.15)" class="w-7 h-7 rounded bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition text-sm">－</button>
        <span class="text-[11px] text-slate-400 w-10 text-center">{{ Math.round(zoom * 100) }}%</span>
        <button @click="resetView" class="text-xs px-2 py-1 rounded bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition" title="重置视图">⤢ 适应</button>
      </div>

      <svg ref="svgRef" class="w-full h-full touch-none select-none"
        @mousedown="onCanvasDown" @wheel.prevent="onWheel">
        <g :transform="`translate(${pan.x},${pan.y}) scale(${zoom})`">
          <!-- 连线 -->
          <g>
            <path v-for="(e, i) in edges" :key="'e'+i" :d="e.d" fill="none"
              :class="e.active ? 'stroke-emerald-500' : 'stroke-slate-400 dark:stroke-slate-500'"
              stroke-width="2" marker-end="url(#arrow)" />
            <!-- 临时连线 -->
            <path v-if="connecting.active" :d="tempEdge" fill="none" stroke="emerald" stroke-width="2" stroke-dasharray="5 4" />
          </g>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" class="fill-slate-400 dark:fill-slate-500" />
            </marker>
          </defs>
          <!-- 节点 -->
          <g v-for="step in current.steps" :key="step.id"
            :data-step-id="step.id" :transform="`translate(${step.x},${step.y})`"
            class="cursor-move" @mousedown="onNodeDown($event, step)">
            <rect :width="NODE_W" :height="NODE_H" rx="12"
              class="transition-shadow"
              :class="selectedId === step.id ? 'fill-white dark:fill-slate-800 stroke-emerald-500' : 'fill-white dark:fill-slate-800 stroke-slate-300 dark:stroke-slate-600'"
              stroke-width="2"
              :style="doneSteps.has(step.id) ? 'filter: drop-shadow(0 0 6px rgba(16,185,129,.55))' : ''" />
            <text x="14" y="28" class="fill-slate-800 dark:fill-slate-100" style="font-size:14px;font-weight:600" font-weight="600">{{ trunc(step.title || step.id, 18) }}</text>
            <text x="14" y="48" class="fill-slate-400" style="font-size:11px">{{ trunc(step.done_when || step.deliverable || '（无完成条件）', 26) }}</text>
            <!-- 依赖计数徽标 -->
            <g v-if="step.dependencies.length">
              <circle :cx="NODE_W/2" cy="-2" r="9" class="fill-slate-200 dark:fill-slate-600" />
              <text :x="NODE_W/2" y="2" text-anchor="middle" class="fill-slate-600 dark:fill-slate-200" style="font-size:10px">{{ step.dependencies.length }}</text>
            </g>
            <!-- 输入端口 -->
            <circle cx="0" :cy="NODE_H/2" r="6" class="fill-slate-300 dark:fill-slate-500" />
            <!-- 输出端口（拖拽连线） -->
            <circle :cx="NODE_W" :cy="NODE_H/2" r="7" class="fill-emerald-500 hover:fill-emerald-600 cursor-crosshair"
              @mousedown.stop="onPortDown($event, step)" />
          </g>
        </g>
      </svg>

      <div v-if="!current.steps.length" class="absolute inset-0 flex items-center justify-center pointer-events-none">
        <div class="text-center text-slate-400">
          <div class="text-4xl mb-2">🧩</div>
          <p class="text-sm">画布为空 — 在右侧「步骤检查器」点击「＋ 添加步骤」</p>
        </div>
      </div>
    </main>

    <!-- 右栏：检查器 + 运行 + 触发器 -->
    <aside class="w-80 shrink-0 flex flex-col border-l border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 overflow-y-auto">
      <!-- 工作流元信息 -->
      <div class="p-3 border-b border-slate-200 dark:border-slate-700 space-y-2">
        <input v-model="current.name" placeholder="工作流名称"
          class="w-full text-sm px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
        <input v-model="current.description" placeholder="描述（可选）"
          class="w-full text-xs px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
        <div class="flex gap-2">
          <button @click="saveWorkflow" :disabled="saving" class="flex-1 text-xs px-2 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white transition">💾 保存</button>
          <button @click="openTrigger" class="text-xs px-2 py-1.5 rounded-md bg-amber-500 hover:bg-amber-600 text-white transition" title="配置触发器">⏰ 触发器</button>
          <button v-if="current.name" @click="deleteWorkflow(current.name)" class="text-xs px-2 py-1.5 rounded-md bg-red-500 hover:bg-red-600 text-white transition" title="删除">🗑</button>
        </div>
        <p v-if="saveError" class="text-[11px] text-red-500">{{ saveError }}</p>
        <p v-if="saveOk" class="text-[11px] text-emerald-500">✓ 已保存（v{{ saveOk }}）</p>
      </div>

      <!-- 步骤检查器 -->
      <div class="p-3 border-b border-slate-200 dark:border-slate-700">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-semibold">步骤检查器</h2>
          <button @click="addStep" class="text-xs px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white transition">＋ 添加步骤</button>
        </div>

        <div v-if="!selected" class="text-xs text-slate-400 py-4 text-center">在画布中选择一个步骤节点</div>

        <div v-else class="space-y-2">
          <label class="block text-[11px] text-slate-400">标题</label>
          <input v-model="selected.title" class="w-full text-sm px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
          <label class="block text-[11px] text-slate-400">目标交付物</label>
          <input v-model="selected.deliverable" class="w-full text-sm px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
          <label class="block text-[11px] text-slate-400">完成条件（done_when）</label>
          <textarea v-model="selected.done_when" rows="2" class="w-full text-xs px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400 resize-none"></textarea>
          <label class="block text-[11px] text-slate-400">描述</label>
          <textarea v-model="selected.description" rows="2" class="w-full text-xs px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400 resize-none"></textarea>

          <div class="pt-1">
            <div class="text-[11px] text-slate-400 mb-1">依赖（前置步骤 → 本步骤）</div>
            <div class="space-y-1 max-h-32 overflow-y-auto pr-1">
              <label v-for="s in current.steps.filter(s => s.id !== selected.id)" :key="s.id"
                class="flex items-center gap-2 text-xs px-2 py-1 rounded bg-slate-50 dark:bg-slate-900">
                <input type="checkbox" :checked="selected.dependencies.includes(s.id)"
                  @change="toggleDep(s.id, $event.target.checked)" class="accent-blue-600" />
                <span class="truncate">{{ s.title || s.id }}</span>
              </label>
            </div>
          </div>

          <button @click="removeStep(selected.id)" class="w-full text-xs px-2 py-1.5 rounded-md bg-red-50 dark:bg-red-900/30 text-red-600 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-900/50 transition">删除此步骤</button>
        </div>
      </div>

      <!-- 运行面板 -->
      <div class="p-3 border-b border-slate-200 dark:border-slate-700">
        <h2 class="text-sm font-semibold mb-2">运行</h2>
        <textarea v-model="runPrompt" rows="2" placeholder="运行提示词（可选，作为整体上下文）"
          class="w-full text-xs px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400 resize-none"></textarea>
        <label class="flex items-center gap-2 text-xs mt-2 text-slate-500">
          <input type="checkbox" v-model="runConcurrent" class="accent-blue-600" /> 并发执行（默认串行）
        </label>
        <button @click="runWorkflow" :disabled="running || !current.name" class="w-full mt-2 text-xs px-2 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white transition">
          {{ running ? '运行中…' : '▶ 运行工作流' }}
        </button>
        <div v-if="runError" class="mt-2 text-[11px] text-red-500 whitespace-pre-wrap">{{ runError }}</div>
        <div v-if="runResult" class="mt-2 text-[11px] text-slate-500 whitespace-pre-wrap bg-slate-50 dark:bg-slate-900 rounded p-2 border border-slate-200 dark:border-slate-700">
          <div v-if="runResult.deadlocked" class="text-amber-500 mb-1">⚠️ 检测到依赖死锁，不满足的步骤已跳过</div>
          <div class="font-medium text-slate-400 mb-1">执行序（{{ runResult.exec_order?.length || 0 }} 步）</div>
          {{ runResult.summary }}
        </div>
      </div>
    </aside>

    <!-- 触发器弹层 -->
    <div v-if="showTrigger" class="fixed inset-0 z-30 flex items-center justify-center bg-black/40" @click.self="showTrigger = false">
      <div class="w-[520px] max-w-[92vw] max-h-[86vh] overflow-y-auto bg-white dark:bg-slate-800 rounded-xl shadow-2xl border border-slate-200 dark:border-slate-700">
        <div class="flex items-center justify-between px-4 py-3 border-b border-slate-200 dark:border-slate-700">
          <h3 class="font-semibold">配置触发器 · {{ current.name || '（未命名）' }}</h3>
          <button @click="showTrigger = false" class="text-slate-400 hover:text-slate-600 text-lg leading-none">✕</button>
        </div>
        <div class="flex gap-1 p-2 border-b border-slate-200 dark:border-slate-700">
          <button @click="triggerTab = 'cron'" class="flex-1 text-xs py-1.5 rounded"
            :class="triggerTab === 'cron' ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-700'">⏰ 定时任务</button>
          <button @click="triggerTab = 'webhook'" class="flex-1 text-xs py-1.5 rounded"
            :class="triggerTab === 'webhook' ? 'bg-blue-600 text-white' : 'bg-slate-100 dark:bg-slate-700'">🔗 Webhook</button>
        </div>

        <!-- cron -->
        <div v-show="triggerTab === 'cron'" class="p-4 space-y-3">
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">Cron 表达式</label>
            <input v-model="cronForm.schedule" placeholder="0 9 * * *" class="w-full text-sm px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
            <p class="text-[10px] text-slate-400 mt-1">示例：<code>0 9 * * *</code> 每天 9:00 · <code>*/15 * * * *</code> 每 15 分钟</p>
          </div>
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">任务名（可选）</label>
            <input v-model="cronForm.name" placeholder="默认：wf-&lt;工作流名&gt;" class="w-full text-sm px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400" />
          </div>
          <div>
            <label class="block text-[11px] text-slate-400 mb-1">提示词（可选）</label>
            <textarea v-model="cronForm.prompt" rows="2" placeholder="留空则仅按工作流步骤执行" class="w-full text-xs px-2 py-1.5 rounded-md bg-slate-50 dark:bg-slate-900 border border-slate-300 dark:border-slate-600 outline-none focus:border-blue-400 resize-none"></textarea>
          </div>
          <button @click="createCron" :disabled="creatingCron" class="w-full text-xs px-2 py-2 rounded-md bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white transition">＋ 创建定时任务（绑定本工作流）</button>
          <div v-if="cronError" class="text-[11px] text-red-500">{{ cronError }}</div>

          <div class="pt-2 border-t border-slate-200 dark:border-slate-700">
            <div class="text-[11px] text-slate-400 mb-1">已绑定本工作流的定时任务</div>
            <div v-if="!boundCron.length" class="text-[11px] text-slate-400">暂无</div>
            <div v-for="j in boundCron" :key="j.id || j.name" class="flex items-center justify-between text-xs bg-slate-50 dark:bg-slate-900 rounded px-2 py-1.5 mt-1">
              <span class="truncate">{{ j.schedule }} · {{ j.name || j.id }}</span>
              <button @click="removeCron(j)" class="text-red-500 hover:text-red-600 shrink-0 ml-2">删除</button>
            </div>
          </div>
        </div>

        <!-- webhook -->
        <div v-show="triggerTab === 'webhook'" class="p-4 space-y-3">
          <p class="text-xs text-slate-400">向你的网关 webhook 路由发送以下请求即可触发本工作流运行（HMAC-SHA256 签名校验，测试模式可关闭）。</p>
          <pre class="text-[11px] bg-slate-900 text-slate-100 rounded-lg p-3 overflow-x-auto">{{ webhookCurl }}</pre>
          <button @click="copyWebhook" class="text-xs px-3 py-1.5 rounded-md bg-slate-100 dark:bg-slate-700 hover:bg-slate-200 dark:hover:bg-slate-600 transition">📋 复制 curl</button>
          <p class="text-[10px] text-slate-400">注：<code>&lt;route_name&gt;</code> 为你在网关设置中配置的 webhook 路由名；<code>X-Signature</code> 为 raw body 的 HMAC-SHA256（hex）。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import api from '@/services/api'

const NODE_W = 224
const NODE_H = 88

const svgRef = ref(null)
const workflows = ref([])
const current = reactive({ name: '', description: '', steps: [] })
const selectedId = ref('')
const pan = reactive({ x: 48, y: 24 })
const zoom = ref(1)
const dragging = ref(null)
const panning = ref(null)
const connecting = reactive({ active: false, from: null, x: 0, y: 0 })

const saving = ref(false)
const saveError = ref('')
const saveOk = ref('')
const running = ref(false)
const runPrompt = ref('')
const runConcurrent = ref(false)
const runResult = ref(null)
const runError = ref('')
const doneSteps = reactive(new Set())

const showTrigger = ref(false)
const triggerTab = ref('cron')
const cronForm = reactive({ schedule: '0 9 * * *', name: '', prompt: '' })
const creatingCron = ref(false)
const cronError = ref('')
const boundCron = ref([])

const selected = computed(() => current.steps.find(s => s.id === selectedId.value) || null)

const findStep = (id) => current.steps.find(s => s.id === id)
const trunc = (s, n) => (s && s.length > n) ? s.slice(0, n) + '…' : (s || '')

// ── 连线（依赖边）──
const edges = computed(() => {
  const out = []
  for (const s of current.steps) {
    for (const d of (s.dependencies || [])) {
      const src = findStep(d)
      if (!src) continue
      const x1 = src.x + NODE_W, y1 = src.y + NODE_H / 2
      const x2 = s.x, y2 = s.y + NODE_H / 2
      const mx = (x1 + x2) / 2
      out.push({ d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`, active: doneSteps.has(d) && doneSteps.has(s.id) })
    }
  }
  return out
})

const tempEdge = computed(() => {
  const src = findStep(connecting.from)
  if (!src) return ''
  const x1 = src.x + NODE_W, y1 = src.y + NODE_H / 2
  const x2 = connecting.x, y2 = connecting.y
  const mx = (x1 + x2) / 2
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`
})

// ── 坐标转换 ──
function toCanvas(e) {
  const rect = svgRef.value.getBoundingClientRect()
  return {
    x: (e.clientX - rect.left - pan.x) / zoom.value,
    y: (e.clientY - rect.top - pan.y) / zoom.value,
  }
}

function onNodeDown(e, step) {
  e.stopPropagation()
  const c = toCanvas(e)
  dragging.value = { id: step.id, sx: c.x, sy: c.y, ox: step.x, oy: step.y }
  selectedId.value = step.id
}
function onPortDown(e, step) {
  e.stopPropagation()
  const c = toCanvas(e)
  connecting.active = true
  connecting.from = step.id
  connecting.x = c.x
  connecting.y = c.y
  selectedId.value = step.id
}
function onCanvasDown(e) {
  panning.value = { sx: e.clientX, sy: e.clientY, px: pan.x, py: pan.y }
}
function onWheel(e) {
  if (!(e.ctrlKey || e.metaKey)) return
  const delta = e.deltaY > 0 ? -0.1 : 0.1
  zoom.value = Math.max(0.3, Math.min(2.5, zoom.value + delta))
}

function onMove(e) {
  if (dragging.value) {
    const c = toCanvas(e); const d = dragging.value; const st = findStep(d.id)
    if (st) { st.x = d.ox + (c.x - d.sx); st.y = d.oy + (c.y - d.sy) }
  } else if (connecting.active) {
    const c = toCanvas(e); connecting.x = c.x; connecting.y = c.y
  } else if (panning.value) {
    const p = panning.value; pan.x = p.px + (e.clientX - p.sx); pan.y = p.py + (e.clientY - p.sy)
  }
}
function onUp(e) {
  if (connecting.active) {
    const el = document.elementFromPoint(e.clientX, e.clientY)
    const nodeEl = el?.closest('[data-step-id]')
    const targetId = nodeEl?.getAttribute('data-step-id')
    if (targetId && targetId !== connecting.from) {
      const target = findStep(targetId)
      const source = findStep(connecting.from)
      if (target && source && !target.dependencies.includes(source.id) && !wouldCycle(source.id, target.id)) {
        target.dependencies.push(source.id)
      }
    }
    connecting.active = false
  }
  dragging.value = null
  panning.value = null
}

// target 依赖 source 是否会产生环：source 是否已（传递）依赖 target
function wouldCycle(sourceId, targetId) {
  const visited = new Set(); const stack = [sourceId]
  while (stack.length) {
    const cur = stack.pop()
    if (cur === targetId) return true
    if (visited.has(cur)) continue
    visited.add(cur)
    const st = findStep(cur)
    if (st) for (const d of (st.dependencies || [])) stack.push(d)
  }
  return false
}

// ── 自动布局：按依赖深度分层 ──
function depthOf(id, seen = new Set()) {
  if (seen.has(id)) return 0
  seen.add(id)
  const st = findStep(id)
  if (!st || !st.dependencies.length) return 0
  let max = 0
  for (const d of st.dependencies) max = Math.max(max, depthOf(d, new Set(seen)) + 1)
  return max
}
function autoLayout() {
  const layers = {}
  for (const s of current.steps) {
    const d = depthOf(s.id)
    ;(layers[d] = layers[d] || []).push(s)
  }
  let col = 0
  for (const k of Object.keys(layers).sort((a, b) => a - b)) {
    const list = layers[k]
    list.forEach((s, i) => { s.x = 48 + col * (NODE_W + 80); s.y = 40 + i * (NODE_H + 36) })
    col++
  }
}
function resetView() { pan.x = 48; pan.y = 24; zoom.value = 1 }

// ── 步骤操作 ──
function nextStepId() {
  let i = current.steps.length + 1
  while (current.steps.some(s => s.id === `step_${i}`)) i++
  return `step_${i}`
}
function addStep() {
  const id = nextStepId()
  current.steps.push({ id, title: '新步骤', description: '', deliverable: '', done_when: '', dependencies: [], x: 120 + current.steps.length * 30, y: 80 + current.steps.length * 30 })
  selectedId.value = id
}
function removeStep(id) {
  const idx = current.steps.findIndex(s => s.id === id)
  if (idx < 0) return
  current.steps.splice(idx, 1)
  for (const s of current.steps) s.dependencies = s.dependencies.filter(d => d !== id)
  doneSteps.delete(id)
  if (selectedId.value === id) selectedId.value = ''
}
function toggleDep(depId, on) {
  const s = selected.value
  if (!s) return
  if (on) { if (!s.dependencies.includes(depId) && depId !== s.id && !wouldCycle(depId, s.id)) s.dependencies.push(depId) }
  else { s.dependencies = s.dependencies.filter(d => d !== depId) }
}

// ── 列表 / 加载 / 保存 / 删除 ──
async function loadList() {
  try { workflows.value = await api.listWorkflows() || [] }
  catch (e) { workflows.value = [] }
}
async function loadWorkflow(name) {
  const t = await api.getWorkflow(name)
  current.name = t.name
  current.description = t.description || ''
  current.steps = (t.steps || []).map(s => ({ ...s, x: s.x || 0, y: s.y || 0, dependencies: s.dependencies || [] }))
  if (current.steps.some(s => !s.x && !s.y)) autoLayout()
  selectedId.value = current.steps[0]?.id || ''
  doneSteps.clear()
  runResult.value = null
}
function newWorkflow() {
  current.name = '新工作流-' + (workflows.value.length + 1)
  current.description = ''
  current.steps = [{ id: 'step_1', title: '第一步', description: '', deliverable: '', done_when: '', dependencies: [], x: 48, y: 80 }]
  selectedId.value = 'step_1'
  doneSteps.clear(); runResult.value = null
}
async function saveWorkflow() {
  saveError.value = ''; saveOk.value = ''
  if (!current.name.trim()) { saveError.value = '请填写工作流名称'; return }
  if (!current.steps.length) { saveError.value = '至少需要一个步骤'; return }
  const ids = new Set(current.steps.map(s => s.id))
  for (const s of current.steps) for (const d of s.dependencies) if (!ids.has(d)) { saveError.value = `步骤「${s.id}」依赖未知步骤「${d}」`; return }
  saving.value = true
  try {
    const res = await api.saveWorkflow({
      name: current.name.trim(),
      description: current.description,
      steps: current.steps.map(s => ({ id: s.id, title: s.title, description: s.description, deliverable: s.deliverable, done_when: s.done_when, dependencies: s.dependencies, x: Math.round(s.x), y: Math.round(s.y) })),
    })
    saveOk.value = res.version
    await loadList()
  } catch (e) { saveError.value = e?.detail || e?.message || '保存失败' }
  finally { saving.value = false }
}
async function deleteWorkflow(name) {
  if (!confirm(`确认删除工作流「${name}」？`)) return
  await api.deleteWorkflow(name)
  if (current.name === name) { current.name = ''; current.steps = []; selectedId.value = '' }
  await loadList()
}

// ── 运行 ──
async function runWorkflow() {
  if (!current.name.trim()) return
  runError.value = ''; running.value = true; doneSteps.clear()
  try {
    await saveWorkflow()
    const res = await api.runWorkflow(current.name.trim(), { prompt: runPrompt.value || undefined, concurrent: runConcurrent.value })
    runResult.value = res
    for (const id of (res.exec_order || [])) doneSteps.add(id)
  } catch (e) {
    runError.value = (e?.detail || e?.message || '运行失败') + '\n（若提示凭证/模型错误，请检查本地模型配置）'
  } finally { running.value = false }
}

// ── 触发器 ──
async function openTrigger() {
  if (!current.name.trim()) { saveError.value = '请先保存工作流再配置触发器'; return }
  await saveWorkflow()
  triggerTab.value = 'cron'
  refreshBoundCron()
  showTrigger.value = true
}
async function refreshBoundCron() {
  try {
    const jobs = await api.listCronJobs() || []
    boundCron.value = jobs.filter(j => j.workflow === current.name.trim())
  } catch { boundCron.value = [] }
}
async function createCron() {
  cronError.value = ''
  if (!cronForm.schedule.trim()) { cronError.value = '请填写 cron 表达式'; return }
  creatingCron.value = true
  try {
    await api.createCronJob({
      name: cronForm.name || `wf-${current.name.trim()}`,
      prompt: cronForm.prompt || '',
      schedule: cronForm.schedule.trim(),
      deliver: 'local',
      workflow: current.name.trim(),
    })
    cronForm.name = ''; cronForm.prompt = ''
    await refreshBoundCron()
  } catch (e) { cronError.value = e?.detail || e?.message || '创建失败' }
  finally { creatingCron.value = false }
}
async function removeCron(j) {
  if (j.id) await api.deleteCronJob(j.id)
  await refreshBoundCron()
}
const webhookCurl = computed(() => {
  const host = (typeof window !== 'undefined' && window.location.origin) || 'https://<your-host>'
  const body = JSON.stringify({ type: 'run_workflow', workflow: current.name || '<name>' })
  return `curl -X POST '${host}/webhooks/<route_name>' \\\n  -H 'Content-Type: application/json' \\\n  -H 'X-Signature: sha256=<hmac>' \\\n  -d '${body}'`
})
function copyWebhook() {
  if (navigator.clipboard) navigator.clipboard.writeText(webhookCurl.value)
}

onMounted(() => { loadList(); window.addEventListener('mousemove', onMove); window.addEventListener('mouseup', onUp) })
onUnmounted(() => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) })
</script>
