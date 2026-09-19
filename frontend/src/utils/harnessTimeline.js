/**
 * C1 — 时间线 harness 分类 / 筛选纯函数（MessageList 消费）。
 * 缺 harness 信号 → class='none'，筛选「未核验」时包含，不编造成功。
 */

export function harnessClassOf(tool) {
  if (!tool) return 'none'
  const h = tool.harness
  if (tool.status === 'error' || tool.is_error || tool.phase === 'error') return 'error'
  if (!h) return 'none'
  if (h.precheck === 'blocked' || tool.phase === 'blocked') return 'blocked'
  if (h.outcome === 'verified') return 'verified'
  if (h.outcome === 'verify_failed' || h.outcome === 'verifier_error') return 'fail'
  if (h.outcome === 'unverified_tool') return 'unverified'
  return 'unknown'
}

export const HARNESS_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'verified', label: '已核验' },
  { id: 'unverified', label: '未核验' },
  { id: 'fail', label: '失败' },
  { id: 'blocked', label: '拦截' },
]

export function filterToolsByHarness(tools, filterId) {
  const list = tools || []
  if (!filterId || filterId === 'all') return list
  return list.filter((t) => {
    const c = harnessClassOf(t)
    if (filterId === 'verified') return c === 'verified'
    if (filterId === 'unverified') return c === 'unverified' || c === 'none' || c === 'unknown'
    if (filterId === 'fail') return c === 'fail' || c === 'error'
    if (filterId === 'blocked') return c === 'blocked'
    return true
  })
}

export function harnessFilterCounts(tools) {
  const counts = { all: 0, verified: 0, unverified: 0, fail: 0, blocked: 0 }
  for (const t of tools || []) {
    counts.all += 1
    const c = harnessClassOf(t)
    if (c === 'verified') counts.verified += 1
    else if (c === 'fail' || c === 'error') counts.fail += 1
    else if (c === 'blocked') counts.blocked += 1
    else counts.unverified += 1
  }
  return counts
}

export function hasHarnessSignal(tools) {
  return (tools || []).some((t) => t && t.harness)
}

export function harnessDetailLines(tool) {
  const h = tool && tool.harness
  if (!h) return ['Harness：暂无信号']
  const lines = []
  lines.push(`precheck：${h.precheck || 'unknown'}`)
  if (h.precheck_msg) lines.push(`precheck_msg：${h.precheck_msg}`)
  if (h.max_attempts != null) lines.push(`max_attempts：${h.max_attempts}`)
  if (h.retries_attempted != null) lines.push(`retries：${h.retries_attempted}`)
  lines.push(`outcome：${h.outcome || 'unknown'}`)
  if (h.outcome_reason) lines.push(`reason：${h.outcome_reason}`)
  if (h.circuit_open != null) lines.push(`circuit_open：${h.circuit_open}`)
  return lines
}
