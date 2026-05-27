# Vermes v1.1.4 原生窗口改造报告

> **时间**: 2026-05-28 05:00 CST
> **执行方**: Hermes (MiMo V2.5 Pro)
> **协同方**: QClaw
> **目标**: 全面进入桌面级原生窗口，不再弹浏览器

---

## 一、已完成的改造

### 1. pywebview 原生 OAuth 窗口
- **文件**: `hermes_cli/gui_app.py`
- **改动**: VermesAPI 新增 `open_oauth_window(url)` / `close_oauth_window()`
- **实现**: `webview.create_window('微信登录', url, 420x620)` 打开原生窗口
- **效果**: 微信登录不再弹浏览器，全程在 pywebview 原生窗口内完成

### 2. 前端环境检测
- **文件**: `frontend/src/components/ChatView.vue`
- **改动**: `const isPywebview = !!window.pywebview`
- **逻辑**:
  - pywebview 环境 → `window.pywebview.api.open_oauth_window()` 原生窗口
  - 浏览器环境 → `window.open()` 弹窗（兼容开发模式）
- **涉及函数**: `openWeChatQR()`, `openWeChatPopup()`, `onWeChatLogin()`

### 3. 移除 webbrowser.open() 回退
- **文件**: `hermes_cli/gui_app.py`
- **改动**: pywebview 失败不再弹浏览器，改为提示"请检查 pywebview 是否正确安装"
- **效果**: 桌面应用不再意外打开浏览器

### 4. 二次启动静默退出
- **文件**: `hermes_cli/gui_app.py`
- **改动**: 已有实例运行时，打印提示后 `sys.exit(0)`
- **旧行为**: `webbrowser.open()` 打开浏览器
- **新行为**: 静默退出，提示"请切换到已打开的窗口"

### 5. OAuth 窗口关闭用 pywebview API
- **文件**: `frontend/src/components/ChatView.vue`
- **改动**: `onWeChatLogin()` 中检测 pywebview 环境，调用 `close_oauth_window()`
- **旧**: `window.open('', 'wechat-login')?.close()`
- **新**: `window.pywebview.api.close_oauth_window()`

---

## 二、全链路流程（原生窗口）

```
用户点击「微信登录」
  ↓
openWeChatQR() → fetch /api/wechat/qrurl → 获取 state + OAuth URL
  ↓
检测 window.pywebview?
  ├─ 是 → pywebview.api.open_oauth_window(url) → 原生窗口打开
  └─ 否 → window.open(url, 'wechat-login', 420x620) → 浏览器弹窗
  ↓
startPolling() → 每 2 秒轮询 /api/wechat/poll?state=xxx
  ↓
用户在微信中确认授权
  ↓
vbit.top/callback → 设置 session.scanned=true, session.token=xxx
  ↓
下一次轮询拿到 {scanned: true, token: xxx}
  ↓
onWeChatLogin(data)
  ├─ 关闭 OAuth 窗口 (pywebview API / window.open)
  ├─ 保存 token 到 localStorage
  ├─ 同步到后端 .env (VBIT_API_KEY)
  ├─ 更新 UI (isLoggedIn, userName, userAvatar)
  └─ 停止轮询
  ↓
登录成功 ✅
```

---

## 三、gui_app.py 改动详情

### VermesAPI 类
```python
class VermesAPI:
    _oauth_window = None  # OAuth 弹窗引用

    def open_external_browser(self, url):
        """系统浏览器打开（保留，其他功能用）"""

    def open_oauth_window(self, url):
        """原生窗口打开微信 OAuth"""
        import webview
        VermesAPI._oauth_window = webview.create_window(
            '微信登录', url,
            width=420, height=620, resizable=False
        )
        VermesAPI._oauth_window.events.closed += on_closed

    def close_oauth_window(self):
        """关闭 OAuth 原生窗口"""
        if VermesAPI._oauth_window:
            VermesAPI._oauth_window.destroy()
            VermesAPI._oauth_window = None
```

### 二次启动
```python
if lock_fd is None:
    print("[Vermes] 已有实例在运行，请切换到已打开的窗口。")
    sys.exit(0)  # 静默退出，不再弹浏览器
```

### pywebview 失败处理
```python
except Exception as e:
    print(f"[Vermes] ❌ 原生窗口失败: {e}")
    print("[Vermes] 请检查 pywebview 是否正确安装: pip install pywebview")
    # 不再回退到 webbrowser.open()
```

---

## 四、前端改动详情

### ChatView.vue 环境检测
```javascript
const isPywebview = typeof window !== 'undefined' && !!window.pywebview
```

### openWeChatQR() 分支
```javascript
if (isPywebview) {
  window.pywebview.api.open_oauth_window(data.url)
} else {
  const popup = window.open(data.url, 'wechat-login', `width=${w},height=${h},...`)
  if (!popup || popup.closed) showWeChatModal.value = true
}
```

### onWeChatLogin() 关闭窗口
```javascript
if (isPywebview) {
  window.pywebview.api.close_oauth_window()
} else {
  try { window.open('', 'wechat-login')?.close() } catch(e) {}
}
```

---

## 五、不动的原因（QClaw 分析确认）

| 问题 | 为什么不动 |
|------|-----------|
| CORS 头冲突 | 桌面走 127.0.0.1，无跨域 |
| X-Frame-Options 重复 | 同上 |
| HSTS | 桌面不走 HTTPS |
| 服务器前端部署 | 打包到 DMG 里了 |
| 在线模式分支 | isOnline 永远 false |
| /api/env 可写 | 本机正常功能 |
| checkQuota 逻辑 | 不走在线分支 |

---

## 六、待验证项

- [ ] pywebview `create_window()` 是否支持同时打开两个窗口
- [ ] OAuth 窗口关闭后 `_oauth_window` 引用是否正确清理
- [ ] Windows 平台 pywebview 行为是否一致
- [ ] 开发环境（浏览器）回退路径是否正常

---

## 七、Git 提交记录

```
64673bd feat: pywebview 原生窗口微信登录，彻底告别浏览器弹窗
7b3f0f3 fix: QClaw安全审计修复 + Hermes协同验证
68131e8 docs: 添加桌面端分析报告 + 全链路测试报告
```

---

*报告生成时间: 2026-05-28 05:00 CST*
