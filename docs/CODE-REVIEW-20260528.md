# 代码审查报告：pywebview 原生窗口微信登录

**审查人**: QClaw  
**被审查人**: Hermes  
**日期**: 2026-05-28 06:29-06:49  
**Commit**: `488962e` → `cb35e4e`（含 3 个修复）  
**结论**: 方案正确，发现 3 个 bug（含 1 个致命），已全部修复

---

## 一、Hermes 提交内容

### 方案：B+C 组合（pywebview 第二原生窗口 + evaluate_js URL 监控）

**gui_app.py 新增 `open_oauth_window()`：**
- `webview.create_window()` 创建独立 OAuth 窗口（420×620）
- `on_loaded` 事件：页面加载完成后检查 URL 是否含 `code=` + `vbit.top`
- `poll_url` 线程：每 1.5 秒轮询 `evaluate_js('window.location.href')` 作为备用
- `result_ready.wait(timeout=300)` 同步阻塞最多 5 分钟
- 返回 `{"success": True, "code": code}` 给 JS 调用方

**ChatView.vue 改动：**
- 检测 `window.pywebview` 环境，分两条路径
- pywebview: 调用 `open_oauth_window(wechatOAuthUrl)` → 原生窗口
- 浏览器: `window.open()` 弹窗 + 轮询（开发模式兜底）
- 移除 WxLogin.js SDK、`loadWxLoginScript()`、`_wxLoginHandler` postMessage 监听
- 移除 `wechatQrReady` ref

### ✅ 正确的部分

1. **双重监控设计**：`on_loaded` 事件 + `poll_url` 轮询线程，互为冗余
2. **条件过滤**：`'code=' in current_url and 'vbit.top' in current_url` 双重条件，不会误触发
3. **线程安全**：`result_ready`（threading.Event）正确协调两个检测通道
4. **前端分支清晰**：`isPywebview` 检测 + 浏览器 fallback
5. **SDK 清理干净**：无残留引用

---

## 二、发现的 3 个 Bug

### Bug 1（中等）：用户关闭窗口要等 5 分钟超时

**现象**：用户手动关闭 OAuth 窗口后，前端要等 5 分钟才收到 "timeout or cancelled" 错误。

**根因**：`poll_url` 线程因 `evaluate_js` 异常退出 `break`，但 `result_ready` 没有被 `set()`。主线程的 `result_ready.wait(300)` 只能等超时。

**修复**：添加 `win.events.closed` 事件处理器：
```python
def on_closed():
    if not result_ready.is_set():
        VermesAPI._oauth_result = {"success": False, "error": "cancelled"}
        result_ready.set()

win.events.closed += on_closed
```

**教训**：pywebview 的 `events.closed` 事件是处理窗口关闭的标准方式。任何长时间阻塞等待的场景，都要考虑"用户主动取消"的退出路径。

---

### Bug 2（中等）：state 返回值被 `or True` 吞掉

**现象**：`open_oauth_window` 返回的 `result.state` 永远是 `True`（布尔值），不是真实的 OAuth state 参数。

**根因**：
```python
# 问题代码
VermesAPI._oauth_result = {"success": True, "code": code, "state": result_ready.set() or True}
```

`result_ready.set()` 返回 `None`（Python threading.Event.set() 的返回值），所以 `None or True` = `True`。

**修复**：拆成两行：
```python
VermesAPI._oauth_result = {"success": True, "code": code, "state": state}
result_ready.set()
```

**教训**：
- 不要在赋值表达式里嵌入有副作用的函数调用（`result_ready.set()`）。可读性差且容易踩坑。
- Python 的 `threading.Event.set()` 返回 `None`，不是 `True`。不要依赖它的返回值。
- `x or y` 在 `x` 为 falsy 时返回 `y`。`None or True` = `True`，这不是你想要的。

---

### Bug 3（致命）：调用不存在的 API 端点

**现象**：pywebview 登录流程在获取到 code 后，调用 `POST /api/wechat/exchange-code`，返回 404。

**根因分析**：

微信 OAuth 完整流程：
```
1. 用户扫码 → 微信重定向到 callback?code=xxx&state=yyy
2. 服务端 callback 处理器：
   - 用 code 换 access_token（服务端→微信API）
   - 用 access_token 获取用户信息（昵称、头像）
   - 创建/查找 One-API token
   - 存入 wechatSessions[state]
   - 返回 HTML 页面
3. 前端需要拿到 token/userName/userAvatar
```

关键事实：**callback 处理器已经完成了 code→token 的全部工作**，结果存在 `wechatSessions[state]` 里。

Hermes 的 `exchangeWechatCode(code)` 试图用 code 再次换取 token，但：
1. `/api/wechat/exchange-code` 端点不存在（404）
2. 即使存在，WeChat 的 code 是一次性的，callback 已经用过了，再次换会失败
3. 前端拿到的应该是 `state`（用于从 `wechatSessions` 查结果），不是 `code`

**正确流程**：
```
pywebview 拿到 code+state
  → callback 处理器已经处理了 code 并存入 wechatSessions[state]
  → 前端用 state 轮询 /api/wechat/poll?state=xxx
  → poll 返回 {scanned: true, token, userName, userAvatar}
  → onWeChatLogin(data)
```

**修复**：
1. `open_oauth_window` 返回真实 `state` 值
2. ChatView 新增 `pollForResult()` 函数，用 state 轮询 `/api/wechat/poll`
3. 删除无用的 `exchangeWechatCode` 函数

**教训**：
- **理解完整的请求生命周期**：不要只看"前端拿到 code"就认为需要"前端用 code 换 token"。要追踪 code 在服务端经历了什么。
- **区分"前端持有的凭证"和"服务端持有的凭证"**：code 是给服务端用的，state 是给前端查结果用的。
- **新端点需要先检查是否存在**：在调用任何 API 之前，先确认后端有对应的路由。不要假设"应该有这个端点"。
- **复用已有的轮询机制**：浏览器模式已经用 `startPolling()` + `/api/wechat/poll` 拿结果，pywebview 模式应该复用同样的机制。

---

## 三、pywebview OAuth 最佳实践总结

### 3.1 窗口生命周期管理

```python
# ✅ 正确：三个事件都要处理
win.events.loaded += on_loaded    # 页面加载完成
win.events.closed += on_closed    # 用户关闭窗口
poller = threading.Thread(target=poll_url, daemon=True)  # 备用轮询

# ❌ 错误：只处理 loaded，不处理 closed
win.events.loaded += on_loaded
# 用户关闭窗口 → 5 分钟超时
```

### 3.2 线程同步

```python
# ✅ 正确：Event + wait
result_ready = threading.Event()
# ... 在回调里 ...
VermesAPI._oauth_result = {"success": True, "code": code, "state": state}
result_ready.set()  # 分开写，不要嵌套

# ❌ 错误：嵌套赋值
VermesAPI._oauth_result = {"state": result_ready.set() or True}  # state 永远是 True
```

### 3.3 OAuth 流程分工

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   前端 JS    │     │  服务端 Express │     │  微信 API   │
└──────┬──────┘     └──────┬───────┘     └──────┬──────┘
       │                   │                     │
       │  open_oauth_window(oauthUrl)            │
       │──────────────────►│                     │
       │                   │  用户扫码确认        │
       │                   │◄────────────────────│
       │                   │                     │
       │                   │  code → access_token │
       │                   │────────────────────►│
       │                   │  ◄── token + user    │
       │                   │                     │
       │                   │  存入 wechatSessions[state]
       │                   │                     │
       │  on_loaded 检测到 code+state             │
       │◄──────────────────│                     │
       │                   │                     │
       │  poll ?state=xxx  │                     │
       │──────────────────►│                     │
       │  ◄── {token, user}│                     │
       │                   │                     │
       │  onWeChatLogin()  │                     │
       │                   │                     │

前端职责：获取 state → 轮询结果
服务端职责：获取 code → 换 token → 存储结果
```

### 3.4 端点调用原则

| 操作 | 调用方 | 端点 | 存在？ |
|------|--------|------|--------|
| code 换 token | 服务端 callback | 微信 API | ✅ |
| 存储登录结果 | 服务端 callback | wechatSessions | ✅ |
| 查询登录结果 | 前端 poll | `/api/wechat/poll` | ✅ |
| 前端再次换 token | ~~前端~~ | ~~`/api/wechat/exchange-code`~~ | ❌ 不存在 |

---

## 四、给 Hermes 的建议

1. **追踪完整的数据流**：不要只关注"前端拿到了什么"，要追踪"这个数据在每一层经历了什么"。
2. **复用已有机制**：浏览器模式已经有 `startPolling()` + `/api/wechat/poll`，pywebview 模式应该复用。
3. **不要假设端点存在**：新增 API 调用前，先 `grep` 后端代码确认路由存在。
4. **事件驱动 > 轮询**：`on_loaded` 是主通道，`poll_url` 是备用。但"用户关闭窗口"这种事件也要处理。
5. **避免在赋值里嵌入副作用函数**：`result_ready.set() or True` 这种写法可读性差且容易出错。

---

## 五、最终 Commit 链

| Commit | 作者 | 内容 |
|--------|------|------|
| `488962e` | Hermes | feat: pywebview 原生窗口微信登录，移除 WxLogin.js SDK |
| `4f52142` | QClaw | fix: OAuth 窗口关闭事件处理，避免 5 分钟超时等待 |
| `a0ef690` | QClaw | fix: pywebview OAuth 流程修复 — 用 state poll 替代不存在的 exchange-code 端点 |
| `cb35e4e` | QClaw | build: 前端同步 (OAuth 流程修复) |
