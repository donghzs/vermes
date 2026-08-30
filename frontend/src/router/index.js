import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../components/ChatView.vue'
import Settings from '../components/Settings.vue'
import StudioChat from '../components/StudioChat.vue'
import ScholarForgePanel from '../components/ScholarForgePanel.vue'
import ThreeDStudio from '../components/ThreeDStudio.vue'
import SkillManager from '../components/SkillManager.vue'
import BricksPage from '../components/BricksPage.vue'
import BenchmarkDashboard from '../components/BenchmarkDashboard.vue'
import KanbanBoard from '../components/KanbanBoard.vue'
import WorkflowsPage from '../components/WorkflowsPage.vue'

const routes = [
  { path: '/', component: ChatView },
  { path: '/settings', component: Settings },
  { path: '/studio', component: StudioChat },
  { path: '/scholarforge', component: ScholarForgePanel },
  { path: '/3d-studio', component: ThreeDStudio },
  // P1-3：四态合一积木市场（skill/tool/module/software 统一入口）
  { path: '/bricks', component: BricksPage },
  // P4-4 T2: benchmark 可视化大盘
  { path: '/benchmark', component: BenchmarkDashboard },
  // 旧路由保留重定向，避免外链 404。
  // 说明：ModuStore/SkillMarketPage 是纯路由页，已退役；ToolSkillDrawer / SoftwareDiscover
  // 是内嵌于 App.vue 的全局抽屉（非路由），不在本次重定向范围，仍独立可用。
  { path: '/module-store', redirect: '/bricks' },
  { path: '/skill-market', redirect: '/bricks' },
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
