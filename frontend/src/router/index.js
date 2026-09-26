import { createRouter, createWebHistory } from 'vue-router'
// A3 主包瘦身（2026-09-14）：除落地页 ChatView 外，其余页面组件一律改为路由级
// 动态 import，让每个页面各自成 chunk，不再全部塞进初始主包。
// 背景：原 11 个路由组件全部静态导入，而 vite.config.js 的 manualChunks 只切了
// 第三方库（vue/three/markdown/highlight/katex/codemirror），**从未切过应用自身代码**
// → 应用代码全堆在主包，实测 index chunk 1,714.36 kB，首屏要把它下载并解析完才出界面。
// 说明：
//  · ChatView 是 '/' 落地页，保持静态导入——进 App 立刻要用，切成异步反而多一次往返。
//  · SkillManager 原本在此静态导入，但**从未被任何 route 引用**（死引用），已移除；
//    它真正的引用方是全局抽屉 ToolSkillDrawer.vue，功能不受影响。
//  · Shenmotang 自身仅 100 行，但它静态 import 了 BotRooms/AgentsPage/KanbanBoard，
//    懒加载后这 ~3,447 行随神魔堂一起移出主包（进到该页才下载）。
import ChatView from '../components/ChatView.vue'

const routes = [
  { path: '/', component: ChatView },
  { path: '/settings', component: () => import('../components/Settings.vue') },
  // MCP 指挥中心已并入 Agent 管理（全宽页面）
  { path: '/mcp', redirect: '/agents' },
  { path: '/studio', component: () => import('../components/StudioChat.vue') },
  { path: '/scholarforge', component: () => import('../components/ScholarForgePanel.vue') },
  { path: '/3d-studio', component: () => import('../components/ThreeDStudio.vue') },
  // P1-3：四态合一积木市场（skill/tool/module/software 统一入口）
  { path: '/bricks', component: () => import('../components/BricksPage.vue') },
  // P4-4 T2: benchmark 可视化大盘
  { path: '/benchmark', component: () => import('../components/BenchmarkDashboard.vue') },
  // T0 用量/成本大盘（2026-09-26，洁室自研；后端 /api/analytics/usage 已有）
  { path: '/usage', component: () => import('../components/UsageDashboard.vue') },
  // 旧路由保留重定向，避免外链 404。
  // 说明：ModuStore/SkillMarketPage 是纯路由页，已退役；ToolSkillDrawer / SoftwareDiscover
  // 是内嵌于 App.vue 的全局抽屉（非路由），不在本次重定向范围，仍独立可用。
  { path: '/module-store', redirect: '/bricks' },
  { path: '/skill-market', redirect: '/bricks' },
  // 蜂群协作看板已收编进神魔堂（🐝 蜂群看板 tab）——保留重定向避免旧外链/书签 404
  { path: '/kanban', redirect: '/shenmotang' },
  // A2 工作流编排：可视化 DAG 编辑器 + 触发器配置
  { path: '/workflows', component: () => import('../components/WorkflowsPage.vue') },
  // G13：成长页 —— 成长/能力自检/我懂你 全宽呈现（原侧栏底部 EvolutionPanel + modal 提级）
  { path: '/growth', component: () => import('../components/GrowthPage.vue') },
  // ⛩️ 神魔堂：融合入口（诸神会晤 + 神魔架）
  { path: '/shenmotang', component: () => import('../components/Shenmotang.vue') },
  // Agent 管理：全宽页面（已装技能/工具/MCP/记忆/知识库/自造神/封神榜）
  { path: '/agents', component: () => import('../components/AgentManagement.vue') },
  // 旧入口重定向到神魔堂（避免旧外链/书签 404）
  { path: '/bot-rooms', redirect: '/shenmotang' },
  // 神魔架（请神/造神）已并入 Agent 管理 /agents
  { path: '/roster', redirect: '/agents' },
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
