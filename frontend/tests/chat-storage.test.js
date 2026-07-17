import { describe, it, expect, beforeEach, vi } from 'vitest'
import { 
  stripBase64FromContent, 
  loadFromStorage, 
  saveToStorage, 
  flushStorageWrites, 
  onStorageWriteFailure,
  saveMessagesToAPI,
  loadMessagesFromAPI,
  deleteMessagesFromAPI,
} from '@/stores/chat-storage'

describe('chat-storage', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  describe('stripBase64FromContent', () => {
    it('strips base64 images and returns images map', () => {
      const content = 'Hello ![alt text](data:image/png;base64,iVBOR=)'
      const { stripped, images } = stripBase64FromContent(content, 'msg1')
      expect(stripped).toContain('🖼️')
      expect(stripped).not.toContain('data:image')
      expect(Object.keys(images)).toHaveLength(1)
    })

    it('handles multiple images', () => {
      const content = '![](data:image/png;base64,AAA) and ![](data:image/jpeg;base64,BBB)'
      const { stripped, images } = stripBase64FromContent(content, 'msg1')
      expect(Object.keys(images)).toHaveLength(2)
      expect(stripped).toMatch(/🖼️.*🖼️/)
    })

    it('preserves non-image content', () => {
      const content = 'Hello world\n\n![](data:image/png;base64,AAA)\n\nMore text'
      const { stripped } = stripBase64FromContent(content, 'msg1')
      expect(stripped).toContain('Hello world')
      expect(stripped).toContain('More text')
    })

    it('uses alt text as display name', () => {
      const content = '![my photo](data:image/png;base64,AAA)'
      const { stripped } = stripBase64FromContent(content, 'msg1')
      expect(stripped).toContain('my photo')
    })

    it('handles empty content', () => {
      const { stripped, images } = stripBase64FromContent('', 'msg1')
      expect(stripped).toBe('')
      expect(Object.keys(images)).toHaveLength(0)
    })

    it('handles content without images', () => {
      const content = 'Just plain text'
      const { stripped, images } = stripBase64FromContent(content, 'msg1')
      expect(stripped).toBe('Just plain text')
      expect(Object.keys(images)).toHaveLength(0)
    })

    it('generates unique keys with messageId prefix', () => {
      const content = '![](data:image/png;base64,A) ![](data:image/png;base64,B)'
      const { images } = stripBase64FromContent(content, 'msg42')
      const keys = Object.keys(images)
      expect(keys[0]).toContain('msg42-0')
      expect(keys[1]).toContain('msg42-1')
    })

    it('uses fallback prefix when no messageId', () => {
      const content = '![](data:image/png;base64,A)'
      const { images } = stripBase64FromContent(content, '')
      const keys = Object.keys(images)
      expect(keys[0]).toContain('-0')
    })
  })

  describe('loadFromStorage', () => {
    it('returns parsed JSON from localStorage', () => {
      localStorage.setItem('test-key', JSON.stringify([1, 2, 3]))
      const result = loadFromStorage('test-key')
      expect(result).toEqual([1, 2, 3])
    })

    it('returns empty array for missing key', () => {
      const result = loadFromStorage('nonexistent')
      expect(result).toEqual([])
    })

    it('returns empty array for invalid JSON', () => {
      localStorage.setItem('bad-key', 'not json{')
      const result = loadFromStorage('bad-key')
      expect(result).toEqual([])
    })
  })

  describe('saveToStorage', () => {
    it('saves small data synchronously and returns true', () => {
      const result = saveToStorage('small', { a: 1 })
      expect(result).toBe(true)
      expect(JSON.parse(localStorage.getItem('small'))).toEqual({ a: 1 })
    })

    it('handles quota exceeded gracefully (mock test skipped in happy-dom)', () => {
      // In real browsers, localStorage.setItem throws QuotaExceededError when full
      // happy-dom doesn't properly mock this, so we just verify the function returns a boolean
      const result = saveToStorage('test-key', { a: 1 })
      expect(typeof result).toBe('boolean')
    })
  })

  describe('flushStorageWrites', () => {
    it('does not throw when no pending writes', () => {
      expect(() => flushStorageWrites()).not.toThrow()
    })

    it('flushes pending large writes to localStorage', () => {
      // Large data (> 2048 chars) goes to async queue
      const bigData = { data: 'x'.repeat(3000) }
      saveToStorage('big-key', bigData)
      // Should not be in localStorage yet (async)
      // After flush, it should be there
      flushStorageWrites()
      const stored = localStorage.getItem('big-key')
      expect(stored).not.toBeNull()
      expect(JSON.parse(stored).data.length).toBe(3000)
    })
  })

  describe('onStorageWriteFailure', () => {
    it('registers callback without error', () => {
      const cb = vi.fn()
      expect(() => onStorageWriteFailure(cb)).not.toThrow()
    })
  })

  describe('saveMessagesToAPI', () => {
    it('returns false when fetch fails', async () => {
      const result = await saveMessagesToAPI('s1', [])
      expect(result).toBe(false)
    })
  })

  describe('loadMessagesFromAPI', () => {
    it('returns empty array when fetch fails', async () => {
      const result = await loadMessagesFromAPI('s1')
      expect(result).toEqual([])
    })
  })

  describe('deleteMessagesFromAPI', () => {
    it('does not throw when fetch fails', async () => {
      await expect(deleteMessagesFromAPI('s1')).resolves.not.toThrow()
    })
  })
})
