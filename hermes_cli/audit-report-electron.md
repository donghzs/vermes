# Vermes Electron 架构审计报告

**审计日期**: 2026-06-02  
**项目路径**: `/Users/dongzusheng/Projects/vermes-electron/`  
**基线版本**: 2.0.6  

---

## 1. 整体架构

```
┌─────────────────────────────────────────────────┐
│  electron/main.js     ← Electron Shell (新)     │
│  electron/preload.js  ← contextBridge            │
├─────────────────────────────────────────────────┤
│  frontend/ ← Vue 3 + Vite + Pinia + Tailwind    │
│    (共享给所有 GUI 模式)                          │
├─────────────────────────────────────────────────┤
│  hermes_cli/web_server.py ← FastAPI 后端         │
│  hermes_cli/gui_app.py  ← pywebview 原生窗 (旧)  │
├─────────────────────────────────────────────────┤
│  agent/ / tools/ / gateway/  ← 核心 Agent 引擎   │
└─────────────────────────────────────────────────┘
```

### 共存的三条 UI 路径

| 模式 | 入口 | 构建方式 | 状态 |
|------|------|----------|------|
| **Electron** | `electron/main.js` | PyInstaller + electron-builder | ✅ 新方案 |
| **pywebview** | `gui_app.py` → `vermes-gui.spec` | PyInstaller | 🟡 旧方案，仍可用 |
| **TUI/CLI** | `ui-tui/` + `hermes_cli/` | 源码运行 | ✅ |

**关键事实**: Electron 模式下，PyInstaller 构建后端独立二进制，electron-builder 将其作为 `extraResources` 打包进 .app。

---

## 2. 代码规模统计

| 部分 | 文件数 | 代码量 | 备注 |
|------|--------|--------|------|
| `electron/` | 5 | ~560 行 | main.js(497) + preload.js(60) + package.json |
| `frontend/src/` | 30+ | ~6,100 行 | Vue 3 组件、stores、router |
| `hermes_cli/` | 50+ | ~20,000+ 行 | web_server(2818), gui_app(547), main.py 等 |
| `agent/` | 80+ | ~50,000+ 行 | 核心引擎 |
| `tools/` | 90+ | ~60,000+ 行 | 工具集 |
| `tests/` | 400+ | ~150,000+ 行 | 测试套件 |

---

## 3. 构建管线 ✅

### 3.1 构建流程

```
scripts/sync-version.sh      ← 版本同步 (prebuild)
        ↓
pyinstaller vermes-backend.spec  ← Python 后端打包
        ↓
dist/vermes-backend/vermes-backend
        ↓
electron-builder --mac/--win  ← Electron 壳打包
        ↓
dist-electron/Vermes-2.0.6-arm64.dmg
```

### 3.2 关键文件

| 文件 | 说明 |
|------|------|
| `backend_main.py` | Electron 后端专用入口，仅 FastAPI + uvicorn |
| `vermes-backend.spec` | 后端 PyInstaller spec，剥离 pywebview/pyobjc |
| `scripts/sync-version.sh` | 从 `hermes_cli/__init__.py` 同步版本到各文件 |
| `BUILD.md` | 完整构建文档 |

### 3.3 版本号管理

版本号唯一来源：`hermes_cli/__init__.py` → `__version__`

同步目标：
- `electron/version.txt` — Electron 运行时读取
- `electron/package.json` — npm 包版本
- `frontend/package.json` — npm 包版本
- `frontend/vite.config.js` — 编译时自动读取注入 `__APP_VERSION__`

---

## 4. Electron 层代码质量

### 4.1 优点 ✅

- **main.js (497 行)**: 结构清晰，单例锁、后端生命周期管理、OAuth 微信登录窗口
- **preload.js (60 行)**: 最小化 contextBridge，暴露安全 API + 更新事件
- **安全**: `contextIsolation: true`、`nodeIntegration: false`、URL 导航拦截
- **微信 OAuth**: 完整的扫码登录流程，含回调检测、轮询、超时处理
- **自动更新**: 已集成 `electron-updater`，支持检查/下载/安装重启

### 4.2 问题

#### P2 — 硬编码 asset 路径

```js
// electron/main.js:43-44
function getIconPath() {
  const iconFile = process.platform === 'win32' ? 'icon.png' : 'vermes.icns';
  return path.join(__dirname, 'assets', iconFile);
}
```

`icon.png` 用于 BrowserWindow 构造，但 electron-builder 的 build 配置已定义 icon。函数调用冗余。

#### P3 — 包体膨胀

`dist-electron/Vermes-2.0.6.dmg` + `dist-electron/Vermes-2.0.6-arm64.dmg` 占用 ~180MB。PyInstaller + Electron 双打包是主要来源。

---

## 5. 前端（Vue 3）质量

### 5.1 优点 ✅

- 架构清晰：Pinia stores 拆分为 chat / session / storage / quota / update 五个模块
- 消息持久化三级缓存：API → IndexedDB → localStorage
- SSE 流式解析完善：thinking、tool_start/end、ping 心跳、超时检测、断线重连
- 配额系统完整：微信登录、每日免费额度、过期提示
- 默认配置集中管理：`frontend/src/config/defaults.js` 统一维护默认模型/provider
- 更新机制双模：Electron 模式用 `electron-updater`，Web 模式用后端 SSE

### 5.2 问题

#### P2 — pywebview/Electron 存储不互通

pywebview 用 `~/.vermes/webview_data`，Electron 用 `partition: 'persist:vermes'`。用户切换模式丢失 localStorage 数据。

---

## 6. 后端（FastAPI / web_server.py）

### 6.1 优点 ✅

- 完整的 session token 认证机制（`_SESSION_TOKEN` + HMAC 比对）
- DNS rebinding 防护（Host header 校验）
- 请求日志中间件（调试友好，API key 自动打码）
- 全局异常处理器（防止崩溃）
- CORS 只允许 localhost

---

## 7. 跨平台兼容性

### 7.1 Windows ✅

`win_adapter.py` 实现完善：WebView2 检测、DPI 感知、单例聚焦、系统托盘、UTF-8 编码。

### 7.2 macOS ✅

macOS 默认菜单、`.icns` 图标、`.app` bundle 信息正确配置。

### 7.3 Linux

不在目标范围内。Vermes 定位桌面级易用工具，面向小白与非技术人员；Linux 用户以技术人员为主且偏好 CLI/自建。

---

## 8. 安全审计

| 项目 | 状态 | 说明 |
|------|------|------|
| contextIsolation | ✅ | `true` |
| nodeIntegration | ✅ | `false` |
| webSecurity | ⚠️ | OAuth 窗口 `webSecurity: false`（合理但需注意） |
| 外部 URL 拦截 | ✅ | `setWindowOpenHandler` + `will-navigate` 双重防护 |
| 单实例锁 | ✅ | `requestSingleInstanceLock` |
| 前端 XSS | ✅ | `dompurify` 依赖存在 |
| API key 日志打码 | ✅ | web_server 日志自动 redact |
| Host header 校验 | ✅ | DNS rebinding 防护 |
| 后端 session token | ✅ | 每启动随机生成，前端注入 |

---

## 9. 变更摘要（2026-06-02 第二次审计）

### P0（已修复）

| # | 问题 | 修复 |
|:--|:-----|:-----|
| 1 | `vermes-backend.spec` 不存在 | ✅ 创建 spec + `backend_main.py` |
| 2 | DevTools 在 `ready-to-show` 打开 | ✅ 已注释 |

### P1（已修复）

| # | 问题 | 修复 |
|:--|:-----|:-----|
| 3 | 无 Electron 自动更新 | ✅ 集成 `electron-updater`：main.js 配置 + preload IPC + update.js 双模 |
| 4 | BUILD.md 缺失 | ✅ 创建完整构建文档 |

### P2（已修复）

| # | 问题 | 修复 |
|:--|:-----|:-----|
| 5 | 根目录构建产物散乱 | ✅ `.gitignore` 添加 `dist-electron/`、`*.blockmap`、`Vermes-*.zip/dmg` |
| 6 | 版本号来源不统一 | ✅ `scripts/sync-version.sh` + `prebuild` npm script，`hermes_cli/__init__.py` 为唯一来源 |
| 7 | 默认 model 硬编码 | ✅ `frontend/src/config/defaults.js` 集中管理，chat.js + ChatHeader.vue 统一引用 |

### 剩余 P2

| # | 问题 |
|:--|:-----|
| 8 | pywebview/Electron 存储不互通（跨模式迁移需额外设计） |

---

*审计基于代码阅读 + 部分构建验证。前端 Vite 构建通过（148 modules, 443KB JS）。*
