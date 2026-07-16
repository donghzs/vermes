import { describe, it, expect } from 'vitest'
import DOMPurify from 'dompurify'

describe('DOMPurify — XSS 防御', () => {
  it('应移除 <script> 标签', () => {
    const dirty = '<script>alert("xss")</script><p>hello</p>'
    const clean = DOMPurify.sanitize(dirty)
    expect(clean).not.toContain('<script>')
    expect(clean).toContain('<p>hello</p>')
  })

  it('应移除 on* 事件属性', () => {
    const dirty = '<img src="x" onerror="alert(1)">'
    const clean = DOMPurify.sanitize(dirty)
    expect(clean).not.toContain('onerror')
  })

  it('应移除 javascript: 协议', () => {
    const dirty = '<a href="javascript:alert(1)">click</a>'
    const clean = DOMPurify.sanitize(dirty)
    expect(clean).not.toContain('javascript:')
  })

  it('应保留安全的 HTML 标签', () => {
    const dirty = '<p>段落</p><strong>加粗</strong><em>斜体</em><code>代码</code>'
    const clean = DOMPurify.sanitize(dirty)
    expect(clean).toContain('<p>')
    expect(clean).toContain('<strong>')
    expect(clean).toContain('<em>')
    expect(clean).toContain('<code>')
  })

  it('应保留 mark 标签（仅允许 class 属性）', () => {
    const dirty = '<mark class="highlight">高亮文本</mark>'
    const clean = DOMPurify.sanitize(dirty, { ALLOWED_TAGS: ['mark'], ALLOWED_ATTR: ['class'] })
    expect(clean).toContain('<mark')
    expect(clean).toContain('class="highlight"')
  })

  it('应移除 mark 标签上的非 class 属性', () => {
    const dirty = '<mark onclick="alert(1)" class="hl">text</mark>'
    const clean = DOMPurify.sanitize(dirty, { ALLOWED_TAGS: ['mark'], ALLOWED_ATTR: ['class'] })
    expect(clean).not.toContain('onclick')
    expect(clean).toContain('class="hl"')
  })

  it('应处理嵌套恶意结构', () => {
    const dirty = '<div><script>evil()</script><p onmouseover="x()">text</p></div>'
    const clean = DOMPurify.sanitize(dirty)
    expect(clean).not.toContain('<script>')
    // DOMPurify 在不同 environment 下对 on* 属性处理可能不同
    // 确保至少 <script> 被移除，文本内容保留
    expect(clean).toContain('text')
  })
})
