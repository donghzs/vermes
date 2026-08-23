import { ref } from 'vue'

// 全局右侧大面板状态（工具/技能/MCP 管理 + 产物工作台）。模块级单例，Sidebar 触发、App 渲染。
const open = ref(false)
const tab = ref('skills') // 'skills' | 'tools' | 'software' | 'experts' | 'mcp' | 'memory' | 'knowledge' | 'artifacts'
// 产物工作台子 tab：产物 / 文件 / 变更 / 预览
const artifactTab = ref('artifacts') // 'artifacts' | 'files' | 'changes' | 'preview'
// 首次收到产物事件时自动展开面板
const autoOpenOnArtifact = ref(true)
// 面板宽度（可拖拽 resize）
const panelWidth = ref(420)

export function useRightPanel() {
  function openPanel(t = 'skills') {
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
