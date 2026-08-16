import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../components/ChatView.vue'
import Settings from '../components/Settings.vue'
import StudioChat from '../components/StudioChat.vue'
import ScholarForgePanel from '../components/ScholarForgePanel.vue'
import ThreeDStudio from '../components/ThreeDStudio.vue'
import ModuStore from '../components/ModuStore.vue'

const routes = [
  { path: '/', component: ChatView },
  { path: '/settings', component: Settings },
  { path: '/studio', component: StudioChat },
  { path: '/scholarforge', component: ScholarForgePanel },
  { path: '/3d-studio', component: ThreeDStudio },
  { path: '/module-store', component: ModuStore },
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
