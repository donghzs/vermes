import { describe, it, expect, beforeEach, vi } from 'vitest'

/**
 * 预览 tab 内置浏览器逻辑测试
 */

import { ref } from 'vue'

function setupPreview() {
  const previewUrl = ref('')
  const previewSrc = ref('')
  const previewLoaded = ref(false)

  function loadPreview() {
    const url = previewUrl.value.trim()
    if (!url) return
    if (url.startsWith('http://') || url.startsWith('https://') || url.startsWith('file://')) {
      previewSrc.value = url
    } else {
      previewSrc.value = `/api/v1/artifacts/${encodeURIComponent(url)}`
    }
    previewLoaded.value = true
  }

  function refreshPreview() {
    if (previewSrc.value) {
      const src = previewSrc.value
      previewSrc.value = ''
      setTimeout(() => { previewSrc.value = src }, 50)
    }
  }

  return { previewUrl, previewSrc, previewLoaded, loadPreview, refreshPreview }
}

describe('预览 tab 内置浏览器', () => {
  beforeEach(() => {
    global.__vermesPreview = setupPreview()
  })

  it('http URL 直接设置为 iframe src', () => {
    const { previewUrl, previewSrc, previewLoaded, loadPreview } = global.__vermesPreview
    previewUrl.value = 'https://example.com'
    loadPreview()
    expect(previewSrc.value).toBe('https://example.com')
    expect(previewLoaded.value).toBe(true)
  })

  it('https URL 直接设置为 iframe src', () => {
    const { previewUrl, previewSrc, loadPreview } = global.__vermesPreview
    previewUrl.value = 'https://vbit.top/vermes/'
    loadPreview()
    expect(previewSrc.value).toBe('https://vbit.top/vermes/')
  })

  it('本地路径走 /api/v1/artifacts/ 端点', () => {
    const { previewUrl, previewSrc, loadPreview } = global.__vermesPreview
    previewUrl.value = 'output/report.html'
    loadPreview()
    expect(previewSrc.value).toBe('/api/v1/artifacts/output%2Freport.html')
  })

  it('file:// URL 直接设置', () => {
    const { previewUrl, previewSrc, loadPreview } = global.__vermesPreview
    previewUrl.value = 'file:///tmp/test.html'
    loadPreview()
    expect(previewSrc.value).toBe('file:///tmp/test.html')
  })

  it('空 URL 不加载', () => {
    const { previewUrl, previewLoaded, loadPreview } = global.__vermesPreview
    previewUrl.value = '   '
    loadPreview()
    expect(previewLoaded.value).toBe(false)
  })

  it('refreshPreview 先清空再恢复 src', async () => {
    const { previewUrl, previewSrc, loadPreview, refreshPreview } = global.__vermesPreview
    previewUrl.value = 'https://example.com'
    loadPreview()
    refreshPreview()
    expect(previewSrc.value).toBe('') // 立即清空
    await new Promise(r => setTimeout(r, 100))
    expect(previewSrc.value).toBe('https://example.com') // 50ms 后恢复
  })
})
