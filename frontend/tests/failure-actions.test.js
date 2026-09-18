import { describe, it, expect } from 'vitest'
import { classifyFailure, friendlyFailureText } from '../src/utils/failureActions.js'

describe('U-P0-7 classifyFailure', () => {
  it('401 → 去设置检查 Key', () => {
    const r = classifyFailure(new Error('401 Unauthorized'))
    expect(r.code).toBe('auth_invalid')
    expect(r.action?.type).toBe('settings')
    expect(r.action?.label).toContain('设置')
  })

  it('402/额度用完 → quota 路径', () => {
    const r = classifyFailure('insufficient_quota 免费额度已用完')
    expect(r.code).toBe('quota_exhausted')
    expect(r.action?.type).toBe('quota')
  })

  it('缺 API Key → settings', () => {
    const r = classifyFailure('❌ 未配置 API Key，请在设置页添加')
    expect(r.code).toBe('missing_key')
    expect(r.action?.type).toBe('settings')
  })

  it('对话过长 → new_session', () => {
    const r = classifyFailure('context_length exceeded')
    expect(r.code).toBe('context_too_long')
    expect(r.action?.type).toBe('new_session')
  })

  it('网络错误 → retry', () => {
    const r = classifyFailure('Failed to fetch')
    expect(r.code).toBe('network')
    expect(r.action?.type).toBe('retry')
  })

  it('未知错误仍给可行动重试，不编造修复承诺', () => {
    const r = classifyFailure('weird backend glitch 0xdead')
    expect(r.code).toBe('unknown')
    expect(r.action?.type).toBe('retry')
    expect(r.text).not.toMatch(/已完成|成功/)
  })

  it('friendlyFailureText 保持 ✅ 前缀兼容', () => {
    expect(friendlyFailureText('401').startsWith('❌')).toBe(true)
  })
})
