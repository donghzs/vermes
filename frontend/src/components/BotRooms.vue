<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { useBotRoomStore } from '../stores/botRoom'
import { showToast as toast } from '../utils/toast'
import api from '../services/api'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

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
function renderMarkdown(text) {
  try { return md.render(text || '') } catch (e) { return '<pre>' + (text || '') + '</pre>' }
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
const editModal = ref({ open: false, title: '', announcement: '', tasks: '' })

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
})

const ORG_TYPE_LABELS = { dispatcher: '分派', executor: '执行', auditor: '审计', aggregator: '汇总' }

async function openOrgBoard() {
  orgBoard.value.open = true
  await loadOrgBoard()
  startOrgPolling()
}

function closeOrgBoard() {
  orgBoard.value.open = false
  stopOrgPolling()
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

async function loadOrgBoard() {
  if (!bot.currentRoomId) return
  orgBoard.value.loading = true
  try {
    const res = await api.getBotRoomOrg(bot.currentRoomId)
    if (res && res.ok) {
      orgBoard.value.roles = res.roles || []
      orgBoard.value.tasks = res.tasks || []
      orgBoard.value.profileNames = res.profile_names || {}
      orgBoard.value.statusLabels = res.status_labels || {}
    }
  } catch (e) {
    // 静默：轮询期间网络抖动忽略
  } finally {
    orgBoard.value.loading = false
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

function latestAuditBySub(t) {
  const latest = {}
  if (!t || !t.audit_log) return latest
  for (const a of t.audit_log) {
    if (!a.sub_id) continue
    const prev = latest[a.sub_id]
    if (!prev || (a.round || 0) >= (prev.round || 0)) latest[a.sub_id] = a
  }
  return latest
}

// 子任务最新审计状态图标
function subStatusIcon(t, p) {
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

onUnmounted(stopOrgPolling)

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
  }
}
async function handleEditSave() {
  const m = editModal.value
  const r = await bot.updateRoom({ title: m.title, announcement: m.announcement, tasks: m.tasks })
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
        <div v-if="bot.loadingRooms" class="text-xs text-gray-400 px-1">加载中…</div>
        <div v-else-if="bot.rooms.length === 0" class="text-xs text-gray-400 px-1">还没有群，点「+ 建群」创建一个。</div>
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
        <div v-if="bot.loadingTimeline" class="text-xs text-gray-400">加载消息…</div>
        <div v-else-if="messages.length === 0" class="text-sm text-gray-400 mt-8 text-center">
          还没有消息。输入 @名字 点名 Agent，可 @ 多人协作；
          Agent 回复中会 @ 接力其他成员，形成协作链。
        </div>
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
                      {{ c.name || c.id }}{{ c.transport === 'acp' ? '（登堂）' : '' }}
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
            <div v-else-if="contacts.length === 0" class="text-xs text-gray-400">暂无联系人（请先在「神魔架」造神或登堂）</div>
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
                <span v-else class="text-[10px] px-1.5 py-0.5 rounded" :class="c.transport === 'acp' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ c.transport === 'acp' ? '登堂' : '原生' }}</span>
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
            <span class="ml-auto text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="c.transport === 'acp' ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ c.transport === 'acp' ? '⛩️ 登堂' : '原生' }}</span>
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
        <div v-if="bot.loadingMembers" class="text-xs text-gray-400">加载成员…</div>
        <div v-else-if="(bot.members || []).length === 0" class="text-sm text-gray-400 py-6 text-center">群里还没有成员，点「＋ 拉人」把 Agent 拉进来。</div>
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
                <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="m.transport === 'acp' || (m.ref_id || '').startsWith('a2a:') ? 'bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300' : 'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-600 dark:text-indigo-300'">{{ m.transport === 'acp' || (m.ref_id || '').startsWith('a2a:') ? '⛩️ 登堂' : '原生' }}</span>
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
          </div>
          <button class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 text-lg leading-none px-1" @click="closeOrgBoard">×</button>
        </div>

        <div class="flex-1 overflow-y-auto p-3 space-y-3">
          <!-- 空组织引导 -->
          <div v-if="!orgBoard.roles.length && !orgBoard.tasks.length" class="text-center py-6 text-sm text-gray-400">
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
                    <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="orgStatusClass(t.status)">{{ orgStatusText(t.status) }}</span>
                  </div>
                  <div class="text-[11px] text-gray-400 mt-1 flex items-center gap-2">
                    <span>第 {{ t.current_round || 1 }} 轮</span>
                    <template v-if="t.plan && t.plan.length">
                      <span>·</span>
                      <span>{{ passedSubCount(t) }}/{{ t.plan.length }} 子任务通过</span>
                    </template>
                    <span class="ml-auto">{{ formatTime(t.updated_at || t.created_at) }}</span>
                  </div>
                </button>

                <!-- 展开详情：子任务 + 审计流水 + 交付物 -->
                <div v-if="orgBoard.expandedTask === t.id" class="border-t border-gray-200 dark:border-gray-700 px-3 py-2.5 space-y-3 bg-gray-50/60 dark:bg-gray-900/40">
                  <div>
                    <div class="text-xs font-semibold text-gray-500 dark:text-gray-400 mb-1">老板指令</div>
                    <div class="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{{ t.brief }}</div>
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
                >{{ orgContactName(c) }}（{{ c.transport === 'acp' ? '登堂' : '原生' }}）</option>
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
