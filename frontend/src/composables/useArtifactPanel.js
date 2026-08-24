import { ref } from 'vue'

// 产物右侧面板独立状态（不与 Agent 管理抽屉 useRightPanel 共享）
// WorkBuddy 风格：hover 提示「展开右栏」，极简无常驻文字。
const open = ref(false)
const tab = ref('artifacts') // 'artifacts' | 'changes'
const width = ref(420)
const autoOpen = ref(false)   // 收到新产物时是否自动展开（默认关，避免小文件/中间产物频繁打扰）

export function useArtifactPanel() {
  function openPanel(t = 'artifacts') {
    tab.value = t
    open.value = true
  }
  function closePanel() { open.value = false }
  function togglePanel(t = 'artifacts') {
    if (open.value && tab.value === t) {
      open.value = false
    } else {
      tab.value = t
      open.value = true
    }
  }
  function setTab(t) { tab.value = t }
  function setWidth(w) { width.value = w }
  return { open, tab, width, autoOpen, openPanel, closePanel, togglePanel, setTab, setWidth }
}
