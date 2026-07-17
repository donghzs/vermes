import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { showToast, toast, toasts } from '@/utils/toast'

describe('toast', () => {
  beforeEach(() => {
    toasts.value = []
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('showToast adds a toast with default type info', () => {
    showToast('hello')
    expect(toasts.value).toHaveLength(1)
    expect(toasts.value[0].message).toBe('hello')
    expect(toasts.value[0].type).toBe('info')
    expect(toasts.value[0].duration).toBe(3000)
  })

  it('showToast accepts custom type and duration', () => {
    showToast('error msg', 'error', 5000)
    expect(toasts.value[0].type).toBe('error')
    expect(toasts.value[0].duration).toBe(5000)
  })

  it('showToast removes toast after duration', () => {
    showToast('temp', 'info', 1000)
    expect(toasts.value).toHaveLength(1)
    vi.advanceTimersByTime(1000)
    expect(toasts.value).toHaveLength(0)
  })

  it('toast.success calls showToast with success type', () => {
    toast.success('done')
    expect(toasts.value[0].message).toBe('done')
    expect(toasts.value[0].type).toBe('success')
  })

  it('toast.error calls showToast with error type', () => {
    toast.error('failed')
    expect(toasts.value[0].message).toBe('failed')
    expect(toasts.value[0].type).toBe('error')
  })

  it('toast.warning calls showToast with warning type', () => {
    toast.warning('careful')
    expect(toasts.value[0].type).toBe('warning')
  })

  it('toast.info calls showToast with info type', () => {
    toast.info('note')
    expect(toasts.value[0].type).toBe('info')
  })

  it('multiple toasts coexist', () => {
    toast.success('a')
    toast.error('b')
    toast.info('c')
    expect(toasts.value).toHaveLength(3)
  })

  it('each toast gets unique incrementing id', () => {
    toast.success('a')
    toast.success('b')
    expect(toasts.value[0].id).toBeLessThan(toasts.value[1].id)
  })
})
