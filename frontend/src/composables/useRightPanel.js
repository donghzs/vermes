import { ref } from 'vue'

// 全局右侧大面板状态（工具/技能/MCP 管理 + 产物面板）。模块级单例，Sidebar 触发、App 渲染。
const open = ref(false)
const tab = ref('skills') // 'skills' | 'tools' | 'software' | 'experts' | 'mcp' | 'memory' | 'knowledge' | 'artifacts'

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
  return { open, tab, openPanel, closePanel, setTab }
}
