import { describe, it, expect, beforeEach, vi } from 'vitest'
import { DOMPURIFY_BASE_CONFIG, enforceLinkSecurity } from '@/utils/security'

describe('security.js', () => {
  describe('DOMPURIFY_BASE_CONFIG', () => {
    it('has allowed tags array', () => {
      expect(Array.isArray(DOMPURIFY_BASE_CONFIG.ALLOWED_TAGS)).toBe(true)
      expect(DOMPURIFY_BASE_CONFIG.ALLOWED_TAGS.length).toBeGreaterThan(0)
    })

    it('includes common formatting tags', () => {
      const tags = DOMPURIFY_BASE_CONFIG.ALLOWED_TAGS
      expect(tags).toContain('p')
      expect(tags).toContain('strong')
      expect(tags).toContain('em')
      expect(tags).toContain('code')
      expect(tags).toContain('a')
    })

    it('has allowed attributes array', () => {
      expect(Array.isArray(DOMPURIFY_BASE_CONFIG.ALLOWED_ATTR)).toBe(true)
      expect(DOMPURIFY_BASE_CONFIG.ALLOWED_ATTR).toContain('href')
      expect(DOMPURIFY_BASE_CONFIG.ALLOWED_ATTR).toContain('src')
    })

    it('disallows data attributes', () => {
      expect(DOMPURIFY_BASE_CONFIG.ALLOW_DATA_ATTR).toBe(false)
    })

    it('sanitizes named props', () => {
      expect(DOMPURIFY_BASE_CONFIG.SANITIZE_NAMED_PROPS).toBe(true)
    })

    it('keeps content', () => {
      expect(DOMPURIFY_BASE_CONFIG.KEEP_CONTENT).toBe(true)
    })
  })

  describe('enforceLinkSecurity', () => {
    it('sets target=_blank and rel on anchor', () => {
      const a = document.createElement('a')
      a.setAttribute('href', 'https://example.com')
      enforceLinkSecurity(a)
      expect(a.getAttribute('target')).toBe('_blank')
      expect(a.getAttribute('rel')).toContain('noopener')
      expect(a.getAttribute('rel')).toContain('noreferrer')
    })

    it('blocks javascript: protocol', () => {
      const a = document.createElement('a')
      a.setAttribute('href', 'javascript:alert(1)')
      enforceLinkSecurity(a)
      expect(a.getAttribute('href')).toBeNull()
      expect(a.getAttribute('data-blocked-href')).toContain('javascript')
      expect(a.getAttribute('title')).toContain('阻止')
    })

    it('blocks data: protocol', () => {
      const a = document.createElement('a')
      a.setAttribute('href', 'data:text/html,<script>alert(1)</script>')
      enforceLinkSecurity(a)
      expect(a.getAttribute('href')).toBeNull()
      expect(a.getAttribute('data-blocked-href')).toContain('data')
    })

    it('blocks vbscript: protocol', () => {
      const a = document.createElement('a')
      a.setAttribute('href', 'vbscript:msgbox(1)')
      enforceLinkSecurity(a)
      expect(a.getAttribute('href')).toBeNull()
      expect(a.getAttribute('data-blocked-href')).toContain('vbscript')
    })

    it('allows safe https links', () => {
      const a = document.createElement('a')
      a.setAttribute('href', 'https://example.com/safe')
      enforceLinkSecurity(a)
      expect(a.getAttribute('href')).toBe('https://example.com/safe')
      expect(a.getAttribute('data-blocked-href')).toBeNull()
    })

    it('does nothing for non-anchor elements', () => {
      const div = document.createElement('div')
      div.setAttribute('href', 'javascript:alert(1)')
      enforceLinkSecurity(div)
      // Should not modify non-anchor elements
      expect(div.getAttribute('target')).toBeNull()
    })
  })
})
