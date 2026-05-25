import { createRouter, createWebHistory } from 'vue-router'
import ChatView from '../components/ChatView.vue'
import Settings from '../components/Settings.vue'

const routes = [
  { path: '/', component: ChatView },
  { path: '/settings', component: Settings },
]

const router = createRouter({
  history: createWebHistory('/vermes/'),
  routes,
})

// 在线模式拦截设置页面（防止用户操控服务器）
router.beforeEach((to) => {
  if (to.path === '/settings' && typeof window !== 'undefined' && window.__VERMES_ONLINE__) {
    return '/'
  }
})

export default router
