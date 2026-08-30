// 能力级统一调用封装（P3-3，对应后端 P3-2 POST /api/invoke）。
//
// 与 utils/invokeTool.js（工具级 POST /api/tools/invoke）互补非冗余：
// 本模块按「能力 cap」调用，后端走 cap→路由→model_capable 闸门→dispatch；
// invokeTool 按工具名直调。二者职责不同（详见 vermes_p3_design_2026-08-30.md §七）。
//
// 请求体: { cap, payload: { args, tier?, provider? }, session_id? }
// 响应:   { cap, tool, result }  |  { error }  |  { capability_check: 'not_satisfied', missing }
// 端点无 auth（桌面本地运行），仅带 Content-Type。
export async function invokeCap(cap, payload = {}, sessionId = null) {
  const body = { cap, payload: payload || {}, session_id: sessionId }
  const resp = await fetch('/api/invoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
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

  // 后端不抛出，结构化返回；需把「不执行」显式转为错误供前端提示
  if (data && data.error) {
    const err = data.error
    throw new Error(typeof err === 'string' ? err : JSON.stringify(err))
  }
  if (data && data.capability_check === 'not_satisfied') {
    const missing = (data.missing || []).join(', ')
    throw new Error(`当前模型不满足该能力所需维度：${missing || '未知'}（provider=${data.provider || '?'}）`)
  }
  return data
}

// v1 临时启发集：需结构化参数（如契约）才能跑的 cap，徽标点击不直调，
// 引导用户到对应 brick 面板。后续应由后端暴露 cap schema 取代（见 design §七 待办）。
export const CAP_NEEDS_PAYLOAD = new Set([
  'cadir_build',
  'cadir_compile',
  'cadir_verify_step',
  'cadir_verify_stl',
])

export function capNeedsPayload(cap) {
  return CAP_NEEDS_PAYLOAD.has(cap)
}
