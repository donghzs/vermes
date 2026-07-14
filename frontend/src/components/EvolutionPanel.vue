<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'

const status = ref(null)
const loading = ref(true)
const expanded = ref(false)
const achievements = ref([])
const dagData = ref(null)
const collapsed = ref(true) // 默认折叠为微型指示器，点击展开详情
const emergenceData = ref(null)
const skillsData = ref(null)

async function fetchStatus() {
  try {
    const r = await fetch('/api/evolution/status')
    if (r.ok) {
      status.value = await r.json()
    }
  } catch {
    // 静默失败
  } finally {
    loading.value = false
  }
}

async function fetchAchievements() {
  try {
    const r = await fetch('/api/evolution/achievements?limit=5')
    if (r.ok) {
      achievements.value = await r.json()
    }
  } catch {
    // 静默失败
  }
}

async function fetchDag() {
  try {
    const r = await fetch('/api/evolution/dag?limit=50')
    if (r.ok) {
      dagData.value = await r.json()
    }
  } catch {
    // 静默失败
  }
}

async function fetchEmergence() {
  try {
    const r = await fetch('/api/emergence/status')
    if (r.ok) {
      emergenceData.value = await r.json()
    }
  } catch {
    // 静默失败
  }
}

async function fetchSkills() {
  try {
    const r = await fetch('/api/emergence/skills')
    if (r.ok) {
      skillsData.value = await r.json()
    }
  } catch {
    // 静默失败
  }
}

async function confirmSkill(id) {
  try {
    await fetch(`/api/emergence/skill/${id}/confirm`, { method: 'POST' })
    await fetchSkills()
  } catch {
    // 静默失败
  }
}

async function rejectSkill(id) {
  try {
    await fetch(`/api/emergence/skill/${id}/reject`, { method: 'POST' })
    await fetchSkills()
  } catch {
    // 静默失败
  }
}

onMounted(() => {
  fetchStatus()
  fetchAchievements()
  fetchDag()
  fetchEmergence()
  fetchSkills()
  // 每 30 秒刷新
  const timer = setInterval(() => {
    fetchStatus()
    fetchEmergence()
  }, 30000)
  onUnmounted(() => clearInterval(timer))
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
</script>

<template>
  <!-- 微型指示器模式（默认）：仅一行小字 -->
  <div v-if="!loading && status?.active && collapsed" class="evolution-mini" @click="collapsed = false">
    <span class="evo-mini-icon">🧠</span>
    <span class="evo-mini-text">{{ status.total_outcomes || 0 }} 次 · {{ status.success_rate || 0 }}%</span>
    <span class="evo-mini-expand">▶</span>
  </div>
  <!-- 完整面板模式（点击展开） -->
  <div v-if="!loading && status?.active && !collapsed" class="evolution-panel">
    <div class="evo-header" @click="expanded = !expanded">
      <span class="evo-icon">🧠</span>
      <span class="evo-title">进化系统</span>
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
    </div>
    <div class="evo-progress">
      <div class="evo-progress-bar" :class="progressBarColor"
           :style="{ width: Math.min(100, status.success_rate || 0) + '%' }">
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
        <div v-for="s in skillsData.active" :key="s.id" class="evo-edge">
          <span class="evo-edge-src">⚡ {{ s.name }}</span>
          <span class="evo-edge-count">{{ s.description?.slice(0, 40) }}</span>
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
</style>
