// ScholarForge 面板共享状态（Pinia）。
//
// 决策 #5（用户 2026-07-25 评审）：SchemaForm 的 project_id 应从「当前选中项目」自动填充，
// 不让用户手填。故这里集中维护 currentProjectId，供 SchemaForm 读取。
//
// P0c-2：补齐项目 CRUD（create/remove/update），供 ProjectSpace 使用。
// 走裸 fetch 与 invokeTool 同理——避开 services/api.js 在线模式 /v1 前缀。
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useScholarStore = defineStore('scholar', () => {
  const currentProjectId = ref(null)
  const projects = ref([])
  const projectsLoaded = ref(false)

  // 跨子组件协调：FlowGuide / Uploader 触发工具箱动作时统一经此。
  const activeTab = ref('tools') // 'tools' | 'projects' | 'quality' | 'guide'
  const pendingTool = ref(null) // FlowGuide 点「执行」→ ToolBox 选中该工具
  const pendingPrefill = ref(null) // Uploader 导入 → { tool, field, value }

  // FlowGuide：跳到工具箱并选中指定工具
  function runToolInBox(toolName) {
    pendingTool.value = toolName
    activeTab.value = 'tools'
  }

  // Uploader：跳到工具箱、选中工具并预填某字段（如上传 PDF 的文献文本喂给 review.draft）
  function prefillTool(toolName, field, value) {
    pendingPrefill.value = { tool: toolName, field, value }
    pendingTool.value = toolName
    activeTab.value = 'tools'
  }

  function clearPending() {
    pendingTool.value = null
    pendingPrefill.value = null
  }

  async function loadProjects() {
    try {
      const resp = await fetch('/api/scholar/projects')
      if (!resp.ok) return
      const data = await resp.json()
      projects.value = Array.isArray(data) ? data : (data.projects || [])
    } catch (e) {
      projects.value = []
    } finally {
      projectsLoaded.value = true
    }
  }

  async function createProject({ title, paper_type, target_words }) {
    const resp = await fetch('/api/scholar/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, paper_type, target_words }),
    })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || `创建失败（HTTP ${resp.status}）`)
    }
    const proj = await resp.json()
    await loadProjects()
    if (proj && proj.id != null) {
      currentProjectId.value = proj.id
      setActiveProject(proj.id)
    }
    return proj
  }

  async function removeProject(pid) {
    const resp = await fetch(`/api/scholar/projects/${pid}`, { method: 'DELETE' })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || `删除失败（HTTP ${resp.status}）`)
    }
    if (currentProjectId.value === pid) currentProjectId.value = null
    await loadProjects()
  }

  async function updateProject(pid, patch) {
    const resp = await fetch(`/api/scholar/projects/${pid}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}))
      throw new Error(data.detail || `更新失败（HTTP ${resp.status}）`)
    }
    const proj = await resp.json()
    await loadProjects()
    return proj
  }

  // 把当前选中项目种入后端「激活项目」，使 agent 对话路径零样本写回也能落到正确项目。
  // 桌面本地运行，失败静默（不影响面板本身）。
  async function setActiveProject(pid) {
    const id = Number(pid)
    if (!Number.isInteger(id) || id <= 0) return
    try {
      await fetch('/api/scholar/active-project', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: id }),
      })
    } catch (e) {
      /* 忽略：本地后端不可达时不阻断面板 */
    }
  }

  // 选中项目 = 更新当前 id + 种入后端激活项目
  function selectProject(pid) {
    currentProjectId.value = pid
    setActiveProject(pid)
  }

  return {
    currentProjectId,
    projects,
    projectsLoaded,
    activeTab,
    pendingTool,
    pendingPrefill,
    runToolInBox,
    prefillTool,
    clearPending,
    loadProjects,
    createProject,
    removeProject,
    updateProject,
    setActiveProject,
    selectProject,
  }
})
