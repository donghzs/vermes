import { describe, it, expect, vi, beforeEach } from 'vitest'
import { logger } from '@/utils/logger'

describe('logger', () => {
  beforeEach(() => {
    vi.stubEnv('PROD', false)
  })

  it('error always calls console.error', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    logger.error('test error')
    expect(spy).toHaveBeenCalledWith('test error')
    spy.mockRestore()
  })

  it('warn calls console.warn in dev', () => {
    const spy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    logger.warn('test warn')
    expect(spy).toHaveBeenCalledWith('test warn')
    spy.mockRestore()
  })

  it('log calls console.log in dev', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {})
    logger.log('test log')
    expect(spy).toHaveBeenCalledWith('test log')
    spy.mockRestore()
  })

  it('info calls console.info in dev', () => {
    const spy = vi.spyOn(console, 'info').mockImplementation(() => {})
    logger.info('test info')
    expect(spy).toHaveBeenCalledWith('test info')
    spy.mockRestore()
  })

  it('logger methods accept multiple args', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    logger.error('err', 1, { a: 2 })
    expect(spy).toHaveBeenCalledWith('err', 1, { a: 2 })
    spy.mockRestore()
  })
})
