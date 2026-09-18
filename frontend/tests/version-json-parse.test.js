/**
 * version.json 解析契约测试（不依赖浏览器 localStorage）。
 * 目标：Mac 架构嵌套 / downloads 段 / 顶层扁平 都能解出 downloadUrl 与 sha256。
 */
import { describe, it, expect } from 'vitest'

const ARM = {
  url: 'https://vbit.top/vermes/downloads/Vermes-2.4.8-arm64.dmg',
  sha256: '1cf3fcaaf357c5841fae5adb4ab1cb0d03195c9334ad0cd46feaae3b936b6ad8',
  size: 277533304,
}
const X64 = {
  url: 'https://vbit.top/vermes/downloads/Vermes-2.4.8.dmg',
  sha256: '9cc1c9072df52463a5e508be6c931969962e7b1409e9a69ecb2d3d4cac023739',
  size: 282718942,
}
const WIN = {
  url: 'https://vbit.top/vermes/downloads/Vermes-2.4.8-win-x64.exe',
  sha256: 'a72d47f05a79c6d4c88e1c6d736698fe77dbe58d21682ca5d2a2a4e2511be8f9',
  size: 299734287,
}

// 与 update.js 相同的解析规则（抽成纯函数便于单测；若 update.js 再改请同步这里）
function parseDownload(res, { isMac, isArm }) {
  const urlOf = (node) => {
    if (!node) return ''
    if (typeof node === 'string') return node
    if (typeof node !== 'object') return ''
    return node.url || node.dmg || node.zip || node.exe || node.installer || ''
  }
  const pickArchNode = (root) => {
    if (!root || typeof root !== 'object') return null
    if (urlOf(root)) return root
    const order = isMac
      ? (isArm ? ['arm64', 'x64', 'mac'] : ['x64', 'arm64', 'mac'])
      : (['x64', 'win', 'windows', 'ia32', 'arm64'])
    for (const key of order) {
      if (root[key] && urlOf(root[key])) return root[key]
    }
    for (const v of Object.values(root)) {
      if (v && typeof v === 'object' && urlOf(v)) return v
    }
    return null
  }
  const downloads = (res.downloads && typeof res.downloads === 'object') ? res.downloads : null
  const platRoot = isMac
    ? (downloads?.mac || res.mac || res.macOS || res.macos || res.darwin || null)
    : (downloads?.windows || res.windows || res.win || null)
  const platNode = pickArchNode(platRoot)
  return {
    url: urlOf(platNode),
    sha256: (platNode && typeof platNode === 'object' && (platNode.sha256 || platNode.sha_256)) || '',
  }
}

const res = {
  version: '2.4.8',
  downloads: { mac: { arm64: ARM, x64: X64 }, windows: WIN },
  mac: { arm64: ARM, x64: X64 },
  windows: WIN,
}

describe('version.json 下载解析契约', () => {
  it('Mac + arm64 从 downloads 或顶层嵌套都能命中 arm 包', () => {
    const a = parseDownload(res, { isMac: true, isArm: true })
    expect(a.url).toBe(ARM.url)
    expect(a.sha256).toBe(ARM.sha256)
  })

  it('Mac + x64 命中 x64 包', () => {
    const a = parseDownload(res, { isMac: true, isArm: false })
    expect(a.url).toBe(X64.url)
  })

  it('仅顶层 mac 嵌套（无 downloads）仍可解析', () => {
    const a = parseDownload({ version: '2.4.8', mac: { arm64: ARM, x64: X64 } }, { isMac: true, isArm: true })
    expect(a.url).toBe(ARM.url)
  })

  it('Windows 扁平 url 可解析', () => {
    const a = parseDownload(res, { isMac: false, isArm: false })
    expect(a.url).toBe(WIN.url)
  })

  it('旧式顶层 mac.url 字符串节点仍兼容', () => {
    const a = parseDownload({ mac: { url: ARM.url, sha256: ARM.sha256 } }, { isMac: true, isArm: true })
    expect(a.url).toBe(ARM.url)
    expect(a.sha256).toBe(ARM.sha256)
  })
})
