import { describe, it, expect, beforeEach } from 'vitest'
import { useRightPanel } from '../src/composables/useRightPanel'

describe('useRightPanel composable', () => {
  beforeEach(() => {
    // 重置模块级状态
    const { open, tab, artifactTab, autoOpenOnArtifact, panelWidth } = useRightPanel()
    open.value = false
    tab.value = 'skills'
    artifactTab.value = 'artifacts'
    autoOpenOnArtifact.value = true
    panelWidth.value = 420
  })

  it('openPanel 设置 tab 并打开面板', () => {
    const { open, tab, openPanel } = useRightPanel()
    expect(open.value).toBe(false)
    openPanel('artifacts')
    expect(open.value).toBe(true)
    expect(tab.value).toBe('artifacts')
  })

  it('closePanel 关闭面板但不改 tab', () => {
    const { open, tab, openPanel, closePanel } = useRightPanel()
    openPanel('tools')
    closePanel()
    expect(open.value).toBe(false)
    expect(tab.value).toBe('tools')
  })

  it('setTab 切换 tab', () => {
    const { tab, setTab } = useRightPanel()
    setTab('mcp')
    expect(tab.value).toBe('mcp')
  })

  it('setArtifactTab 切换产物子 tab', () => {
    const { artifactTab, setArtifactTab } = useRightPanel()
    setArtifactTab('changes')
    expect(artifactTab.value).toBe('changes')
  })

  it('panelWidth 可调', () => {
    const { panelWidth } = useRightPanel()
    expect(panelWidth.value).toBe(420)
    panelWidth.value = 600
    expect(panelWidth.value).toBe(600)
  })

  it('autoOpenOnArtifact 默认 true', () => {
    const { autoOpenOnArtifact } = useRightPanel()
    expect(autoOpenOnArtifact.value).toBe(true)
    autoOpenOnArtifact.value = false
    expect(autoOpenOnArtifact.value).toBe(false)
  })

  it('tab 有效值包括 artifacts/files/changes/preview', () => {
    const { artifactTab, setArtifactTab } = useRightPanel()
    const valid = ['artifacts', 'files', 'changes', 'preview']
    for (const v of valid) {
      setArtifactTab(v)
      expect(artifactTab.value).toBe(v)
    }
  })
})
