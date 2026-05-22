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

export default router
