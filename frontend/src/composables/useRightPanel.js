import { ref } from 'vue'

// 全局右侧大面板状态（产物工作台专用）。模块级单例，Sidebar 触发、App 渲染。
// Agent 管理已独立为全宽页面 /agents（AgentManagement.vue）。
const open = ref(false)
const tab = ref('artifacts') // 'artifacts' | 'files' | 'changes' | 'preview'
const artifactTab = ref('artifacts')
// autoOpenOnArtifact 已移除（2026-09-22, P0-3）：它是第二套「产物到达自动开面板」
// 开关，但全仓无任何消费方，与 useArtifactPanel.autoOpen 并存会造成双状态机漂移。
// 自动弹出统一由 useArtifactPanel.autoOpen 负责（且只在 onDelivery 时触发）。
const panelWidth = ref(420)

export function useRightPanel() {
  function openPanel(t = 'artifacts') {
    tab.value = t
    open.value = true
  }
  function closePanel() {
    open.value = false
  }
  function setTab(t) {
    tab.value = t
  }
  function setArtifactTab(t) {
    artifactTab.value = t
  }
  return { open, tab, artifactTab, panelWidth, openPanel, closePanel, setTab, setArtifactTab }
}
