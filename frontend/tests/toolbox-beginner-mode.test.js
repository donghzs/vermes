import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ToolBox from '@/components/scholar/ToolBox.vue'
import {
  BEGINNER_FLOW,
  BEGINNER_ENTRY,
  BEGINNER_NAMES,
  matchTool,
} from '@/utils/beginnerFlow'
import { toolLabel } from '@/utils/toolLabels'

/**
 * 工具箱「新手模式」（2026-09-17 非技术用户上手专项）。
 *
 * 后端另有一条守卫（vermes_cli/scholarforge/tests/test_tool_labels.py）静态扫描
 * beginnerFlow.js，负责「流程里引用的工具名是否都存在」；这里负责「渲染得对不对」。
 */

vi.mock('@/stores/scholar', () => ({
  useScholarStore: () => ({
    pendingTool: null,
    pendingPrefill: null,
    clearPending: vi.fn(),
  }),
}))
vi.mock('@/utils/invokeTool', () => ({ invokeTool: vi.fn() }))

const TOOLS = [
  { name: 'scholarforge_run_pipeline', description: '一条龙写作', emoji: '🚀' },
  { name: 'scholarforge_search', description: '找文献', emoji: '🔍' },
  { name: 'scholarforge_save_literature_cards', description: '存文献卡片', emoji: '📇' },
  { name: 'scholarforge_outline', description: '生成大纲', emoji: '📝' },
  { name: 'scholarforge_write', description: '写章节', emoji: '✍️' },
  { name: 'scholarforge_replace_citations', description: '补真实引用', emoji: '🔗' },
  { name: 'scholarforge_polish', description: '润色', emoji: '✨' },
  { name: 'scholarforge_score', description: '评分', emoji: '🎯' },
  { name: 'scholarforge_export', description: '导出 Word', emoji: '📄' },
  // 流程外的工具（验证搜索时不会变成 0 结果）
  { name: 'scholarforge_stats_table', description: '统计结果转三线表', emoji: '📊' },
  { name: 'scholarforge_plagiarism_check', description: '查重', emoji: '🛡️' },
]

async function mountBox() {
  const w = mount(ToolBox, {
    global: {
      stubs: { SchemaForm: true, ToolResult: true },
    },
  })
  await flushPromises()
  return w
}

beforeEach(() => {
  localStorage.clear()
  global.fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ tools: TOOLS }),
  }))
})

describe('新手模式 · 默认与流程', () => {
  it('默认进入新手模式（目标用户不是开发者）', async () => {
    const w = await mountBox()
    expect(w.text()).toContain('写论文的 8 步')
    expect(w.text()).toContain('不知道从哪开始')
  })

  it('8 步按写作顺序渲染，且带 1..8 编号', async () => {
    const w = await mountBox()
    const steps = w.findAll('[data-test="beginner-step"]')
    expect(steps.length).toBe(BEGINNER_FLOW.length)
    const labels = steps.map((s) => s.text())
    BEGINNER_FLOW.forEach((s, i) => {
      expect(labels[i]).toContain(String(i + 1))
      expect(labels[i]).toContain(toolLabel(s.name))
    })
  })

  it('步骤显示中文名与白话提示，不露内部标识符', async () => {
    const w = await mountBox()
    const steps = w.findAll('[data-test="beginner-step"]')
    steps.forEach((s) => {
      expect(s.text()).not.toMatch(/scholarforge_[a-z_]+/)
    })
    // 每步都得有 hint，否则只是把 28 个工具换了个皮
    BEGINNER_FLOW.forEach((s, i) => {
      expect(steps[i].text()).toContain(s.hint)
    })
  })

  it('提示里不出现技术字段名', async () => {
    // 非技术用户看到 project_id / cohens_d 只会更困惑
    const banned = ['project_id', 'section_key', 'cohens_d', 'p_value', 'schema']
    for (const s of BEGINNER_FLOW) {
      for (const b of banned) {
        expect(s.hint.toLowerCase()).not.toContain(b)
      }
    }
  })

  it('入口 CTA 是「一条龙写作」且不在 8 步里', async () => {
    const w = await mountBox()
    expect(w.text()).toContain(toolLabel(BEGINNER_ENTRY))
    // 它是"覆盖整条流程"的快捷方式，放第 1 步会让人以为必须先跑它
    expect(BEGINNER_FLOW.map((s) => s.name)).not.toContain(BEGINNER_ENTRY)
  })
})

describe('新手模式 · 搜索', () => {
  it('搜中文名能命中流程外的工具（不会 0 结果）', async () => {
    const w = await mountBox()
    await w.find('input[type="text"]').setValue('三线表')
    await flushPromises()
    expect(w.text()).toContain('其它匹配的工具')
    expect(w.text()).toContain(toolLabel('scholarforge_stats_table'))
  })

  it('搜英文标识符仍可用（向后兼容）', async () => {
    const w = await mountBox()
    // 新手模式下搜到流程外工具会落到「其它匹配」，故先切全部模式
    await w.findAll('button').find((b) => b.text().includes('全部工具')).trigger('click')
    await w.find('input[type="text"]').setValue('stats_table')
    await flushPromises()
    expect(w.text()).toContain(toolLabel('scholarforge_stats_table'))
  })
})

describe('模式切换', () => {
  it('切到「全部工具」显示分组视图', async () => {
    const w = await mountBox()
    await w.findAll('button').find((b) => b.text().includes('全部工具')).trigger('click')
    await flushPromises()
    expect(w.text()).toContain('写作主链')
    expect(w.text()).not.toContain('写论文的 8 步')
  })

  it('选择被记住（localStorage）', async () => {
    const w = await mountBox()
    await w.findAll('button').find((b) => b.text().includes('全部工具')).trigger('click')
    await flushPromises()
    expect(localStorage.getItem('vermes.toolbox.mode')).toBe('all')
  })

  it('上次选了「全部」则下次进入还是全部', async () => {
    localStorage.setItem('vermes.toolbox.mode', 'all')
    const w = await mountBox()
    expect(w.text()).toContain('写作主链')
    expect(w.text()).not.toContain('写论文的 8 步')
  })
})

describe('beginnerFlow 数据契约', () => {
  it('流程无重复步骤', () => {
    const names = BEGINNER_FLOW.map((s) => s.name)
    expect(new Set(names).size).toBe(names.length)
  })

  it('BEGINNER_NAMES 覆盖流程 + 入口', () => {
    for (const s of BEGINNER_FLOW) expect(BEGINNER_NAMES.has(s.name)).toBe(true)
    expect(BEGINNER_NAMES.has(BEGINNER_ENTRY)).toBe(true)
    expect(BEGINNER_NAMES.size).toBe(BEGINNER_FLOW.length + 1)
  })

  it('matchTool 按中文名匹配（不能退回英文标识符）', () => {
    const t = { name: 'scholarforge_stats_table', description: '统计结果转三线表' }
    expect(matchTool(t, '三线表')).toBe(true)
    expect(matchTool(t, '查重')).toBe(false)
  })

  it('matchTool 空关键字不过滤', () => {
    const t = { name: 'scholarforge_write', description: '写章节' }
    expect(matchTool(t, '')).toBe(true)
  })

  it('matchTool 也能命中英文标识符（向后兼容）', () => {
    const t = { name: 'scholarforge_stats_table', description: 'x' }
    expect(matchTool(t, 'stats_table')).toBe(true)
  })
})
