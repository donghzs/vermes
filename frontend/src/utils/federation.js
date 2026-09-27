/**
 * T5 联邦可见性：从当前会话时间线解析 @B 接力 / A2A peer / 跨群联邦信号。
 *
 * 纪律（P1 教训迁移）：
 * - 只读调用方传入的 timeline（当前房），不跨房拉取、不猜其他任务——不重开枚举面。
 * - 状态展示复用真枚举语义（done/running/…）；联邦过程态单独标注，不造伪任务态。
 */

/** peer 行：[私聊 @A ↔ @B]（hermes peer / A2A） */
const PEER_RE = /\[私聊\s*@([^↔\]]+?)\s*↔\s*@([^\]]+?)\]/

/** 公开接力兜底：（接力）@A 在回复中点名了你 */
const RELAY_RE = /（接力）\s*@(\S+?)\s+在回复中点名了你/

/** 跨群联邦线索：联系人池即可私聊，不限同群（peer_dm 注释语义进文案时） */
const CROSS_RE = /跨群|不限同群|联邦/

/**
 * @param {Array} timeline 房间消息（只解析传入的这份，scope=当前房）
 * @param {{roomId?: string}} opts
 * @returns {Array<{id,kind,from,to,transport,scope,at,content}>}
 */
export function parseFederationSignals(timeline, opts = {}) {
  const out = []
  const list = Array.isArray(timeline) ? timeline : []
  for (const m of list) {
    const content = String((m && m.content) || '')
    if (!content) continue
    const at = m.created_at || m.ts || ''
    const peer = PEER_RE.exec(content)
    if (peer) {
      out.push({
        id: m.id || `peer-${out.length}`,
        kind: 'peer',
        from: peer[1].trim(),
        to: peer[2].trim(),
        transport: 'A2A',
        // 跨群线索出现在同一段文案时标出；否则视为当前房内
        scope: CROSS_RE.test(content) ? 'cross-group' : 'room',
        roomId: opts.roomId || '',
        at,
        content: content.slice(0, 120),
      })
      continue
    }
    const relay = RELAY_RE.exec(content)
    if (relay) {
      out.push({
        id: m.id || `relay-${out.length}`,
        kind: 'relay',
        from: relay[1].trim(),
        to: '',
        transport: 'relay',
        scope: 'room',
        roomId: opts.roomId || '',
        at,
        content: content.slice(0, 120),
      })
    }
  }
  return out
}

/** 最近一条联邦活动（状态条用） */
export function latestFederation(timeline, opts = {}) {
  const sigs = parseFederationSignals(timeline, opts)
  return sigs.length ? sigs[sigs.length - 1] : null
}

export function federationLabel(sig) {
  if (!sig) return ''
  if (sig.kind === 'peer') {
    return `${sig.from} ↔ ${sig.to} · A2A${sig.scope === 'cross-group' ? ' · 跨群' : ''}`
  }
  return `${sig.from} 公开接力`
}

export function federationScopeLabel(scope) {
  if (scope === 'cross-group') return '跨群联邦'
  return '同群'
}
