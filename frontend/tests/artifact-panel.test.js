import { describe, it, expect } from 'vitest'

/**
 * ArtifactPanel diff 着色渲染逻辑测试
 * 
 * 提取核心 diff 行着色逻辑为纯函数测试（不依赖 Vue 组件挂载）
 */

function diffLineClass(line) {
  if (line.startsWith('+')) return 'text-green-600 dark:text-green-400'
  if (line.startsWith('-')) return 'text-red-600 dark:text-red-400'
  return 'text-gray-500 dark:text-gray-400'
}

function diffLineIcon(line) {
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'remove'
  return 'none'
}

describe('Diff 着色渲染逻辑', () => {
  it('+ 开头行标绿色', () => {
    expect(diffLineClass('+new line')).toContain('green')
  })

  it('- 开头行标红色', () => {
    expect(diffLineClass('-removed line')).toContain('red')
  })

  it('普通行标灰色', () => {
    expect(diffLineClass('context line')).toContain('gray')
  })

  it('空行标灰色', () => {
    expect(diffLineClass('')).toContain('gray')
  })

  it('+++ 文件头标绿色（以 + 开头）', () => {
    expect(diffLineClass('+++ b/newfile.py')).toContain('green')
  })

  it('--- 文件头标红色（以 - 开头）', () => {
    expect(diffLineClass('--- a/oldfile.py')).toContain('red')
  })

  it('diff 行图标 add/remove/none', () => {
    expect(diffLineIcon('+x')).toBe('add')
    expect(diffLineIcon('-y')).toBe('remove')
    expect(diffLineIcon('z')).toBe('none')
  })
})

/**
 * formatSize 文件大小格式化逻辑
 */
function formatSize(bytes) {
  if (bytes < 1024) return bytes + 'B'
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + 'KB'
  return (bytes / 1048576).toFixed(1) + 'MB'
}

describe('formatSize 文件大小格式化', () => {
  it('小于 1KB 显示 B', () => {
    expect(formatSize(0)).toBe('0B')
    expect(formatSize(512)).toBe('512B')
    expect(formatSize(1023)).toBe('1023B')
  })

  it('1KB-1MB 显示 KB', () => {
    expect(formatSize(1024)).toBe('1.0KB')
    expect(formatSize(5120)).toBe('5.0KB')
    expect(formatSize(1048575)).toBe('1024.0KB')
  })

  it('≥1MB 显示 MB', () => {
    expect(formatSize(1048576)).toBe('1.0MB')
    expect(formatSize(5242880)).toBe('5.0MB')
  })
})

/**
 * fileIcon 文件图标映射
 */
function fileIcon(ext) {
  const map = {
    '.md': '📝', '.html': '🌐', '.json': '📋', '.csv': '📊',
    '.py': '🐍', '.js': '📜', '.ts': '📜', '.txt': '📄',
    '.pdf': '📕', '.docx': '📘', '.xlsx': '📗', '.pptx': '📙',
    '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.gif': '🖼️', '.svg': '🖼️',
    '.step': '⚙️', '.stp': '⚙️', '.stl': '🖨️', '.gcode': '⚙️',
  }
  return map[ext] || '📄'
}

describe('fileIcon 文件图标映射', () => {
  it('已知扩展名返回对应图标', () => {
    expect(fileIcon('.md')).toBe('📝')
    expect(fileIcon('.py')).toBe('🐍')
    expect(fileIcon('.html')).toBe('🌐')
    expect(fileIcon('.stl')).toBe('🖨️')
  })

  it('未知扩展名返回默认 📄', () => {
    expect(fileIcon('.xyz')).toBe('📄')
    expect(fileIcon('')).toBe('📄')
  })

  it('制造业格式有图标', () => {
    expect(fileIcon('.step')).toBe('⚙️')
    expect(fileIcon('.gcode')).toBe('⚙️')
  })
})
