// 2.1.2 安全加固：全局 DOMPurify 配置，供所有组件复用
export const DOMPURIFY_BASE_CONFIG = {
  ALLOWED_TAGS: [
    'p', 'br', 'hr',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'blockquote', 'pre', 'code',
    'strong', 'em', 's', 'del', 'ins',
    'a', 'img',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'div', 'span', 'sup', 'sub', 'mark'
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'target', 'rel',
    'src', 'alt', 'width', 'height',
    'class', 'id',
    'start',
    'data-artifact', 'data-title'
  ],
  ALLOW_DATA_ATTR: false,
  SANITIZE_NAMED_PROPS: true,
  KEEP_CONTENT: true
}

// 强制链接安全：noopener noreferrer + 拦截 javascript: 伪协议
// 产物链接（data-artifact）不加 target=_blank，由 handleContentClick 拦截打开面板
export function enforceLinkSecurity(node) {
  if (node.tagName === 'A') {
    const isArtifact = node.hasAttribute('data-artifact')
    if (!isArtifact) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
    const href = (node.getAttribute('href') || '').trim().toLowerCase()
    if (href.startsWith('javascript:') || href.startsWith('data:') || href.startsWith('vbscript:')) {
      node.removeAttribute('href')
      node.setAttribute('data-blocked-href', href)
      node.setAttribute('title', '已阻止危险链接')
    }
  }
}
