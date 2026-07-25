// 论文工具直接调用封装。
//
// 决策 #1（用户 2026-07-25 评审）：ScholarForge 多数工具（查重 / simhash / AIGC /
// Word 导出）依赖本地环境，在线跑不了；invokeTool 直接用裸 fetch('/api/tools/invoke')，
// 绕过 services/api.js 的在线模式 /v1 前缀陷阱，省复杂度。
//
// 端点（blueprint.py:298）无 auth 依赖（桌面本地运行），仅带 Content-Type。
// 请求体: { name, args }   响应: { result }   失败: { error } 或 HTTP 400 { detail }
export async function invokeTool(name, args) {
  const resp = await fetch('/api/tools/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, args: args || {} }),
  })

  let data = null
  try {
    data = await resp.json()
  } catch (e) {
    data = null
  }

  if (!resp.ok) {
    const msg = (data && (data.detail || data.error)) || `HTTP ${resp.status}`
    throw new Error(msg)
  }

  const result = data ? data.result : null
  // registry.dispatch 失败时返回 { error: ... }，需抛出以便前端显式报错
  if (result && typeof result === 'object' && result.error) {
    const err = result.error
    throw new Error(typeof err === 'string' ? err : JSON.stringify(err))
  }
  return result
}
