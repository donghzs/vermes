/**
 * MCP 指挥中心调用统计聚合（C4 可测纯函数）。
 * 与 MCPManager.vue 内 statsByServer 语义对齐：per-tool → per-server。
 */
export function aggregateMcpStatsByServer(tools) {
  const m = {}
  for (const t of (tools || [])) {
    if (!t || !t.server) continue
    const s = m[t.server] || (m[t.server] = {
      calls: 0, errors: 0, interrupts: 0, total_ms: 0, max_ms: 0, tools: 0,
    })
    s.calls += t.calls || 0
    s.errors += t.errors || 0
    s.interrupts += t.interrupts || 0
    s.total_ms += t.total_ms || 0
    s.max_ms = Math.max(s.max_ms, t.max_ms || 0)
    s.tools += 1
  }
  for (const s of Object.values(m)) {
    s.avg_ms = s.calls ? Math.round(s.total_ms / s.calls) : 0
    // 三态：成功率分母排除 interrupts
    s.rate = s.calls ? Math.round((s.calls - s.errors - s.interrupts) / s.calls * 100) : null
  }
  return m
}

export function summarizeMcpStats(stats) {
  const summary = stats?.summary
  if (!summary || !(summary.calls > 0)) return null
  return {
    calls: summary.calls || 0,
    errors: summary.errors || 0,
    interrupts: summary.interrupts || 0,
    success_rate: summary.success_rate || 0,
    tool_count: stats?.count || 0,
  }
}
