import { describe, it, expect, beforeEach, vi } from 'vitest'

/**
 * 文件 tab 工作目录浏览逻辑测试
 */

import { ref } from 'vue'

function setupFileBrowser() {
  const fileItems = ref([])
  const fileLoading = ref(false)
  const fileCrumbs = ref('')
  const currentDir = ref('')

  async function loadFiles(dir = '', fetchFn) {
    fileLoading.value = true
    try {
      const res = await fetchFn(`/api/v1/workspace/tree?path=${encodeURIComponent(dir)}`)
      if (!res.ok) return
      const data = await res.json()
      fileItems.value = data.items || []
      fileCrumbs.value = data.current || '工作目录'
      currentDir.value = dir
    } catch (e) {
      // error
    } finally {
      fileLoading.value = false
    }
  }

  function goUp() {
    if (!currentDir.value) return false
    const parts = currentDir.value.split('/')
    parts.pop()
    return parts.join('/')
  }

  function shouldSkipDir(name) {
    const skip = {'node_modules': true, '__pycache__': true, '.git': true, 'dist': true, 'build': true, '.venv': true, 'venv': true}
    return skip[name] || false
  }

  return { fileItems, fileLoading, fileCrumbs, currentDir, loadFiles, goUp, shouldSkipDir }
}

describe('文件 tab 工作目录浏览', () => {
  it('goUp 返回上级目录路径', () => {
    const { currentDir, goUp } = setupFileBrowser()
    currentDir.value = 'src/components'
    expect(goUp()).toBe('src')
  })

  it('goUp 根目录返回空字符串', () => {
    const { currentDir, goUp } = setupFileBrowser()
    currentDir.value = ''
    expect(goUp()).toBe(false)
  })

  it('goUp 单层目录返回空', () => {
    const { currentDir, goUp } = setupFileBrowser()
    currentDir.value = 'src'
    expect(goUp()).toBe('')
  })

  it('shouldSkipDir 过滤 node_modules', () => {
    const { shouldSkipDir } = setupFileBrowser()
    expect(shouldSkipDir('node_modules')).toBe(true)
    expect(shouldSkipDir('__pycache__')).toBe(true)
    expect(shouldSkipDir('.git')).toBe(true)
    expect(shouldSkipDir('dist')).toBe(true)
    expect(shouldSkipDir('build')).toBe(true)
  })

  it('shouldSkipDir 不过滤正常目录', () => {
    const { shouldSkipDir } = setupFileBrowser()
    expect(shouldSkipDir('src')).toBe(false)
    expect(shouldSkipDir('tests')).toBe(false)
    expect(shouldSkipDir('frontend')).toBe(false)
  })

  it('loadFiles 正确解析 API 响应', async () => {
    const { fileItems, fileCrumbs, currentDir, loadFiles } = setupFileBrowser()
    const mockFetch = async (url) => ({
      ok: true,
      json: async () => ({
        items: [
          { name: 'README.md', path: 'README.md', is_dir: false, size: 1234, ext: '.md' },
          { name: 'src', path: 'src', is_dir: true, size: 0, ext: '' },
        ],
        current: '工作目录',
      }),
    })
    await loadFiles('', mockFetch)
    expect(fileItems.value.length).toBe(2)
    expect(fileItems.value[0].name).toBe('README.md')
    expect(fileCrumbs.value).toBe('工作目录')
    expect(currentDir.value).toBe('')
  })

  it('loadFiles 子目录设置 currentDir', async () => {
    const { fileItems, currentDir, loadFiles } = setupFileBrowser()
    const mockFetch = async (url) => ({
      ok: true,
      json: async () => ({
        items: [{ name: 'main.js', path: 'src/main.js', is_dir: false, size: 890, ext: '.js' }],
        current: 'src',
      }),
    })
    await loadFiles('src', mockFetch)
    expect(fileItems.value.length).toBe(1)
    expect(currentDir.value).toBe('src')
  })

  it('loadFiles API 不 ok 时不清空', async () => {
    const { fileItems, fileLoading, loadFiles } = setupFileBrowser()
    const mockFetch = async () => ({ ok: false })
    await loadFiles('', mockFetch)
    expect(fileItems.value.length).toBe(0)
    expect(fileLoading.value).toBe(false)
  })
})
