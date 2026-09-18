/**
 * U-P0-7 失败可行动 — 把错误文案映射到可执行修复路径。
 * 缺信号/未知错误只给通用「重试」，不编造具体修复承诺。
 */

const RULES = [
  {
    id: 'need_login',
    test: (m) => /need_login|微信登录|登录后/.test(m) || /免费体验仅限/.test(m),
    text: '需要登录后才能继续免费体验',
    action: { type: 'wechat', label: '微信扫码登录' },
  },
  {
    id: 'quota_exhausted',
    test: (m) => /402|insufficient_quota|额度已用完|额度已用尽|今日额度|积分.*用/.test(m),
    text: '免费额度已用完',
    action: { type: 'quota', label: '查看续用方式' },
  },
  {
    id: 'auth_invalid',
    test: (m) => /401|Unauthorized|invalid_api_key|API Key 无效|Key 无效|已过期/.test(m),
    text: 'API Key 无效或已过期',
    action: { type: 'settings', label: '去设置检查 Key' },
  },
  {
    id: 'auth_forbidden',
    test: (m) => /403|Forbidden|没有权限|访问被拒绝/.test(m),
    text: '访问被拒绝，可能是 Key 权限或模型不可用',
    action: { type: 'settings', label: '去设置检查权限/模型' },
  },
  {
    id: 'missing_key',
    test: (m) => /No API key|未配置 API Key|请先填写 API|还差.*API/i.test(m),
    text: '尚未配置可用的模型或 API Key',
    action: { type: 'settings', label: '去配置模型' },
  },
  {
    id: 'model_missing',
    test: (m) => /model.*not found|模型不存在|404|Not Found/.test(m),
    text: '当前模型不可用',
    action: { type: 'settings', label: '去切换模型' },
  },
  {
    id: 'context_too_long',
    test: (m) => /context_length|max_tokens|对话太长/.test(m),
    text: '上下文过长，本会话可能发不下新消息',
    action: { type: 'new_session', label: '新建会话' },
  },
  {
    id: 'network',
    test: (m) => /fetch|NetworkError|ECONN|timeout|超时|网络/.test(m),
    text: '网络或服务暂时不可用',
    action: { type: 'retry', label: '重试发送' },
  },
]

/**
 * @param {unknown} error Error | string
 * @returns {{ text: string, code: string, action: { type: string, label: string } | null }}
 */
export function classifyFailure(error) {
  const raw = typeof error === 'string' ? error : (error?.message || String(error || ''))
  for (const rule of RULES) {
    if (rule.test(raw)) {
      return { text: rule.text, code: rule.id, action: { ...rule.action } }
    }
  }
  return {
    text: '发送失败',
    code: 'unknown',
    action: { type: 'retry', label: '重试发送' },
  }
}

/** 兼容旧字符串提示调用点 */
export function friendlyFailureText(error) {
  return '❌ ' + classifyFailure(error).text
}
