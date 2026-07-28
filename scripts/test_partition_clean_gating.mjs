#!/usr/bin/env node
// G0/G2 两次启动模拟实测（docs/design-startup-integrity-guards-final.md §G0/G2 兼容性论证）
//
// 测的是磁盘上 electron/main.js 的真实源码段（正则抽取 CLEAN_STAMP_FILE →
// maybeCleanPartitionStorage 整块后注入 mock 执行），不是复制出来的逻辑副本。
//
// 断言矩阵（终稿 §5 失败分支矩阵对应行）：
//   1. 首启（无版本戳）      → 执行清理；storages 不含 'indexdb'；成功后写戳
//   2. 同版本二次启动        → 零清理（G0 修复核心断言）
//   3. 版本变更              → 再次清理（防护面不缩）；戳更新
//   4. 版本戳读失败          → 保守视为变更 → 清理（多清 localstorage，不丢 IDB）
//   5. 清理失败              → 不写戳（下次启动重清，幂等自愈）
//
// 运行：node scripts/test_partition_clean_gating.mjs

import { readFileSync, mkdtempSync, rmSync, readFileSync as rf, existsSync } from 'fs'
import fsModule from 'fs'
import pathModule from 'path'
import { tmpdir } from 'os'
import { fileURLToPath } from 'url'

const __dirname = pathModule.dirname(fileURLToPath(import.meta.url))
const mainJsPath = pathModule.join(__dirname, '..', 'electron', 'main.js')
const src = readFileSync(mainJsPath, 'utf8')

// 抽取真实源码段：从 CLEAN_STAMP_FILE 声明到 createWindow 注释前
const start = src.indexOf('const CLEAN_STAMP_FILE')
const end = src.indexOf('// ── 创建窗口 ──')
if (start === -1 || end === -1 || end <= start) {
  console.error('FAIL: 无法在 electron/main.js 中定位 G2 门控源码段（标记丢失？）')
  process.exit(1)
}
const guardSrc = src.slice(start, end)

// 静态断言 0：main.js 全文不得再出现含 indexdb 的 clearStorageData storages 列表
const clearCalls = [...src.matchAll(/clearStorageData\s*\(\s*\{[^}]*storages\s*:\s*\[([^\]]*)\]/g)]
for (const m of clearCalls) {
  if (m[1].includes('indexdb')) {
    console.error(`FAIL[静态]: main.js 仍存在清 indexdb 的 clearStorageData: ${m[0].slice(0, 120)}`)
    process.exit(1)
  }
}
console.log(`PASS[静态]: main.js 中 ${clearCalls.length} 处 clearStorageData 均不含 indexdb`)

// ── 沙箱执行环境 ──
let failures = 0
function check(name, cond, detail = '') {
  if (cond) { console.log(`PASS: ${name}`) }
  else { console.error(`FAIL: ${name} ${detail}`); failures++ }
}

function makeEnv({ version, stampReadThrows = false, clearRejects = false }) {
  const userData = mkdtempSync(pathModule.join(tmpdir(), 'vermes-g2-'))
  const calls = { clear: [], swUnregister: 0 }
  const app = {
    getVersion: () => version,
    getPath: (k) => { if (k !== 'userData') throw new Error('unexpected getPath: ' + k); return userData },
  }
  const fsProxy = new Proxy(fsModule, {
    get(target, prop) {
      if (prop === 'readFileSync' && stampReadThrows) {
        return () => { throw new Error('simulated EACCES') }
      }
      return target[prop]
    },
  })
  const ses = {
    clearStorageData: (opts) => {
      calls.clear.push(opts)
      return clearRejects ? Promise.reject(new Error('simulated clear failure')) : Promise.resolve()
    },
    getServiceWorkers: () => Promise.resolve({ getAll: () => [] }),
  }
  // 注入真实源码段
  const factory = new Function('fs', 'path', 'app', 'console', `
    ${guardSrc}
    return { maybeCleanPartitionStorage, readCleanStamp, writeCleanStamp };
  `)
  const api = factory(fsProxy, pathModule, app, { log: () => {}, error: () => {} })
  return { userData, calls, ses, api, cleanup: () => rmSync(userData, { recursive: true, force: true }) }
}

const tick = () => new Promise((r) => setTimeout(r, 20))

// 场景 1+2+3：首启 → 同版本二启 → 升版本三启（同一 userData 连续模拟）
{
  const env = makeEnv({ version: '1.0.0' })
  // 启动 1：无版本戳
  env.api.maybeCleanPartitionStorage(env.ses)
  await tick()
  check('场景1 首启执行清理', env.calls.clear.length === 1)
  check('场景1 storages 不含 indexdb', env.calls.clear.length > 0 && !env.calls.clear[0].storages.includes('indexdb'),
    JSON.stringify(env.calls.clear[0] || {}))
  check('场景1 保留 localstorage/serviceworkers 清理（防护面不缩）',
    env.calls.clear.length > 0 && env.calls.clear[0].storages.includes('localstorage') && env.calls.clear[0].storages.includes('serviceworkers'))
  const stampPath = pathModule.join(env.userData, 'last-clean-version')
  check('场景1 清理成功后写版本戳', existsSync(stampPath) && rf(stampPath, 'utf8').trim() === '1.0.0')

  // 启动 2：同版本 —— G0 修复核心
  env.api.maybeCleanPartitionStorage(env.ses)
  await tick()
  check('场景2 同版本二次启动零清理（G0 修复）', env.calls.clear.length === 1)

  // 启动 3：版本变更
  const env3 = { ...env, api: null }
  {
    // 换 version 但复用同一 userData：重新构造注入（版本戳仍在磁盘）
    const calls3 = { clear: [] }
    const app3 = { getVersion: () => '1.1.0', getPath: () => env.userData }
    const factory = new Function('fs', 'path', 'app', 'console', `${guardSrc}\nreturn { maybeCleanPartitionStorage };`)
    const api3 = factory(fsModule, pathModule, app3, { log: () => {}, error: () => {} })
    const ses3 = { clearStorageData: (o) => { calls3.clear.push(o); return Promise.resolve() }, getServiceWorkers: () => Promise.resolve({ getAll: () => [] }) }
    api3.maybeCleanPartitionStorage(ses3)
    await tick()
    check('场景3 版本变更再次清理（防护面不缩）', calls3.clear.length === 1)
    check('场景3 版本戳更新为新版本', rf(stampPath, 'utf8').trim() === '1.1.0')
  }
  env.cleanup()
}

// 场景 4：版本戳读失败 → 保守清理
{
  const env = makeEnv({ version: '1.0.0', stampReadThrows: true })
  env.api.maybeCleanPartitionStorage(env.ses)
  await tick()
  check('场景4 版本戳读失败 → 保守执行清理', env.calls.clear.length === 1)
  env.cleanup()
}

// 场景 5：清理失败 → 不写戳（下次重清）
{
  const env = makeEnv({ version: '1.0.0', clearRejects: true })
  env.api.maybeCleanPartitionStorage(env.ses)
  await tick()
  const stampPath = pathModule.join(env.userData, 'last-clean-version')
  check('场景5 清理失败不写版本戳（下次启动重清）', !existsSync(stampPath))
  env.cleanup()
}

console.log(failures === 0 ? '\n✅ G0/G2 两次启动模拟：全部断言通过' : `\n❌ ${failures} 个断言失败`)
process.exit(failures === 0 ? 0 : 1)
