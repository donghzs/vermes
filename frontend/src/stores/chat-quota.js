/**
 * chat-quota.js — 配额检查 + 错误友好化 + 工具函数
 */

/**
 * 错误友好化：翻译常见后端/AI错误为中文用户提示
 */
export function friendlyError(msg) {
  const m = msg || ''
  // 网络/连接错误
  if (m.includes('fetch') || m.includes('NetworkError') || m.includes('Failed to fetch'))
    return '❌ 网络连接失败，请检查网络后重试'
  if (m.includes('timeout') || m.includes('Timeout') || m.includes('ETIMEDOUT'))
    return '❌ 请求超时，模型响应太慢，请稍后重试或换一个模型'
  if (m.includes('ECONNREFUSED') || m.includes('ECONNRESET'))
    return '❌ 连接被拒绝，服务可能正在重启，请稍后重试'
  // API 错误
  if (m.includes('401') || m.includes('Unauthorized') || m.includes('invalid_api_key'))
    return '❌ API Key 无效，请在设置页检查或更换 Key'
  if (m.includes('403') || m.includes('Forbidden'))
    return '❌ 访问被拒绝，API Key 可能没有权限，请检查设置'
  if (m.includes('429') || m.includes('rate_limit') || m.includes('Too Many Requests'))
    return '❌ 请求太频繁，请稍后再试'
  if (m.includes('500') || m.includes('Internal Server Error'))
    return '❌ 服务端错误，请稍后重试'
  if (m.includes('502') || m.includes('503') || m.includes('504'))
    return '❌ 服务暂时不可用，请稍后重试'
  if (m.includes('model') && m.includes('not found'))
    return '❌ 模型不存在，请在设置页检查模型配置'
  if (m.includes('context_length') || m.includes('max_tokens'))
    return '❌ 对话太长，请新建会话或减少上下文'
  // Agent 相关
  if (m.includes('No API key') || m.includes('No base_url'))
    return '❌ 未配置 API Key，请在设置页添加'
  if (m.includes('免费体验'))
    return '❌ ' + m
  // 默认
  return '❌ 出错了：' + (m.length > 100 ? m.slice(0, 100) + '...' : m)
}

/**
 * 文件大小格式化
 */
export function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}
