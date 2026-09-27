/**
 * T4 消息↔任务绑定层：@派活 → kanban 任务 + 消息携带 task_id。
 *
 * 真枚举（kanban_db.VALID_STATUSES）：
 *   triage / todo / scheduled / ready / running / blocked / review / done / archived
 * 状态只认这 9 个，不用 dispatched/planning 口播。
 */

export const TASK_STATUS_META = {
  triage:    { label: '分诊',   dot: 'bg-gray-400' },
  todo:      { label: '待办',   dot: 'bg-slate-400' },
  scheduled: { label: '已排期', dot: 'bg-indigo-400' },
  ready:     { label: '就绪',   dot: 'bg-blue-400' },
  running:   { label: '执行中', dot: 'bg-green-500' },
  blocked:   { label: '阻塞',   dot: 'bg-red-500' },
  review:    { label: '复核',   dot: 'bg-amber-400' },
  done:      { label: '完成',   dot: 'bg-emerald-600' },
  archived:  { label: '归档',   dot: 'bg-gray-300' },
}

export function taskStatusMeta(status) {
  return TASK_STATUS_META[status] || { label: status || '未知', dot: 'bg-gray-300' }
}

/**
 * 消息里的任务标记：只认显式【任务 #123】。
 * P1：不解析裸 #数字——否则 issue #42 误渲染，且手敲 #1 #2 可枚举他人任务。
 */
const TASK_MARK_RE = /【任务\s*#(\d+)】/g

export function parseTaskIds(text) {
  const ids = []
  const s = String(text || '')
  let m
  TASK_MARK_RE.lastIndex = 0
  while ((m = TASK_MARK_RE.exec(s))) {
    const id = m[1]
    if (id && !ids.includes(id)) ids.push(id)
  }
  return ids
}

export function formatTaskMark(taskId) {
  return `【任务 #${taskId}】`
}

/**
 * 识别「派活」指令。
 * 命中：文本含「派活」；标题取「派活」后一句，缺省整句。
 */
export function parseDispatch(text) {
  const s = String(text || '').trim()
  const idx = s.indexOf('派活')
  if (idx < 0) return null
  const after = s.slice(idx + 2).replace(/^[\s:：、,，]+/, '').trim()
  const title = after ? after.split(/\n/)[0].slice(0, 80) : s.split(/\n/)[0].slice(0, 80)
  return { title: title || '未命名任务', raw: s }
}

/**
 * @名字 → 候选 id（按 name/id 子串匹配）。
 * P2：显式 @ 了名字但无命中 → 返回 ''（不静默改派 fallbackId），
 * 避免「@张五 打错却悄悄派给当前 1:1 对象」。无 @ 时才用 fallback。
 */
export function resolveAssignee(contacts, hint, fallbackId) {
  const h = String(hint || '').trim().replace(/^@/, '')
  if (h) {
    const hit = (contacts || []).find(c =>
      c.id === h || c.name === h || (c.name && c.name.includes(h))
    )
    return hit ? hit.id : ''
  }
  return fallbackId || ''
}

function authHeaders() {
  const h = { 'Content-Type': 'application/json' }
  const t = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
  if (t) h['X-Vermes-Session-Token'] = t
  return h
}

/** 建 kanban 任务（复用 plugin API，后端 0 新增）。 */
export async function createKanbanTask({ title, body, assignee }) {
  const resp = await fetch('/api/plugins/kanban/tasks', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      title,
      body: body || null,
      assignee: assignee || null,
      created_by: 'shenmotang',
    }),
  })
  if (!resp.ok) throw new Error(`create task HTTP ${resp.status}`)
  const data = await resp.json()
  return data && data.task ? data.task : data
}

/** 拉任务详情（含 events 进度）。 */
export async function fetchKanbanTask(taskId) {
  const resp = await fetch(`/api/plugins/kanban/tasks/${encodeURIComponent(taskId)}`, {
    headers: authHeaders(),
  })
  if (!resp.ok) throw new Error(`get task HTTP ${resp.status}`)
  return resp.json()
}

/**
 * 发消息前的派活胶水：
 * 检测派活 → 建任务 → 返回附带 task 标记的发送文本。
 * 非派活原样返回。
 */
export async function prepareDispatchSend(text, { contacts, assigneeId } = {}) {
  const d = parseDispatch(text)
  if (!d) return { text, task: null }
  const at = /@([^\s@]+)/.exec(text)
  const assignee = resolveAssignee(contacts, at ? at[1] : '', assigneeId)
  const task = await createKanbanTask({
    title: d.title,
    body: d.raw,
    assignee,
  })
  const id = task && (task.id || task.task_id)
  return {
    text: `${text}\n${formatTaskMark(id)}`,
    task: task || null,
    taskId: id,
  }
}
