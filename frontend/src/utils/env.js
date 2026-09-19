/**
 * 环境上下文 HTTP headers —— 所有裸 fetch 必须带，否则 auth_middleware 401。
 * 通过 mount_spa 的 _serve_index 注入 window.__VERMES_SESSION_TOKEN__。
 */
export function envHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-Vermes-Session-Token': (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || '',
  }
}

/** C2：写请求合并 session token（在业务 headers 之上注入） */
export function withSessionToken(headers = {}) {
  return {
    ...headers,
    'X-Vermes-Session-Token': (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) || headers['X-Vermes-Session-Token'] || '',
  }
}
