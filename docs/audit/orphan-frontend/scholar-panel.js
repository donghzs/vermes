import { defineStore } from 'pinia'

export const useScholarPanelStore = defineStore('scholarPanel', {
  state: () => ({
    // null | 'citation' | 'consensus' | 'plag' | 'score' | 'snapshots'
    activeRightPanel: null,
    showLiteraturePanel: false,
    showAIPanel: false,
    showEventLog: false,
    rightCollapsed: true,  // 默认折叠，按需打开
    showMoreTools: false,  // 顶部"更多工具"下拉
    // 浮出面板是否打开（覆盖右侧固定面板逻辑）
    floatingPanelOpen: false,
  }),
  actions: {
    togglePanel(name) {
      if (this.activeRightPanel === name) {
        this.activeRightPanel = null
        this.floatingPanelOpen = false
      } else {
        // 关闭其他面板
        this.showLiteraturePanel = false
        this.showAIPanel = false
        this.activeRightPanel = name
        this.floatingPanelOpen = true
        this.rightCollapsed = false
      }
    },
    openLiterature() {
      this.showLiteraturePanel = true
      this.showAIPanel = false
      this.activeRightPanel = null
      this.floatingPanelOpen = true
      this.rightCollapsed = false
    },
    openAI() {
      this.showAIPanel = true
      this.showLiteraturePanel = false
      this.activeRightPanel = null
      this.floatingPanelOpen = true
      this.rightCollapsed = false
    },
    closeFloatingPanel() {
      this.floatingPanelOpen = false
      this.activeRightPanel = null
      this.showLiteraturePanel = false
      this.showAIPanel = false
      this.rightCollapsed = true
    },
    closePanel() {
      this.activeRightPanel = null
    },
    toggleRightBar() {
      this.rightCollapsed = !this.rightCollapsed
    },
  },
})
