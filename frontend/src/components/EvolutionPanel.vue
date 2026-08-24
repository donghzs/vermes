<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { toast } from '../utils/toast'
import { useConfirm } from '../composables/useConfirm'
import { useBackendConnectionStore } from '../stores/backendConnection'
const { confirm } = useConfirm()
const backendConn = useBackendConnectionStore()

const status = ref(null)
const loading = ref(true)
const expanded = ref(false)
const achievements = ref([])
const dagData = ref(null)
const collapsed = ref(true) // 默认折叠为微型指示器，点击展开详情
const emergenceData = ref(null)
const skillsData = ref(null)
const graphData = ref(null)        // 学习成长图谱（技能+记忆节点+边+时间线）
const selfModifyHistory = ref([])
const changes = ref([])          // T5 变更流水（L1 通知）
const unreadCount = ref(0)
const proposals = ref([])        // 双闸门没过 / 幅度过大 → 待人工审
let _backendDownToastShown = false
let _roundInProgress = false // 防雪崩：上一轮未完成不发起下一轮

// A.4.5: 后端已知离线时，面板内联展示"重连中…"而非红色 toast 刷屏
const backendOffline = computed(() => backendConn.isOffline)

// 统一 fetch 带超时：3s 后 abort，避免后端卡住时前端无限等待
function _fetchWithTimeout(url, opts = {}, ms = 3000) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), ms)
  const t = (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || ''
  const headers = { ...opts.headers }
  if (t) headers['X-Vermes-Session-Token'] = t
  return fetch(url, { ...opts, headers, signal: ctrl.signal })
    .finally(() => clearTimeout(timer))
}

function _fail(fnName, fn, errMsg) {
  // A.4.5: 后端连接问题不弹 toast——
  // 看门狗广播离线时由面板内联"重连中…"指示；
  // 看门狗还没来得及广播时（窗口期）也不弹，因为后端大概率在自愈中。
  // 只有非网络类错误（HTTP 4xx/5xx、JSON 解析等）才弹 toast。
  const isNetwork = errMsg && (errMsg.includes('Failed to fetch') || errMsg.includes('NetworkError') || errMsg.includes('aborted') || errMsg.includes('fetch'))
  if (isNetwork) return
  if (!_backendDownToastShown) {
    _backendDownToastShown = true
    toast.error(`${fnName}失败: ${errMsg || '网络错误'}`)
  }
}

async function fetchStatus() {
  try {
    const r = await _fetchWithTimeout('/api/evolution/status')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    status.value = await r.json()
    // 后端恢复 → 重置失联 toast 节流
    _backendDownToastShown = false
  } catch (e) {
    _fail('进化状态', fetchStatus, e.message)
  } finally {
    loading.value = false
  }
}

async function fetchAchievements() {
  try {
    const r = await _fetchWithTimeout('/api/evolution/achievements?limit=5')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    achievements.value = await r.json()
  } catch (e) {
    _fail('成就列表', fetchAchievements, e.message)
  }
}

async function fetchDag() {
  try {
    const r = await _fetchWithTimeout('/api/evolution/dag?limit=50')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    dagData.value = await r.json()
  } catch (e) {
    _fail('DAG关系', fetchDag, e.message)
  }
}

async function fetchEmergence() {
  try {
    const r = await _fetchWithTimeout('/api/emergence/status')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    emergenceData.value = await r.json()
  } catch (e) {
    _fail('涌现状态', fetchEmergence, e.message)
  }
}

async function fetchSkills() {
  try {
    const r = await _fetchWithTimeout('/api/emergence/skills')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    skillsData.value = await r.json()
  } catch (e) {
    _fail('涌现技能', fetchSkills, e.message)
  }
}

async function fetchGraph() {
  // 学习成长图谱：技能+记忆节点 + tool_sequence 交集/词法边 + 成长时间线
  try {
    const r = await _fetchWithTimeout('/api/emergence/graph')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    graphData.value = await r.json()
  } catch (e) {
    _fail('成长图谱', fetchGraph, e.message)
  }
}

function timelineActionLabel(action) {
  const m = {
    extracted: '提取', auto_adopted: '自动采纳', confirmed: '确认',
    rejected: '拒绝', promoted: '晋升', demoted: '降级', reactivated: '复活',
  }
  return m[action] || action || ''
}

async function confirmSkill(id) {
  try {
    const r = await _fetchWithTimeout(`/api/emergence/skill/${id}/confirm`, { method: 'POST' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    await Promise.allSettled([fetchSkills(), fetchGraph(), fetchChanges()])
    toast.success('技能已确认')
  } catch (e) {
    _fail('确认技能', () => confirmSkill(id), e.message)
  }
}

async function rejectSkill(id) {
  try {
    const r = await _fetchWithTimeout(`/api/emergence/skill/${id}/reject`, { method: 'POST' })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    await Promise.allSettled([fetchSkills(), fetchGraph(), fetchChanges()])
    toast.success('技能已拒绝')
  } catch (e) {
    _fail('拒绝技能', () => rejectSkill(id), e.message)
  }
}

async function fetchSelfModifyHistory() {
  try {
    const r = await _fetchWithTimeout('/api/evolution/self_modify_history?limit=50')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const data = await r.json()
    selfModifyHistory.value = data.events || []
  } catch (e) {
    _fail('自改写历史', fetchSelfModifyHistory, e.message)
  }
}

// ── 审批分层：已自动调整（L1，可撤回）+ 待审提案（L2）──────────────
// L1 的语义是「静默执行 + 通知 + 可撤回」。下面这两个区就是「通知」和
// 「可撤回」的落点 —— 没有它们，自动 apply 就是在背着用户改配置。

async function fetchChanges() {
  try {
    const r = await _fetchWithTimeout('/api/changes?limit=20')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const data = await r.json()
    changes.value = data.changes || []
    unreadCount.value = data.unread || 0
  } catch (e) {
    _fail('变更记录', fetchChanges, e.message)
  }
}

async function fetchProposals() {
  try {
    const r = await _fetchWithTimeout('/api/evolution/proposals?status=proposed')
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const data = await r.json()
    proposals.value = data.proposals || []
  } catch (e) {
    _fail('待审提案', fetchProposals, e.message)
  }
}

async function markChangesRead() {
  try {
    await _fetchWithTimeout('/api/changes/read', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all: true }),
    })
    unreadCount.value = 0
    changes.value = changes.value.map(c => ({ ...c, unread: false }))
  } catch (e) {
    _fail('标记已读', markChangesRead, e.message)
  }
}

// 「撤回」对不同变更是不同的动作：配置改动是还原 .bak 快照，技能采纳是
// 打回 rejected。用一个按钮表达同一个意图，但必须分派到正确的端点。
function _retractEndpoint(c) {
  if (c.ref_kind === 'skill') return `/api/emergence/skill/${c.ref_id}/reject`
  return `/api/evolution/proposals/${c.ref_id}/retract`
}

async function retractChange(c) {
  const isSkill = c.ref_kind === 'skill'
  if (!await confirm({
    title: isSkill ? '撤回技能采纳' : '撤回自动调整',
    message: isSkill
      ? `${c.title}\n\n将停用该技能，系统不会再自动使用这个模式。`
      : `${c.title}\n\n将把配置还原到这次调整之前的快照。`,
    confirmText: '撤回',
    danger: true,
  })) return
  try {
    const r = await _fetchWithTimeout(_retractEndpoint(c), { method: 'POST' })
    const data = await r.json()
    if (data.ok) {
      await Promise.allSettled([fetchChanges(), fetchSkills(), fetchGraph(), fetchProposals()])
      toast.success(isSkill ? '已撤回，技能已停用' : '已撤回，配置已还原')
    } else {
      throw new Error(data.error || '未知错误')
    }
  } catch (e) {
    _fail('撤回变更', () => retractChange(c), e.message)
  }
}

async function applyProposal(p) {
  if (!await confirm({
    title: '应用提案',
    message: `${p.title}\n\n${p.rationale || ''}\n\n将写入 config.yaml（带备份，可回滚）。`,
    confirmText: '应用',
  })) return
  try {
    const r = await _fetchWithTimeout(`/api/evolution/proposals/${p.id}/apply`, { method: 'POST' }, 15000)
    const data = await r.json()
    if (data.ok && data.applied) {
      await Promise.allSettled([fetchProposals(), fetchChanges()])
      toast.success('提案已应用')
    } else if (data.ok && !data.applied) {
      // 审批被拒/超时不是错误，是用户的选择 —— 别报红。
      toast.info(data.reason === 'denied_or_timeout' ? '已取消' : (data.error || '未应用'))
    } else {
      throw new Error(data.error || '未知错误')
    }
  } catch (e) {
    _fail('应用提案', () => applyProposal(p), e.message)
  }
}

async function rejectProposal(p) {
  try {
    const r = await _fetchWithTimeout(`/api/evolution/proposals/${p.id}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: 'user_rejected' }),
    })
    const data = await r.json()
    if (data.ok) {
      await fetchProposals()
      toast.success('已拒绝')
    } else {
      throw new Error(data.error || '未知错误')
    }
  } catch (e) {
    _fail('拒绝提案', () => rejectProposal(p), e.message)
  }
}

// 系统自作主张做过的事，统一在一个区里交代 —— 配置自动调参（T4）、
// 技能自动采纳（T3）、能力自动激活（T2）本质是同一类：没问就做了。
// 已经被撤掉的不再展示；备份被回收或过了 24h 的，后端把 retractable
// 置 false，这里据此换成灰字说明，避免用户点进一个必然失败的操作。
const _AUTO_KINDS = ['config_auto_apply', 'skill_adopted', 'capability_activated']
const _RETRACTED = ['retracted', 'rejected']
const autoApplied = computed(() =>
  (changes.value || []).filter(
    c => _AUTO_KINDS.includes(c.kind) && !_RETRACTED.includes(c.ref_status)
  )
)

function changeTime(iso) {
  if (!iso) return ''
  return String(iso).slice(5, 16).replace('T', ' ')
}

async function rollbackChange(target, backup) {
  if (!await confirm({ title: '回滚自我改写', message: `${target}\n\n将恢复备份、撤销该次改写。此操作不可自动恢复。`, confirmText: '回滚', danger: true })) {
    return
  }
  try {
    const r = await _fetchWithTimeout('/api/evolution/self_modify_rollback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_path: target, backup_path: backup || null }),
    })
    const data = await r.json()
    if (data.ok) {
      await fetchSelfModifyHistory()
      toast.success('回滚成功')
    } else {
      throw new Error(data.detail || '未知错误')
    }
  } catch (e) {
    _fail('回滚改写', () => rollbackChange(target, backup), e.message)
  }
}

async function retractCapability(capName) {
  if (!await confirm({ title: '撤回能力', message: `撤回能力「${capName}」？\n\n撤回后该能力将不再被自动建议，但原始记录保留。`, confirmText: '撤回', danger: true })) {
    return
  }
  try {
    const r = await _fetchWithTimeout('/api/evolution/retract', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_type: 'capability', target_name: capName }),
    })
    const data = await r.json()
    if (data.ok) {
      await fetchSelfModifyHistory()
      toast.success('能力已撤回')
    } else {
      throw new Error(data.detail || '未知错误')
    }
  } catch (e) {
    _fail('撤回能力', () => retractCapability(capName), e.message)
  }
}

async function _refreshAll() {
  if (_roundInProgress) return // 防雪崩：上一轮未完成不发起下一轮
  _roundInProgress = true
  try {
    await Promise.allSettled([
      fetchStatus(),
      fetchAchievements(),
      fetchDag(),
      fetchEmergence(),
      fetchSkills(),
      fetchGraph(),
      fetchSelfModifyHistory(),
      fetchChanges(),
      fetchProposals(),
    ])
  } finally {
    _roundInProgress = false
  }
}

onMounted(() => {
  _refreshAll()
  // 每 30 秒刷新全部数据（含成就/DAG/技能）
  const timer = setInterval(() => _refreshAll(), 30000)
  onUnmounted(() => clearInterval(timer))
})

// A.4.5: 后端由主进程看门狗自愈恢复 → 立即重刷数据并解除 toast 节流，
// 用户无需手动刷新即可看到面板重新填充。
watch(() => backendConn.online, (up) => {
  if (up) {
    _backendDownToastShown = false
    _refreshAll()
  }
})

const successRateColor = computed(() => {
  const rate = status.value?.success_rate || 0
  if (rate >= 80) return 'text-green-500'
  if (rate >= 60) return 'text-yellow-500'
  return 'text-red-500'
})

const progressBarColor = computed(() => {
  const rate = status.value?.success_rate || 0
  if (rate >= 80) return 'bg-green-500'
  if (rate >= 60) return 'bg-yellow-500'
  return 'bg-red-500'
})

// P4: 接地率（verified_rate）着色——与成功率同阈值。
const verifiedRateColor = computed(() => {
  const rate = status.value?.verified_rate || 0
  if (rate >= 80) return 'text-green-500'
  if (rate >= 60) return 'text-yellow-500'
  return 'text-red-500'
})

const verifiedBarColor = computed(() => {
  const rate = status.value?.verified_rate || 0
  if (rate >= 80) return 'bg-green-500'
  if (rate >= 60) return 'bg-yellow-500'
  return 'bg-red-500'
})

const richnessTierLabel = (tier) => {
  const map = { cold_start: '冷启动', building: '积累中', learning: '学习中', fluent: '熟练' }
  return map[tier] || tier
}

const richnessTierColor = (tier) => {
  const map = { cold_start: 'text-gray-400', building: 'text-blue-400', learning: 'text-green-400', fluent: 'text-emerald-300' }
  return map[tier] || 'text-gray-400'
}

const healthStatus = computed(() => {
  const h = emergenceData.value?.health
  if (!h) return null
  if (h.healthy === null) return { text: '冷启动中', color: 'text-gray-400' }
  if (h.healthy) return { text: '运行正常', color: 'text-green-400' }
  return { text: `静默 ${h.stale_hours}h`, color: 'text-red-400' }
})

const edgeTypeLabel = (relType) => {
  const map = { caused_emotion: '引发情绪', triggered: '触发反模式', queried: '检索文档', retrieved: '检索分块' }
  return map[relType] || relType
}

const nodeTypeLabel = (nodeType) => {
  const map = { outcome: '工具调用', emotional_state: '情绪状态', anti_pattern: '反模式', document: '文档', chunk: '分块' }
  return map[nodeType] || nodeType
}

const smStatusLabel = (s) => {
  const map = {
    committed: '已应用', proposed: '待审批', held: '冷启动挂起',
    rejected: '已拒绝', rolled_back: '已回滚', retracted: '已撤回',
    activated: '能力已激活', denied: '能力已拒绝', unknown: '未知'
  }
  return map[s] || s
}
const smStatusColor = (s) => {
  const map = {
    committed: 'text-green-500', proposed: 'text-blue-400', held: 'text-yellow-500',
    rejected: 'text-red-400', rolled_back: 'text-gray-400', retracted: 'text-orange-400',
    activated: 'text-green-500', denied: 'text-red-400', unknown: 'text-gray-400'
  }
  return map[s] || 'text-gray-400'
}
const smTypeLabel = (t) => {
  const map = {
    self_modify: '源码改写', self_modify_rollback: '回滚',
    capability_activate: '能力激活', __retraction__: '撤回'
  }
  return map[t] || t
}
</script>

<template>
  <!-- 微型指示器模式（默认）：仅一行小字 -->
  <div v-if="!loading && status?.active && collapsed" class="evolution-mini" @click="collapsed = false">
    <span class="evo-mini-icon">🧠</span>
    <span class="evo-mini-text">{{ status.total_outcomes || 0 }} 次 · 成功 {{ status.success_rate || 0 }}% · 接地 {{ status.verified_rate || 0 }}%</span>
    <!-- L1 的角标：面板折叠时唯一的「有事发生」信号。没有它，自动调整
         就只存在于日志里，用户永远不会主动去看。 -->
    <span v-if="unreadCount > 0" class="evo-mini-badge" :title="`${unreadCount} 项自动调整待查看`">{{ unreadCount }}</span>
    <span v-else-if="proposals.length" class="evo-mini-badge evo-mini-badge--pending" :title="`${proposals.length} 条提案待审`">{{ proposals.length }}</span>
    <span class="evo-mini-expand">▶</span>
  </div>
  <!-- 完整面板模式（点击展开） -->
  <div v-if="!loading && status?.active && !collapsed" class="evolution-panel">
    <div class="evo-header" @click="expanded = !expanded">
      <span class="evo-icon">🧠</span>
      <span class="evo-title">进化系统</span>
      <!-- A.4.5: 后端离线时内联"重连中…"，替代红色 toast 刷屏 -->
      <span v-if="backendOffline" class="text-xs text-amber-400/80 ml-1">· 重连中…</span>
      <span class="evo-expand">{{ expanded ? '▼' : '▶' }}</span>
      <span class="evo-collapse" @click.stop="collapsed = true">收起</span>
    </div>
    <div class="evo-grid">
      <div class="evo-card">
        <div class="evo-value">{{ status.total_outcomes || 0 }}</div>
        <div class="evo-label">工具调用</div>
      </div>
      <div class="evo-card">
        <div class="evo-value" :class="successRateColor">
          {{ status.success_rate || 0 }}%
        </div>
        <div class="evo-label">成功率</div>
      </div>
      <!-- P4: 接地率（verified_rate）——跨会话持久化的统一验证信号占比 -->
      <div class="evo-card">
        <div class="evo-value" :class="verifiedRateColor">
          {{ status.verified_rate || 0 }}%
        </div>
        <div class="evo-label">接地率</div>
      </div>
    </div>
    <div class="evo-progress">
      <div class="evo-progress-bar" :class="progressBarColor"
           :style="{ width: Math.min(100, status.success_rate || 0) + '%' }">
      </div>
    </div>
    <div class="evo-progress evo-progress--alt">
      <div class="evo-progress-bar" :class="verifiedBarColor"
           :style="{ width: Math.min(100, status.verified_rate || 0) + '%' }">
      </div>
    </div>

    <!-- ── 已自动调整（L1：静默执行 + 通知 + 24h 可撤回）──────────── -->
    <!-- 这一区不藏在 expanded 里：需要用户行动的东西必须一眼可见。 -->
    <div v-if="autoApplied.length" class="evo-changes">
      <div class="evo-changes-head">
        <span class="evo-key">
          已自动调整 {{ autoApplied.length }} 项
          <span class="evo-changes-hint">· 24h 内可撤回</span>
        </span>
        <button v-if="unreadCount > 0" class="evo-btn-read" @click="markChangesRead">全部已读</button>
      </div>
      <div v-for="c in autoApplied" :key="c.id" class="evo-change-card" :class="{ 'evo-change--unread': c.unread }">
        <div class="evo-change-main">
          <div class="evo-change-title">
            <span v-if="c.unread" class="evo-dot" title="未读"></span>{{ c.title }}
          </div>
          <div v-if="c.summary" class="evo-change-desc">{{ c.summary }}</div>
        </div>
        <button v-if="c.retractable" class="evo-btn-retract" @click="retractChange(c)">撤回</button>
        <span v-else class="evo-change-expired" title="超过 24h 或备份已被回收">不可撤回</span>
        <span class="evo-sm-time">{{ changeTime(c.created) }}</span>
      </div>
    </div>

    <!-- ── 待审提案（L2：闸门没过 / 幅度 >20%）───────────────────── -->
    <div v-if="proposals.length" class="evo-changes evo-proposals">
      <div class="evo-changes-head">
        <span class="evo-key">待审提案 {{ proposals.length }} 条</span>
      </div>
      <div v-for="p in proposals" :key="p.id" class="evo-change-card">
        <div class="evo-change-main">
          <div class="evo-change-title">{{ p.title }}</div>
          <div v-if="p.rationale" class="evo-change-desc">{{ p.rationale }}</div>
          <!-- 说清楚它为什么需要人来看，而不是只丢一个「待审」标签 -->
          <div v-if="p.deterministic_result?.tier_reason" class="evo-change-why">
            ⚠ {{ p.deterministic_result.tier_reason }}
          </div>
        </div>
        <button class="evo-btn-confirm" @click="applyProposal(p)">应用</button>
        <button class="evo-btn-reject" @click="rejectProposal(p)">拒绝</button>
      </div>
    </div>

    <div v-if="expanded" class="evo-detail">
      <div v-if="status.current_emotion" class="evo-row">
        <span class="evo-key">当前状态</span>
        <span class="evo-val">{{ status.current_emotion }}</span>
      </div>
      <div v-if="status.anti_patterns_count > 0" class="evo-row">
        <span class="evo-key">反模式</span>
        <span class="evo-val text-yellow-500">{{ status.anti_patterns_count }} 个</span>
      </div>
      <div v-if="status.top_domains?.length" class="evo-row">
        <span class="evo-key">活跃领域</span>
        <span class="evo-val">{{ status.top_domains.map(d => d[0]).join(', ') }}</span>
      </div>
      <div v-if="status.role_stats?.length" class="evo-row">
        <span class="evo-key">角色</span>
        <span class="evo-val">{{ status.role_stats.map(r => `${r[0]}(${r[1]})`).join(', ') }}</span>
      </div>
      <!-- 最近成就 -->
      <div v-if="achievements?.length" class="evo-achievements">
        <div class="evo-key mb-1">最近成就</div>
        <div v-for="a in achievements" :key="a.id" class="evo-achievement">
          <span class="evo-badge">🏆</span>
          <span class="evo-achievement-text">{{ a.description || a.name }}</span>
        </div>
      </div>
      <!-- DAG 关系图 -->
      <div v-if="dagData?.edges?.length" class="evo-dag">
        <div class="evo-key mb-1">关联图谱 ({{ dagData.totals?.edges || 0 }} 条边)</div>
        <div class="evo-dag-graph">
          <div v-for="e in dagData.edges" :key="`${e.source_type}-${e.target_type}-${e.rel_type}`" class="evo-dag-row">
            <span class="evo-node evo-node-src">{{ nodeTypeLabel(e.source_type) }}</span>
            <span class="evo-link-line"></span>
            <span class="evo-link-label">{{ edgeTypeLabel(e.rel_type) }}</span>
            <span class="evo-link-line"></span>
            <span class="evo-node evo-node-tgt">{{ nodeTypeLabel(e.target_type) }}</span>
            <span class="evo-edge-count">{{ e.count }}</span>
          </div>
        </div>
      </div>
      <!-- 热门检索文档 -->
      <div v-if="dagData?.top_documents?.length" class="evo-dag">
        <div class="evo-key mb-1">热门检索文档</div>
        <div v-for="d in dagData.top_documents" :key="d.doc_id" class="evo-edge">
          <span class="evo-edge-src">📄 #{{ d.doc_id }}</span>
          <span class="evo-edge-count">{{ d.query_count }} 次检索</span>
        </div>
      </div>
      <!-- 反模式 TOP -->
      <div v-if="dagData?.anti_patterns?.length" class="evo-dag">
        <div class="evo-key mb-1">高频反模式</div>
        <div v-for="ap in dagData.anti_patterns" :key="ap.id" class="evo-edge">
          <span class="evo-edge-src">⚠️ {{ ap.pattern }}</span>
          <span class="evo-edge-count">{{ ap.frequency }}次</span>
        </div>
      </div>
      <!-- 策略表现 -->
      <div v-if="status?.top_strategies?.length" class="evo-dag">
        <div class="evo-key mb-1">策略表现 ({{ status.strategies_count || 0 }} 个策略)</div>
        <div v-for="s in status.top_strategies" :key="s[1]" class="evo-edge">
          <span class="evo-edge-src">📋 {{ s[0] }}</span>
          <span class="evo-edge-type">{{ (s[2] * 100).toFixed(0) }}%</span>
          <span class="evo-edge-count">{{ s[3] }}次</span>
        </div>
      </div>
      <!-- 最近失败 -->
      <div v-if="status?.recent_failures?.length" class="evo-dag">
        <div class="evo-key mb-1">最近失败</div>
        <div v-for="f in status.recent_failures" :key="`${f[0]}-${f[1]}`" class="evo-edge">
          <span class="evo-edge-src">❌ {{ f[0] }}</span>
          <span class="evo-edge-type">{{ f[1] }}</span>
          <span class="evo-edge-count">{{ f[2] }}次</span>
        </div>
      </div>
      <!-- ── 涌现系统：Richness + 能力 + 技能确认 ── -->
      <div v-if="emergenceData" class="evo-dag evo-emergence">
        <div class="evo-key mb-1">涌现系统</div>

        <!-- 健康状态 -->
        <div v-if="healthStatus" class="evo-row">
          <span class="evo-key">链路状态</span>
          <span class="evo-val" :class="healthStatus.color">{{ healthStatus.text }}</span>
        </div>

        <!-- 跨会话连续性 -->
        <div v-if="emergenceData.continuity" class="evo-row">
          <span class="evo-key">跨会话</span>
          <span class="evo-val" :class="emergenceData.continuity.has_snapshot ? 'text-green-400' : 'text-gray-400'">
            {{ emergenceData.continuity.has_snapshot ? '有快照' : '无快照' }}
          </span>
        </div>

        <!-- Richness 信号 -->
        <div v-if="emergenceData.richness" class="evo-row">
          <span class="evo-key">记忆丰富度</span>
          <span class="evo-val" :class="richnessTierColor(emergenceData.richness.tier)">
            {{ richnessTierLabel(emergenceData.richness.tier) }}
            <span class="text-xs opacity-60">({{ emergenceData.richness.score }})</span>
          </span>
        </div>

        <!-- Richness 4 维进度条 -->
        <div v-if="emergenceData.richness" class="evo-richness-bars">
          <div class="evo-richness-bar">
            <span class="evo-richness-label">事件密度</span>
            <div class="evo-mini-bar"><div class="evo-mini-fill bg-blue-400" :style="{ width: Math.min(100, emergenceData.richness.raw_event_density * 100) + '%' }"></div></div>
          </div>
          <div class="evo-richness-bar">
            <span class="evo-richness-label">簇密度</span>
            <div class="evo-mini-bar"><div class="evo-mini-fill bg-green-400" :style="{ width: Math.min(100, emergenceData.richness.cluster_density * 100) + '%' }"></div></div>
          </div>
          <div class="evo-richness-bar">
            <span class="evo-richness-label">会话密度</span>
            <div class="evo-mini-bar"><div class="evo-mini-fill bg-purple-400" :style="{ width: Math.min(100, emergenceData.richness.session_density * 100) + '%' }"></div></div>
          </div>
          <div class="evo-richness-bar">
            <span class="evo-richness-label">交接密度</span>
            <div class="evo-mini-bar"><div class="evo-mini-fill bg-yellow-400" :style="{ width: Math.min(100, emergenceData.richness.handoff_density * 100) + '%' }"></div></div>
          </div>
        </div>

        <!-- 簇统计一列 -->

        <!-- autoResolve 策略阈值（只读，来自 config.yaml） -->
        <div v-if="emergenceData.autoResolve" class="evo-row">
          <span class="evo-key">自动处置阈值</span>
          <span class="evo-val text-xs">
            <span class="evo-cluster-chip">重复≥{{ emergenceData.autoResolve.duplicate }}</span>
            <span class="evo-cluster-chip">过时≥{{ emergenceData.autoResolve.outdated }}</span>
            <span class="evo-cluster-chip">簇间隔≥{{ emergenceData.autoResolve.cluster_min_interval }}s</span>
          </span>
        </div>

        <!-- 簇统计 -->
        <div v-if="emergenceData.clusters && Object.keys(emergenceData.clusters).length" class="evo-row">
          <span class="evo-key">簇状态</span>
          <span class="evo-val text-xs">
            <span v-for="(cnt, state) in emergenceData.clusters" :key="state" class="evo-cluster-chip">
              {{ state }}: {{ cnt }}
            </span>
          </span>
        </div>

        <!-- 能力注册表 -->
        <div v-if="emergenceData.capabilities?.active?.length" class="evo-row">
          <span class="evo-key">已激活能力</span>
          <span class="evo-val">
            <span v-for="cap in emergenceData.capabilities.active" :key="cap" class="evo-cap-chip evo-cap-active">{{ cap }}</span>
          </span>
        </div>
        <div v-if="emergenceData.capabilities?.installed?.length" class="evo-row">
          <span class="evo-key">已安装能力</span>
          <span class="evo-val">
            <span v-for="cap in emergenceData.capabilities.installed" :key="cap" class="evo-cap-chip">{{ cap }}</span>
          </span>
        </div>
      </div>

      <!-- ── 技能确认交互 ── -->
      <div v-if="skillsData?.pending?.length" class="evo-dag evo-skills-pending">
        <div class="evo-key mb-1">待确认技能 ({{ skillsData.pending.length }})</div>
        <div v-for="s in skillsData.pending" :key="s.id" class="evo-skill-card">
          <div class="evo-skill-info">
            <div class="evo-skill-name">{{ s.name }}</div>
            <div class="evo-skill-desc text-xs text-gray-400">{{ s.description }}</div>
            <div v-if="s.reason" class="evo-skill-reason text-[11px] text-amber-500/80 mt-0.5">{{ s.reason }}</div>
          </div>
          <div class="evo-skill-meta">
            <span v-if="s.usage_count" class="evo-skill-stat">×{{ s.usage_count }}</span>
            <span v-if="s.success_rate" class="evo-skill-stat">{{ Math.round(s.success_rate * 100) }}%</span>
          </div>
          <div class="evo-skill-actions">
            <button class="evo-btn-confirm" @click="confirmSkill(s.id)">采纳</button>
            <button class="evo-btn-reject" @click="rejectSkill(s.id)">忽略</button>
          </div>
        </div>
      </div>

      <!-- 已激活技能列表 -->
      <div v-if="skillsData?.active?.length" class="evo-dag">
        <div class="evo-key mb-1">已激活技能 ({{ skillsData.active.length }})</div>
        <div v-for="s in skillsData.active" :key="s.id" class="evo-skill-row">
          <span class="evo-skill-badge" :class="{ 'evo-badge-proven': s.grade === 'proven' }">
            {{ s.grade === 'proven' ? '⚡proven' : '⚡' }}
          </span>
          <span class="evo-edge-src">{{ s.name }}</span>
          <span v-if="s.usage_count" class="evo-skill-stat">×{{ s.usage_count }}</span>
          <span v-if="s.success_rate" class="evo-skill-stat">{{ Math.round(s.success_rate * 100) }}%</span>
          <span class="evo-edge-count">{{ s.description?.slice(0, 36) }}</span>
        </div>
      </div>

      <!-- ── 学习成长图谱（技能+记忆节点，对标 learning_graph）── -->
      <div v-if="graphData?.totals?.nodes" class="evo-dag evo-growth-graph">
        <div class="evo-key mb-1">
          成长图谱 ({{ graphData.totals.skills || 0 }} 技能 · {{ graphData.totals.memories || 0 }} 记忆 · {{ graphData.totals.edges || 0 }} 条关联)
        </div>
        <div class="evo-graph-nodes">
          <div v-for="n in graphData.nodes" :key="n.id" class="evo-graph-node" :class="`evo-node-${n.kind}`">
            <span class="evo-node-icon">{{ n.kind === 'skill' ? (n.grade === 'proven' ? '⚡' : (n.status === 'stale' ? '⚠️' : '💡')) : '🧠' }}</span>
            <span class="evo-node-label">{{ n.label }}</span>
            <span v-if="n.kind === 'skill' && n.usage_count" class="evo-node-stat">×{{ n.usage_count }}</span>
          </div>
        </div>
        <div v-if="graphData.timeline?.length" class="evo-growth-timeline">
          <div class="evo-key mb-1 mt-2">成长轨迹 ({{ graphData.timeline.length }})</div>
          <div v-for="(t, i) in graphData.timeline.slice(0, 10)" :key="i" class="evo-timeline-row">
            <span class="evo-timeline-action">{{ timelineActionLabel(t.action) }}</span>
            <span class="evo-timeline-name">{{ t.skill_name }}</span>
            <span v-if="t.detail" class="evo-timeline-detail text-gray-400">{{ t.detail.slice(0, 40) }}</span>
          </div>
        </div>
      </div>

      <!-- ── 自我改写审批日志 ── -->
      <div v-if="selfModifyHistory?.length" class="evo-dag evo-selfmod">
        <div class="evo-key mb-1">自我改写审批 ({{ selfModifyHistory.length }})</div>
        <div v-for="(e, i) in selfModifyHistory" :key="i"
          class="evo-edge evo-sm-row" :class="{ 'evo-sm-row--danger': e.type === 'self_modify_rollback' }">
          <span class="evo-sm-type" :class="{ 'evo-sm-type--danger': e.type === 'self_modify_rollback' }">
            {{ smTypeLabel(e.type) }}
          </span>
          <span class="evo-sm-status" :class="smStatusColor(e.status)">{{ smStatusLabel(e.status) }}</span>
          <span class="evo-sm-target" :title="e.target || e.detail">{{ e.target || e.detail }}</span>
          <button v-if="e.status === 'committed' && e.backup" class="evo-btn-rollback"
            @click="rollbackChange(e.target, e.backup)" :title="`回滚: ${e.target}`">回滚</button>
          <button v-if="e.status === 'activated'" class="evo-btn-retract"
            @click="retractCapability(e.target)" :title="`撤回: ${e.target}`">撤回</button>
          <span class="evo-sm-time">{{ (e.timestamp || '').slice(5, 16).replace('T', ' ') }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 微型指示器 */
.evolution-mini {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin: 0 0.75rem 0.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.375rem;
  cursor: pointer;
  user-select: none;
  font-size: 0.6875rem;
  color: #9ca3af;
  transition: background 0.15s;
}
.evolution-mini:hover {
  background: rgba(34, 197, 94, 0.06);
}
.dark .evolution-mini:hover {
  background: rgba(34, 197, 94, 0.1);
}
.evo-mini-icon { font-size: 0.75rem; }
.evo-mini-text {
  color: #6b7280;
  font-weight: 500;
}
.dark .evo-mini-text { color: #9ca3af; }
.evo-mini-expand {
  margin-left: auto;
  font-size: 0.5625rem;
  opacity: 0.6;
}
/* 完整面板 */
.evolution-panel {
  margin: 0 0.75rem 0.75rem;
  padding: 0.625rem;
  border-radius: 0.75rem;
  background: rgba(34, 197, 94, 0.03);
  border: 1px solid rgba(34, 197, 94, 0.15);
}
.dark .evolution-panel {
  background: rgba(34, 197, 94, 0.05);
  border-color: rgba(34, 197, 94, 0.2);
}
.evo-header {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  margin-bottom: 0.5rem;
  cursor: pointer;
  user-select: none;
}
.evo-icon { font-size: 0.875rem; }
.evo-title {
  font-size: 0.75rem;
  font-weight: 600;
  color: #6b7280;
}
.dark .evo-title { color: #9ca3af; }
.evo-expand {
  margin-left: auto;
  font-size: 0.625rem;
  color: #9ca3af;
}
.evo-collapse {
  font-size: 0.5625rem;
  color: #9ca3af;
  cursor: pointer;
  padding: 0.0625rem 0.25rem;
  border-radius: 0.25rem;
  transition: background 0.15s;
}
.evo-collapse:hover {
  background: rgba(156, 163, 175, 0.15);
}
.evo-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.5rem;
}
.evo-card {
  text-align: center;
  padding: 0.375rem;
  border-radius: 0.5rem;
  background: rgba(255, 255, 255, 0.6);
}
.dark .evo-card {
  background: rgba(31, 41, 55, 0.5);
}
.evo-value {
  font-size: 1.125rem;
  font-weight: 700;
  color: #1f2937;
}
.dark .evo-value { color: #e5e7eb; }
.evo-label {
  font-size: 0.625rem;
  color: #9ca3af;
  margin-top: 0.125rem;
}
.evo-progress {
  margin-top: 0.5rem;
  height: 0.375rem;
  border-radius: 9999px;
  background: rgba(229, 231, 235, 0.8);
  overflow: hidden;
}
.dark .evo-progress { background: rgba(55, 65, 81, 0.8); }
.evo-progress-bar {
  height: 100%;
  border-radius: 9999px;
  transition: width 0.5s ease;
}
.evo-detail {
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px solid rgba(229, 231, 235, 0.5);
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.dark .evo-detail { border-top-color: rgba(55, 65, 81, 0.5); }
.evo-row {
  display: flex;
  justify-content: space-between;
  font-size: 0.6875rem;
}
.evo-key { color: #9ca3af; }
.evo-val { color: #4b5563; }
.dark .evo-val { color: #d1d5db; }
.evo-achievements {
  margin-top: 0.5rem;
  padding-top: 0.375rem;
  border-top: 1px solid rgba(229, 231, 235, 0.3);
}
.dark .evo-achievements { border-top-color: rgba(55, 65, 81, 0.3); }
.evo-achievement {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.6875rem;
  color: #92400e;
  padding: 0.125rem 0;
}
.dark .evo-achievement { color: #fbbf24; }
.evo-badge { font-size: 0.75rem; }
.evo-achievement-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evo-dag {
  margin-top: 0.5rem;
  padding-top: 0.375rem;
  border-top: 1px solid rgba(229, 231, 235, 0.3);
}
.dark .evo-dag { border-top-color: rgba(55, 65, 81, 0.3); }
.evo-edge {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.625rem;
  color: #6b7280;
  padding: 0.125rem 0;
}
.dark .evo-edge { color: #9ca3af; }
.evo-edge-src { color: #4b5563; }
.dark .evo-edge-src { color: #d1d5db; }
.evo-edge-arrow { color: #22c55e; font-weight: 600; }
.evo-edge-tgt { color: #4b5563; }
.dark .evo-edge-tgt { color: #d1d5db; }
.evo-edge-type {
  margin-left: auto;
  font-size: 0.5625rem;
  color: #9ca3af;
  background: rgba(34, 197, 94, 0.08);
  padding: 0.0625rem 0.25rem;
  border-radius: 0.25rem;
}
.evo-edge-count {
  font-weight: 600;
  color: #22c55e;
  min-width: 2rem;
  text-align: right;
}
/* DAG 图形化 */
.evo-dag-graph {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.evo-dag-row {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.625rem;
}
.evo-node {
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-weight: 500;
  white-space: nowrap;
}
.evo-node-src {
  background: rgba(34, 197, 94, 0.12);
  color: #16a34a;
}
.dark .evo-node-src {
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
}
.evo-node-tgt {
  background: rgba(59, 130, 246, 0.12);
  color: #2563eb;
}
.dark .evo-node-tgt {
  background: rgba(59, 130, 246, 0.15);
  color: #60a5fa;
}
.evo-link-line {
  flex: 1;
  min-width: 0.5rem;
  height: 1px;
  background: rgba(156, 163, 175, 0.4);
}
.dark .evo-link-line {
  background: rgba(75, 85, 99, 0.6);
}
.evo-link-label {
  font-size: 0.5625rem;
  color: #9ca3af;
  background: rgba(34, 197, 94, 0.06);
  padding: 0.0625rem 0.25rem;
  border-radius: 0.25rem;
  white-space: nowrap;
}

/* ── 涌现系统样式 ── */
.evo-emergence {
  border-left: 2px solid #22c55e;
  padding-left: 0.5rem;
}
.evo-richness-bars {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0.25rem 0;
}
.evo-richness-bar {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.5625rem;
}
.evo-richness-label {
  width: 3.5rem;
  color: #6b7280;
  flex-shrink: 0;
}
.dark .evo-richness-label {
  color: #9ca3af;
}
.evo-mini-bar {
  flex: 1;
  height: 3px;
  border-radius: 2px;
  background: rgba(156, 163, 175, 0.2);
  overflow: hidden;
}
.dark .evo-mini-bar {
  background: rgba(75, 85, 99, 0.3);
}
.evo-mini-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}
.evo-cluster-chip {
  display: inline-block;
  padding: 0.0625rem 0.375rem;
  margin: 0 0.125rem;
  border-radius: 0.25rem;
  background: rgba(34, 197, 94, 0.1);
  color: #22c55e;
  font-size: 0.5625rem;
}
.dark .evo-cluster-chip {
  background: rgba(34, 197, 94, 0.15);
  color: #4ade80;
}
.evo-cap-chip {
  display: inline-block;
  padding: 0.0625rem 0.375rem;
  margin: 0 0.125rem;
  border-radius: 0.25rem;
  background: rgba(59, 130, 246, 0.1);
  color: #3b82f6;
  font-size: 0.5625rem;
}
.dark .evo-cap-chip {
  background: rgba(59, 130, 246, 0.15);
  color: #60a5fa;
}
.evo-cap-active {
  background: rgba(34, 197, 94, 0.12);
  color: #22c55e;
}
.dark .evo-cap-active {
  background: rgba(34, 197, 94, 0.2);
  color: #4ade80;
}

/* ── 技能确认交互 ── */
.evo-skills-pending {
  border-left: 2px solid #f59e0b;
  padding-left: 0.5rem;
}
.evo-skill-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.375rem;
  margin: 0.25rem 0;
  border-radius: 0.375rem;
  background: rgba(245, 158, 11, 0.06);
}
.dark .evo-skill-card {
  background: rgba(245, 158, 11, 0.08);
}
.evo-skill-info {
  flex: 1;
  min-width: 0;
}
.evo-skill-name {
  font-size: 0.75rem;
  font-weight: 500;
  color: #f59e0b;
}
.dark .evo-skill-name {
  color: #fbbf24;
}
.evo-skill-desc {
  margin-top: 0.125rem;
  line-height: 1.2;
}
.evo-skill-reason { line-height: 1.3; }
.evo-skill-meta {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}
.evo-skill-stat {
  font-size: 0.625rem;
  color: #6b7280;
  background: rgba(107, 114, 128, 0.12);
  padding: 0.0625rem 0.3125rem;
  border-radius: 0.25rem;
  white-space: nowrap;
}
.dark .evo-skill-stat { color: #9ca3af; background: rgba(156, 163, 175, 0.12); }
.evo-skill-row {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
  padding: 0.25rem 0;
  font-size: 0.7rem;
}
.evo-skill-badge {
  font-size: 0.625rem;
  color: #6b7280;
  flex-shrink: 0;
}
.evo-badge-proven { color: #10b981; font-weight: 600; }
.dark .evo-badge-proven { color: #34d399; }

/* 学习成长图谱 */
.evo-growth-graph { border-left: 2px solid #8b5cf6; padding-left: 0.5rem; }
.evo-graph-nodes {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  max-height: 200px;
  overflow-y: auto;
}
.evo-graph-node {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.7rem;
  padding: 0.125rem 0.25rem;
  border-radius: 0.25rem;
}
.evo-node-skill { background: rgba(139, 92, 246, 0.06); }
.dark .evo-node-skill { background: rgba(139, 92, 246, 0.12); }
.evo-node-memory { background: rgba(16, 185, 129, 0.06); }
.dark .evo-node-memory { background: rgba(16, 185, 129, 0.12); }
.evo-node-icon { flex-shrink: 0; }
.evo-node-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #374151;
}
.dark .evo-node-label { color: #d1d5db; }
.evo-node-stat {
  font-size: 0.625rem;
  color: #6b7280;
  flex-shrink: 0;
}
.dark .evo-node-stat { color: #9ca3af; }
.evo-growth-timeline { margin-top: 0.5rem; }
.evo-timeline-row {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.7rem;
  padding: 0.125rem 0;
}
.evo-timeline-action {
  color: #8b5cf6;
  font-size: 0.625rem;
  flex-shrink: 0;
}
.dark .evo-timeline-action { color: #a78bfa; }
.evo-timeline-name { color: #374151; }
.dark .evo-timeline-name { color: #d1d5db; }
.evo-timeline-detail { font-size: 0.625rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.evo-skill-actions {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}
.evo-btn-confirm, .evo-btn-reject {
  font-size: 0.625rem;
  padding: 0.1875rem 0.5rem;
  border-radius: 0.25rem;
  border: none;
  cursor: pointer;
  transition: opacity 0.15s;
}
.evo-btn-confirm {
  background: #22c55e;
  color: white;
}
.evo-btn-confirm:hover {
  opacity: 0.85;
}
.evo-btn-reject {
  background: rgba(156, 163, 175, 0.2);
  color: #6b7280;
}
.dark .evo-btn-reject {
  background: rgba(75, 85, 99, 0.3);
  color: #9ca3af;
}
.evo-btn-reject:hover {
  opacity: 0.85;
}

/* ── 自我改写审批日志 ── */
.evo-selfmod {
  border-left: 2px solid #8b5cf6;
  padding-left: 0.5rem;
}
.evo-sm-row {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.625rem;
  padding: 0.1875rem 0;
}
.evo-sm-type {
  flex-shrink: 0;
  width: 3.5rem;
  color: #8b5cf6;
  font-weight: 500;
}
.dark .evo-sm-type { color: #a78bfa; }
.evo-sm-status {
  flex-shrink: 0;
  width: 4.5rem;
  font-weight: 500;
}
.evo-sm-target {
  flex: 1;
  min-width: 0;
  color: #6b7280;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dark .evo-sm-target { color: #9ca3af; }
.evo-sm-time {
  flex-shrink: 0;
  color: #9ca3af;
  font-size: 0.5625rem;
  font-variant-numeric: tabular-nums;
}
.evo-btn-rollback {
  flex-shrink: 0;
  font-size: 0.5625rem;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  border: 1px solid rgba(168, 85, 247, 0.4);
  background: rgba(168, 85, 247, 0.1);
  color: #8b5cf6;
  cursor: pointer;
  transition: opacity 0.15s;
}
.dark .evo-btn-rollback {
  background: rgba(168, 85, 247, 0.18);
  color: #a78bfa;
}
.evo-btn-rollback:hover {
  opacity: 0.85;
}
/* Danger styling for rollback rows */
.evo-sm-row--danger {
  background: rgba(239, 68, 68, 0.04);
  border-left: 2px solid rgba(239, 68, 68, 0.3);
}
.dark .evo-sm-row--danger {
  background: rgba(239, 68, 68, 0.08);
  border-left-color: rgba(239, 68, 68, 0.5);
}
.evo-sm-type--danger {
  color: #ef4444;
  font-weight: 500;
}
/* Retract button for capabilities */
.evo-btn-retract {
  flex-shrink: 0;
  font-size: 0.5625rem;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  border: 1px solid rgba(245, 158, 11, 0.4);
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
  cursor: pointer;
  transition: opacity 0.15s;
}
.dark .evo-btn-retract {
  background: rgba(245, 158, 11, 0.18);
  color: #f59e0b;
}
.evo-btn-retract:hover {
  opacity: 0.85;
}

/* ── T5 变更通知中心：L1 已自动调整 + L2 待审提案 ─────────────────── */
/* 折叠态角标：面板收起时唯一的「有事发生」信号 */
.evo-mini-badge {
  flex-shrink: 0;
  min-width: 0.875rem;
  padding: 0 0.25rem;
  border-radius: 0.5rem;
  background: #f59e0b;
  color: #fff;
  font-size: 0.5625rem;
  line-height: 0.875rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
.evo-mini-badge--pending {
  background: rgba(107, 114, 128, 0.75);
}
.dark .evo-mini-badge--pending {
  background: rgba(156, 163, 175, 0.6);
}

.evo-changes {
  margin-top: 0.5rem;
  padding-top: 0.375rem;
  border-top: 1px solid rgba(229, 231, 235, 0.3);
}
.dark .evo-changes { border-top-color: rgba(55, 65, 81, 0.3); }
.evo-changes-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.375rem;
  margin-bottom: 0.25rem;
}
.evo-changes-hint {
  font-weight: 400;
  color: #9ca3af;
  font-size: 0.5625rem;
}
.evo-btn-read {
  flex-shrink: 0;
  font-size: 0.5625rem;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
  border: 1px solid rgba(156, 163, 175, 0.35);
  background: transparent;
  color: #6b7280;
  cursor: pointer;
  transition: opacity 0.15s;
}
.dark .evo-btn-read { color: #9ca3af; }
.evo-btn-read:hover { opacity: 0.75; }

.evo-change-card {
  display: flex;
  align-items: flex-start;
  gap: 0.375rem;
  padding: 0.1875rem 0.25rem;
  border-radius: 0.25rem;
  font-size: 0.625rem;
}
.evo-change--unread {
  background: rgba(245, 158, 11, 0.06);
  border-left: 2px solid rgba(245, 158, 11, 0.35);
}
.dark .evo-change--unread {
  background: rgba(245, 158, 11, 0.1);
  border-left-color: rgba(245, 158, 11, 0.5);
}
.evo-change-main { flex: 1; min-width: 0; }
.evo-change-title {
  display: flex;
  align-items: center;
  gap: 0.25rem;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dark .evo-change-title { color: #d1d5db; }
.evo-change-desc {
  color: #9ca3af;
  font-size: 0.5625rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.evo-change-why {
  color: #d97706;
  font-size: 0.5625rem;
  margin-top: 0.0625rem;
}
.dark .evo-change-why { color: #f59e0b; }
.evo-dot {
  flex-shrink: 0;
  width: 0.3125rem;
  height: 0.3125rem;
  border-radius: 50%;
  background: #f59e0b;
}
.evo-change-expired {
  flex-shrink: 0;
  font-size: 0.5625rem;
  color: #9ca3af;
  padding: 0.0625rem 0.375rem;
}
.evo-proposals .evo-change-card {
  background: rgba(59, 130, 246, 0.05);
  border-left: 2px solid rgba(59, 130, 246, 0.3);
  margin-bottom: 0.125rem;
}
.dark .evo-proposals .evo-change-card {
  background: rgba(59, 130, 246, 0.1);
  border-left-color: rgba(59, 130, 246, 0.5);
}
</style>
