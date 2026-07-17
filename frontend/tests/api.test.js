import { describe, it, expect, beforeEach, vi } from 'vitest'
import { 
  WECHAT_QUOTA_KEY, 
  getWechatDailyQuota, 
  useWechatQuota, 
  getRemainingQuota, 
  saveQuota,
  getRecommendedIds,
  getRecommendedProviders,
  isConfigLoaded,
} from '@/services/api'

describe('api.js pure functions', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('getWechatDailyQuota', () => {
    it('returns default 500 for no stored data', () => {
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(500)
      expect(q.date).toBe(new Date().toDateString())
    })

    it('returns stored quota for today', () => {
      const today = new Date().toDateString()
      localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: 250, date: today }))
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(250)
      expect(q.date).toBe(today)
    })

    it('resets to 500 for stale date', () => {
      localStorage.setItem(WECHAT_QUOTA_KEY, JSON.stringify({ remaining: 0, date: 'Mon Jan 01 2024' }))
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(500)
    })

    it('handles corrupted JSON', () => {
      localStorage.setItem(WECHAT_QUOTA_KEY, 'not json')
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(500)
    })
  })

  describe('useWechatQuota', () => {
    it('decrements remaining quota', () => {
      useWechatQuota(1)
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(499)
    })

    it('decrements by count', () => {
      useWechatQuota(5)
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(495)
    })

    it('does not go below 0', () => {
      useWechatQuota(999)
      const q = getWechatDailyQuota()
      expect(q.remaining).toBe(0)
    })
  })

  describe('getRemainingQuota / saveQuota', () => {
    it('saveQuota persists and getRemainingQuota reads', () => {
      saveQuota(42)
      const q = getRemainingQuota()
      expect(q.remaining).toBe(42)
    })

    it('getRemainingQuota returns null for no data', () => {
      expect(getRemainingQuota()).toBeNull()
    })

    it('getRemainingQuota returns null for corrupted data', () => {
      localStorage.setItem('vermes_quota', 'bad json')
      expect(getRemainingQuota()).toBeNull()
    })
  })

  describe('recommended providers', () => {
    it('getRecommendedIds returns array of ids', () => {
      const ids = getRecommendedIds()
      expect(Array.isArray(ids)).toBe(true)
      expect(ids.length).toBeGreaterThan(0)
    })

    it('getRecommendedProviders returns array with id and free flag', () => {
      const providers = getRecommendedProviders()
      expect(Array.isArray(providers)).toBe(true)
      for (const p of providers) {
        expect(p.id).toBeTruthy()
        expect(typeof p.free).toBe('boolean')
      }
    })

    it('includes vbit and agnes as free providers', () => {
      const providers = getRecommendedProviders()
      const vbit = providers.find(p => p.id === 'vbit')
      const agnes = providers.find(p => p.id === 'agnes')
      expect(vbit).toBeDefined()
      expect(vbit.free).toBe(true)
      expect(agnes).toBeDefined()
      expect(agnes.free).toBe(true)
    })
  })

  describe('isConfigLoaded', () => {
    it('returns boolean', () => {
      expect(typeof isConfigLoaded()).toBe('boolean')
    })
  })
})
