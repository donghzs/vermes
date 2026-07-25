// ScholarForge 面板共享状态（Pinia）。
//
// 决策 #5（用户 2026-07-25 评审）：SchemaForm 的 project_id 应从「当前选中项目」自动填充，
// 不让用户手填。故这里集中维护 currentProjectId，供 SchemaForm 读取。
//
// P0c-1 仅做「选择已有项目」（ProjectSpace 的 CRUD 留 P0c-2）；未选项目时工具不带项目上下文。
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

  return { currentProjectId, projects, projectsLoaded, loadProjects }
})
