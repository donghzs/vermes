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
    if (proj && proj.id != null) currentProjectId.value = proj.id
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

  return {
    currentProjectId,
    projects,
    projectsLoaded,
    loadProjects,
    createProject,
    removeProject,
    updateProject,
  }
})
