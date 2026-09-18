import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import SceneStatusBar from '../src/components/SceneStatusBar.vue'

describe('U-P0-6 SceneStatusBar', () => {
  it('一切正常时只渲染细 chips，不出现彩色横幅', () => {
    const w = mount(SceneStatusBar, {
      props: {
        items: [{ key: 'project', icon: '🗂️', label: '#52 幼小衔接' }],
      },
    })
    expect(w.find('[data-testid="scene-status-chips"]').exists()).toBe(true)
    expect(w.find('[data-testid="prereq-banner"]').exists()).toBe(false)
    expect(w.text()).toContain('#52')
  })

  it('无 items 且无 alert 时不渲染（界面保持干净）', () => {
    const w = mount(SceneStatusBar, { props: { items: [] } })
    expect(w.find('[data-testid="scene-status-chips"]').exists()).toBe(false)
    expect(w.find('[data-testid="prereq-banner"]').exists()).toBe(false)
  })

  it('有 alert 时优先 PrereqBanner，且不叠 chips', async () => {
    const w = mount(SceneStatusBar, {
      props: {
        items: [{ key: 'x', label: '不该出现' }],
        alert: { title: '还没选项目', text: '写回会散落', primaryLabel: '去选一个' },
      },
    })
    expect(w.find('[data-testid="prereq-banner"]').exists()).toBe(true)
    expect(w.find('[data-testid="scene-status-chips"]').exists()).toBe(false)
    await w.find('[data-testid="prereq-primary"]').trigger('click')
    expect(w.emitted('primary')).toHaveLength(1)
  })

  it('Harness 降级 chip 可并入 items，正常时不默认出现绿灯', () => {
    // 模拟 useHarnessLight.harnessChip 的形状
    const degraded = { key: 'harness', icon: '🛡', label: 'Harness 2' }
    const w = mount(SceneStatusBar, { props: { items: [degraded] } })
    expect(w.text()).toContain('Harness 2')
  })
})
