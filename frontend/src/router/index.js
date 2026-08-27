import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../components/ChatView.vue'
import Settings from '../components/Settings.vue'
import StudioChat from '../components/StudioChat.vue'
import ScholarForgePanel from '../components/ScholarForgePanel.vue'
import ThreeDStudio from '../components/ThreeDStudio.vue'
import ModuStore from '../components/ModuStore.vue'
import SkillManager from '../components/SkillManager.vue'
import SkillMarketPage from '../components/SkillMarketPage.vue'
import KanbanBoard from '../components/KanbanBoard.vue'
import WorkflowsPage from '../components/WorkflowsPage.vue'

const routes = [
  { path: '/', component: ChatView },
  { path: '/settings', component: Settings },
  { path: '/studio', component: StudioChat },
  { path: '/scholarforge', component: ScholarForgePanel },
  { path: '/3d-studio', component: ThreeDStudio },
  { path: '/module-store', component: ModuStore },
  // G2 技能市场发现页：复用 SkillManager 的完整发现 tab（搜索/来源筛选/一键装），
  // 默认打开 market 而非 installed
  // 独立全屏版（高宽度利用率 + 中文映射 + 网格卡片），替代 SkillManager 紧凑布局
  { path: '/skill-market', component: SkillMarketPage },
  // 蜂群协作看板：Vermes 任务图可视化（多 Agent 并行执行）
  { path: '/kanban', component: KanbanBoard },
  // A2 工作流编排：可视化 DAG 编辑器 + 触发器配置
  { path: '/workflows', component: WorkflowsPage },
]

// Electron 桌面端加载在 /，Web 端加载在 /vermes/
const base = (typeof window !== 'undefined' && window.__VERMES_ONLINE__) ? '/vermes/' : '/'

const router = createRouter({
  history: createWebHistory(base),
  routes,
})

// 在线模式拦截设置页面（防止用户操控服务器）
router.beforeEach((to) => {
  if (to.path === '/settings' && typeof window !== 'undefined' && window.__VERMES_ONLINE__) {
    return '/'
  }
})

export default router
