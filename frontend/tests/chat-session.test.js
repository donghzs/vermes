import { describe, it, expect, beforeEach, vi } from 'vitest'
import { 
  uid, 
  SESSION_TEMPLATES, 
  QUICK_START_SUGGESTIONS, 
  getSessionStats,
  SESSIONS_KEY,
  MESSAGES_KEY_PREFIX,
  MAX_SESSIONS,
  enforceSessionLimit,
  renameSession,
  pinSession,
  getMessageCount,
  getFirstMessage,
  searchAllMessages,
} from '@/stores/chat-session'
import * as chatStorage from '@/stores/chat-storage'

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

  describe('MAX_SESSIONS', () => {
    it('is 30', () => {
      expect(MAX_SESSIONS).toBe(30)
    })
  })

  describe('enforceSessionLimit', () => {
    it('does nothing when under limit', () => {
      const sessions = [
        { id: 's1', name: 'S1' },
        { id: 's2', name: 'S2' },
      ]
      enforceSessionLimit(sessions, 's1', SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      expect(sessions).toHaveLength(2)
    })

    it('removes oldest non-current session when at limit', () => {
      const sessions = []
      for (let i = 0; i < MAX_SESSIONS; i++) {
        sessions.push({ id: `s${i}`, name: `S${i}` })
      }
      enforceSessionLimit(sessions, 's0', SESSIONS_KEY, MESSAGES_KEY_PREFIX)
      expect(sessions.length).toBeLessThan(MAX_SESSIONS)
      // Current session should be preserved
      expect(sessions.find(s => s.id === 's0')).toBeDefined()
    })
  })

  describe('renameSession', () => {
    it('renames the session', () => {
      const sessions = [{ id: 's1', name: 'Old' }]
      renameSession(sessions, 's1', 'New Name', SESSIONS_KEY)
      expect(sessions[0].name).toBe('New Name')
    })

    it('does nothing for non-existent session', () => {
      const sessions = [{ id: 's1', name: 'Old' }]
      renameSession(sessions, 'unknown', 'New', SESSIONS_KEY)
      expect(sessions[0].name).toBe('Old')
    })
  })

  describe('pinSession', () => {
    it('sets pinned flag on session', () => {
      const sessions = [{ id: 's1', pinned: false }]
      pinSession(sessions, 's1', true, SESSIONS_KEY)
      expect(sessions[0].pinned).toBe(true)
    })

    it('unpins session', () => {
      const sessions = [{ id: 's1', pinned: true }]
      pinSession(sessions, 's1', false, SESSIONS_KEY)
      expect(sessions[0].pinned).toBe(false)
    })
  })

  describe('getMessageCount', () => {
    beforeEach(() => {
      localStorage.clear()
    })

    it('returns 0 for no messages', () => {
      expect(getMessageCount('s1')).toBe(0)
    })

    it('returns count of stored messages', () => {
      const msgs = [
        { role: 'user', content: 'hello' },
        { role: 'assistant', content: 'hi' },
      ]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify(msgs))
      expect(getMessageCount('s1')).toBe(2)
    })

    it('returns 0 for corrupted data', () => {
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', 'bad json')
      expect(getMessageCount('s1')).toBe(0)
    })
  })

  describe('getFirstMessage', () => {
    beforeEach(() => {
      localStorage.clear()
    })

    it('returns empty string for no messages', () => {
      expect(getFirstMessage('s1')).toBe('')
    })

    it('returns first user message text', () => {
      const msgs = [
        { role: 'system', content: 'system prompt' },
        { role: 'user', content: 'Hello World' },
        { role: 'assistant', content: 'Hi there' },
      ]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify(msgs))
      expect(getFirstMessage('s1')).toBe('Hello World')
    })

    it('truncates long messages to 40 chars', () => {
      const long = 'A'.repeat(100)
      const msgs = [{ role: 'user', content: long }]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify(msgs))
      const result = getFirstMessage('s1')
      expect(result.length).toBe(43) // 40 + '...'
      expect(result).toContain('...')
    })

    it('replaces image markdown with emoji', () => {
      const msgs = [{ role: 'user', content: '![img](data:image/png;base64,abc)' }]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify(msgs))
      expect(getFirstMessage('s1')).toContain('🖼️')
    })
  })

  describe('searchAllMessages', () => {
    beforeEach(() => {
      localStorage.clear()
    })

    it('returns empty array for no sessions', () => {
      expect(searchAllMessages([], 'keyword', 'all', MESSAGES_KEY_PREFIX)).toEqual([])
    })

    it('finds matching messages across sessions', () => {
      const sessions = [
        { id: 's1', name: 'S1' },
        { id: 's2', name: 'S2' },
      ]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify([
        { role: 'user', content: 'hello world', timestamp: 2000 },
      ]))
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's2', JSON.stringify([
        { role: 'assistant', content: 'hello there', timestamp: 1000 },
      ]))
      const results = searchAllMessages(sessions, 'hello', 'all', MESSAGES_KEY_PREFIX)
      expect(results).toHaveLength(2)
    })

    it('filters by keyword (case insensitive)', () => {
      const sessions = [{ id: 's1', name: 'S1' }]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify([
        { role: 'user', content: 'Hello World', timestamp: 1000 },
        { role: 'assistant', content: 'Goodbye', timestamp: 2000 },
      ]))
      const results = searchAllMessages(sessions, 'hello', 'all', MESSAGES_KEY_PREFIX)
      expect(results).toHaveLength(1)
      expect(results[0].content).toContain('Hello')
    })

    it('skips system messages', () => {
      const sessions = [{ id: 's1', name: 'S1' }]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify([
        { role: 'system', content: 'system prompt with keyword', timestamp: 1000 },
        { role: 'user', content: 'keyword here', timestamp: 2000 },
      ]))
      const results = searchAllMessages(sessions, 'keyword', 'all', MESSAGES_KEY_PREFIX)
      expect(results).toHaveLength(1)
      expect(results[0].role).toBe('user')
    })

    it('sorts by timestamp descending', () => {
      const sessions = [{ id: 's1', name: 'S1' }]
      localStorage.setItem(MESSAGES_KEY_PREFIX + 's1', JSON.stringify([
        { role: 'user', content: 'first', timestamp: 1000 },
        { role: 'user', content: 'second', timestamp: 5000 },
      ]))
      const results = searchAllMessages(sessions, '', 'all', MESSAGES_KEY_PREFIX)
      expect(results[0].timestamp).toBeGreaterThan(results[1].timestamp)
    })
  })
})
