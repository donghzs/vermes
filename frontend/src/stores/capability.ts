// P3-3 能力满足度 store（轻量响应式模块，非 pinia，沿用 services/api.js 模块级 reactive 风格）。
//
// 职责：
//  · 订阅后端 vermes-model-change SSE（P3-2 B GET /api/model-change/stream），模型切换时
//    重新拉取当前模型对各 cap 的满足度；
//  · capSatisfied(cap) 供 BrickCard 徽标灰显判定（true=可运行，false=灰显）。
//
// 单一真相源在后端（GET /api/invoke/capable 复用 model_capable），本模块只消费布尔。
import { reactive } from 'vue'

// cap -> 是否满足当前模型维度（true 可运行 / false 灰显）
const capSatisfiedMap = reactive({})

let es: EventSource | null = null

async function fetchCapable(cap: string): Promise<void> {
  try {
    const resp = await fetch(`/api/invoke/capable?cap=${encodeURIComponent(cap)}`)
    if (resp.ok) {
      const d = await resp.json()
      capSatisfiedMap[cap] = !!d.satisfied
    }
  } catch (e) {
    // 网络/后端异常：保持默认 true（不灰显），降级不阻断 UI
  }
}

async function refreshAll(): Promise<void> {
  const caps = Object.keys(capSatisfiedMap)
  await Promise.all(caps.map(fetchCapable))
}

function ensureWatch(): void {
  if (es) return
  try {
    es = new EventSource('/api/model-change/stream')
  } catch (e) {
    return // 非浏览器环境（测试）静默跳过
  }
  es.onmessage = (ev: MessageEvent) => {
    let payload: any = null
    try {
      payload = JSON.parse(ev.data)
    } catch (e) {
      return
    }
    if (payload && payload.event === 'vermes-model-change') {
      refreshAll()
    }
  }
  // EventSource 断线自动重连，无需手动处理
}

export function capSatisfied(cap: string): boolean {
  // 纯函数（供模板渲染，无副作用）：未知 cap 默认 true（不灰显），等 trackCap 补查
  if (!(cap in capSatisfiedMap)) return true
  return capSatisfiedMap[cap]
}

// 组件挂载时调用：登记 cap 并后台补查满足度（避免在渲染期触发副作用）
export function trackCap(cap: string): void {
  if (!(cap in capSatisfiedMap)) {
    ensureWatch()
    fetchCapable(cap)
  }
}

export function startCapabilityWatch(): void {
  ensureWatch()
}
