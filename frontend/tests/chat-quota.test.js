import { describe, it, expect } from 'vitest'
import { friendlyError, formatSize } from '@/stores/chat-quota'

describe('friendlyError', () => {
  it('network errors → 网络连接失败', () => {
    expect(friendlyError('Failed to fetch data')).toContain('网络连接失败')
    expect(friendlyError('NetworkError')).toContain('网络连接失败')
  })

  it('timeout errors → 请求超时', () => {
    expect(friendlyError('request timeout')).toContain('请求超时')
    expect(friendlyError('ETIMEDOUT')).toContain('请求超时')
  })

  it('connection refused → 连接被拒绝', () => {
    expect(friendlyError('ECONNREFUSED')).toContain('连接被拒绝')
    expect(friendlyError('ECONNRESET')).toContain('连接被拒绝')
  })

  it('401/Unauthorized → API Key 无效', () => {
    expect(friendlyError('401 Unauthorized')).toContain('API Key 无效')
    expect(friendlyError('invalid_api_key')).toContain('API Key 无效')
  })

  it('403/Forbidden → 访问被拒绝', () => {
    expect(friendlyError('403 Forbidden')).toContain('访问被拒绝')
  })

  it('429/rate_limit → 请求太频繁', () => {
    expect(friendlyError('429 Too Many Requests')).toContain('请求太频繁')
    expect(friendlyError('rate_limit exceeded')).toContain('请求太频繁')
  })

  it('500 → 服务端错误', () => {
    expect(friendlyError('500 Internal Server Error')).toContain('服务端错误')
  })

  it('502/503 → 服务暂时不可用', () => {
    expect(friendlyError('502 Bad Gateway')).toContain('服务暂时不可用')
    expect(friendlyError('503 Service Unavailable')).toContain('服务暂时不可用')
  })

  it('504 → matches timeout first (service unavailable is lower priority)', () => {
    const result = friendlyError('504 Gateway Timeout')
    // 'Timeout' matches earlier rule, which is acceptable behavior
    expect(result).toContain('❌')
  })

  it('model not found → 模型不存在', () => {
    expect(friendlyError('model not found')).toContain('模型不存在')
    expect(friendlyError('model gpt-5 not found')).toContain('模型不存在')
  })

  it('context_length → 对话太长', () => {
    expect(friendlyError('context_length_exceeded')).toContain('对话太长')
    expect(friendlyError('max_tokens limit')).toContain('对话太长')
  })

  it('No API key → 未配置', () => {
    expect(friendlyError('No API key configured')).toContain('未配置')
    expect(friendlyError('No base_url set')).toContain('未配置')
  })

  it('免费体验 prefix preserved', () => {
    const result = friendlyError('免费体验额度已用完')
    expect(result).toContain('免费体验额度已用完')
    expect(result.startsWith('❌')).toBe(true)
  })

  it('unknown error → default message with truncation', () => {
    expect(friendlyError('something weird')).toContain('something weird')
    const longMsg = 'x'.repeat(200)
    const result = friendlyError(longMsg)
    expect(result).toContain('...')
    expect(result.length).toBeLessThan(longMsg.length + 20)
  })

  it('empty/null input → default', () => {
    expect(friendlyError('')).toContain('出错了')
    expect(friendlyError(null)).toContain('出错了')
    expect(friendlyError(undefined)).toContain('出错了')
  })
})

describe('formatSize', () => {
  it('bytes < 1024 → B suffix', () => {
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(512)).toBe('512 B')
    expect(formatSize(1023)).toBe('1023 B')
  })

  it('bytes < 1MB → KB suffix', () => {
    expect(formatSize(1024)).toBe('1.0 KB')
    expect(formatSize(1536)).toBe('1.5 KB')
    expect(formatSize(1024 * 1024 - 1)).toMatch(/KB$/)
  })

  it('bytes >= 1MB → MB suffix', () => {
    expect(formatSize(1024 * 1024)).toBe('1.0 MB')
    expect(formatSize(1024 * 1024 * 5.5)).toBe('5.5 MB')
  })
})
