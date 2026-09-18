import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StateBlock from '../src/components/StateBlock.vue'
import PrereqBanner from '../src/components/PrereqBanner.vue'

describe('U-P0-4 StateBlock', () => {
  it('empty 默认文案可覆盖，detail 可解释原因', () => {
    const w = mount(StateBlock, {
      props: { state: 'empty', text: '暂无会话', detail: '新建对话开始' },
    })
    expect(w.text()).toContain('暂无会话')
    expect(w.text()).toContain('新建对话开始')
  })

  it('无 actionLabel 时不渲染默认操作按钮', () => {
    const w = mount(StateBlock, { props: { state: 'empty', text: '空' } })
    expect(w.find('[data-testid="state-block-action"]').exists()).toBe(false)
  })

  it('actionLabel + action 事件：缺模型/缺项目类失败可行动', async () => {
    const w = mount(StateBlock, {
      props: { state: 'error', text: '加载失败', actionLabel: '重试' },
    })
    const btn = w.find('[data-testid="state-block-action"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')
    expect(w.emitted('action')).toHaveLength(1)
  })

  it('actions 插槽可自定义按钮组', () => {
    const w = mount(StateBlock, {
      props: { state: 'empty', text: '暂无会话' },
      slots: { actions: '<button data-testid="custom">新建</button>' },
    })
    expect(w.find('[data-testid="custom"]').exists()).toBe(true)
  })

  it('loading 态不编造成成功/空列表', () => {
    const w = mount(StateBlock, { props: { state: 'loading', text: '加载中…' } })
    expect(w.text()).toContain('加载中…')
    expect(w.text()).not.toMatch(/成功|已完成/)
  })
})

describe('U-P0-4 PrereqBanner', () => {
  it('visible=false 时不渲染（条件已满足）', () => {
    const w = mount(PrereqBanner, { props: { visible: false, title: '还没选项目' } })
    expect(w.find('[data-testid="prereq-banner"]').exists()).toBe(false)
  })

  it('title + text + 主次操作事件齐全', async () => {
    const w = mount(PrereqBanner, {
      props: {
        visible: true,
        title: '还没选项目',
        text: '写回会落进隐藏默认项目',
        primaryLabel: '去选一个',
        secondaryLabel: '看看流程',
        dismissible: true,
      },
    })
    expect(w.text()).toContain('还没选项目')
    expect(w.text()).toContain('写回会落进隐藏默认项目')
    await w.find('[data-testid="prereq-primary"]').trigger('click')
    await w.find('[data-testid="prereq-secondary"]').trigger('click')
    await w.find('[data-testid="prereq-dismiss"]').trigger('click')
    expect(w.emitted('primary')).toHaveLength(1)
    expect(w.emitted('secondary')).toHaveLength(1)
    expect(w.emitted('dismiss')).toHaveLength(1)
  })

  it('tone=red 时使用红色预警样式类', () => {
    const w = mount(PrereqBanner, {
      props: { visible: true, tone: 'red', text: '已自动回滚' },
    })
    expect(w.find('[data-testid="prereq-banner"]').classes().some(c => c.includes('red'))).toBe(true)
  })

  it('仅 primary 时 secondary 按钮不出现', () => {
    const w = mount(PrereqBanner, {
      props: { visible: true, title: 'X', primaryLabel: '去配置' },
    })
    expect(w.find('[data-testid="prereq-primary"]').exists()).toBe(true)
    expect(w.find('[data-testid="prereq-secondary"]').exists()).toBe(false)
  })
})
