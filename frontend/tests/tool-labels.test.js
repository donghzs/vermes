import { describe, it, expect } from 'vitest'
import { toolLabel, TOOL_NAME_MAP } from '@/utils/toolLabels'

/**
 * 工具中文名的显示契约。
 *
 * 后端另有一条守卫测试（vermes_cli/scholarforge/tests/test_tool_labels.py）负责
 * 「28 个论文工具是否都配了中文名」；这里负责「拿到名字后显示得对不对」。
 * 两者分工：那边防漏配，这边防回退。
 */
describe('工具中文名显示', () => {
  it('论文工具显示中文名，而不是内部标识符', () => {
    expect(toolLabel('scholarforge_stats_table')).toBe('统计转三线表')
    expect(toolLabel('scholarforge_check_stats')).toBe('核统计数据')
    expect(toolLabel('scholarforge_plagiarism_check')).toBe('查重')
  })

  it('未配置的名称原样返回 —— 绝不能显示空白或 undefined', () => {
    // 新增工具而中文名还没配时，宁可露英文也不要开天窗
    expect(toolLabel('scholarforge_brand_new_tool')).toBe('scholarforge_brand_new_tool')
    expect(toolLabel('')).toBe('')
    expect(toolLabel(undefined)).toBe('')
  })

  it('通用工具的原有映射没被搬丢', () => {
    // 这些是从 MessageList.vue 的内联对象搬过来的，搬漏了对话里就会露英文
    expect(toolLabel('read_file')).toBe('读取文件')
    expect(toolLabel('terminal')).toBe('终端')
    expect(toolLabel('web_search')).toBe('网页搜索')
    expect(toolLabel('code_execution')).toBe('执行代码')
  })

  it('每个论文工具的中文名都不是「去掉前缀的英文名」', () => {
    // 防假本地化：如果中文名等于 name 去掉 scholarforge_，等于没翻译
    for (const [name, label] of Object.entries(TOOL_NAME_MAP)) {
      if (!name.startsWith('scholarforge_')) continue
      expect(label).not.toBe(name.replace('scholarforge_', ''))
    }
  })

  it('中文名不为空且不含空格占位', () => {
    for (const [name, label] of Object.entries(TOOL_NAME_MAP)) {
      expect(label && label.trim(), `${name} 的中文名为空`).toBeTruthy()
    }
  })

  it('论文工具之间的中文名互不重复', () => {
    // 两个工具同名，用户点下去不知道会跑哪个
    const seen = new Map()
    for (const [name, label] of Object.entries(TOOL_NAME_MAP)) {
      if (!name.startsWith('scholarforge_')) continue
      expect(seen.has(label), `中文名「${label}」被 ${seen.get(label)} 和 ${name} 同时占用`).toBe(false)
      seen.set(label, name)
    }
  })
})
