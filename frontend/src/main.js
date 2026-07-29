import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './style.css'
import router from './router'
import { fetchProviderConfig, default as api } from './services/api'

const app = createApp(App)
app.use(createPinia())
app.use(router)

// 桌面模式：从后端注入的全局变量读取 session token
// 后端在 HTML 中注入 window.__VERMES_SESSION_TOKEN__，进程重启后 token 变化
// 在线模式不需要 session token（用 One-API Bearer token）
if (typeof window !== 'undefined' && window.__VERMES_SESSION_TOKEN__) {
  api.setToken(window.__VERMES_SESSION_TOKEN__)
}

// 启动时拉取云端模型/推荐提供商配置（失败自动 fallback）
fetchProviderConfig()

// 全局错误捕获：把错误直接显示到页面上
// 2.1.2 加固：使用 textContent 替代 innerHTML，防止错误信息本身包含恶意脚本
app.config.errorHandler = (err, instance, info) => {
  console.error('Vue错误:', err, info)
  const el = document.getElementById('app')
  if (el) {
    el.innerHTML = ''
    const container = document.createElement('div')
    container.style.cssText = 'padding:32px;font-family:monospace;color:#ef4444;background:#fef2f2;min-height:100vh;white-space:pre-wrap;'
    container.innerHTML = '<h2 style="color:#dc2626;margin-bottom:12px;">🚨 Vue 渲染错误</h2>'
    
    const infoDiv = document.createElement('div')
    infoDiv.style.marginBottom = '8px'
    infoDiv.innerHTML = '<strong>信息:</strong> '
    infoDiv.appendChild(document.createTextNode(info))
    container.appendChild(infoDiv)
    
    const errDiv = document.createElement('div')
    errDiv.style.marginBottom = '8px'
    errDiv.innerHTML = '<strong>错误:</strong> '
    errDiv.appendChild(document.createTextNode(String(err)))
    container.appendChild(errDiv)
    
    const details = document.createElement('details')
    details.style.marginTop = '16px'
    const summary = document.createElement('summary')
    summary.style.cssText = 'cursor:pointer;color:#7c3aed;'
    summary.textContent = '错误堆栈'
    details.appendChild(summary)
    
    const pre = document.createElement('pre')
    pre.style.cssText = 'margin-top:8px;font-size:12px;overflow:auto;background:#1e1e2e;color:#cdd6f4;padding:12px;border-radius:8px;'
    pre.textContent = err?.stack || '无堆栈'
    details.appendChild(pre)
    container.appendChild(details)
    
    el.appendChild(container)
  }
}

window.onerror = (msg, src, line, col, err) => {
  console.error('全局错误:', msg, err)
  const el = document.getElementById('app')
  if (el && !el.innerHTML.includes('错误')) {
    el.innerHTML = ''
    const container = document.createElement('div')
    container.style.cssText = 'padding:32px;font-family:monospace;color:#ef4444;background:#fef2f2;min-height:100vh;white-space:pre-wrap;'
    container.innerHTML = '<h2 style="color:#dc2626;margin-bottom:12px;">🚨 全局JS错误</h2>'
    
    const msgDiv = document.createElement('div')
    msgDiv.innerHTML = '<strong>消息:</strong> '
    msgDiv.appendChild(document.createTextNode(String(msg)))
    container.appendChild(msgDiv)
    
    const locDiv = document.createElement('div')
    locDiv.innerHTML = '<strong>位置:</strong> '
    locDiv.appendChild(document.createTextNode(String(src) + ':' + line + ':' + col))
    container.appendChild(locDiv)
    
    el.appendChild(container)
  }
}

function escapeHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')
}

app.mount('#app')
