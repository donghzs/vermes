import { ref } from 'vue'

// 产物右侧面板 — 浏览器标签模型
// activeTabId: 'tasks' | 'artifacts' | 'workspace' | 'changes' | 'file:<id>'
const open = ref(false)
const width = ref(420)
const autoOpen = ref(true)  // 产物交付自动弹出右栏
const activeTabId = ref('tasks')
const fileTabs = ref([]) // { id: 'file:<id>', kind, title, path, icon }

export function useArtifactPanel() {
  function openPanel(view) {
    open.value = true
    if (view && !view.startsWith('file:')) activeTabId.value = view
  }
  function closePanel() { open.value = false }
  function togglePanel(view) {
    open.value = !open.value
    if (open.value && view && !view.startsWith('file:')) activeTabId.value = view
  }

  function setView(v) {
    activeTabId.value = v
    open.value = true
  }

  // 兼容旧调用：setTab('artifacts') / setTab('preview') → 切功能视图
  function setTab(v) {
    if (v === 'preview') v = 'artifacts'
    if (v === 'changes' || v === 'artifacts' || v === 'tasks' || v === 'workspace') {
      activeTabId.value = v
      open.value = true
    }
  }

  function openFileTab(kind, id, title, path, icon) {
    const tabId = 'file:' + id
    if (!fileTabs.value.find(t => t.id === tabId)) {
      fileTabs.value.push({ id: tabId, kind, title, path, icon })
    }
    activeTabId.value = tabId
    open.value = true
  }

  // 直接打开某个产物的文件标签并渲染其内容（而非只切到产物列表）
  function openArtifactFile(id, artifactsRef) {
    const a = artifactsRef && artifactsRef.value ? artifactsRef.value.find(x => x.id === id) : null
    if (a) {
      openFileTab('artifact', a.id, a.title || a.path?.split('/').pop() || '未知文件', a.path, a.icon)
    } else {
      openFileTab('artifact', id, id, id, null)
    }
  }

  function closeFileTab(tabId) {
    const idx = fileTabs.value.findIndex(t => t.id === tabId)
    if (idx < 0) return
    fileTabs.value.splice(idx, 1)
    if (activeTabId.value === tabId) {
      activeTabId.value = fileTabs.value.length ? fileTabs.value[0].id : 'tasks'
    }
  }

  function setWidth(w) { width.value = w }

  return {
    open, width, autoOpen, activeTabId, fileTabs,
    openPanel, closePanel, togglePanel,
    setView, setTab, openFileTab, closeFileTab, openArtifactFile, setWidth,
  }
}
