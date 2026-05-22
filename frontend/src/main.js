import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './style.css'
import router from './router'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 全局错误捕获：把错误直接显示到页面上
app.config.errorHandler = (err, instance, info) => {
  console.error('Vue错误:', err, info)
  const el = document.getElementById('app')
  if (el) {
    el.innerHTML = '<div style="padding:32px;font-family:monospace;color:#ef4444;background:#fef2f2;min-height:100vh;white-space:pre-wrap;">'
      + '<h2 style="color:#dc2626;margin-bottom:12px;">🚨 Vue 渲染错误</h2>'
      + '<div style="margin-bottom:8px;"><strong>信息:</strong> ' + escapeHtml(info) + '</div>'
      + '<div style="margin-bottom:8px;"><strong>错误:</strong> ' + escapeHtml(String(err)) + '</div>'
      + '<details style="margin-top:16px;"><summary style="cursor:pointer;color:#7c3aed;">错误堆栈</summary>'
      + '<pre style="margin-top:8px;font-size:12px;overflow:auto;background:#1e1e2e;color:#cdd6f4;padding:12px;border-radius:8px;">' + escapeHtml(err?.stack || '无堆栈') + '</pre></details>'
      + '</div>'
  }
}

window.onerror = (msg, src, line, col, err) => {
  console.error('全局错误:', msg, err)
  const el = document.getElementById('app')
  if (el && !el.innerHTML.includes('错误')) {
    el.innerHTML = '<div style="padding:32px;font-family:monospace;color:#ef4444;background:#fef2f2;min-height:100vh;white-space:pre-wrap;">'
      + '<h2 style="color:#dc2626;margin-bottom:12px;">🚨 全局JS错误</h2>'
      + '<div><strong>消息:</strong> ' + escapeHtml(String(msg)) + '</div>'
      + '<div><strong>位置:</strong> ' + escapeHtml(String(src) + ':' + line + ':' + col) + '</div>'
      + '</div>'
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')
}

app.mount('#app')
