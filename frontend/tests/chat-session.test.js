import { describe, it, expect, beforeEach, vi } from 'vitest'
import { uid, SESSION_TEMPLATES, QUICK_START_SUGGESTIONS, getSessionStats } from '@/stores/chat-session'

describe('chat-session exports', () => {
  describe('uid', () => {
    it('generates unique string ids', () => {
      const a = uid()
      const b = uid()
      expect(a).not.toBe(b)
      expect(typeof a).toBe('string')
      expect(a.length).toBeGreaterThan(5)
    })
  })

  describe('SESSION_TEMPLATES', () => {
    it('has 6 templates', () => {
      expect(SESSION_TEMPLATES).toHaveLength(6)
    })

    it('each template has id, name, icon, systemPrompt', () => {
      for (const tpl of SESSION_TEMPLATES) {
        expect(tpl.id).toBeTruthy()
        expect(tpl.name).toBeTruthy()
        expect(tpl.icon).toBeTruthy()
        expect(typeof tpl.systemPrompt).toBe('string')
      }
    })

    it('first template is blank', () => {
      expect(SESSION_TEMPLATES[0].id).toBe('blank')
      expect(SESSION_TEMPLATES[0].systemPrompt).toBe('')
    })

    it('custom template has empty systemPrompt', () => {
      const custom = SESSION_TEMPLATES.find(t => t.id === 'custom')
      expect(custom).toBeDefined()
      expect(custom.systemPrompt).toBe('')
    })
  })

  describe('QUICK_START_SUGGESTIONS', () => {
    it('has 4 suggestions', () => {
      expect(QUICK_START_SUGGESTIONS).toHaveLength(4)
    })

    it('each suggestion has text and icon', () => {
      for (const s of QUICK_START_SUGGESTIONS) {
        expect(s.text).toBeTruthy()
        expect(s.icon).toBeTruthy()
      }
    })
  })

  describe('getSessionStats', () => {
    it('empty messages → count 0', () => {
      const stats = getSessionStats([], 'sid', 'gpt-4')
      expect(stats.count).toBe(0)
    })

    it('returns count excluding system messages', () => {
      const msgs = [
        { sessionId: 's1', role: 'system', timestamp: 1000 },
        { sessionId: 's1', role: 'user', timestamp: 1000 },
        { sessionId: 's1', role: 'assistant', timestamp: 2000 },
        { sessionId: 's1', role: 'user', timestamp: 3000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'gpt-4')
      expect(stats.count).toBe(3) // excludes system
    })

    it('duration in seconds for < 1 min', () => {
      const msgs = [
        { sessionId: 's1', role: 'user', timestamp: 1000 },
        { sessionId: 's1', role: 'assistant', timestamp: 11000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'm')
      expect(stats.duration).toContain('秒')
    })

    it('duration in minutes for < 1 hour', () => {
      const msgs = [
        { sessionId: 's1', role: 'user', timestamp: 1000 },
        { sessionId: 's1', role: 'assistant', timestamp: 121000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'm')
      expect(stats.duration).toContain('分钟')
    })

    it('duration in hours for >= 1 hour', () => {
      const msgs = [
        { sessionId: 's1', role: 'user', timestamp: 1000 },
        { sessionId: 's1', role: 'assistant', timestamp: 3700000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'm')
      expect(stats.duration).toContain('小时')
    })

    it('returns current model', () => {
      const msgs = [
        { sessionId: 's1', role: 'user', timestamp: 1000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'deepseek-v4')
      expect(stats.model).toBe('deepseek-v4')
    })

    it('filters by sessionId', () => {
      const msgs = [
        { sessionId: 's1', role: 'user', timestamp: 1000 },
        { sessionId: 's2', role: 'user', timestamp: 2000 },
      ]
      const stats = getSessionStats(msgs, 's1', 'm')
      expect(stats.count).toBe(1)
    })
  })
})
