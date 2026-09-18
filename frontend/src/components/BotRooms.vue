<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useBotRoomStore } from '../stores/botRoom'
import { showToast as toast } from '../utils/toast'
import api from '../services/api'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'
import { DOMPURIFY_BASE_CONFIG } from '../utils/security'
import StateBlock from './StateBlock.vue'
import SceneStatusBar from './SceneStatusBar.vue'
import { useHarnessLight } from '../composables/useHarnessLight'

const bot = useBotRoomStore()

// ── 交付物富渲染（老板查看报告/任务成果，不挤在群聊纯文本气泡里）──
const md = new MarkdownIt({
  html: true, linkify: true, typographer: true,
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) {
      try { return '<pre class="hljs"><code>' + hljs.highlight(code, { language: lang, ignoreIllegals: true }).value + '</code></pre>' } catch (e) {}
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(code) + '</code></pre>'
  },
})
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const token = tokens[idx]; const href = token.attrGet('href') || ''
  if (/^https?:\/\//i.test(href)) { token.attrSet('target', '_blank'); token.attrSet('rel', 'noopener noreferrer') }
  return self.renderToken(tokens, idx, options)
}
// 交付物 markdown 渲染缓存（2026-09-15 性能修复）
// 模板里此函数写在 v-for 内部（:1060 用 m.content、:1549 用 t.final_output），
// 且组织看板是 **4 秒轮询** —— 每次轮询触发的重渲染都会对每条交付物重跑
// md.render + DOMPurify.sanitize。而交付物一旦产出内容就固定不变，这些计算全是浪费。
// 按文本做 key 天然正确（纯函数，md/消毒配置均为模块级常量）；内容变化时 key 变化自动重算。
const _deliverableMdCache = new Map()
function renderMarkdown(text) {
  const key = text || ''
  const hit = _deliverableMdCache.get(key)
  if (hit !== undefined) return hit
  let out
  try { out = DOMPurify.sanitize(md.render(key), DOMPURIFY_BASE_CONFIG) } catch (e) { out = '<pre>' + key.replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</pre>' }
  if (_deliverableMdCache.size >= 300) _deliverableMdCache.delete(_deliverableMdCache.keys().next().value)
  _deliverableMdCache.set(key, out)
  return out
}

// ── 交付物全屏查看弹窗（老板点「📄 全屏」读报告 + 下载 .md）──
const deliverableModal = ref({ open: false, title: '', content: '' })
function openDeliverable(content, title) {
  deliverableModal.value = { open: true, title: title || '交付物', content: content || '' }
}
function closeDeliverable() { deliverableModal.value.open = false }
function downloadDeliverable() {
  const c = deliverableModal.value.content
  if (!c) return
  const name = (deliverableModal.value.title || '交付物').replace(/[\\/:*?"<>|]/g, '_') + '.md'
  const blob = new Blob([c], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = name
  document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
}

// 气泡内直接下载（不必先开全屏）
function downloadDeliverableContent(content, title) {
  if (!content) return
  const name = (title || '交付物').replace(/[\\/:*?"<>|]/g, '_') + '.md'
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a'); a.href = url; a.download = name
  document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(url)
}

// ── 新建群弹窗（2026-09-07 改版：建群即建组织——模板搭岗，一键开干） ──
const createModal = ref({
  open: false, name: '', announcement: '', tasks: '', selected: [],
  // 🏢 组织区（可选）：useOrg 开启后选模板/指派联系人坐岗，建群即搭好组织
  useOrg: false,
  templates: [],
  orgKey: null,        // 选中的模板 key（'custom' = 自定义岗位）
  roles: [],           // 岗位指派 [{role_id,name,type,profile_id,description}]
  loadingOrg: false,
})
const contacts = ref([])        // 联系人列表（可拉人进群的全量 agent）
const loadingContacts = ref(false)
const orgTemplates = ref([])    // 后端 ORG_TEMPLATES（建群弹窗复用）

// 建群弹窗当前模板岗位列表（useOrg + 选了模板时生效）
const createRoles = computed(() => {
  const m = createModal.value
  if (!m.useOrg) return []
  if (m.orgKey === 'custom') return m.roles
  const t = (m.templates || []).find(x => x.key === m.orgKey)
  return (t && t.roles) || []
})

// 模板岗位→指派态岗位（每岗可指派 profile_id，改岗名/描述）
function rolesWithAssign(tplRoles) {
  return (tplRoles || []).map(r => ({ ...r, profile_id: null }))
}

function pickOrgTemplate(key) {
  const m = createModal.value
  m.orgKey = key
  if (key === 'custom') {
    // 自定义：给 4 个默认空岗（分派/执行×2/审计/汇总 可自行改名）
    m.roles = [
      { role_id: 'dispatcher', name: '分派', type: 'dispatcher', profile_id: null },
      { role_id: 'executor1', name: '执行', type: 'executor', profile_id: null },
      { role_id: 'auditor', name: '审计', type: 'auditor', profile_id: null },
      { role_id: 'aggregator', name: '汇总', type: 'aggregator', profile_id: null },
    ]
    return
  }
  const t = (m.templates || []).find(x => x.key === key)
  if (t) m.roles = rolesWithAssign(t.roles)
}

// 已指派去重（岗位下拉不能重复选同一联系人）
function createAssignedIds() {
  const ids = []
  for (const r of createModal.value.roles) {
    if (r.profile_id && !ids.includes(r.profile_id)) ids.push(r.profile_id)
  }
  return ids
}

function assignRole(r, pid) { r.profile_id = pid || null }

// ── 拉人进群弹窗（2026-09-07 神魔堂收口：👥 拉人不再死链，多选未入群联系人直接拉入） ──
const inviteModal = ref({ open: false, selected: [], submitting: false })
// ── 群成员管理弹窗（微信式：点成员看详情/移出） ──
const memberModal = ref({ open: false })

// ── 群公告/群任务编辑弹窗 ──
const editModal = ref({ open: false, title: '', announcement: '', tasks: '', use_sandbox: false })

// ── 组织任务看板（2026-09-07 ⑭ 秘书模式/组织流水线）：右侧滑出面板 ──
const orgBoard = ref({
  open: false,
  loading: false,
  roles: [],
  tasks: [],
  profileNames: {},
  statusLabels: {},
  expandedTask: null, // 当前展开的任务 id
  polling: null,      // 轮询定时器
  reviewing: false,   // 验收/打回提交中
  rejectingId: null,  // 正在打回的任务 id（展开打回意见输入框）
  rejectComment: '',  // 打回意见
})

// ── 任务级 LLM 覆盖（D2）：模型下拉选项（复用 /model/options） ──
const modelOptions = ref([])   // [{ value: "provider/model", label: "显示名" }]
const taskModelDirty = ref(null)  // 正在保存覆盖的任务 id

async function loadModelOptions() {
  try {
    const data = await api.getModels()
    const rows = (data && data.providers) || []
    const opts = []
    for (const p of rows) {
      const cur = p.is_current ? ' · 当前' : ''
      for (const m of (p.models || [])) {
        opts.push({ value: `${p.slug}/${m}`, label: `${p.name} / ${m}${cur}` })
      }
    }
    modelOptions.value = [
      { value: 'auto:balanced', label: '🤖 Auto · 均衡（智能路由）' },
      { value: 'auto:cost', label: '🤖 Auto · 经济（智能路由）' },
      { value: 'auto:speed', label: '🤖 Auto · 速度（智能路由）' },
      ...opts,
    ]
  } catch (e) {
    console.warn('[BotRooms] 加载模型列表失败', e)
    modelOptions.value = []
  }
}

async function setTaskModel(t, val) {
  if (!bot.currentRoomId || taskModelDirty.value) return
  const override = val || null
  taskModelDirty.value = t.id
  try {
    const res = await api.patchBotRoomOrgTask(bot.currentRoomId, t.id, { model_override: override })
    if (res && res.ok) {
      t.model_override = override || null
      toast(override ? '已设置任务级模型' : '已恢复执行者默认模型')
    } else {
      toast((res && res.error) || '设置失败')
      await loadOrgBoard()
    }
  } catch (e) {
    toast('设置失败，请重试')
    await loadOrgBoard()
  } finally {
    taskModelDirty.value = null
  }
}

const ORG_TYPE_LABELS = { dispatcher: '分派', executor: '执行', auditor: '审计', aggregator: '汇总' }

// C3 术语一致性（2026-09-14）：「登堂 / 原生」徽标此前在本组件 5 处各写各的，
// 文案与判定口径双重不统一：
//   · 文案：'登堂'(1206) / '⛩️ 登堂'(1282,1319) / '（登堂）'(1169) / '(登堂)'(1629) 四种写法
//   · 判定：仅 1319 群成员管理把 A2A 适配器（ref_id 前缀 'a2a:'）算作「登堂」，
//     其余 4 处只认 transport==='acp' → 同一个 A2A agent 在不同面板显示两种身份。
// 统一口径（取更完整的原 1319 为准）：ACP transport 或 A2A 适配器接入均属「登堂」，
// 与品牌语义一致——「原生或 A2A adapter 接入皆一等公民」。
// ⚠️ 有意的行为变更：原先在联系人列表/拉人弹窗/下拉里被标为「原生」的 A2A agent，
//    现统一显示「⛩️ 登堂」。若不希望如此，只需改本函数一处即可全量回退。
function isDengtang(x) {
  return !!x && (x.transport === 'acp' || String(x.ref_id || '').startsWith('a2a:'))
}
function originLabel(x) {
  return isDengtang(x) ? '⛩️ 登堂' : '原生'
}
function originClass(x) {
  return isDengtang(x)
    ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300'
    : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'
}

async function openOrgBoard() {
  orgBoard.value.open = true
  _orgLoadedOnce = false     // 换看板/重开 → 下一次 loadOrgBoard 仍算「首次加载」，保留加载态
  startNowTicker()          // step ③：实时耗时秒针（与 4s 数据轮询解耦）
  await loadOrgBoard()
  startOrgPolling()
  if (!modelOptions.value.length) loadModelOptions()
}

function closeOrgBoard() {
  orgBoard.value.open = false
  stopOrgPolling()
  stopNowTicker()
}

function startOrgPolling() {
  stopOrgPolling()
  orgBoard.value.polling = setInterval(loadOrgBoard, 4000)
}

function stopOrgPolling() {
  if (orgBoard.value.polling) {
    clearInterval(orgBoard.value.polling)
    orgBoard.value.polling = null
  }
}

// A2 卡顿治理（2026-09-14）：区分「首次加载」与「4s 轮询刷新」。
// 首次须给 loading 态（骨架/ AreasPlaceholder）；轮询刷新则不必——否则每 4 秒白闪一次
// 加载态，还额外产生 2 次响应式写入（true→false），每次都触发整子树重渲染。
let _orgLoadedOnce = false

function _sameJSON(a, b) {
  try { return JSON.stringify(a) === JSON.stringify(b) } catch { return false }
}

async function loadOrgBoard() {
  if (!bot.currentRoomId) return
  const isPoll = _orgLoadedOnce   // 已成功加载过 → 本次是轮询刷新
  if (!isPoll) orgBoard.value.loading = true
  try {
    const res = await api.getBotRoomOrg(bot.currentRoomId)
    if (res && res.ok) {
      const roles = res.roles || []
      const tasks = res.tasks || []
      const profileNames = res.profile_names || {}
      const statusLabels = res.status_labels || {}
      // 仅在数据真的变化时替换引用。组织看板绝大多数轮询是「空闲无变化」，
      // 原逻辑无条件整体赋值会让 Vue 因引用变化重渲染整个看板子树，叠加
      // latestAuditBySub 等模板函数开销 → 明明什么都没变却每秒/每 4 秒卡一下。
      // 代价：每次轮询 4 次 JSON.stringify（小载荷，远低于一次全量 diff + 重渲染）。
      if (!_sameJSON(orgBoard.value.roles, roles)) orgBoard.value.roles = roles
      if (!_sameJSON(orgBoard.value.tasks, tasks)) orgBoard.value.tasks = tasks
      if (!_sameJSON(orgBoard.value.profileNames, profileNames)) orgBoard.value.profileNames = profileNames
      if (!_sameJSON(orgBoard.value.statusLabels, statusLabels)) orgBoard.value.statusLabels = statusLabels
      _orgLoadedOnce = true
    }
  } catch (e) {
    // 静默：轮询期间网络抖动忽略
  } finally {
    if (!isPoll) orgBoard.value.loading = false
  }
}

function orgStatusText(s) {
  return (orgBoard.value.statusLabels && orgBoard.value.statusLabels[s]) || s || ''
}

// 通过的子任务数：audit_log 按 sub_id 去重取最新 verdict
function passedSubCount(t) {
  if (!t || !t.audit_log || !t.audit_log.length) return 0
  return Object.values(latestAuditBySub(t)).filter(a => a.verdict === 'pass').length
}

// A2 卡顿治理（2026-09-14）：本函数被模板高频调用且原本每次都 O(n) 重建结果对象。
// 实测调用密度：任务列表 v-for 中，每项 → orgCurrentSubIndex 1 次、passedSubCount 1 次、
// subStatusIcon 每子任务 1 次、subAuditComment 每子任务 2 次（v-if 判空又渲染一次）；
// 而 nowSec 每秒 tick 会让整个看板重渲染 → 上述开销每秒全量重跑一遍，活动态多了就卡。
// 记忆化：以 audit_log 数组引用为键缓存结果。同一次渲染内命中 O(1)；
// 4s 轮询返回新数据时数组身份变化 → 缓存自然失效，语义与计算逻辑完全不变。
// 安全前提：所有调用点均为只读（已逐处核验，无就地修改），故共享同一结果对象无别名风险。
const _LATEST_AUDIT_EMPTY = Object.freeze({})
const _latestAuditCache = new WeakMap()

function latestAuditBySub(t) {
  if (!t || !t.audit_log) return _LATEST_AUDIT_EMPTY
  const cached = _latestAuditCache.get(t.audit_log)
  if (cached) return cached
  const latest = {}
  for (const a of t.audit_log) {
    if (!a.sub_id) continue
    const prev = latest[a.sub_id]
    if (!prev || (a.round || 0) >= (prev.round || 0)) latest[a.sub_id] = a
  }
  _latestAuditCache.set(t.audit_log, latest)
  return latest
}

// 子任务最新状态图标（step ③：执行失败/岗位空挂与「审计打回」区分开，不混成一色红）
function subStatusIcon(t, p) {
  const art = (t && t.artifacts && t.artifacts[p.sub_id]) || ''
  if (typeof art === 'string'
      && (art.indexOf('[执行失败]') === 0 || art.indexOf('[无绑定 agent') === 0)) return '⛔'
  const a = latestAuditBySub(t)[p.sub_id]
  if (!a) return '⏳'
  return a.verdict === 'pass' ? '✅' : '🔴'
}

function subAuditComment(t, subId) {
  const a = latestAuditBySub(t)[subId]
  if (!a || a.verdict === 'pass') return ''
  return a.comment || ''
}

function orgStatusClass(s) {
  const map = {
    done: 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300',
    delivered: 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300',
    rejected: 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300',
    executing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300',
    auditing: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300',
    aggregating: 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300',
    planning: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-300',
    reworking: 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300',
  }
  return map[s] || 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'
}

// ── ⑭ step ③ 产品封装（2026-09-09）：加载骨架 / 执行中实时态 / 失败可视化诊断 / 工期预期 ──
// 活动态 = 流水线还在跑（前端据此显示脉冲动画 + 走实时耗时；终态则停表）
const ORG_ACTIVE_STATUS = ['dispatched', 'planning', 'executing', 'auditing', 'reworking', 'aggregating']

function isOrgActive(t) {
  return ORG_ACTIVE_STATUS.indexOf(t && t.status) >= 0
}

// 实时秒针：让「已运行」每秒自己走，不用等 4s 轮询（只在看板打开时跑）
const nowSec = ref(Math.floor(Date.now() / 1000))
let nowSecTimer = null
function startNowTicker() {
  stopNowTicker()
  nowSec.value = Math.floor(Date.now() / 1000)
  nowSecTimer = setInterval(() => { nowSec.value = Math.floor(Date.now() / 1000) }, 1000)
}
function stopNowTicker() {
  if (nowSecTimer) { clearInterval(nowSecTimer); nowSecTimer = null }
}

// 已耗时：活动态走实时秒针，终态停在 updated_at。
// 诚实呈现「已经跑了多久」，不编造「预计还要多久」——后端无工期字段。
function orgElapsedText(t) {
  if (!t || !t.created_at) return ''
  const end = isOrgActive(t) ? nowSec.value : (t.updated_at || nowSec.value)
  const s = Math.max(0, Math.floor(end - t.created_at))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  return `${m} 分 ${s % 60} 秒`
}

// 进度：已通过交叉审计的子任务 / 计划总数
function orgProgress(t) {
  const total = (t && t.plan && t.plan.length) || 0
  if (!total) return null
  const done = passedSubCount(t)
  return { done, total, pct: Math.min(100, Math.round(done / total * 100)) }
}

// 当前进行到哪个子任务（推断：第一个尚未通过审计的）。
// 注意是「推断」不是后端执行指针——后端未暴露 in-flight 子任务，此处按审计结果倒推。
function orgCurrentSubIndex(t) {
  const plan = (t && t.plan) || []
  const latest = latestAuditBySub(t)
  for (let i = 0; i < plan.length; i++) {
    const a = latest[plan[i].sub_id]
    if (!a || a.verdict !== 'pass') return i + 1
  }
  return plan.length
}

// 失败可视化诊断：后端 org_tasks 无 error 字段，全部从真数据推导，不编造：
//   ① artifacts 值前缀 '[执行失败] ' / '[无绑定 agent…' → 执行者报错 / 岗位空挂
//   ② audit_log 最新一轮 verdict=reject         → 审计打回及意见
//   ③ plan[i]._escalated                        → 超过审计轮次上限，已上报老板
function orgFailures(t) {
  const out = []
  if (!t) return out
  const arts = t.artifacts || {}
  for (const sid of Object.keys(arts)) {
    const v = arts[sid]
    if (typeof v !== 'string') continue
    if (v.indexOf('[执行失败]') === 0) out.push({ kind: 'exec', sub_id: sid, title: '执行者报错', text: v })
    else if (v.indexOf('[无绑定 agent') === 0) out.push({ kind: 'unbound', sub_id: sid, title: '岗位未坐人', text: v })
  }
  const latest = latestAuditBySub(t)
  for (const sid of Object.keys(latest)) {
    const a = latest[sid]
    if (a && a.verdict === 'reject') {
      out.push({
        kind: 'audit', sub_id: sid, title: '审计打回',
        text: a.comment || '（审计未给出具体意见）',
        round: a.round, auditor: a.auditor,
      })
    }
  }
  for (const p of (t.plan || [])) {
    if (p && p._escalated) {
      out.push({ kind: 'escalated', sub_id: p.sub_id, title: '超过审计上限', text: '达到最大审计轮次仍未通过，已上报老板裁决。' })
    }
  }
  return out
}

// 工期预期：告诉老板「现在卡在哪一步、在等什么」，而不是拍一个假 ETA
const ORG_NEXT_HINT = {
  dispatched: '已派活，等执行者接单',
  planning: '正在拆解为子任务…',
  executing: '执行者正在产出工件…',
  auditing: '交叉审计中，通过即落定',
  reworking: '正按审计意见重做…',
  aggregating: '正在汇总最终交付物…',
}
function orgNextHint(s) { return ORG_NEXT_HINT[s] || '' }

// 活动任务数（头部工期提示用）
const activeTaskCount = computed(
  () => (orgBoard.value.tasks || []).filter(t => isOrgActive(t)).length
)

function orgRoleTypeText(t) {
  return ORG_TYPE_LABELS[t] || t || ''
}

function orgTaskRoleName(roleId) {
  const r = (orgBoard.value.roles || []).find(x => x.role_id === roleId)
  return r ? r.name : roleId
}

function orgProfileName(pid) {
  return (orgBoard.value.profileNames && orgBoard.value.profileNames[pid]) || pid || ''
}

// 老板验收/打回（按钮化，复用后端文字触发语义）
async function reviewTask(t, action) {
  if (!bot.currentRoomId || orgBoard.value.reviewing) return
  if (action === 'accept') {
    orgBoard.value.reviewing = true
    try {
      await bot.sendMessage('验收通过')
      toast('已验收通过')
      await loadOrgBoard()
    } finally {
      orgBoard.value.reviewing = false
    }
  } else {
    // 打回：先展开意见输入框
    orgBoard.value.rejectingId = orgBoard.value.rejectingId === t.id ? null : t.id
    orgBoard.value.rejectComment = ''
  }
}

async function confirmReject(t) {
  if (!bot.currentRoomId || orgBoard.value.reviewing) return
  const comment = (orgBoard.value.rejectComment || '').trim()
  const msg = comment ? `打回: ${comment}` : '打回'
  orgBoard.value.reviewing = true
  try {
    await bot.sendMessage(msg)
    toast(comment ? '已打回重做' : '已打回重做')
    orgBoard.value.rejectingId = null
    orgBoard.value.rejectComment = ''
    await loadOrgBoard()
  } finally {
    orgBoard.value.reviewing = false
  }
}

function toggleTaskDetail(t) {
  orgBoard.value.expandedTask = orgBoard.value.expandedTask === t.id ? null : t.id
  if (orgBoard.value.expandedTask === t.id) {
    loadTaskDetail(t.id)
  }
}

async function loadTaskDetail(taskId) {
  if (!bot.currentRoomId) return
  try {
    const res = await api.getBotRoomOrgTask(bot.currentRoomId, taskId)
    if (res && res.ok) {
      const idx = orgBoard.value.tasks.findIndex(x => x.id === taskId)
      if (idx >= 0) orgBoard.value.tasks[idx] = res.task
    }
  } catch (e) { /* 静默 */ }
}

onUnmounted(() => { stopOrgPolling(); stopNowTicker() })

// ── 🏢 从模板搭组织（⑭ 2026-09-07）：模板只是岗位骨架，指派现有联系人坐岗 ──
const orgModal = ref({
  open: false,
  loading: false,
  applying: false,
  templates: [],
  selectedKey: null,
  contacts: [],       // 联系人（可指派坐岗的全量 agent）
  loadingContacts: false,
})

const ORG_TYPE_BADGE = { dispatcher: '分派', executor: '执行', auditor: '审计', aggregator: '汇总' }

function orgTypeBadge(t) { return ORG_TYPE_BADGE[t] || t || '' }

// 当前模板的角色列表
const orgModalRoles = computed(() => {
  const t = orgModal.value.templates.find(x => x.key === orgModal.value.selectedKey)
  return (t && t.roles) || []
})

// 已被其他岗位选走的联系人（下拉不可重复指派）
function orgAssignedIds() {
  const ids = []
  for (const r of orgModalRoles.value) {
    if (r.profile_id) ids.push(r.profile_id)
  }
  return ids
}

function orgContactName(c) { return c.name || c.id || '' }

async function openOrgModal() {
  orgModal.value.open = true
  orgModal.value.loading = true
  try {
    const [tr, ct] = await Promise.all([api.getBotOrgTemplates(), api.listAgentContacts()])
    if (tr && tr.ok) {
      orgModal.value.templates = tr.templates || []
      if (!orgModal.value.selectedKey && orgModal.value.templates.length) {
        orgModal.value.selectedKey = orgModal.value.templates[0].key
      }
    }
    orgModal.value.contacts = (ct && ct.contacts) || (ct && ct.profiles) || []
  } catch (e) {
    toast('组织模板加载失败')
  } finally {
    orgModal.value.loading = false
  }
}

function orgPickTemplate(key) {
  orgModal.value.selectedKey = key
  // 换模板时清掉已指派（岗位骨架不同）
  for (const r of orgModalRoles.value) r.profile_id = null
}

// 指派 / 取消指派联系人坐岗
function orgAssign(role, pid) {
  role.profile_id = pid || null
}

async function orgApply() {
  const roles = orgModalRoles.value.filter(r => r.profile_id)
  if (!roles.length) {
    toast('至少给一个岗位指派联系人')
    return
  }
  orgModal.value.applying = true
  try {
    const res = await api.applyBotOrg(bot.currentRoomId, roles)
    if (res && res.ok) {
      toast(`组织已搭建，拉入 ${res.pulled ? res.pulled.length : 0} 个 agent`)
      orgModal.value.open = false
      await Promise.all([bot.loadMembers(bot.currentRoomId), loadOrgBoard()])
    } else {
      toast((res && res.error) || '组织搭建失败')
    }
  } catch (e) {
    toast('组织搭建失败')
  } finally {
    orgModal.value.applying = false
  }
}

// ── 输入 ──
const inputText = ref('')
const timelineRef = ref(null)

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

const messages = computed(() => bot.timeline)
const currentRoom = computed(() => bot.currentRoom)
// 流式：正在输入的 agent（按 author_ref 过滤出 active 的）
const streamingAgents = computed(() => {
  const out = {}
  for (const [aid, s] of Object.entries(bot.streaming || {})) {
    if (s && s.active) out[aid] = s
  }
  return out
})

// 自动滚到底部
async function scrollToBottom() {
  await nextTick()
  const el = timelineRef.value
  if (el) el.scrollTop = el.scrollHeight
}
watch(messages, scrollToBottom)

// ── 联系人加载 ──
async function loadContacts() {
  loadingContacts.value = true
  try {
    const r = await api.listAgentContacts()
    if (r && r.ok) contacts.value = r.contacts || []
  } catch (e) {
    console.error('联系人加载失败', e)
    contacts.value = []
  } finally {
    loadingContacts.value = false
  }
}

// 已入群的成员 id 集合（用于联系人列表里标记"已在群"）
const memberIds = computed(() => new Set((bot.members || []).map(m => m.ref_id)))

// 可拉人进群的联系人 = 全量联系人（未入群的）
const addableContacts = computed(() => contacts.value.filter(c => !memberIds.value.has(c.id)))

// ── 新建群（建群即建组织） ──
function openCreate() {
  createModal.value = {
    open: true, name: '', announcement: '', tasks: '', selected: [],
    useOrg: false, templates: [], orgKey: null, roles: [], loadingOrg: false,
  }
  loadContacts()
  // 预载组织模板（懒加载一次缓存全局 orgTemplates）
  if (!orgTemplates.value.length) {
    createModal.value.loadingOrg = true
    api.getBotOrgTemplates().then(tr => {
      if (tr && tr.ok) {
        orgTemplates.value = tr.templates || []
        createModal.value.templates = orgTemplates.value
        // 默认勾选「搭组织」+ 选中第一个模板（公司）——主路径即组织协作
        createModal.value.useOrg = true
        if (orgTemplates.value.length) pickOrgTemplate(orgTemplates.value[0].key)
      }
    }).catch(() => {}).finally(() => { createModal.value.loadingOrg = false })
  } else {
    createModal.value.templates = orgTemplates.value
    createModal.value.useOrg = true
    if (orgTemplates.value.length) pickOrgTemplate(orgTemplates.value[0].key)
  }
}
function toggleSelect(id) {
  const s = createModal.value.selected
  const i = s.indexOf(id)
  if (i >= 0) s.splice(i, 1)
  else s.push(id)
}
async function handleCreate() {
  const m = createModal.value
  const name = m.name.trim()
  if (!name) { toast('请填写群名称', 'error'); return }
  // 岗位指派了联系人 → 这些人自动进群（后端 org/apply 拉人）；勾选的补充成员另算
  const orgRoles = m.useOrg ? (m.roles || []).filter(r => r.profile_id) : []
  const assignedIds = new Set(orgRoles.map(r => r.profile_id))
  const extraSelected = m.selected.filter(id => !assignedIds.has(id))
  const r = await bot.createRoom(name, extraSelected, { announcement: m.announcement, tasks: m.tasks })
  if (r && r.ok) {
    let orgRes = null
    if (orgRoles.length) {
      orgRes = await api.applyBotOrg(r.room_id, orgRoles)
      if (!(orgRes && orgRes.ok)) {
        toast((orgRes && orgRes.error) || '群已建但组织搭建失败', 'error')
      }
    }
    createModal.value = { open: false, name: '', announcement: '', tasks: '', selected: [], useOrg: false, templates: [], orgKey: null, roles: [], loadingOrg: false }
    if (orgRes && orgRes.ok) {
      const pulled = orgRes.pulled ? orgRes.pulled.length : 0
      toast(`组织已就绪：${orgRoles.length} 个岗位${pulled ? `，自动拉入 ${pulled} 个 agent` : ''}`)
    } else {
      toast('群已创建', 'success')
    }
    // 若建群时房间未选，切到新群并加载
    await bot.selectRoom(r.room_id)
    await scrollToBottom()
  } else {
    toast((r && r.error) || '创建失败', 'error')
  }
}

// ── 群公告/群任务编辑 ──
function openEdit() {
  const r = currentRoom.value
  if (!r) return
  editModal.value = {
    open: true,
    title: r.title || r.id,
    announcement: r.announcement || '',
    tasks: r.tasks || '',
    use_sandbox: !!(r.use_sandbox),
  }
}
async function handleEditSave() {
  const m = editModal.value
  const r = await bot.updateRoom({ title: m.title, announcement: m.announcement, tasks: m.tasks, use_sandbox: m.use_sandbox })
  if (r && r.ok) {
    editModal.value = { open: false }
    toast('已保存', 'success')
  } else {
    toast((r && r.error) || '保存失败', 'error')
  }
}

// ── 拉人/踢人 ──
async function addMember(id) {
  const r = await bot.addMember(id)
  if (r && r.ok) toast('已拉入群', 'success')
  else toast((r && r.error) || '拉人失败', 'error')
}
async function removeMember(id) {
  const r = await bot.removeMember(id)
  if (r && r.ok) toast('已移出群', 'success')
  else toast((r && r.error) || '移出失败', 'error')
}

// 拉人弹窗：打开时载入联系人，未入群的可多选拉入（已入群的置灰标记）
function openInvite() {
  if (!bot.currentRoomId) { toast('请先选择或创建群', 'error'); return }
  inviteModal.value = { open: true, selected: [], submitting: false }
  loadContacts()
}
function toggleInvite(id) {
  const s = inviteModal.value.selected
  const i = s.indexOf(id)
  if (i >= 0) s.splice(i, 1)
  else s.push(id)
}
async function handleInvite() {
  const sel = inviteModal.value.selected
  if (sel.length === 0) { toast('请勾选要拉入的 Agent', 'error'); return }
  inviteModal.value.submitting = true
  try {
    let ok = true
    for (const id of sel) {
      const r = await bot.addMember(id)
      if (!(r && r.ok)) { ok = false; toast((r && r.error) || `拉入 ${id} 失败`, 'error') }
    }
    if (ok) toast(`已拉入 ${sel.length} 个 Agent`, 'success')
    inviteModal.value.open = false
  } finally {
    inviteModal.value.submitting = false
  }
}

// 群成员管理：微信式弹窗，点成员可看详情 / 移出群
function openMembers() {
  if (!bot.currentRoomId) return
  memberModal.value = { open: true }
}
async function removeMemberFromModal(m) {
  const nm = (m && (m.name || m.ref_id)) || ''
  if (!confirm(`确定把「${nm}」移出群？`)) return
  await removeMember(m.ref_id)
  // 若移出后群已空，保持弹窗开着但列表同步为空
}

async function handleSelect(id) {
  await bot.selectRoom(id)
  await scrollToBottom()
}

// ⚙️ 2026-09-07 解散群（微信式）：二次确认后调 store.deleteRoom
async function confirmDeleteRoom(id) {
  const rid = id || bot.currentRoomId
  const room = bot.rooms.find(x => x.id === rid)
  const name = (room && (room.title || room.id)) || rid
  if (!window.confirm(`确定解散群「${name}」吗？群成员、消息、组织岗位与任务将一并删除，不可恢复。`)) return
  const r = await bot.deleteRoom(rid)
  if (r && r.ok) toast('群已解散')
  else toast((r && r.error) || '解散失败', 'error')
}

function handleSend() {
  const text = inputText.value.trim()
  if (!text || bot.sending) return
  if (!bot.currentRoomId) { toast('请先选择或创建群', 'error'); return }
  bot.sendMessage(text)
  inputText.value = ''
  closeMention()
}

// ── Phase 2：完整 @ 补全交互 ──
const textareaRef = ref(null)
const mention = ref({ active: false, query: '', start: -1, index: 0 })

const mentionMatches = computed(() => {
  if (!mention.value.active) return []
  const q = (mention.value.query || '').toLowerCase()
  const cands = bot.mentionCandidates
  if (!q) return cands
  return cands.filter(
    c => (c.name || '').toLowerCase().includes(q) ||
         (c.ref_id || '').toLowerCase().includes(q),
  )
})

function closeMention() {
  mention.value = { active: false, query: '', start: -1, index: 0 }
}

function updateMention() {
  const el = textareaRef.value
  if (!el) return
  const pos = el.selectionStart ?? 0
  const before = (inputText.value || '').slice(0, pos)
  const m = /(?:^|\s)@([^\s@]*)$/.exec(before)
  if (!m) { closeMention(); return }
  mention.value = {
    active: true,
    query: m[1],
    start: pos - m[1].length - 1,
    index: 0,
  }
}

function _insertToken(token) {
  const el = textareaRef.value
  const text = inputText.value || ''
  if (!el) return
  const pos = el.selectionStart ?? text.length
  const needSpace = pos > 0 && !/\s$/.test(text.slice(0, pos))
  const full = (needSpace ? ' ' : '') + token + ' '
  inputText.value = text.slice(0, pos) + full + text.slice(pos)
  closeMention()
  nextTick(() => {
    const np = pos + full.length
    el.focus()
    try { el.setSelectionRange(np, np) } catch { /* ignore */ }
  })
}

function applyMention(c) {
  _insertToken('@' + (c.insert || c.ref_id))
}

function mentionAtInput(m) {
  _insertToken('@' + (m.insert || m.ref_id))
}

function onKeydown(e) {
  const ms = mention.value
  const list = mentionMatches.value
  if (ms.active && list.length) {
    if (e.key === 'ArrowDown') {
      e.preventDefault(); ms.index = (ms.index + 1) % list.length; return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault(); ms.index = (ms.index - 1 + list.length) % list.length; return
    }
    if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) {
      e.preventDefault()
      applyMention(list[Math.min(ms.index, list.length - 1)])
      return
    }
    if (e.key === 'Escape') { e.preventDefault(); closeMention(); return }
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

function vis(refId) {
  const hit = bot.memberByRef[refId]
  if (hit) return hit
  // ⑭ 组织流水线虚拟作者（非群成员，映射为人读名）
  const ORG_AUTHORS = {
    'org:aggregator': '📦 交付物',
    'org:boss': '老板',
    'org:secretary': '秘书',
  }
  const label = ORG_AUTHORS[refId]
  if (label) return { name: label, hue: 260, initial: label.slice(0, 1) }
  let h = 0
  for (const ch of String(refId || '')) h = (h * 31 + ch.charCodeAt(0)) % 360
  return { name: refId, hue: h, initial: String(refId || '?').slice(0, 1) }
}

function _onRoomUpdate(e) { bot.onRoomUpdate(e.detail) }

const { harnessChip } = useHarnessLight()
const sceneItems = computed(() => {
  const items = []
  if (!bot.botModeDisabled) items.push({ key: 'bot', icon: '⛩️', label: 'Bot Mode 已启用', title: '群聊多 Agent' })
  if (bot.currentRoomId) {
    const room = (bot.rooms || []).find(r => r.id === bot.currentRoomId)
    items.push({
      key: 'room',
      icon: '💬',
      label: room ? (room.title || room.id) : bot.currentRoomId,
      title: '当前群',
    })
    const n = (bot.members || []).length
    items.push({ key: 'members', icon: '👥', label: n ? `${n} 成员` : '成员加载中/暂无', title: '群成员' })
  }
  const h = harnessChip()
  if (h) items.push(h)
  return items
})

onMounted(async () => {
  window.addEventListener('vermes:room_update', _onRoomUpdate)
  await bot.loadRooms()
  if (bot.currentRoomId) {
    await Promise.all([bot.loadTimeline(bot.currentRoomId), bot.loadMembers(bot.currentRoomId)])
    await scrollToBottom()
  }
})
onUnmounted(() => {
  window.removeEventListener('vermes:room_update', _onRoomUpdate)
})
</script>

<template>
  <div class="flex h-full bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100">
    <div v-if="bot.botModeDisabled" class="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
      <div class="text-4xl">💤</div>
      <div class="text-lg font-semibold">Bot Mode 未启用</div>
      <div class="text-sm text-gray-400 max-w-sm">当前配置已关闭 Bot Mode（bot_mode.enabled=false）。开启后此处可进行群聊多 Agent 协作；单聊功能不受影响。</div>
    </div>
    <template v-else>
    <!-- U-P0-6：Bot Mode 关闭时整页说明；开启后仅细状态 chips -->
    <div class="flex-1 flex flex-col min-w-0">
      <SceneStatusBar :items="sceneItems" />
      <div class="flex-1 flex overflow-hidden">
    <!-- 左：群列表 + 新建群 -->
    <aside class="w-64 shrink-0 border-r border-gray-200 dark:border-gray-700 flex flex-col">
      <div class="p-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <div class="text-sm font-semibold">群聊</div>
        <button
          class="px-2 py-1 text-xs rounded bg-blue-500 hover:bg-blue-600 text-white transition"
          @click="openCreate"
        >+ 建群</button>
      </div>
      <div class="flex-1 overflow-y-auto p-2 space-y-1">
        <!-- 仅当「尚无缓存」时才显示加载态；已有缓存则后台静默刷新，避免每次进入神魔堂空白闪烁 -->
        <StateBlock
          v-if="bot.loadingRooms && bot.rooms.length === 0"
          state="loading"
          text="加载群列表…"
          compact
        />
        <StateBlock
          v-else-if="bot.rooms.length === 0"
          state="empty"
          icon="👥"
          text="还没有群"
          detail="点「+ 建群」创建一个，可拉入多位 Agent 协作"
          compact
        />
        <button
          v-for="r in bot.rooms"
          :key="r.id"
          class="w-full text-left px-2 py-2 rounded text-sm transition group/room relative"
          :class="r.id === bot.currentRoomId ? 'bg-blue-500 text-white' : 'hover:bg-gray-100 dark:hover:bg-gray-700'"
          @click="handleSelect(r.id)"
        >
          <div class="font-medium truncate pr-6">{{ r.title || r.id }}</div>
          <div v-if="r.tasks" class="text-[11px] opacity-70 truncate">📋 {{ r.tasks }}</div>
          <!-- ⚙️ 2026-09-07 解散群：悬停显示，防误触 -->
          <span
            class="absolute right-1 top-1/2 -translate-y-1/2 px-1 py-0.5 rounded text-[10px] opacity-0 group-hover/room:opacity-100 transition cursor-pointer"
            :class="r.id === bot.currentRoomId ? 'text-white/80 hover:text-red-200' : 'text-gray-400 hover:text-red-500'"
            title="解散群"
            @click.stop="confirmDeleteRoom(r.id)"
          >✕</span>
        </button>
      </div>
    </aside>

    <!-- 右：群详情 + 时间线 + 输入 -->
    <main class="flex-1 flex flex-col min-w-0">
      <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div class="flex items-center justify-between gap-3">
          <div class="min-w-0">
            <div class="font-semibold truncate">{{ currentRoom ? (currentRoom.title || currentRoom.id) : '未选择群' }}</div>
            <div class="text-xs text-gray-400">输入 @ 唤起补全 · 不 @ 则默认 Agent 应答</div>
          </div>
          <div class="flex items-center gap-1">
            <!-- ⚙️ 2026-09-07 解散群 -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-red-50 dark:bg-red-900/30 text-red-500 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900/50 transition"
              title="解散当前群（删群 + 清成员/消息/组织）"
              @click="confirmDeleteRoom(currentRoom.id)"
            >解散群</button>
            <!-- 群公告/群任务入口 -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
              title="编辑群公告 / 群任务"
              @click="openEdit"
            >📋 群公告</button>
            <!-- 群成员管理（微信式：看谁在群、点成员详情/移出） -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
              :title="`群成员 ${(bot.members || []).length} 人`"
              @click="openMembers"
            >👥 群成员{{ (bot.members || []).length ? `(${(bot.members || []).length})` : '' }}</button>
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-blue-500 hover:bg-blue-600 text-white transition"
              title="拉新 Agent 进群"
              @click="openInvite"
            >＋ 拉人</button>
            <!-- 组织任务看板入口（秘书/组织流水线） -->
            <button
              v-if="currentRoom"
              class="px-2 py-1 text-xs rounded bg-emerald-600 hover:bg-emerald-700 text-white transition"
              title="组织岗位与任务流水线看板"
              @click="orgBoard.open ? closeOrgBoard() : openOrgBoard()"
            >📊 看板</button>
          </div>
        </div>
        <!-- 群公告条 -->
        <div v-if="currentRoom && (currentRoom.announcement || currentRoom.tasks)" class="mt-2 flex flex-wrap gap-2">
          <span v-if="currentRoom.announcement" class="px-2 py-0.5 text-[11px] rounded bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300">📢 {{ currentRoom.announcement }}</span>
          <span v-if="currentRoom.tasks" class="px-2 py-0.5 text-[11px] rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300">📋 {{ currentRoom.tasks }}</span>
        </div>
        <!-- 房间成员：点击插入 @，长按/右键可踢 -->
        <div class="mt-2 flex items-center gap-1 flex-wrap">
          <button
            v-for="m in bot.mentionCandidates"
            :key="m.ref_id"
            class="group flex items-center gap-1 pl-0.5 pr-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 transition"
            :title="`@${m.insert}（点击 @；点 × 移出群）`"
            @click="mentionAtInput(m)"
          >
            <svg viewBox="0 0 32 32" class="w-5 h-5 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${m.hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ m.initial }}</text>
            </svg>
            <span class="text-xs">{{ m.name }}</span>
            <span
              class="text-[10px] text-gray-400 hover:text-red-500 px-0.5"
              title="移出群"
              @click.stop="removeMember(m.ref_id)"
            >×</span>
          </button>
          <span v-if="bot.mentionCandidates.length === 0" class="text-xs text-gray-400">（群里还没有 agent，点「👥 拉人」）</span>
        </div>
      </div>

      <!-- 时间线 -->
      <div ref="timelineRef" class="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        <!-- 同上：有缓存则后台刷新，不遮挡已加载的消息 -->
        <StateBlock
          v-if="bot.loadingTimeline && bot.timeline.length === 0"
          state="loading"
          text="加载消息…"
        />
        <StateBlock
          v-else-if="messages.length === 0"
          state="empty"
          icon="💬"
          text="还没有消息"
          detail="输入 @名字 点名 Agent，可 @ 多人协作；Agent 回复中会 @ 接力其他成员，形成协作链"
        />
        <template v-for="m in messages" :key="m.id">
          <div v-if="m.author_type === 'user'" class="flex justify-end">
            <div class="max-w-[75%] px-3 py-2 rounded-2xl rounded-tr-sm bg-blue-500 text-white text-sm whitespace-pre-wrap break-words">
              {{ m.content }}
            </div>
          </div>
          <div v-else-if="m.author_type === 'system'" class="flex justify-center">
            <div class="max-w-[80%] px-3 py-1.5 rounded text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 text-center">
              {{ m.content }}
            </div>
          </div>
          <div v-else class="flex justify-start gap-2">
            <svg viewBox="0 0 32 32" class="w-7 h-7 rounded-full shrink-0 mt-4">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${vis(m.author_ref).hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ vis(m.author_ref).initial }}</text>
            </svg>
            <div class="max-w-[72%]">
              <div class="text-[11px] text-gray-400 mb-0.5 px-1">@{{ vis(m.author_ref).name || m.author_ref }}</div>
              <!-- 组织交付物：富渲染（报告/成果老板可读），气泡内直接 markdown 渲染 + 全屏/下载 -->
              <div
                v-if="m.author_ref === 'org:aggregator' || m.author_ref === 'org:secretary'"
                class="px-3 py-2 rounded-2xl rounded-tl-sm bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800"
              >
                <div class="text-[11px] text-emerald-600 dark:text-emerald-400 mb-1 font-medium">📦 {{ vis(m.author_ref).name }}</div>
                <div class="deliverable-md max-h-72 overflow-y-auto text-sm" v-html="renderMarkdown(m.content)"></div>
                <div class="flex items-center gap-2 mt-2">
                  <button class="text-[11px] px-2 py-0.5 rounded bg-emerald-500 text-white hover:bg-emerald-600 transition" @click="openDeliverable(m.content, vis(m.author_ref).name)">📄 全屏查看</button>
                  <button class="text-[11px] px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-700 transition" @click="downloadDeliverableContent(m.content, vis(m.author_ref).name)">⬇️ 下载</button>
                </div>
              </div>
              <div v-else class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm whitespace-pre-wrap break-words">
                {{ m.content }}
              </div>
            </div>
          </div>
        </template>

        <!-- 流式：正在输入的 agent（真群聊流式体验，逐 delta 累积） -->
        <template v-for="(s, aid) in streamingAgents" :key="'stream-' + aid">
          <div class="flex justify-start gap-2">
            <svg viewBox="0 0 32 32" class="w-7 h-7 rounded-full shrink-0 mt-4">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${vis(aid).hue}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ vis(aid).initial }}</text>
            </svg>
            <div class="max-w-[72%]">
              <div class="text-[11px] text-gray-400 mb-0.5 px-1">@{{ vis(aid).name || aid }} <span class="text-emerald-500">▌正在输入…</span></div>
              <div class="px-3 py-2 rounded-2xl rounded-tl-sm bg-gray-100 dark:bg-gray-700 text-sm whitespace-pre-wrap break-words">
                {{ s.text || '…' }}
              </div>
            </div>
          </div>
        </template>
      </div>

      <!-- 输入 -->
      <div class="border-t border-gray-200 dark:border-gray-700 p-3">
        <div class="flex items-end gap-2">
          <div class="relative flex-1">
            <textarea
              ref="textareaRef"
              v-model="inputText"
              rows="2"
              placeholder="输入消息，输入 @ 唤起成员补全，例如：@法律顾问 帮我看看这份合同"
              class="w-full resize-none px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 outline-none focus:border-blue-500"
              :disabled="!bot.currentRoomId || bot.sending"
              @keydown="onKeydown"
              @input="updateMention"
              @click="updateMention"
            ></textarea>
            <div
              v-if="mention.active && mentionMatches.length"
              class="absolute bottom-full left-0 mb-1 w-72 max-h-56 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1 z-20"
            >
              <div class="px-2 pb-1 text-[11px] text-gray-400">↑↓ 选择 · Enter/Tab 确认 · Esc 取消</div>
              <button
                v-for="(c, i) in mentionMatches"
                :key="c.ref_id"
                class="w-full flex items-center gap-2 px-2 py-1.5 text-left transition"
                :class="i === mention.index ? 'bg-blue-50 dark:bg-blue-900/40' : 'hover:bg-gray-100 dark:hover:bg-gray-700'"
                @mousedown.prevent="applyMention(c)"
              >
                <svg viewBox="0 0 32 32" class="w-6 h-6 rounded-full shrink-0">
                  <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue}, 65%, 45%)`" />
                  <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="15" font-weight="600">{{ c.initial }}</text>
                </svg>
                <div class="min-w-0">
                  <div class="text-sm truncate">@{{ c.insert }}</div>
                  <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.ref_id }}</div>
                </div>
              </button>
            </div>
          </div>
          <button
            class="px-4 py-2 rounded-lg bg-blue-500 hover:bg-blue-600 text-white text-sm transition disabled:opacity-50"
            :disabled="!bot.currentRoomId || bot.sending"
            @click="handleSend"
          >{{ bot.sending ? '发送中…' : '发送' }}</button>
        </div>
      </div>
    </main>
      </div><!-- /flex overflow -->
    </div><!-- /scene flex-col -->
    </template>

    <!-- 新建群弹窗（2026-09-07：建群即建组织——模板搭岗指派，一键开干；不搭组织也可纯闲聊群） -->
    <div
      v-if="createModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="createModal.open = false"
    >
      <div class="w-[32rem] max-w-[94vw] max-h-[88vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">🏢 建群（组织）</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">群 = 组织：搭好岗位分工后发需求，Agent 自动拆解→执行→审计→汇总→交付。不搭组织也可当普通群聊。</p>
        <div class="flex flex-col gap-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群名称 *</label>
            <input v-model="createModal.name" placeholder="如：法务项目组 / 我的智囊团" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" @keydown.enter="handleCreate" />
          </div>

          <!-- 🏢 组织模式开关（默认开：模板已预选） -->
          <div class="rounded-lg border border-gray-200 dark:border-gray-700 p-3">
            <label class="flex items-center justify-between cursor-pointer">
              <span class="text-sm font-medium">🏢 搭组织（分工协作）</span>
              <input type="checkbox" v-model="createModal.useOrg" class="accent-emerald-500 w-4 h-4" />
            </label>
            <div v-if="createModal.loadingOrg" class="text-xs text-gray-400 mt-2">加载模板…</div>
            <template v-else-if="createModal.useOrg">
              <div class="text-[11px] text-gray-400 mt-1.5">模板只定岗位骨架（谁拆解/执行/审计/汇总），人从你的 Agent 里选——不用为每个场景预设角色。</div>
              <!-- 模板选择 -->
              <div class="flex gap-1.5 mt-2 flex-wrap">
                <button
                  v-for="t in createModal.templates"
                  :key="t.key"
                  class="px-2 py-1 text-[11px] rounded-full border transition"
                  :class="createModal.orgKey === t.key ? 'bg-emerald-500 border-emerald-500 text-white' : 'border-gray-300 dark:border-gray-600 hover:border-emerald-400'"
                  @click="pickOrgTemplate(t.key)"
                >{{ t.name }}</button>
                <button
                  class="px-2 py-1 text-[11px] rounded-full border transition"
                  :class="createModal.orgKey === 'custom' ? 'bg-emerald-500 border-emerald-500 text-white' : 'border-gray-300 dark:border-gray-600 hover:border-emerald-400'"
                  @click="pickOrgTemplate('custom')"
                >✏️ 自定义</button>
              </div>
              <!-- 岗位指派 -->
              <div v-if="createRoles.length" class="mt-2.5 space-y-1.5">
                <div v-for="r in createRoles" :key="r.role_id" class="flex items-center gap-2 text-xs">
                  <span class="w-16 shrink-0">
                    <input v-model="r.name" class="w-full px-1 py-0.5 rounded bg-transparent border border-transparent hover:border-gray-300 dark:hover:border-gray-600 outline-none focus:border-emerald-400" title="可改名" />
                  </span>
                  <span class="w-8 shrink-0 text-[10px] px-1 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-500">{{ ({ dispatcher: '分派', executor: '执行', auditor: '审计', aggregator: '汇总' })[r.type] || r.type }}</span>
                  <select
                    v-model="r.profile_id"
                    class="flex-1 min-w-0 px-1.5 py-1 rounded bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-emerald-400"
                  >
                    <option :value="null">— 指派 Agent（可留空后补）—</option>
                    <option v-for="c in contacts" :key="c.id" :value="c.id" :disabled="createAssignedIds().includes(c.id) && r.profile_id !== c.id">
                      {{ c.name || c.id }}{{ isDengtang(c) ? '（登堂）' : '' }}
                    </option>
                  </select>
                </div>
              </div>
              <div v-else class="text-xs text-gray-400 mt-2">模板加载失败或无岗位，可换「✏️ 自定义」。</div>
            </template>
          </div>

          <div>
            <label class="text-xs text-gray-500 mb-1 block">群公告</label>
            <input v-model="createModal.announcement" placeholder="可留空，建群后也能编辑" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群任务</label>
            <input v-model="createModal.tasks" placeholder="可留空，建群后也能编辑" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">补充拉人（可选：岗位指派外的助手）</label>
            <div v-if="loadingContacts" class="text-xs text-gray-400">加载联系人…</div>
            <StateBlock
              v-else-if="contacts.length === 0"
              state="empty"
              icon="👤"
              text="暂无联系人"
              detail="请先在「神魔架」造神或登堂"
              compact
            />
            <div v-else class="max-h-36 overflow-y-auto space-y-1">
              <label
                v-for="c in contacts"
                :key="c.id"
                class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
              >
                <input type="checkbox" :checked="createModal.selected.includes(c.id)" :disabled="createAssignedIds().includes(c.id)" @change="toggleSelect(c.id)" class="accent-blue-500" />
                <svg viewBox="0 0 32 32" class="w-5 h-5 rounded-full shrink-0">
                  <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue || 0}, 65%, 45%)`" />
                  <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="13" font-weight="600">{{ (c.name || '?').slice(0, 1) }}</text>
                </svg>
                <div class="min-w-0 flex-1">
                  <div class="text-sm truncate">{{ c.name }}</div>
                  <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.id }}</div>
                </div>
                <span v-if="createAssignedIds().includes(c.id)" class="text-[10px] text-emerald-500">已坐岗</span>
                <span v-else class="text-[10px] px-1.5 py-0.5 rounded" :class="originClass(c)">{{ originLabel(c) }}</span>
              </label>
            </div>
          </div>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="createModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleCreate" class="px-3 py-1.5 text-sm rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white">{{ createModal.useOrg && createRoles.some(r => r.profile_id) ? '🚀 建组织开工' : '创建群' }}</button>
        </div>
      </div>
    </div>

    <!-- 群公告/群任务编辑弹窗 -->
    <div
      v-if="editModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="editModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">编辑群信息</h3>
        <div class="flex flex-col gap-3 mt-3">
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群名称</label>
            <input v-model="editModal.title" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500" />
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群公告</label>
            <textarea v-model="editModal.announcement" rows="3" placeholder="群公告，成员可见" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500 resize-none"></textarea>
          </div>
          <div>
            <label class="text-xs text-gray-500 mb-1 block">群任务</label>
            <textarea v-model="editModal.tasks" rows="3" placeholder="群任务，成员可见" class="w-full px-3 py-2 text-sm rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-blue-500 resize-none"></textarea>
          </div>
          <div class="flex items-start gap-3 rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 px-3 py-2.5">
            <input id="sandbox-toggle" type="checkbox" v-model="editModal.use_sandbox" class="mt-0.5 accent-blue-500" />
            <div class="flex-1">
              <label for="sandbox-toggle" class="text-sm font-medium block cursor-pointer">🐝 执行沙箱（蜂群隔离工作区）</label>
              <p class="text-[11px] text-gray-400 leading-snug mt-0.5">开启后，本群 native/CLI 执行者子任务下沉到蜂群工程沙箱跑（隔离 workspace + git branch + 心跳回收 + 失败重试）。默认关=内联直跑。ACP 异构 agent 不受影响（永不进沙箱）。</p>
            </div>
          </div>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="editModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleEditSave" class="px-3 py-1.5 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 text-white">保存</button>
        </div>
      </div>
    </div>

    <!-- 拉人进群弹窗（2026-09-07：微信式多选拉人，未入群的可勾选，已入群的置灰标记） -->
    <div
      v-if="inviteModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="inviteModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold mb-1">拉人进群</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">勾选要拉入的 Agent（原生 + 已登堂的封神榜 Agent 都能拉，@ 它即可协作）。</p>
        <div v-if="loadingContacts" class="text-xs text-gray-400">加载联系人…</div>
        <div v-else-if="addableContacts.length === 0" class="text-sm text-gray-400 py-6 text-center">
          没有可拉入的新 Agent（全部已在群）。<br/>可到「🔥 神魔架」造神或从封神榜登堂后再拉。
        </div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1">
          <label
            v-for="c in addableContacts"
            :key="c.id"
            class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-gray-50 dark:hover:bg-gray-700 cursor-pointer"
          >
            <input type="checkbox" :checked="inviteModal.selected.includes(c.id)" @change="toggleInvite(c.id)" class="accent-blue-500" />
            <svg viewBox="0 0 32 32" class="w-6 h-6 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${c.hue || 0}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="14" font-weight="600">{{ (c.name || '?').slice(0, 1) }}</text>
            </svg>
            <div class="min-w-0">
              <div class="text-sm truncate">{{ c.name }}</div>
              <div class="text-[11px] text-gray-400 truncate">{{ c.description || c.id }}</div>
            </div>
            <span class="ml-auto text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="originClass(c)">{{ originLabel(c) }}</span>
          </label>
        </div>
        <div class="mt-4 flex justify-end gap-2">
          <button @click="inviteModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button @click="handleInvite" :disabled="inviteModal.submitting || inviteModal.selected.length === 0" class="px-3 py-1.5 text-sm rounded-lg bg-blue-500 hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed text-white">{{ inviteModal.submitting ? '拉入中…' : `拉入 ${inviteModal.selected.length} 个` }}</button>
        </div>
      </div>
    </div>

    <!-- 群成员管理弹窗（微信式：看谁在群、点移出；transport 徽标区分原生/登堂） -->
    <div
      v-if="memberModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="memberModal.open = false"
    >
      <div class="w-[28rem] max-w-[92vw] max-h-[85vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <div class="flex items-center justify-between mb-1">
          <h3 class="text-base font-semibold">群成员（{{ (bot.members || []).length }}）</h3>
          <button @click="memberModal.open = false" class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none px-1">✕</button>
        </div>
        <p class="text-xs text-gray-500 dark:text-gray-400 mb-4">点成员名可快速 @；移出后该 Agent 不再应答群消息。</p>
        <StateBlock v-if="bot.loadingMembers" state="loading" text="加载成员…" compact />
        <StateBlock
          v-else-if="(bot.members || []).length === 0"
          state="empty"
          icon="👥"
          text="群里还没有成员"
          detail="点「＋ 拉人」把 Agent 拉进来"
          compact
        />
        <div v-else class="space-y-1">
          <div
            v-for="m in bot.members"
            :key="m.ref_id"
            class="flex items-center gap-2 px-2 py-2 rounded hover:bg-gray-50 dark:hover:bg-gray-700"
          >
            <svg viewBox="0 0 32 32" class="w-8 h-8 rounded-full shrink-0">
              <circle cx="16" cy="16" r="16" :fill="`hsl(${Number(m.hue) || ((m.name || m.ref_id || '').split('').reduce((h, ch) => (h * 31 + ch.charCodeAt(0)) % 360, 0))}, 65%, 45%)`" />
              <text x="16" y="22" text-anchor="middle" fill="#fff" font-size="14" font-weight="600">{{ (m.name || m.ref_id || '?').slice(0, 1) }}</text>
            </svg>
            <div class="min-w-0 flex-1">
              <div class="text-sm truncate flex items-center gap-1.5">
                <span class="truncate">{{ m.name || m.ref_id }}</span>
                <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="originClass(m)">{{ originLabel(m) }}</span>
              </div>
              <div class="text-[11px] text-gray-400 truncate">{{ m.ref_id }}</div>
            </div>
            <button
              class="text-xs px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition shrink-0"
              title="在输入框 @ 这个 Agent"
              @click="mentionAtInput({ ...m, name: m.name || m.ref_id, insert: (m.name || m.ref_id), hue: m.hue })"
            >@</button>
            <button
              class="text-xs px-2 py-1 rounded bg-red-50 dark:bg-red-900/30 text-red-500 hover:bg-red-100 dark:hover:bg-red-900/50 transition shrink-0"
              title="移出群"
              @click="removeMemberFromModal(m)"
            >移出</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 组织任务看板（⑭ 秘书模式/组织流水线，2026-09-07） -->
    <div
      v-if="orgBoard.open"
      class="fixed inset-0 z-50 bg-black/40"
      @click.self="closeOrgBoard"
    >
      <div class="absolute right-0 top-0 bottom-0 w-[26rem] max-w-[92vw] flex flex-col bg-white dark:bg-gray-800 shadow-2xl">
        <!-- 头部 -->
        <div class="px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div>
            <div class="font-semibold text-sm">📊 组织 · 任务看板</div>
            <div v-if="orgBoard.roles.length" class="text-[11px] text-gray-400 mt-0.5">
              {{ orgBoard.roles.length }} 个岗位 · {{ orgBoard.tasks.length }} 个任务
            </div>
            <!-- 工期预期（step ③）：只说「后台跑、不用盯」，不拍假 ETA -->
            <div
              v-if="activeTaskCount"
              class="text-[11px] text-blue-500 dark:text-blue-400 mt-0.5 flex items-center gap-1"
              title="任务通常需数十秒到数分钟，取决于子任务数与执行者模型——不拍假的预计剩余时间"
            >
              <span class="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse"></span>
              {{ activeTaskCount }} 个任务后台执行中 · 可随时关闭此面板，进度不中断
            </div>
          </div>
          <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none px-1" @click="closeOrgBoard">×</button>
        </div>

        <div class="flex-1 overflow-y-auto p-3 space-y-3">
          <!-- 首次加载骨架（step ③）：避免打开面板先白屏一拍，轮询刷新时不闪骨架 -->
          <div v-if="orgBoard.loading && !orgBoard.roles.length && !orgBoard.tasks.length" class="space-y-2">
            <div v-for="i in 3" :key="i" class="rounded-lg border border-gray-200 dark:border-gray-700 p-3 animate-pulse">
              <div class="h-3 w-1/2 bg-gray-200 dark:bg-gray-700 rounded"></div>
              <div class="h-2 w-3/4 bg-gray-100 dark:bg-gray-800 rounded mt-2"></div>
              <div class="h-2 w-1/3 bg-gray-100 dark:bg-gray-800 rounded mt-1.5"></div>
            </div>
            <div class="text-[11px] text-gray-400 text-center pt-1">正在读取组织看板…</div>
          </div>

          <!-- 空组织引导 -->
          <div v-else-if="!orgBoard.roles.length && !orgBoard.tasks.length" class="text-center py-6 text-sm text-gray-400">
            <div class="text-3xl mb-2">🏗️</div>
            <div class="px-2">
              这个群还没有组织架构。两种搭法：<br/>
              <span class="text-xs">① 群里有 1 个 Agent → 直接发需求，秘书自动搭组织并派活；<br/>
              ② 点下方按钮，用「岗位模板」从现有 Agent 里指派各岗位。</span>
            </div>
            <button
              class="mt-3 px-3 py-1.5 text-xs rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white transition"
              @click="openOrgModal"
            >🏢 用模板搭组织（岗位骨架，指派现有 Agent 坐岗）</button>
            <div class="mt-2 text-[11px] text-gray-400 px-4">
              模板只定「谁拆解 / 谁执行 / 谁审计 / 谁汇总」，人从你现有 Agent 里选 —— 不用为每个场景预设一堆角色。
            </div>
          </div>

          <!-- 岗位表 -->
          <div v-if="orgBoard.roles.length">
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-xs font-semibold text-gray-500 dark:text-gray-400">岗位表（组织架构）</div>
              <button class="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-300 transition" title="重新指派岗位（换人/换模板）" @click="openOrgModal">✏️ 调整</button>
            </div>
            <div class="space-y-1">
              <div
                v-for="r in orgBoard.roles"
                :key="r.role_id"
                class="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-gray-50 dark:bg-gray-900 text-sm"
              >
                <span class="text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300 shrink-0">{{ orgRoleTypeText(r.type) }}</span>
                <span class="font-medium truncate">{{ r.name }}</span>
                <span class="text-xs text-gray-400 truncate">← {{ orgProfileName(r.profile_id) }}</span>
              </div>
            </div>
          </div>

          <!-- 任务列表 -->
          <div v-if="orgBoard.tasks.length">
            <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1.5">任务流水线</div>
            <div class="space-y-2">
              <div
                v-for="t in orgBoard.tasks"
                :key="t.id"
                class="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden"
              >
                <button class="w-full text-left px-3 py-2 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition" @click="toggleTaskDetail(t)">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-sm font-medium truncate">{{ t.title || t.brief?.slice(0, 30) || t.id }}</span>
                    <span
                      class="text-[10px] px-1.5 py-0.5 rounded shrink-0 inline-flex items-center gap-1"
                      :class="[orgStatusClass(t.status), isOrgActive(t) ? 'animate-pulse' : '']"
                    >
                      <span v-if="isOrgActive(t)" class="w-1 h-1 rounded-full bg-current"></span>
                      {{ orgStatusText(t.status) }}
                    </span>
                  </div>
                  <div class="text-[11px] text-gray-400 mt-1 flex items-center gap-2">
                    <span>第 {{ t.current_round || 1 }} 轮</span>
                    <template v-if="t.plan && t.plan.length">
                      <span>·</span>
                      <span v-if="isOrgActive(t)" title="按审计结果推断当前进度（后端未暴露执行指针）">{{ orgCurrentSubIndex(t) }}/{{ t.plan.length }} 进行中</span>
                      <span v-else>{{ passedSubCount(t) }}/{{ t.plan.length }} 子任务通过</span>
                    </template>
                    <span v-if="isOrgActive(t) && orgNextHint(t.status)" class="truncate">· {{ orgNextHint(t.status) }}</span>
                    <span class="ml-auto shrink-0" :title="formatTime(t.updated_at || t.created_at)">{{ orgElapsedText(t) }}</span>
                  </div>
                  <!-- 进度条（step ③）：让「还在跑 / 跑到哪」一眼可见 -->
                  <div v-if="orgProgress(t)" class="mt-1.5 h-1 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
                    <div
                      class="h-1 rounded-full transition-all duration-500"
                      :class="t.status === 'rejected' ? 'bg-red-400' : (isOrgActive(t) ? 'bg-blue-500' : 'bg-green-500')"
                      :style="{ width: (orgProgress(t).pct || 0) + '%' }"
                    ></div>
                  </div>
                </button>

                <!-- 展开详情：子任务 + 审计流水 + 交付物 -->
                <div v-if="orgBoard.expandedTask === t.id" class="border-t border-gray-200 dark:border-gray-700 px-3 py-2.5 space-y-3 bg-gray-50/60 dark:bg-gray-900/40">
                  <div>
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">老板指令</div>
                    <div class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ t.brief }}</div>
                  </div>

                  <!-- 失败可视化诊断（step ③）：后端 org_tasks 无 error 字段，
                       全部从 artifacts 前缀 / audit_log reject / plan._escalated 推导，不编造来源 -->
                  <div v-if="orgFailures(t).length" class="rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 p-2">
                    <div class="text-xs font-semibold text-red-600 dark:text-red-300 mb-1">
                      ⚠️ 失败诊断 · {{ orgFailures(t).length }} 项
                    </div>
                    <div class="space-y-1">
                      <div v-for="(f, i) in orgFailures(t)" :key="i" class="text-[11px] flex items-start gap-1.5">
                        <span class="shrink-0">{{ f.kind === 'audit' ? '🔴' : '⛔' }}</span>
                        <div class="min-w-0 flex-1">
                          <div class="text-red-700 dark:text-red-200">
                            {{ f.title }}<span v-if="f.sub_id"> · {{ f.sub_id }}</span><span v-if="f.round"> · 第 {{ f.round }} 轮</span><span v-if="f.auditor"> · {{ f.auditor }}</span>
                          </div>
                          <div class="text-red-600/80 dark:text-red-300/80 whitespace-pre-wrap break-words mt-0.5">{{ f.text }}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 任务级 LLM 覆盖（D2）：只对 native/CLI 执行者生效，ACP 模型自持 -->
                  <div class="rounded-lg border border-dashed border-indigo-200 dark:border-indigo-800 bg-white dark:bg-gray-800/60 p-2">
                    <div class="flex items-center justify-between mb-1">
                      <span class="text-xs font-semibold text-gray-500 dark:text-gray-400">🎛️ 任务级模型覆盖</span>
                      <span v-if="taskModelDirty === t.id" class="text-[10px] text-indigo-500">保存中…</span>
                    </div>
                    <select
                      :value="t.model_override || ''"
                      @change="setTaskModel(t, $event.target.value)"
                      class="w-full px-2 py-1.5 text-xs rounded-lg bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-indigo-500"
                    >
                      <option value="">用执行者默认模型</option>
                      <option v-for="m in modelOptions" :key="m.value" :value="m.value">{{ m.label }}</option>
                    </select>
                    <p class="text-[10px] text-gray-400 mt-1">仅对原生/CLI 执行者生效；登堂的 ACP agent 模型自持，此覆盖不作用于它们。</p>
                  </div>

                  <!-- 子任务（plan） -->
                  <div v-if="t.plan && t.plan.length">
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">子任务</div>
                    <div class="space-y-1">
                      <div v-for="(p, i) in t.plan" :key="i" class="text-xs flex items-start gap-2 px-2 py-1 rounded bg-white dark:bg-gray-800">
                        <span class="mt-0.5">{{ subStatusIcon(t, p) }}</span>
                        <div class="min-w-0 flex-1">
                          <div class="text-gray-700 dark:text-gray-200">[{{ orgProfileName(p.assignee) }}] {{ p.instruction }}</div>
                          <div v-if="p.acceptance" class="text-gray-400 mt-0.5">验收标准：{{ p.acceptance }}</div>
                          <div v-if="subAuditComment(t, p.sub_id)" class="text-gray-400 mt-0.5">意见：{{ subAuditComment(t, p.sub_id) }}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 审计流水（audit_log） -->
                  <div v-if="t.audit_log && t.audit_log.length">
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">交叉审计流水</div>
                    <div class="space-y-1">
                      <div v-for="(a, i) in t.audit_log" :key="i" class="text-[11px] flex items-start gap-1.5 px-2 py-1 rounded bg-white dark:bg-gray-800">
                        <span>{{ a.verdict === 'pass' ? '🟢' : '🔴' }}</span>
                        <div class="min-w-0">
                          <span class="text-gray-500">第 {{ a.round }} 轮 · {{ a.auditor }}</span>
                          <span v-if="a.sub_id" class="text-gray-400"> · 审 {{ a.executor || a.sub_id }}</span>
                          <div v-if="a.comment" class="text-gray-500 mt-0.5">{{ a.comment }}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- 交付物 -->
                  <div v-if="t.final_output">
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">📦 交付物</div>
                    <div class="deliverable-md text-sm text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-800 rounded p-2 max-h-48 overflow-y-auto" v-html="renderMarkdown(t.final_output)"></div>
                    <button class="mt-1.5 text-[11px] px-2 py-0.5 rounded bg-emerald-500 text-white hover:bg-emerald-600 transition" @click="openDeliverable(t.final_output, t.title || '交付物')">📄 全屏查看</button>
                  </div>

                  <!-- 老板验收/打回（2026-09-07：按钮化，不用手打文字） -->
                  <div v-if="t.status === 'delivered'" class="pt-1">
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">老板验收</div>
                    <div class="flex items-center gap-2">
                      <button
                        class="flex-1 px-3 py-1.5 text-xs rounded-lg bg-green-500 hover:bg-green-600 text-white transition disabled:opacity-50"
                        :disabled="orgBoard.reviewing"
                        @click="reviewTask(t, 'accept')"
                      >✅ 验收通过</button>
                      <button
                        class="flex-1 px-3 py-1.5 text-xs rounded-lg bg-orange-500 hover:bg-orange-600 text-white transition disabled:opacity-50"
                        :disabled="orgBoard.reviewing"
                        @click="reviewTask(t, 'reject')"
                      >🔁 打回重做</button>
                    </div>
                    <input
                      v-if="orgBoard.rejectingId === t.id"
                      v-model="orgBoard.rejectComment"
                      type="text"
                      placeholder="打回意见（可选，回车确认）"
                      class="mt-1.5 w-full px-2.5 py-1.5 text-xs rounded-lg bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 outline-none focus:border-orange-400"
                      @keydown.enter="confirmReject(t)"
                      @keydown.esc="orgBoard.rejectingId = null"
                    />
                    <div v-if="orgBoard.rejectingId === t.id" class="mt-1.5 flex gap-1.5">
                      <button class="px-2 py-1 text-[11px] rounded bg-orange-500 text-white hover:bg-orange-600 transition" @click="confirmReject(t)">确认打回</button>
                      <button class="px-2 py-1 text-[11px] rounded bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 transition" @click="orgBoard.rejectingId = null">取消</button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <!-- 🏢 用模板搭组织弹窗（⑭ 2026-09-07：岗位骨架 + 指派现有联系人坐岗） -->
    <div
      v-if="orgModal.open"
      class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      @click.self="orgModal.open = false"
    >
      <div class="w-[34rem] max-w-[94vw] max-h-[88vh] overflow-y-auto rounded-xl bg-white dark:bg-gray-800 p-5 shadow-xl">
        <h3 class="text-base font-semibold">🏢 搭组织（岗位骨架）</h3>
        <p class="text-xs text-gray-500 dark:text-gray-400 mt-1 mb-3">
          模板只定义分工骨架（谁拆解/执行/审计/汇总），每个岗位从你现有的 Agent 联系人里指派 —— 无需预设一堆角色 Agent。
        </p>

        <!-- 模板选择 -->
        <div v-if="orgModal.loading" class="text-sm text-gray-400 py-4 text-center">加载模板…</div>
        <template v-else>
          <div class="flex gap-2 flex-wrap mb-3">
            <button
              v-for="t in orgModal.templates"
              :key="t.key"
              class="px-3 py-1.5 text-xs rounded-lg border transition"
              :class="orgModal.selectedKey === t.key
                ? 'bg-emerald-600 border-emerald-600 text-white'
                : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:border-emerald-500'"
              @click="orgPickTemplate(t.key)"
            >
              {{ t.name }}
              <span class="opacity-70">（{{ t.roles.length }} 岗）</span>
            </button>
          </div>

          <!-- 选中模板描述 -->
          <div v-if="(orgModal.templates || []).find(t => t.key === orgModal.selectedKey)" class="text-[11px] text-gray-400 mb-3">
            {{ (orgModal.templates || []).find(t => t.key === orgModal.selectedKey).description }}
          </div>

          <!-- 岗位指派 -->
          <div class="space-y-2">
            <div v-if="!orgModal.contacts.length" class="text-xs text-gray-400 text-center py-3">
              暂无联系人，请先到「🔥 神魔架」造神 / 登堂。
            </div>
            <div
              v-for="(r, i) in orgModalRoles"
              :key="r.role_id || i"
              class="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-50 dark:bg-gray-900"
            >
              <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0 bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300">{{ orgTypeBadge(r.type) }}</span>
              <div class="min-w-0 flex-1">
                <div class="text-sm font-medium truncate">{{ r.name }}</div>
                <div class="text-[10px] text-gray-400 truncate" :title="r.description">{{ r.description }}</div>
              </div>
              <select
                class="text-xs px-2 py-1 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 outline-none max-w-[10rem]"
                :value="r.profile_id || ''"
                @change="orgAssign(r, $event.target.value)"
              >
                <option value="">（指派 Agent）</option>
                <option
                  v-for="c in orgModal.contacts"
                  :key="c.id"
                  :value="c.id"
                  :disabled="orgAssignedIds().includes(c.id)"
                >{{ orgContactName(c) }}（{{ originLabel(c) }}）</option>
              </select>
            </div>
          </div>
        </template>

        <div class="mt-4 flex justify-end gap-2">
          <button @click="orgModal.open = false" class="px-3 py-1.5 text-sm rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600">取消</button>
          <button
            @click="orgApply"
            :disabled="orgModal.applying"
            class="px-3 py-1.5 text-sm rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-50"
          >{{ orgModal.applying ? '搭建中…' : '✅ 搭好组织' }}</button>
        </div>
      </div>
    </div>

    <!-- 📄 交付物全屏查看（老板读报告/成果：富渲染 markdown + 下载 .md） -->
    <div
      v-if="deliverableModal.open"
      class="fixed inset-0 z-[60] flex items-center justify-center bg-black/60"
      @click.self="closeDeliverable"
    >
      <div class="w-[46rem] max-w-[95vw] h-[86vh] flex flex-col rounded-xl bg-white dark:bg-gray-800 shadow-2xl overflow-hidden">
        <div class="shrink-0 flex items-center gap-2 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
          <span class="text-base">📦</span>
          <span class="flex-1 text-sm font-semibold text-gray-700 dark:text-gray-200 truncate">{{ deliverableModal.title }}</span>
          <button @click="downloadDeliverable" class="px-3 py-1.5 text-xs rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white transition">⬇️ 下载 .md</button>
          <button @click="closeDeliverable" class="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-600 dark:text-gray-300 transition">✕</button>
        </div>
        <div class="flex-1 overflow-y-auto p-5">
          <div class="deliverable-md prose-sm max-w-none" v-html="renderMarkdown(deliverableModal.content)"></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 交付物 markdown 富渲染样式（复用单聊 ArtifactPanel 视觉基线） */
.deliverable-md :deep(h1) { font-size: 1.5em; font-weight: 700; margin: 0.6em 0 0.4em; }
.deliverable-md :deep(h2) { font-size: 1.25em; font-weight: 600; margin: 0.6em 0 0.4em; }
.deliverable-md :deep(h3) { font-size: 1.1em; font-weight: 600; margin: 0.5em 0 0.3em; }
.deliverable-md :deep(p) { margin: 0.5em 0; line-height: 1.7; }
.deliverable-md :deep(ul), .deliverable-md :deep(ol) { margin: 0.5em 0; padding-left: 1.5em; }
.deliverable-md :deep(li) { margin: 0.2em 0; }
.deliverable-md :deep(blockquote) { border-left: 3px solid #22c55e; padding-left: 1em; color: #6b7280; margin: 0.5em 0; }
.deliverable-md :deep(table) { width: 100%; border-collapse: collapse; margin: 0.5em 0; }
.deliverable-md :deep(th), .deliverable-md :deep(td) { border: 1px solid #e5e7eb; padding: 0.4em 0.6em; }
.deliverable-md :deep(th) { background: #f9fafb; font-weight: 600; }
.deliverable-md :deep(code) { background: #f3f4f6; padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
.deliverable-md :deep(pre.hljs) { border-radius: 8px; padding: 1em; overflow-x: auto; margin: 0.5em 0; }
.deliverable-md :deep(pre code) { background: none; padding: 0; }
.dark .deliverable-md :deep(th) { background: #1f2937; }
.dark .deliverable-md :deep(td), .dark .deliverable-md :deep(th) { border-color: #4b5563; }
.dark .deliverable-md :deep(code) { background: #374151; }
</style>
