# 🖥️ Vermes 桌面原生窗口改造分析

> **日期**：2026-05-28 04:38  
> **目标**：全面进入桌面级应用，浏览器不再弹出，所有功能在原生窗口实现  
> **原则**：不影响正常使用功能和用户体验  

---

## 当前架构

### 入口
- `gui_app.py`：双击 `.app` 启动 → pywebview 原生窗口 + FastAPI 后端(127.0.0.1:9119)
- 失败回退：`webbrowser.open()` 打开系统浏览器（**需移除**）

### 前端
- Vue3 SPA → 通过 `window.__VERMES_ONLINE__` 区分在线/桌面模式
- **桌面模式**（`__VERMES_ONLINE__` 为 false）：聊天走 `/api/` → FastAPI → 本地 Hermes 引擎
- **在线模式**（`__VERMES_ONLINE__` 为 true）：聊天走 `/v1/` → One-API

### 微信登录
- 桌面端：`window.open()` 打开 OAuth 弹窗 → 弹窗跳转回调 → 服务端 poll 轮询
- 在线端：同上（都是浏览器环境）

---

## 🔍 原生窗口下的关键问题

### 🔴 P0：pywebview 中 `window.open()` 行为异常

**现状**：微信登录用 `window.open(url, 'wechat-login', 'width=420,height=620,...')` 打开 OAuth 弹窗

**问题**：
- pywebview 中 `window.open()` 可能被拦截或行为不一致
- OAuth 回调页 `https://vbit.top/api/wechat/callback` 跳转回来后，回调窗口和主窗口之间的通信方式（poll 轮询）依赖**同源**
- pywebview 的窗口和系统浏览器之间**不同源**，poll 轮询在主窗口检测不到回调窗口的登录结果

**修复方案**：微信登录改为**内嵌 iframe** 或**pywebview 新窗口**，不弹系统浏览器

> ⚠️ 微信 OAuth 页面设置了 `X-Frame-Options: DENY`，**不能 iframe 内嵌**！

**正确方案**：用 pywebview 的 `webview.create_window()` 打开第二个原生窗口承载 OAuth 页面，回调后自动关闭

---

### 🟡 P1：gui_app.py 回退到 webbrowser.open

**现状**：第162行、第196行 `webbrowser.open(url)` — pywebview 失败后回退打开系统浏览器

**问题**：用户要求不再弹浏览器

**修复**：
1. pywebview 失败后，不回退浏览器，而是显示错误提示
2. 或者直接不回退，让用户知道窗口启动失败

---

### 🟡 P2：在线模式 vs 桌面模式分支

**现状**：`isOnline` 区分两套逻辑，桌面模式下走 `/api/`（本地 Hermes 引擎），在线模式走 `/v1/`（One-API）

**分析**：原生桌面窗口 = 桌面模式，**isOnline 永远为 false**（因为 hostname 是 127.0.0.1）

**影响**：
- ✅ 聊天走本地引擎，正常
- ✅ 积分系统走本地 FastAPI，正常
- ⚠️ 微信登录后 token 写入 `/api/env`（第232行 `fetch('/api/env', PUT)`）— 这个是桌面版专属，正常
- ⚠️ 在线模式下的 `checkQuota` 直接放行（第89行 `return { allowed: true }`）— 但桌面模式不走这个分支，无影响

**结论**：桌面模式下 `isOnline = false`，不受在线模式分支影响。**不需要改。**

---

### 🟡 P3：微信登录回调页关闭弹窗

**现状**：`onWeChatLogin` 第236行 `window.open('', 'wechat-login')?.close()` 尝试关闭 OAuth 弹窗

**问题**：pywebview 窗口中这个操作可能不生效

**修复**：用 pywebview API 关闭第二个窗口，而不是 JS `window.open/close`

---

## ✅ 不需要改的

| 项目 | 原因 |
|------|------|
| CORS 头冲突 | 桌面模式请求走 127.0.0.1，不涉及 CORS |
| X-Frame-Options 重复 | 同上，桌面模式无跨域 |
| HSTS 头 | 桌面模式不走 HTTPS |
| 前端 dist 部署到服务器 | 桌面版打包到 DMG 里，服务器上的在线版是另一个产品线 |
| `markdown-it` html:true | 单用户桌面应用，无风险 |
| `/api/env` 可写 | 桌面本机，正常功能 |
| 桌面版积分 checkQuota 逻辑 | isOnline=false，走桌面分支，正常 |
| uid() 函数 | 已修，无影响 |
| 事件监听器清理 | 已修，无影响 |

---

## 📋 需要修的

| # | 优先级 | 问题 | 工作量 | 影响范围 |
|---|--------|------|--------|----------|
| 1 | **P0** | 微信登录：`window.open()` 改为 pywebview 新窗口 | 中 | gui_app.py + ChatView.vue |
| 2 | **P1** | gui_app.py 移除 `webbrowser.open()` 回退 | 小 | gui_app.py |
| 3 | **P1** | OAuth 弹窗关闭：JS `window.open/close` 改为 pywebview API | 小 | ChatView.vue + gui_app.py |
| 4 | **P2** | gui_app.py 二次启动回退浏览器改为聚焦已有窗口 | 小 | gui_app.py |

---

## 详细修复方案

### Fix 1（P0）：微信登录 — pywebview 新窗口方案

**思路**：前端调 pywebview API 打开第二个原生窗口，OAuth 回调后服务端 poll 轮询照常工作

**gui_app.py 改动**：
```python
class VermesAPI:
    def open_oauth_window(self, url):
        """用 pywebview 新窗口打开 OAuth 页面"""
        import webview
        oauth_window = webview.create_window(
            title='微信登录',
            url=url,
            width=420,
            height=620,
            resizable=False,
        )
        self._oauth_window = oauth_window
        return {"success": True}
    
    def close_oauth_window(self):
        """关闭 OAuth 窗口"""
        if hasattr(self, '_oauth_window') and self._oauth_window:
            self._oauth_window.destroy()
            self._oauth_window = None
        return {"success": True}
```

**ChatView.vue 改动**：
```javascript
// 检测 pywebview 环境
const isPywebview = typeof window !== 'undefined' && window.pywebview !== undefined

async function openWeChatQR() {
  // ... 获取 url 和 state 的逻辑不变 ...
  
  if (isPywebview) {
    // pywebview 环境：用原生窗口
    await window.pywebview.api.open_oauth_window(wechatOAuthUrl.value)
  } else {
    // 浏览器环境：用 window.open 弹窗
    window.open(wechatOAuthUrl.value, 'wechat-login', ...)
  }
  startPolling()
}

function onWeChatLogin(data) {
  if (isPywebview) {
    window.pywebview.api.close_oauth_window()
  } else {
    try { window.open('', 'wechat-login')?.close() } catch(e) {}
  }
  // ... 其余逻辑不变 ...
}
```

### Fix 2（P1）：移除 webbrowser.open 回退

**gui_app.py main() 函数**：
```python
def main(lock_fd, port):
    url = f"http://127.0.0.1:{port}"
    try:
        import webview
        webview.create_window(...)
        webview.start(gui='edgechromium', private_mode=False)
        return
    except Exception as e:
        # 不再回退浏览器，显示错误
        print(f"[Vermes] ❌ 原生窗口启动失败: {e}")
        print(f"[Vermes] 请手动打开浏览器访问: {url}")
    
    # 保持进程运行
    shutdown_event.wait()
```

### Fix 3（P1）：二次启动聚焦已有窗口

**gui_app.py 已有实例检测**：
```python
if lock_fd is None:
    # 已有实例在运行 → 聚焦窗口而非开浏览器
    print("[Vermes] 已有实例在运行")
    # pywebview 无法跨进程聚焦窗口，但可以打开已有页面
    # 简单方案：不做任何操作，提示用户切换到已有窗口
    sys.exit(0)
```

---

## ⚠️ 风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|----------|
| pywebview 新窗口可能不支持 OAuth redirect | 中 | 回调 URL 是 vbit.top 域名，pywebview 窗口可以正常加载 |
| poll 轮询在主窗口，OAuth 在子窗口，两者独立 | 低 | poll 轮询走的是服务端 API，不依赖窗口间通信 |
| pywebview 不可用时用户无法登录 | 低 | 保留浏览器模式作为 fallback，但默认不弹 |

---

*报告生成时间：2026-05-28 04:38 CST*
