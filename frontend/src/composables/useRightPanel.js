import { ref } from 'vue'

// 全局右侧大面板状态（产物工作台专用）。模块级单例，Sidebar 触发、App 渲染。
// Agent 管理已独立为全宽页面 /agents（AgentManagement.vue）。
const open = ref(false)
const tab = ref('artifacts') // 'artifacts' | 'files' | 'changes' | 'preview'
const artifactTab = ref('artifacts')
const autoOpenOnArtifact = ref(true)
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
  return { open, tab, artifactTab, autoOpenOnArtifact, panelWidth, openPanel, closePanel, setTab, setArtifactTab }
}
