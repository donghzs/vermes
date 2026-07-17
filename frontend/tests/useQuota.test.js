import { describe, it, expect, beforeEach, vi } from 'vitest'
import { useQuota } from '@/composables/useQuota'

describe('useQuota composable', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('returns reactive refs with defaults', () => {
    const { serverQuota, referralCode } = useQuota()
    expect(serverQuota.value.remaining).toBeDefined()
    expect(referralCode.value).toBe('')
  })

  it('quotaDisplay shows login prompt when need_login', () => {
    const { serverQuota, quotaDisplay } = useQuota()
    serverQuota.value = { ...serverQuota.value, need_login: true }
    expect(quotaDisplay.value.text).toContain('登录')
    expect(quotaDisplay.value.remaining).toBe(0)
  })

  it('quotaDisplay shows points and days when logged in', () => {
    const { serverQuota, quotaDisplay } = useQuota()
    serverQuota.value = {
      remaining: 150,
      total_limit: 500,
      spent_today: 50,
      bonus_points: 0,
      days_left: 15,
      is_wechat: true,
    }
    const display = quotaDisplay.value
    expect(display.text).toContain('150')
    expect(display.text).toContain('500')
    expect(display.text).toContain('15')
    expect(display.remaining).toBe(150)
  })

  it('refreshQuota sets need_login when no wechat openid', async () => {
    const { serverQuota, refreshQuota } = useQuota()
    await refreshQuota()
    expect(serverQuota.value.need_login).toBe(true)
    expect(serverQuota.value.total_limit).toBe(500)
  })

  it('teardownQuotaEvents does not throw without setup', () => {
    const { teardownQuotaEvents } = useQuota()
    expect(() => teardownQuotaEvents()).not.toThrow()
  })

  it('copyReferralCode does nothing when code is empty', () => {
    const { copyReferralCode } = useQuota()
    expect(() => copyReferralCode()).not.toThrow()
  })

  it('copyReferralCode copies text when code is set', () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    const { referralCode, copyReferralCode } = useQuota()
    referralCode.value = 'ABC123'
    copyReferralCode()
    expect(writeText).toHaveBeenCalled()
    const copiedText = writeText.mock.calls[0][0]
    expect(copiedText).toContain('ABC123')
    expect(copiedText).toContain('vbit.top')
  })
})
