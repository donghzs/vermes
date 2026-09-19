import { describe, it, expect } from 'vitest'
import {
  harnessClassOf,
  filterToolsByHarness,
  harnessFilterCounts,
  hasHarnessSignal,
  harnessDetailLines,
  HARNESS_FILTERS,
} from '../src/utils/harnessTimeline.js'

const tools = [
  { id: 'a', name: 'write_file', harness: { outcome: 'verified', precheck: 'ok' } },
  { id: 'b', name: 'read_file', harness: { outcome: 'unverified_tool', precheck: 'ok' } },
  { id: 'c', name: 'terminal', harness: { precheck: 'blocked' } },
  { id: 'd', name: 'search', harness: { outcome: 'verify_failed', precheck: 'ok' } },
  { id: 'e', name: 'other' }, // 无 harness
  { id: 'f', name: 'boom', status: 'error', harness: { outcome: 'ok' } },
]

describe('C1 harnessTimeline', () => {
  it('classifies without fake green', () => {
    expect(harnessClassOf(tools[0])).toBe('verified')
    expect(harnessClassOf(tools[1])).toBe('unverified')
    expect(harnessClassOf(tools[2])).toBe('blocked')
    expect(harnessClassOf(tools[3])).toBe('fail')
    expect(harnessClassOf(tools[4])).toBe('none')
    expect(harnessClassOf(tools[5])).toBe('error')
  })

  it('filters by outcome buckets', () => {
    expect(filterToolsByHarness(tools, 'verified').map(t => t.id)).toEqual(['a'])
    expect(filterToolsByHarness(tools, 'unverified').map(t => t.id)).toEqual(['b', 'e'])
    expect(filterToolsByHarness(tools, 'blocked').map(t => t.id)).toEqual(['c'])
    expect(filterToolsByHarness(tools, 'fail').map(t => t.id).sort()).toEqual(['d', 'f'])
    expect(filterToolsByHarness(tools, 'all')).toHaveLength(6)
  })

  it('counts for chips', () => {
    const c = harnessFilterCounts(tools)
    expect(c.all).toBe(6)
    expect(c.verified).toBe(1)
    expect(c.unverified).toBe(2)
    expect(c.blocked).toBe(1)
    expect(c.fail).toBe(2)
  })

  it('hasHarnessSignal only when present', () => {
    expect(hasHarnessSignal(tools)).toBe(true)
    expect(hasHarnessSignal([{ name: 'x' }])).toBe(false)
  })

  it('detail lines honest when no signal', () => {
    expect(harnessDetailLines({ name: 'x' })[0]).toContain('暂无信号')
    const lines = harnessDetailLines(tools[0])
    expect(lines.some(l => l.includes('verified'))).toBe(true)
  })

  it('filter ids are stable for UI', () => {
    expect(HARNESS_FILTERS.map(f => f.id)).toEqual(['all', 'verified', 'unverified', 'fail', 'blocked'])
  })
})
