import { ref } from 'vue'

// 产物右侧面板 — 浏览器标签模型
// activeTabId: 'tasks' | 'artifacts' | 'workspace' | 'changes' | 'file:<id>'
const open = ref(false)
const width = ref(420)
// 产物交付自动弹出右栏。
// P0-1(2026-09-22): 只在后端 onDelivery 事件到达时触发 —— 该事件的 artifacts
// 已由后端过滤为「最终交付物」。tool_step 的过程产物（execute_code 沙箱内的
// write_file/patch，见 tools/code_execution_tool.py:1415）永不自动弹。
// 用户偏好持久化：置 false 则完全不自动弹，仍可用「详情面板」按钮手动打开。
const AUTO_OPEN_KEY = 'vermes-artifact-auto-open'
function _readAutoOpen() {
  try { return localStorage.getItem(AUTO_OPEN_KEY) !== 'false' } catch { return true }
}
const autoOpen = ref(_readAutoOpen())

// P2-3（2026-09-22）打扰预算：单轮会话最多自动弹 N 次，超出降级为静默通知。
// 防止一轮里多次 onDelivery（多步交付/并行 agent）连续把面板弹到前台。
// 预算在每轮 sendMessage 起点由 resetAutoOpenBudget() 归零。
const AUTO_OPEN_BUDGET = 1
const autoOpenUsed = ref(0)
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

  // 用户可彻底关闭「交付物自动弹出」（设置项入口）。关闭后仍可手动开面板。
  function setAutoOpen(v) {
    autoOpen.value = !!v
    try { localStorage.setItem(AUTO_OPEN_KEY, autoOpen.value ? 'true' : 'false') } catch { /* 隐私模式下忽略 */ }
  }

  // 申请一次自动弹出配额：返回 true 才允许弹，用完即静默（P2-3）
  function consumeAutoOpen() {
    if (!autoOpen.value) return false
    if (autoOpenUsed.value >= AUTO_OPEN_BUDGET) return false
    autoOpenUsed.value += 1
    return true
  }
  function resetAutoOpenBudget() { autoOpenUsed.value = 0 }

  return {
    open, width, autoOpen, activeTabId, fileTabs, autoOpenUsed,
    openPanel, closePanel, togglePanel,
    setView, setTab, openFileTab, closeFileTab, openArtifactFile, setWidth, setAutoOpen,
    consumeAutoOpen, resetAutoOpenBudget,
  }
}
