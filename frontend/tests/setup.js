/**
 * Vitest 全局 setup：Node 26 的实验性 localStorage 在无 --localstorage-file 时
 * 不可用（undefined / 抛错），而业务测试大量依赖 localStorage.clear/get/set。
 * happy-dom 理应注入，但部分 worker 路径下 Node 原生占位优先且不可用 —— 这里
 * 统一兜底，保证测试环境稳定。
 */
const g = globalThis

function createMemoryStorage() {
  const store = new Map()
  return {
    getItem(k) {
      const key = String(k)
      return store.has(key) ? store.get(key) : null
    },
    setItem(k, v) {
      store.set(String(k), String(v))
    },
    removeItem(k) {
      store.delete(String(k))
    },
    clear() {
      store.clear()
    },
    key(i) {
      return [...store.keys()][i] ?? null
    },
    get length() {
      return store.size
    },
  }
}

function storageUsable(s) {
  if (!s || typeof s !== 'object') return false
  if (typeof s.clear !== 'function' || typeof s.getItem !== 'function') return false
  try {
    const k = '__vermes_vitest_probe__'
    s.setItem(k, '1')
    s.getItem(k)
    s.removeItem(k)
    return true
  } catch {
    return false
  }
}

if (!storageUsable(g.localStorage)) {
  try {
    Object.defineProperty(g, 'localStorage', {
      value: createMemoryStorage(),
      writable: true,
      configurable: true,
    })
  } catch {
    g.localStorage = createMemoryStorage()
  }
}

if (!storageUsable(g.sessionStorage)) {
  try {
    Object.defineProperty(g, 'sessionStorage', {
      value: createMemoryStorage(),
      writable: true,
      configurable: true,
    })
  } catch {
    g.sessionStorage = createMemoryStorage()
  }
}
