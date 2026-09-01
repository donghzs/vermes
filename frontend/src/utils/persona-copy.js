/**
 * G13 人格叙事 copy system — Vermes 做"人"语态常量
 *
 * 定位：感知缺口修复的顶层叙事契约。所有进化/记忆/底线相关 UI 文案
 *   统一从本文件取，避免走回仪表盘语言。
 *
 * 三原则：会学 · 有谱 · 有界
 * 禁用词：系统/指标/状态面板/dashboard
 * 规范：数字可保留但必须有人话前缀，不裸奔 metric
 */

// ── 微指示器人格化状态（折叠态一行文案）──────────────────────
export function personaMiniStatus(status, emergenceData, unreadCount, proposalCount) {
  const outcomes = status?.total_outcomes || 0
  const successRate = status?.success_rate || 0
  // 有新涌现簇（clusters 是 {stage: count} 对象，不是数组）
  const newClusters = emergenceData?.clusters?.emerging || 0

  // 有待审 → 优先提醒
  if (proposalCount > 0) {
    return { icon: '🤝', text: `${proposalCount} 件事想跟你确认`, badge: proposalCount, badgeKind: 'pending' }
  }
  // 有未读自动调整
  if (unreadCount > 0) {
    return { icon: '🌱', text: `刚长了 ${unreadCount} 个新理解`, badge: unreadCount, badgeKind: 'unread' }
  }
  // 有新涌现簇
  if (newClusters > 0) {
    return { icon: '🌱', text: `正在长新本事（${newClusters} 个新理解冒头）`, badge: 0 }
  }
  // 常规状态
  if (outcomes === 0) {
    return { icon: '🧠', text: '刚醒来，准备好跟你学', badge: 0 }
  }
  if (successRate >= 90) {
    return { icon: '✨', text: `办成的事十有九成半（${outcomes} 次）`, badge: 0 }
  }
  if (successRate >= 70) {
    return { icon: '🧠', text: `帮你办了 ${outcomes} 次，还在越做越好`, badge: 0 }
  }
  return { icon: '🧠', text: `帮你办了 ${outcomes} 次，正在摸清你的节奏`, badge: 0 }
}

// ── 展开区标题与人格化标签 ──────────────────────────────────────
export const personaTitles = {
  evolution: '我的成长',
  capabilities: '我擅长什么',
  memory: '我懂你',
  boundaries: '我的分寸',
  changes: '我改过自己的地方，都记着',
  proposals: '拿不准的事，想跟你确认',
  skills: '我在学的新本事',
  graph: '成长轨迹',
  selfModify: '自我改写记录',
}

// ── 仪表盘词 → 人格化映射 ─────────────────────────────────────
export const personaLabels = {
  // 指标
  total_outcomes: '帮你办的事',
  success_rate: '办成率',
  verified_rate: '靠谱率',
  // 丰富度 tier
  cold_start: '刚认识你',
  building: '在了解你',
  learning: '挺懂你了',
  fluent: '很懂你了',
  // 健康
  healthy: '我在长新本事',
  stale: '这阵子没新东西冒出来，正常',
  cold: '刚启动，还在热身',
  // 能力
  active: '我擅长',
  installed: '已装好',
  not_installed: '还没学',
  failed: '没装成功',
  built_in: '内置的',
  // 变更类型
  config_auto_apply: '我自动调了',
  config_applied: '你批准后我改了',
  skill_adopted: '我学会了新技能',
  capability_activated: '我激活了新能力',
  source_modify: '我改了自己的代码',
  rollback: '我回滚了',
  // 状态
  committed: '已应用',
  proposed: '待你确认',
  held: '还在观察',
  rejected: '你说不行',
  rolled_back: '已回滚',
  retracted: '已撤回',
  // 技能
  emerging: '正在冒头',
  stable: '已经稳了',
  promoted: '晋升了',
  demoted: '降级了',
  reactivated: '又用起来了',
  // 情绪
  caused_emotion: '引发情绪',
  triggered: '触发反模式',
  queried: '查阅文档',
  retrieved: '检索记忆',
}

// ── 底线理由模板（审批对话框用）──────────────────────────────
export function personaApprovalReason(kind, detail) {
  const reasons = {
    source_modify: `这是改我自己代码的操作，我的底线要求你点头才动手`,
    high_risk: `这事风险偏高，我拿不准该不该做，你定`,
    medium_risk: `这操作有一定影响，跟你确认一下再办`,
    skill_install: `要装新技能了，让你看看安不安全`,
    config_change: `要改配置了，怕动了你不想要的参数`,
  }
  return reasons[kind] || `这件事我做了会影响系统，按规矩得你点头`
}

// ── 成长轻提示（接 change SSE 事件，对话内主动推）──────────────
export function personaChangeNotify(change) {
  const kind = change?.kind
  const title = change?.title || ''
  const summary = change?.summary || ''

  const templates = {
    config_auto_apply: { emoji: '🔧', text: `我刚调了一下${title}：${summary}` },
    skill_adopted: { emoji: '📖', text: `我学会了${title}` },
    capability_activated: { emoji: '✨', text: `我激活了${title}能力` },
    source_modify: { emoji: '✏️', text: `我改了自己的${title}` },
    rollback: { emoji: '↩️', text: `我把${title}回滚了` },
    memory_learned: { emoji: '🌱', text: `${title}${summary ? '：' + summary : ''}` },
  }
  return templates[kind] || { emoji: '📝', text: `${title}${summary ? '：' + summary : ''}` }
}

// ── 跨会话记忆人格化 ──────────────────────────────────────────
export function personaContinuity(continuity) {
  if (!continuity?.has_snapshot) {
    return '我们第一次聊，我记着呢'
  }
  const parts = []
  if (continuity.last_project) parts.push(`上次在做「${continuity.last_project}」`)
  if (continuity.last_topic) parts.push(`聊到${continuity.last_topic}`)
  if (continuity.preference_count) parts.push(`记了你 ${continuity.preference_count} 个偏好`)
  return parts.length ? `我记得${parts.join('，')}` : '我记得我们上次的交流'
}
