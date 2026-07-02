import { defineStore } from 'pinia'

export const useScholarPanelStore = defineStore('scholarPanel', {
  state: () => ({
    activeRightPanel: null,   // null | 'citation' | 'consensus' | 'plag' | 'score' | 'snapshots'
    showLiteraturePanel: true,
    showAIPanel: false,
    showEventLog: false,
    rightCollapsed: false,
  }),
  actions: {
    togglePanel(name) {
      if (this.activeRightPanel === name) {
        this.activeRightPanel = null
      } else {
        this.activeRightPanel = name
      }
    },
    closePanel() {
      this.activeRightPanel = null
    },
    toggleRightBar() {
      this.rightCollapsed = !this.rightCollapsed
    },
  },
})
