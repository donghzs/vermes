<script setup>
// 3D Studio Pro — 小白+AI 协同建模工作站
//
// 布局（对标 Fusion 360）：
// - 顶部：工具栏（视图切换 / 测量 / 剖切 / 线框 / 下载 / 上传）
// - 左侧：会话列表 + 特征树
// - 中央：3D 视口（全屏铺满，面板浮在上面不遮挡）
// - 右侧：可折叠参数面板 + AI 协助（默认折叠，点击展开浮层）
// - 底部：历史时间线

import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import ModelViewer from './ModelViewer.vue'

const router = useRouter()
const route = useRoute()

// ── 状态 ──
const sessions = ref([])
const loading = ref(false)
const error = ref('')
const selectedSession = ref(null)
const selectedFile = ref(null)

// ── 工具栏 ──
const viewMode = ref('perspective') // perspective/front/top/side/iso
const tool = ref('none') // none/measure/section/pick
const sectionPlane = ref('none') // none/xy/yz/xz
const wireframe = ref(false)
const autoRotate = ref(false)
const showGrid = ref(true)

// ── 面板折叠 ──
const rightPanelOpen = ref(true) // 默认展开（用户需要看到参数）
const leftPanelOpen = ref(true)

// ── 模型包围盒（从 ModelViewer 接收） ──
const modelBBox = ref(null) // {length_mm, width_mm, height_mm, volume_mm3}

// ── 参数 ──
const paramsForSession = ref([])
const paramValues = ref({})
const paramInputs = ref({}) // 精确数值输入
const rebuilding = ref(false)
const rebuildMsg = ref('')
let rebuildTimer = null

// ── 参数联动 ──
const linkages = ref({}) // {INNER_DIAMETER: {from: 'OUTER_DIAMETER', formula: (v) => v - 2 * WALL_THICKNESS}}

// ── AI 协助 ──
const aiPrompt = ref('')
const aiThinking = ref(false)
const aiHistory = ref([])

// ── 测量结果 ──
const measureResult = ref(null)

// ── 拾取/上下文工具栏（Phase B） ──
const pickedInfo = ref(null) // {point, face, normal}
const contextMenu = ref(null) // {x, y, visible}
const contextActions = ref([])
const editingDimension = ref(null) // {name, currentValue, newValue}
const showDimEditor = ref(false)

// ── 模型尺寸标注（3D 视口上浮层） ──
const dimensionLabels = ref([]) // [{text, x, y}]

// ── Phase D: 导出菜单 / 工程图 / BOM / 打印建议 ──
const showExportMenu = ref(false)
const showDrawing = ref(false)
const drawingUrl = ref('')
const drawingDims = ref(null)
const showBom = ref(false)
const bomData = ref(null)
const bomLoading = ref(false)
const showPrintAdvice = ref(false)
const printAdvice = ref(null)
const printLoading = ref(false)
const drawingLoading = ref(false)

async function generateDrawing() {
  if (!selectedSession.value) return
  showExportMenu.value = false
  drawingLoading.value = true
  showDrawing.value = true
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${selectedSession.value.session_id}/drawing`)
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.error || `HTTP ${resp.status}`)
    }
    const data = await resp.json()
    drawingUrl.value = data.drawing_url
    drawingDims.value = data.dimensions
  } catch (e) {
    error.value = `工程图生成失败: ${e.message}`
    showDrawing.value = false
  } finally {
    drawingLoading.value = false
  }
}

async function generateBom() {
  if (!selectedSession.value) return
  showExportMenu.value = false
  bomLoading.value = true
  showBom.value = true
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${selectedSession.value.session_id}/bom`)
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.error || `HTTP ${resp.status}`)
    }
    bomData.value = await resp.json()
  } catch (e) {
    error.value = `BOM 生成失败: ${e.message}`
    showBom.value = false
  } finally {
    bomLoading.value = false
  }
}

async function generatePrintAdvice() {
  if (!selectedSession.value) return
  showExportMenu.value = false
  printLoading.value = true
  showPrintAdvice.value = true
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${selectedSession.value.session_id}/print-advice`)
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}))
      throw new Error(err.error || `HTTP ${resp.status}`)
    }
    printAdvice.value = await resp.json()
  } catch (e) {
    error.value = `打印建议生成失败: ${e.message}`
    showPrintAdvice.value = false
  } finally {
    printLoading.value = false
  }
}

// 简易 markdown 渲染（避免引重依赖）
function renderMarkdown(md) {
  if (!md) return ''
  return md
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/^## (.+)$/gm, '<h2 class="text-sm font-medium mt-3 mb-1">$1</h2>')
    .replace(/^### (.+)$/gm, '<h3 class="text-xs font-medium mt-2 mb-1">$1</h3>')
    .replace(/\|(.+)\|/g, (m) => '<table class="w-full text-xs my-2">' + m.split('|').filter(Boolean).map((c, i) => `<tr><td class="py-0.5 px-1 ${i === 0 ? 'font-medium text-gray-400' : ''}">${c.trim()}</td></tr>`).join('') + '</table>')
    .replace(/^\d+\. (.+)$/gm, '<li class="ml-4 text-xs">$1</li>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 text-xs">$1</li>')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>')
}

// ── 时间线 ──
const timeline = ref([]) // [{ts, action, detail}]

// ── 上传 ──
const uploadRef = ref(null)
const uploadMsg = ref('')

// ── 会话列表 ──
async function loadSessions() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetch('/api/mfgcad/sessions')
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    sessions.value = data.sessions || []
    const firstOk = sessions.value.find(s => s.ok && Object.keys(s.files || {}).length > 0)
    if (firstOk && !selectedSession.value) {
      selectSession(firstOk)
    }
  } catch (e) {
    error.value = `加载失败: ${e.message}`
  } finally {
    loading.value = false
  }
}

function selectSession(s) {
  selectedSession.value = s
  selectedFile.value = null
  const files = availableFiles.value
  if (files.length > 0) {
    selectedFile.value = files.find(f => f.ext === 'stl') || files[0]
  }
  loadParameters(s)
  loadTimeline(s)
  aiHistory.value = []
  measureResult.value = null
}

// ── 删除会话 ──
async function deleteSession(sid) {
  if (!sid) return
  if (!confirm('删除该设计会话及其全部产物（STEP/STL 等）？此操作不可恢复。')) return
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${sid}`, { method: 'DELETE' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    if (selectedSession.value?.session_id === sid) {
      selectedSession.value = null
      selectedFile.value = null
      timeline.value = []
    }
    await loadSessions()
  } catch (e) {
    error.value = `删除失败: ${e.message}`
  }
}

async function clearAllSessions() {
  if (!confirm('清空全部设计会话？此操作不可恢复。')) return
  try {
    const resp = await fetch('/api/mfgcad/sessions', { method: 'DELETE' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    selectedSession.value = null
    selectedFile.value = null
    timeline.value = []
    await loadSessions()
  } catch (e) {
    error.value = `清空失败: ${e.message}`
  }
}

const availableFiles = computed(() => {
  if (!selectedSession.value?.files) return []
  return Object.entries(selectedSession.value.files).map(([key, url]) => {
    const filename = url.split('/').pop()
    const ext = filename.split('.').pop().toLowerCase()
    return { key, label: key.toUpperCase(), url, ext, filename }
  }).filter(f => f.ext !== 'step') // 视口优先 STL
})

function selectFile(f) {
  selectedFile.value = f
}

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`
}

function fmtVolume(v) {
  if (v == null) return ''
  return `${v.toFixed(1)} mm³`
}

function fmtDistance(d) {
  if (d == null) return ''
  if (d < 1) return `${(d * 1000).toFixed(1)} μm`
  if (d < 10) return `${d.toFixed(2)} mm`
  return `${d.toFixed(1)} mm`
}

function goChat() { router.push('/') }

// ── 参数加载 ──
async function loadParameters(s) {
  paramsForSession.value = []
  paramValues.value = {}
  paramInputs.value = {}
  rebuildMsg.value = ''
  if (!s?.has_parameters) return
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${s.session_id}/parameters`)
    if (!resp.ok) return
    const data = await resp.json()
    const ps = data.parameters || {}
    const arr = Object.entries(ps).map(([name, p]) => ({
      name,
      label: p.label || name,
      value: p.value,
      min: p.min,
      max: p.max,
      step: p.step || (p.max - p.min) / 100,
      unit: p.unit || '',
      presets: guessPresets(name, p),
    }))
    paramsForSession.value = arr
    const sv = {}
    const si = {}
    for (const p of arr) {
      sv[p.name] = p.value
      si[p.name] = String(p.value)
    }
    paramValues.value = sv
    paramInputs.value = si

    // 自动推断联动关系
    inferLinkages(arr)
  } catch (e) {
    // 无参数则无面板
  }
}

// ── 参数分组与预设 ──
const collapsedGroups = ref({})

function guessParamGroup(name) {
  const n = name.toUpperCase()
  if (n.includes('THREAD') || n.includes('HEX') || n.includes('NUT')) return '螺纹/六角'
  if (n.includes('CHAMFER') || n.includes('FILLET') || n.includes('ROUND')) return '倒角/圆角'
  if (n.includes('BASE') || n.includes('_BASE_')) return '基座'
  if (n.includes('WALL') || n.includes('THICKNESS') || n.includes('BOTTOM')) return '壁厚/底厚'
  if (n.includes('DIAMETER') || n.includes('RADIUS') || n.includes('HEIGHT') || n.includes('WIDTH') || n.includes('LENGTH') || n.includes('DEPTH')) return '主尺寸'
  return '其他'
}

function guessPresets(name, p) {
  const n = name.toUpperCase()
  const presets = []
  if (n.includes('WALL_THICKNESS')) presets.push(2, 3, 5)
  else if (n.includes('HEIGHT') && p.max > 50) presets.push(50, 100, 150)
  else if (n.includes('DIAMETER') && p.max > 20) presets.push(10, 30, 60)
  else if (n.includes('CHAMFER') && n.includes('ANGLE')) presets.push(30, 45)
  else if (n.includes('BOTTOM_THICKNESS')) presets.push(2, 3, 5)
  // 去超出范围的
  return presets.filter(v => v >= p.min && v <= p.max).slice(0, 3)
}

const groupedParams = computed(() => {
  const groups = {}
  for (const p of paramsForSession.value) {
    const g = guessParamGroup(p.name)
    if (!groups[g]) groups[g] = []
    groups[g].push(p)
  }
  return groups
})

function toggleParamGroup(gName) {
  collapsedGroups.value[gName] = !collapsedGroups.value[gName]
}

// ── 参数联动推断 ──
function inferLinkages(params) {
  const links = {}
  // 常见模式：INNER_* = OUTER_* - 2 * WALL_*
  const outerD = params.find(p => p.name.includes('OUTER_DIAMETER') || p.name === 'OUTER_DIAMETER')
  const innerD = params.find(p => p.name.includes('INNER_DIAMETER') || p.name === 'INNER_DIAMETER')
  const wallT = params.find(p => p.name.includes('WALL_THICKNESS') || p.name === 'WALL_THICKNESS')
  if (outerD && innerD && wallT) {
    links['INNER_DIAMETER'] = {
      depends: ['OUTER_DIAMETER', 'WALL_THICKNESS'],
      compute: (vals) => vals['OUTER_DIAMETER'] - 2 * vals['WALL_THICKNESS'],
    }
  }
  const outerR = params.find(p => p.name.includes('OUTER_RADIUS'))
  const innerR = params.find(p => p.name.includes('INNER_RADIUS'))
  if (outerR && innerR && wallT) {
    links['INNER_RADIUS'] = {
      depends: ['OUTER_RADIUS', 'WALL_THICKNESS'],
      compute: (vals) => vals['OUTER_RADIUS'] - vals['WALL_THICKNESS'],
    }
  }
  linkages.value = links
}

function onParamChange(name, value) {
  paramValues.value[name] = value
  paramInputs.value[name] = String(value)

  // 联动
  for (const [target, link] of Object.entries(linkages.value)) {
    if (link.depends.includes(name)) {
      const newVal = link.compute(paramValues.value)
      if (newVal != null && !isNaN(newVal)) {
        paramValues.value[target] = newVal
        paramInputs.value[target] = String(newVal.toFixed(2))
      }
    }
  }

  scheduleRebuild()
}

function onParamInput(name, rawValue) {
  const v = parseFloat(rawValue)
  if (isNaN(v)) return
  onParamChange(name, v)
}

function scheduleRebuild() {
  rebuildMsg.value = '⏳ 预览中…松手后自动重建'
  if (rebuildTimer) clearTimeout(rebuildTimer)
  rebuildTimer = setTimeout(() => rebuild(), 600)
}

async function rebuild() {
  const sid = selectedSession.value?.session_id
  if (!sid || !selectedSession.value?.has_parameters || rebuilding.value) return
  rebuilding.value = true
  rebuildMsg.value = '重建中…'
  try {
    const resp = await fetch(`/api/mfgcad/sessions/${sid}/rebuild`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ parameters: paramValues.value }),
    })
    const data = await resp.json()
    rebuildMsg.value = data.message || '重建完成'
    if (data.child_session && data.child_session.ok) {
      // 记录参数变化摘要
      const changes = []
      for (const [k, v] of Object.entries(paramValues.value)) {
        const orig = paramsForSession.value.find(p => p.name === k)
        if (orig && Math.abs(orig.value - v) > 0.01) {
          const label = orig.label || k
          changes.push(`${label}: ${orig.value}→${typeof v === 'number' ? v.toFixed(1) : v}`)
        }
      }
      const summary = changes.length ? changes.join(', ') : '参数微调'
      timeline.value.unshift({ ts: Date.now(), action: summary, detail: rebuildMsg.value })
      await loadSessions()
      const child = sessions.value.find(x => x.session_id === data.child_session.session_id)
      if (child) selectSession(child)
    }
  } catch (e) {
    rebuildMsg.value = `重建失败: ${e.message}`
  } finally {
    rebuilding.value = false
  }
}

// ── AI 协助 ──
async function aiAssist() {
  const prompt = aiPrompt.value.trim()
  if (!prompt || aiThinking.value) return
  const sid = selectedSession.value?.session_id
  if (!sid) return

  aiThinking.value = true
  aiHistory.value.push({ role: 'user', text: prompt })
  aiPrompt.value = ''

  try {
    const resp = await fetch(`/api/mfgcad/sessions/${sid}/ai-assist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    })
    const data = await resp.json()
    aiHistory.value.push({ role: 'assistant', text: data.message || '处理完成' })
    if (data.child_session || data.rebuilt) {
      timeline.value.unshift({ ts: Date.now(), action: 'AI 修改', detail: prompt })
      await loadSessions()
      const child = sessions.value.find(x => x.session_id === (data.child_session?.session_id || sid))
      if (child) selectSession(child)
    }
  } catch (e) {
    aiHistory.value.push({ role: 'assistant', text: `❌ ${e.message}` })
  } finally {
    aiThinking.value = false
  }
}

// ── AI 上下文建议（Phase C） ──
const aiSuggestions = ref([])

function generateAISuggestions() {
  if (!selectedSession.value) return
  const s = selectedSession.value
  const suggestions = []

  if (s.volume_mm3 && s.volume_mm3 < 1000) {
    suggestions.push({ icon: '💡', text: '模型较小，建议增加壁厚', action: 'wall_thickness' })
  }
  if (s.qa && s.qa.warnings) {
    for (const w of (Array.isArray(s.qa.warnings) ? s.qa.warnings : [])) {
      if (w.includes('wall') || w.includes('壁')) {
        suggestions.push({ icon: '⚠️', text: '壁厚偏薄，建议加到 2mm 以上', action: 'wall_thickness' })
      }
      if (w.includes('hole') || w.includes('孔')) {
        suggestions.push({ icon: '⚠️', text: '孔边距偏小，建议 > 1×孔径', action: 'hole_distance' })
      }
    }
  }
  if (paramsForSession.value.length > 0) {
    suggestions.push({ icon: '🎚️', text: `${paramsForSession.value.length} 个参数可调`, action: 'params' })
  }
  suggestions.push({ icon: '🎨', text: '更换材质或颜色外观', action: 'material' })
  suggestions.push({ icon: '📐', text: '生成 2D 工程图', action: 'drawing' })
  suggestions.push({ icon: '🖨️', text: '3D 打印参数建议', action: 'print' })

  aiSuggestions.value = suggestions
}

function executeSuggestion(s) {
  switch (s.action) {
    case 'wall_thickness':
      aiPrompt.value = '将壁厚增加到 2mm，保持其他尺寸不变'
      aiAssist()
      break
    case 'hole_distance':
      aiPrompt.value = '调整孔位置使孔边距 > 1×孔径'
      aiAssist()
      break
    case 'params':
      rightPanelOpen.value = true
      break
    case 'material':
      aiPrompt.value = '更换为金属质感外观'
      aiAssist()
      break
    case 'drawing':
      aiPrompt.value = '生成 2D 工程图，含三视图和尺寸标注'
      aiAssist()
      break
    case 'print':
      aiPrompt.value = '分析模型并给出 3D 打印参数建议'
      aiAssist()
      break
  }
}

watch(selectedSession, () => {
  nextTick(() => generateAISuggestions())
}, { immediate: true })

// ── 时间线 ──
async function loadTimeline(s) {
  timeline.value = []
  if (s) {
    timeline.value.push({ ts: s.ts * 1000, action: '创建会话', detail: s.request })
    // 查找子会话
    const children = sessions.value.filter(x => x.base_session_id === s.session_id)
    for (const c of children.reverse()) {
      timeline.value.push({ ts: c.ts * 1000, action: '参数修改', detail: c.request })
    }
  }
}

// ── 上传 ──
async function onUpload(e) {
  const file = e.target.files[0]
  if (!file) return
  const formData = new FormData()
  formData.append('file', file)
  formData.append('name', file.name.replace(/\.(step|stp|stl|3mf)$/i, ''))

  try {
    const resp = await fetch('/api/mfgcad/upload', { method: 'POST', body: formData })
    const data = await resp.json()
    if (data.session_id) {
      await loadSessions()
      const newSession = sessions.value.find(x => x.session_id === data.session_id)
      if (newSession) selectSession(newSession)
    }
  } catch (e) {
    uploadMsg.value = `❌ ${e.message}`
  } finally {
    if (uploadRef.value) uploadRef.value.value = ''
  }
}

// ── 下载 ──
function downloadFile(file) {
  if (!file?.url) return
  const a = document.createElement('a')
  a.href = file.url
  a.download = file.filename || 'model'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ── 包围盒回调 ──
function onBBox(bbox) {
  modelBBox.value = bbox
}

// ── 拾取回调 ──
function onMeasure(data) {
  measureResult.value = data
}

// ── 拾取面回调（Phase B） ──
function onPick(data) {
  pickedInfo.value = data
  // 弹出上下文工具栏
  const container = document.querySelector('.model-viewer-wrapper')
  if (container) {
    const rect = container.getBoundingClientRect()
    // 用鼠标最后位置（近似）
    contextMenu.value = {
      x: data.point.x + 20,
      y: data.point.y - 20,
      visible: true,
    }
  }
  // 根据法线方向推断可用操作
  const n = data.normal
  const isFlat = Math.abs(n.y) > 0.9 || Math.abs(n.x) > 0.9 || Math.abs(n.z) > 0.9
  contextActions.value = [
    { id: 'fillet', label: '🔄 倒角/圆角', desc: '给这条边加圆角' },
    { id: 'offset', label: '⬅️ 偏移面', desc: '沿法线偏移这个面' },
    { id: 'extrude', label: '⬆️ 拉伸', desc: '沿法线拉伸这个面' },
    { id: 'cut', label: '✂️ 切割', desc: '在这个面上挖孔/切槽' },
    { id: 'measure', label: '📏 测量', desc: '测量到另一个面的距离' },
  ]
}

function closeContextMenu() {
  if (contextMenu.value) contextMenu.value.visible = false
}

function executeContextAction(action) {
  closeContextMenu()
  if (!pickedInfo.value) return

  switch (action.id) {
    case 'fillet':
      // AI 协助：用自然语言让后端加圆角
      aiPrompt.value = `给选中的面加 0.5mm 圆角（法线方向: ${pickedInfo.value.normal.toArray().map(v => v.toFixed(2)).join(',')}）`
      aiAssist()
      break
    case 'offset':
      showDimEditor.value = true
      editingDimension.value = {
        name: '偏移距离',
        currentValue: 1.0,
        newValue: 1.0,
        unit: 'mm',
        action: 'offset',
      }
      break
    case 'extrude':
      showDimEditor.value = true
      editingDimension.value = {
        name: '拉伸高度',
        currentValue: 5.0,
        newValue: 5.0,
        unit: 'mm',
        action: 'extrude',
      }
      break
    case 'cut':
      aiPrompt.value = `在选中面上挖一个直径 5mm 的通孔（面法线: ${pickedInfo.value.normal.toArray().map(v => v.toFixed(2)).join(',')}）`
      aiAssist()
      break
    case 'measure':
      tool.value = 'measure'
      break
  }
}

function confirmDimensionEdit() {
  if (!editingDimension.value || !pickedInfo.value) return
  const ed = editingDimension.value
  const val = ed.newValue
  const normal = pickedInfo.value.normal.toArray().map(v => v.toFixed(2)).join(',')

  switch (ed.action) {
    case 'offset':
      aiPrompt.value = `将选中面沿法线方向(${normal})偏移 ${val}mm`
      break
    case 'extrude':
      aiPrompt.value = `将选中面沿法线方向(${normal})拉伸 ${val}mm`
      break
  }
  showDimEditor.value = false
  aiAssist()
}

function cancelDimensionEdit() {
  showDimEditor.value = false
  editingDimension.value = null
}

// ── Tooltip 系统 ──
const tooltipVisible = ref(false)
const tooltipText = ref('')
const tooltipX = ref(0)
const tooltipY = ref(0)
let tooltipTimer = null

function showTooltip(e, text) {
  clearTimeout(tooltipTimer)
  const rect = e.currentTarget.getBoundingClientRect()
  tooltipX.value = rect.left + rect.width / 2
  tooltipY.value = rect.bottom + 6
  tooltipText.value = text
  tooltipTimer = setTimeout(() => { tooltipVisible.value = true }, 200)
}
function hideTooltip() {
  clearTimeout(tooltipTimer)
  tooltipVisible.value = false
}

// ── 工具栏切换 ──
function setView(mode) { viewMode.value = mode }
function toggleTool(t) {
  tool.value = tool.value === t ? 'none' : t
  if (tool.value !== 'measure') {
    measureResult.value = null
  }
}
function toggleSection() {
  if (sectionPlane.value === 'none') {
    sectionPlane.value = 'xy'
    tool.value = 'section'
  } else {
    sectionPlane.value = 'none'
    tool.value = 'none'
  }
}
function cycleSection() {
  const order = ['xy', 'yz', 'xz', 'none']
  const idx = order.indexOf(sectionPlane.value)
  sectionPlane.value = order[(idx + 1) % order.length]
  tool.value = sectionPlane.value === 'none' ? 'none' : 'section'
}

const hasModel = computed(() => !!selectedFile.value)

onMounted(async () => {
  await loadSessions()
  // 从右栏产物面板「在 3D 工作室打开」跳转而来：自动选中目标会话
  const sid = route.query.session
  if (sid) {
    const target = sessions.value.find(s => s.session_id === sid)
    if (target) selectSession(target)
  }
})
</script>

<template>
  <div class="flex flex-col h-screen bg-gray-100 dark:bg-gray-900 overflow-hidden">
    <!-- ═══ 顶部工具栏 ═══ -->
    <div class="flex items-center gap-1 px-3 py-2 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <!-- 左侧按钮 -->
      <button @click="leftPanelOpen = !leftPanelOpen" @mouseenter="showTooltip($event, '显示/隐藏设计会话列表')" @mouseleave="hideTooltip" class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
      </button>

      <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

      <!-- 视图切换（图标+文字） -->
      <div class="flex items-center gap-0.5">
        <button @click="setView('perspective')" @mouseenter="showTooltip($event, '透视图：自由角度3D视角，可旋转缩放')" @mouseleave="hideTooltip" class="px-2 py-1 text-xs rounded transition" :class="viewMode === 'perspective' ? 'bg-green-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">🏠 透视</button>
        <button @click="setView('front')" @mouseenter="showTooltip($event, '正视图：从正面看模型，显示长×高')" @mouseleave="hideTooltip" class="px-2 py-1 text-xs rounded transition" :class="viewMode === 'front' ? 'bg-green-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">⬛ 前视</button>
        <button @click="setView('top')" @mouseenter="showTooltip($event, '俯视图：从上方看模型，显示长×宽')" @mouseleave="hideTooltip" class="px-2 py-1 text-xs rounded transition" :class="viewMode === 'top' ? 'bg-green-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">⬛ 顶视</button>
        <button @click="setView('side')" @mouseenter="showTooltip($event, '侧视图：从侧面看模型，显示宽×高')" @mouseleave="hideTooltip" class="px-2 py-1 text-xs rounded transition" :class="viewMode === 'side' ? 'bg-green-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">⬛ 侧视</button>
        <button @click="setView('iso')" @mouseenter="showTooltip($event, '等轴测图：30°标准工程视角')" @mouseleave="hideTooltip" class="px-2 py-1 text-xs rounded transition" :class="viewMode === 'iso' ? 'bg-green-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">📐 等轴</button>
      </div>

      <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

      <!-- 工具按钮（图标+文字） -->
      <button @click="toggleTool('pick')" @mouseenter="showTooltip($event, '选择面：点击模型表面高亮选中，可后续操作')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition" :class="tool === 'pick' ? 'bg-orange-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/></svg> 选择
      </button>
      <button @click="toggleTool('measure')" @mouseenter="showTooltip($event, '测量距离：点击模型上两个点，显示直线距离(mm)')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition" :class="tool === 'measure' ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.3 8.7L8.7 21.3a1 1 0 0 1-1.4 0l-4.6-4.6a1 1 0 0 1 0-1.4L15.3 2.7a1 1 0 0 1 1.4 0l4.6 4.6a1 1 0 0 1 0 1.4z"/><line x1="14" y1="6" x2="18" y2="10"/></svg> 测量
      </button>
      <button @click="cycleSection()" @mouseenter="showTooltip($event, '剖视图：用切平面切开模型看内部结构')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition" :class="tool === 'section' ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="9" width="20" height="6" rx="1"/><line x1="2" y1="12" x2="22" y2="12" stroke-dasharray="3 3"/></svg> 剖切
      </button>
      <button @click="wireframe = !wireframe" @mouseenter="showTooltip($event, '线框模式：只显示模型网格线，看内部结构')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition" :class="wireframe ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg> 线框
      </button>
      <button @click="autoRotate = !autoRotate" @mouseenter="showTooltip($event, '自动旋转：模型缓慢360°旋转展示')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition" :class="autoRotate ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7"/><polyline points="21 3 21 9 15 9"/></svg> 旋转
      </button>
      <button @click="showExportMenu = !showExportMenu" @mouseenter="showTooltip($event, '导出：生成2D工程图、BOM物料清单、3D打印参数建议')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded transition hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> 导出
      </button>

      <div class="w-px h-5 bg-gray-200 dark:bg-gray-600 mx-1"></div>

      <!-- 文件操作 -->
      <button @click="uploadRef?.click()" @mouseenter="showTooltip($event, '打开本地STEP/STL/3MF文件进行查看和编辑')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded bg-blue-50 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 hover:bg-blue-100">📂 打开</button>
      <input ref="uploadRef" type="file" accept=".step,.stp,.stl,.3mf" @change="onUpload" class="hidden" />
      <button v-if="selectedFile" @click="downloadFile(selectedFile)" @mouseenter="showTooltip($event, '下载当前查看的文件到本地')" @mouseleave="hideTooltip" class="flex items-center gap-1 px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200">⬇️ 下载</button>

      <!-- 右侧 -->
      <div class="ml-auto flex items-center gap-2">
        <!-- 剖切平面指示 -->
        <span v-if="sectionPlane !== 'none'" class="text-xs text-blue-500">剖切: {{ sectionPlane.toUpperCase() }}</span>
        <!-- 测量结果 -->
        <span v-if="measureResult" class="text-xs text-yellow-600 bg-yellow-50 dark:bg-yellow-900/20 px-2 py-0.5 rounded">
          📏 {{ fmtDistance(measureResult.distance) }}
        </span>
        <button @click="rightPanelOpen = !rightPanelOpen" @mouseenter="showTooltip($event, '显示/隐藏右侧参数面板和AI协助')" @mouseleave="hideTooltip" class="p-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="15" y1="3" x2="15" y2="21"/></svg>
        </button>
        <button @click="goChat" @mouseenter="showTooltip($event, '返回对话页面，用自然语言描述需求让AI建模')" @mouseleave="hideTooltip" class="text-sm text-gray-500 hover:text-gray-700 px-2">← 对话</button>
      </div>
    </div>

    <!-- Tooltip 浮层 -->
    <div
      v-if="tooltipVisible"
      class="fixed z-50 px-2.5 py-1.5 text-xs text-white bg-gray-800 dark:bg-gray-700 rounded shadow-lg pointer-events-none whitespace-nowrap max-w-xs"
      :style="{ left: tooltipX + 'px', top: tooltipY + 'px', transform: 'translateX(-50%)' }"
    >{{ tooltipText }}</div>

    <!-- ═══ 主体区域 ═══ -->
    <div class="flex flex-1 overflow-hidden relative">
      <!-- 左侧：会话列表 -->
      <div v-if="leftPanelOpen" class="w-56 border-r border-gray-200 dark:border-gray-700 overflow-y-auto bg-white dark:bg-gray-800 flex-shrink-0">
        <div class="px-3 py-2 text-xs font-medium text-gray-400 border-b border-gray-100 dark:border-gray-700 flex items-center justify-between">
          <span>设计会话</span>
          <button v-if="sessions.length" @click="clearAllSessions()" @mouseenter="showTooltip($event, '清空全部设计会话（不可恢复）')" @mouseleave="hideTooltip" class="text-xs text-red-400 hover:text-red-600 px-1">清空</button>
        </div>
        <div v-if="loading" class="p-4 text-center text-sm text-gray-400">加载中…</div>
        <div v-else-if="sessions.length === 0" class="p-4 text-center text-xs text-gray-400">
          暂无会话<br>对话中描述零件或点击「打开」
        </div>
        <div v-else class="py-1">
          <div
            v-for="s in sessions"
            :key="s.session_id"
            @click="selectSession(s)"
            class="px-3 py-2 cursor-pointer border-l-2 transition"
            :class="selectedSession?.session_id === s.session_id
              ? 'border-green-500 bg-green-50 dark:bg-green-900/20'
              : 'border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/50'"
          >
            <div class="flex items-center gap-1.5 mb-0.5">
              <span class="text-xs">{{ s.ok ? '✅' : '❌' }}</span>
              <span v-if="s.has_parameters" class="text-xs text-blue-500">🎚️</span>
              <span class="text-xs text-gray-400 ml-auto">{{ fmtTime(s.ts) }}</span>
              <button @click.stop="deleteSession(s.session_id)" @mouseenter="showTooltip($event, '删除该会话及全部产物')" @mouseleave="hideTooltip" class="text-xs text-red-400 hover:text-red-600 px-1" title="删除">🗑</button>
            </div>
            <p class="text-xs text-gray-600 dark:text-gray-300 truncate">{{ s.request }}</p>
          </div>
        </div>
      </div>

      <!-- 中央：3D 视口（全屏铺满） -->
      <div class="flex-1 relative">
        <ModelViewer
          v-if="selectedFile"
          :src="selectedFile.url"
          :view-mode="viewMode"
          :tool="tool"
          :section-plane="sectionPlane"
          :wireframe="wireframe"
          :auto-rotate="autoRotate"
          :transparent-bg="true"
          @measure="onMeasure"
          @pick="onPick"
          @bbox="onBBox"
        />
        <div v-else class="flex items-center justify-center h-full">
          <div class="text-center max-w-md">
            <div class="text-6xl mb-4">🏭</div>
            <h2 class="text-lg font-medium text-gray-600 dark:text-gray-300 mb-2">3D 建模工作室</h2>
            <p class="text-sm text-gray-400 mb-6">AI 建模 + 精确改参 + 协助优化，从对话开始</p>
            <div class="flex flex-col gap-3 items-center">
              <button @click="goChat" class="px-6 py-2.5 text-sm rounded-lg bg-green-500 text-white hover:bg-green-600 transition shadow-sm">
                💬 对话建模 — 描述需求让 AI 生成
              </button>
              <button @click="uploadRef?.click()" class="px-6 py-2.5 text-sm rounded-lg bg-blue-50 dark:bg-blue-900/30 text-blue-600 hover:bg-blue-100 transition">
                📂 打开本地文件 — 查看 STEP/STL/3MF
              </button>
              <p class="text-xs text-gray-300 mt-2">提示：对话中描述「做个直径60mm高100mm壁厚3mm的笔筒」即可生成模型</p>
            </div>
          </div>
        </div>

        <!-- 导出菜单（Phase D） -->
        <div v-if="showExportMenu" class="absolute top-12 right-32 z-30 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 py-1 min-w-56">
          <div class="px-3 py-1.5 text-xs font-medium text-gray-400 border-b border-gray-100 dark:border-gray-700">导出 / 生成</div>
          <button @click="generateDrawing" class="w-full text-left px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
            📐 2D 工程图（三视图+尺寸）
          </button>
          <button @click="generateBom" class="w-full text-left px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
            📋 BOM + 组装指南
          </button>
          <button @click="generatePrintAdvice" class="w-full text-left px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
            🖨️ 3D 打印参数建议
          </button>
          <div class="border-t border-gray-100 dark:border-gray-700 my-1"></div>
          <button v-if="selectedFile" @click="downloadFile(selectedFile)" class="w-full text-left px-3 py-2 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
            💾 下载当前文件 ({{ selectedFile.ext.toUpperCase() }})
          </button>
        </div>

        <!-- 工程图弹窗（Phase D） -->
        <div v-if="showDrawing" class="absolute inset-0 z-40 bg-black/50 flex items-center justify-center" @click.self="showDrawing = false">
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-3xl max-h-[90vh] overflow-auto p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-medium text-gray-700 dark:text-gray-200">📐 2D 工程图</h3>
              <button @click="showDrawing = false" class="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div v-if="drawingLoading" class="text-center py-12 text-gray-400 text-sm">生成中…</div>
            <div v-else>
              <img :src="drawingUrl" alt="工程图" class="w-full" />
              <div v-if="drawingDims" class="mt-3 flex gap-4 text-xs text-gray-500">
                <span>长: {{ drawingDims.length_mm }}mm</span>
                <span>宽: {{ drawingDims.width_mm }}mm</span>
                <span>高: {{ drawingDims.height_mm }}mm</span>
                <span v-if="drawingDims.volume_mm3">体积: {{ drawingDims.volume_mm3 }}mm³</span>
                <button @click="downloadFile({url: drawingUrl, name: 'engineering_drawing.svg'})" class="ml-auto text-green-500 hover:underline">💾 下载 SVG</button>
              </div>
            </div>
          </div>
        </div>

        <!-- BOM 弹窗（Phase D） -->
        <div v-if="showBom" class="absolute inset-0 z-40 bg-black/50 flex items-center justify-center" @click.self="showBom = false">
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-2xl max-h-[90vh] overflow-auto p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-medium text-gray-700 dark:text-gray-200">📋 BOM + 组装指南</h3>
              <button @click="showBom = false" class="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div v-if="bomLoading" class="text-center py-12 text-gray-400 text-sm">生成中…</div>
            <div v-else-if="bomData">
              <div class="mb-3 text-xs text-gray-500 space-y-1">
                <p>📝 {{ bomData.request }}</p>
                <p>🔩 材料: {{ bomData.material }} | 体积: {{ bomData.volume_cm3 }}cm³</p>
              </div>
              <div v-if="bomData.parts?.length" class="mb-4">
                <table class="w-full text-xs">
                  <thead class="text-gray-400 border-b border-gray-200 dark:border-gray-700">
                    <tr><th class="text-left py-1">参数</th><th class="text-right py-1">值</th><th class="text-right py-1">单位</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(p, i) in bomData.parts" :key="i" class="border-b border-gray-100 dark:border-gray-700">
                      <td class="py-1 text-gray-600 dark:text-gray-300 font-mono">{{ p.label || p.name }}</td>
                      <td class="py-1 text-right text-gray-600 dark:text-gray-300">{{ p.value }}</td>
                      <td class="py-1 text-right text-gray-400">{{ p.unit }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-if="bomData.assembly_guide" class="prose prose-sm dark:prose-invert max-w-none" v-html="renderMarkdown(bomData.assembly_guide)"></div>
              <div v-else class="text-xs text-gray-400">未配置 API Key，仅显示参数清单。配置后在设置→服务中填入。</div>
            </div>
          </div>
        </div>

        <!-- 3D 打印建议弹窗（Phase D） -->
        <div v-if="showPrintAdvice" class="absolute inset-0 z-40 bg-black/50 flex items-center justify-center" @click.self="showPrintAdvice = false">
          <div class="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-md max-h-[90vh] overflow-auto p-4">
            <div class="flex items-center justify-between mb-3">
              <h3 class="text-sm font-medium text-gray-700 dark:text-gray-200">🖨️ 3D 打印参数建议</h3>
              <button @click="showPrintAdvice = false" class="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <div v-if="printLoading" class="text-center py-12 text-gray-400 text-sm">生成中…</div>
            <div v-else-if="printAdvice">
              <div class="grid grid-cols-3 gap-2 mb-3">
                <div class="bg-gray-50 dark:bg-gray-700 rounded p-2 text-center">
                  <div class="text-xs text-gray-400">体积</div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ printAdvice.volume_cm3 }}cm³</div>
                </div>
                <div class="bg-gray-50 dark:bg-gray-700 rounded p-2 text-center">
                  <div class="text-xs text-gray-400">预计用料</div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ printAdvice.estimated_weight_g }}g</div>
                </div>
                <div class="bg-gray-50 dark:bg-gray-700 rounded p-2 text-center">
                  <div class="text-xs text-gray-400">预计时间</div>
                  <div class="text-sm font-medium text-gray-700 dark:text-gray-200">{{ printAdvice.estimate_time || '-' }}</div>
                </div>
              </div>
              <ul class="space-y-1.5">
                <li v-for="(r, i) in printAdvice.recommendations" :key="i" class="text-xs text-gray-600 dark:text-gray-300 flex items-start gap-1.5">
                  <span class="text-green-500 flex-shrink-0">●</span> {{ r }}
                </li>
              </ul>
            </div>
          </div>
        </div>

        <!-- AI 建议浮层（左下，Phase C） -->
        <div v-if="aiSuggestions.length && !rightPanelOpen" class="absolute bottom-14 left-2 max-w-56">
          <div class="bg-white/90 dark:bg-gray-800/90 backdrop-blur rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 p-2">
            <div class="text-xs font-medium text-gray-400 mb-1.5">🤖 AI 建议</div>
            <div class="space-y-1">
              <button
                v-for="(s, i) in aiSuggestions.slice(0, 4)"
                :key="i"
                @click="executeSuggestion(s)"
                class="block w-full text-left text-xs px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300"
              >{{ s.icon }} {{ s.text }}</button>
            </div>
          </div>
        </div>

        <!-- 浮层：文件切换条（底部） -->
        <div v-if="availableFiles.length > 1" class="absolute bottom-12 left-1/2 -translate-x-1/2 flex items-center gap-1 px-2 py-1 rounded-lg bg-white/90 dark:bg-gray-800/90 backdrop-blur shadow-lg border border-gray-200 dark:border-gray-700">
          <button
            v-for="f in availableFiles"
            :key="f.key"
            @click="selectFile(f)"
            class="px-3 py-1 text-xs rounded transition"
            :class="selectedFile?.key === f.key ? 'bg-green-500 text-white' : 'text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-700'"
          >{{ f.label }}</button>
        </div>

        <!-- 浮层：上下文工具栏（Phase B — 选中面后弹出） -->
        <div
          v-if="contextMenu?.visible && pickedInfo"
          class="absolute z-30 bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 py-1 min-w-48"
          :style="{ top: '60px', right: '20px' }"
        >
          <div class="px-3 py-1.5 text-xs font-medium text-gray-400 border-b border-gray-100 dark:border-gray-700">
            选中面操作
          </div>
          <button
            v-for="action in contextActions"
            :key="action.id"
            @click="executeContextAction(action)"
            class="w-full text-left px-3 py-1.5 text-xs hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300"
          >
            {{ action.label }}
          </button>
          <button @click="closeContextMenu" class="w-full text-left px-3 py-1 text-xs text-gray-400 hover:bg-gray-50">取消</button>
        </div>

        <!-- 浮层：尺寸编辑弹窗（Phase B） -->
        <div
          v-if="showDimEditor && editingDimension"
          class="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 z-40 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 p-4 min-w-64"
        >
          <h3 class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ editingDimension.name }}</h3>
          <div class="flex items-center gap-2 mb-3">
            <input
              type="number"
              v-model="editingDimension.newValue"
              :step="0.1"
              :min="0"
              class="w-24 text-sm px-2 py-1.5 rounded border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-green-500"
            />
            <span class="text-sm text-gray-400">{{ editingDimension.unit }}</span>
          </div>
          <div class="flex items-center gap-2">
            <button @click="confirmDimensionEdit" class="px-3 py-1.5 text-xs rounded bg-green-500 text-white hover:bg-green-600">确认</button>
            <button @click="cancelDimensionEdit" class="px-3 py-1.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-500">取消</button>
          </div>
        </div>

        <!-- 浮层：尺寸标注（右上角，从 ModelViewer bbox 回调获得） -->
        <div v-if="modelBBox" class="absolute top-2 right-2 px-3 py-2 rounded-lg bg-white/80 dark:bg-gray-800/80 backdrop-blur shadow-sm z-10">
          <div class="text-xs font-medium text-gray-400 mb-1">📏 包围盒尺寸</div>
          <div class="flex gap-3 text-xs text-gray-600 dark:text-gray-300">
            <span>长 <b>{{ modelBBox.length_mm }}</b>mm</span>
            <span>宽 <b>{{ modelBBox.width_mm }}</b>mm</span>
            <span>高 <b>{{ modelBBox.height_mm }}</b>mm</span>
          </div>
        </div>

        <!-- 浮层：会话信息（左上） -->
        <div v-if="selectedSession" class="absolute top-2 left-2 px-3 py-1.5 rounded-lg bg-white/80 dark:bg-gray-800/80 backdrop-blur shadow-sm max-w-xs">
          <p class="text-xs text-gray-600 dark:text-gray-300 truncate">{{ selectedSession.request }}</p>
          <div class="flex items-center gap-3 mt-1">
            <span v-if="selectedSession.volume_mm3" class="text-xs text-gray-400">📐 {{ fmtVolume(selectedSession.volume_mm3) }}</span>
            <span v-if="selectedSession.qa?.passed" class="text-xs text-gray-400">✅ {{ selectedSession.qa.passed }} 项</span>
            <span v-if="paramsForSession.length" class="text-xs text-blue-500">🎚️ {{ paramsForSession.length }} 参数</span>
          </div>
        </div>
      </div>

      <!-- 右侧：悬浮参数面板 + AI 协助（不挤占视口，浮在 3D 图上方） -->
      <!-- 展开状态 -->
      <div v-if="rightPanelOpen" class="absolute right-0 top-0 bottom-0 w-72 bg-white dark:bg-gray-800 border-l border-gray-200 dark:border-gray-700 shadow-2xl flex flex-col z-20">
        <!-- 折叠按钮 -->
        <button @click.stop="rightPanelOpen = false" @mouseenter="showTooltip($event, '点击收起参数面板')" @mouseleave="hideTooltip" class="absolute -left-6 top-1/2 -translate-y-1/2 p-1.5 rounded-l bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-400 hover:text-gray-600 z-10 shadow-sm">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 18 15 12 9 6"/></svg>
        </button>

        <!-- 参数面板（分组折叠） -->
        <div v-if="paramsForSession.length" class="px-3 py-2 border-b border-gray-200 dark:border-gray-700">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-medium text-gray-500">🎚️ 参数 ({{ paramsForSession.length }})</span>
            <span v-if="rebuilding" class="text-xs text-blue-500">重建中…</span>
            <span v-else-if="rebuildMsg" class="text-xs text-green-600 truncate max-w-32">{{ rebuildMsg }}</span>
          </div>
          <div class="space-y-2 max-h-72 overflow-y-auto">
            <div v-for="(group, gName) in groupedParams" :key="gName">
              <!-- 分组标题（可折叠） -->
              <button @click.stop="toggleParamGroup(gName)" class="flex items-center gap-1 w-full text-xs font-medium text-gray-400 hover:text-gray-600 mb-1">
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" :class="collapsedGroups[gName] ? '-rotate-90' : ''" class="transition-transform"><polyline points="6 9 12 15 18 9"/></svg>
                {{ gName }}
                <span class="text-gray-300 ml-auto">{{ group.length }}</span>
              </button>
              <!-- 参数列表 -->
              <div v-show="!collapsedGroups[gName]" class="space-y-2 pl-2">
                <div v-for="p in group" :key="p.name" class="flex flex-col gap-1">
                  <div class="flex items-center justify-between gap-1">
                    <span class="text-xs text-gray-600 dark:text-gray-300 truncate" :title="p.label || p.name">{{ p.label || p.name }}</span>
                    <div class="flex items-center gap-1 flex-shrink-0">
                      <!-- 精确数值输入框 -->
                      <input
                        type="number"
                        :value="paramInputs[p.name]"
                        @input="onParamInput(p.name, $event.target.value)"
                        @click.stop
                        @focus="rightPanelOpen = true"
                        :step="p.step"
                        :min="p.min"
                        :max="p.max"
                        class="w-20 text-xs text-right px-2 py-1 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-800 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-green-500 cursor-text"
                      />
                      <span class="text-xs text-gray-400 w-8">{{ p.unit }}</span>
                    </div>
                  </div>
                  <!-- 滑块 + 预设快捷按钮 -->
                  <div class="flex items-center gap-1.5">
                    <input
                      type="range"
                      :min="p.min"
                      :max="p.max"
                      :step="p.step"
                      :value="paramValues[p.name]"
                      @input="onParamChange(p.name, parseFloat($event.target.value))"
                      @click.stop
                      class="flex-1 h-1.5 accent-green-500 cursor-pointer"
                    />
                    <!-- 预设快捷值 -->
                    <div v-if="p.presets?.length" class="flex items-center gap-0.5 flex-shrink-0">
                      <button
                        v-for="pv in p.presets"
                        :key="pv"
                        @click.stop="onParamChange(p.name, pv)"
                        :class="Math.abs(paramValues[p.name] - pv) < 0.01 ? 'bg-green-500 text-white' : 'bg-gray-100 dark:bg-gray-700 text-gray-400 hover:bg-gray-200'"
                        class="text-[10px] px-1.5 py-0.5 rounded transition"
                      >{{ pv }}</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 无参数提示 -->
        <div v-else-if="selectedSession && !selectedSession.has_parameters" class="px-3 py-2 border-b border-gray-200 dark:border-gray-700">
          <p class="text-xs text-gray-400">⚠️ 此会话无可调参数，可用 AI 协助重新建模</p>
        </div>

        <!-- AI 协助 -->
        <div class="flex-1 flex flex-col">
          <div class="px-3 py-1.5 border-b border-gray-200 dark:border-gray-700">
            <span class="text-xs font-medium text-gray-500">🤖 AI 协助</span>
          </div>
          <div class="flex-1 overflow-y-auto px-3 py-2 space-y-2">
            <div v-if="aiHistory.length === 0" class="text-xs text-gray-400 text-center py-3">
              描述修改需求<br>
              <span class="text-gray-300">"壁厚5mm" / "高度120"</span>
            </div>
            <div
              v-for="(msg, i) in aiHistory"
              :key="i"
              class="text-xs rounded-lg px-2.5 py-1.5"
              :class="msg.role === 'user' ? 'bg-green-50 dark:bg-green-900/20 ml-4' : 'bg-gray-100 dark:bg-gray-700 mr-4'"
            >{{ msg.text }}</div>
            <div v-if="aiThinking" class="text-xs text-blue-500 px-2">思考中…</div>
          </div>
          <div class="px-2 py-2 border-t border-gray-200 dark:border-gray-700">
            <div class="flex items-center gap-1">
              <input
                v-model="aiPrompt"
                @keydown.enter="aiAssist"
                :disabled="aiThinking"
                placeholder="修改需求…"
                class="flex-1 text-xs px-2 py-1.5 rounded border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 focus:outline-none focus:ring-1 focus:ring-green-500"
              />
              <button @click="aiAssist" :disabled="aiThinking || !aiPrompt.trim()" class="text-xs px-2 py-1.5 rounded bg-green-500 text-white disabled:opacity-50">→</button>
            </div>
          </div>
        </div>
      </div>
      <!-- 折叠状态：悬浮展开按钮（不占布局空间，浮在视口上方） -->
      <button v-else @click="rightPanelOpen = true" @mouseenter="showTooltip($event, '点击展开参数面板和AI协助')" @mouseleave="hideTooltip" class="absolute right-2 top-1/2 -translate-y-1/2 z-20 w-10 h-16 rounded-l-lg bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 shadow-lg hover:bg-gray-50 dark:hover:bg-gray-700 flex flex-col items-center justify-center gap-1 text-gray-400 hover:text-gray-600 transition">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="rotate-180"><polyline points="9 18 15 12 9 6"/></svg>
        <span v-if="paramsForSession.length" class="text-[10px] text-blue-500 font-medium">{{ paramsForSession.length }}</span>
      </button>
    </div>

    <!-- ═══ 底部时间线 ═══ -->
    <div v-if="timeline.length" class="h-10 px-3 py-1.5 bg-white dark:bg-gray-800 border-t border-gray-200 dark:border-gray-700 flex items-center gap-1.5 overflow-x-auto">
      <span class="text-xs text-gray-400 flex-shrink-0">时间线:</span>
      <div
        v-for="(item, i) in timeline"
        :key="i"
        class="flex items-center gap-1 flex-shrink-0 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 rounded px-1.5 py-0.5 transition"
        :title="item.detail || item.action"
      >
        <div class="w-2 h-2 rounded-full" :class="i === 0 ? 'bg-green-500' : 'bg-gray-300 dark:bg-gray-600'"></div>
        <span class="text-xs text-gray-600 dark:text-gray-300 max-w-48 truncate">{{ item.action }}</span>
        <span class="text-xs text-gray-300 dark:text-gray-600">·</span>
        <span class="text-xs text-gray-400">{{ fmtTime(item.ts / 1000) }}</span>
        <span v-if="i < timeline.length - 1" class="text-gray-300 dark:text-gray-600 mx-0.5">→</span>
      </div>
    </div>
  </div>
</template>
