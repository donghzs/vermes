# Vermes 构建指南 (Electron)

Vermes 采用 **Electron 壳 + Python 后端** 架构，前端为 Vue 3 SPA。

```
┌──────────────────────────────────────────┐
│              Electron 壳                  │
│  electron/main.js + preload.js           │
│         Chromium → 127.0.0.1:9119        │
├──────────────────────────────────────────┤
│        FastAPI 后端 (uvicorn)             │
│  vermes_cli/web_server.py                │
├──────────────────────────────────────────┤
│        Vue 3 前端 (SPA)                   │
│  frontend/ → vermes_cli/web_dist/        │
└──────────────────────────────────────────┘
```

## 1. 前置条件

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Node.js | ≥ 20 | Electron + 前端构建 |
| Python | ≥ 3.11 | 后端运行 |
| .venv | — | 项目根目录的 Python 虚拟环境 |
| npm | — | 随 Node.js 安装 |

## 2. 快速开发

```bash
# 1. 安装依赖
cd electron && npm install        # Electron 依赖
cd ../frontend && npm install     # 前端依赖

# 2. 启动后端（终端 1）
cd .. && .venv/bin/python backend_main.py --port 9119

# 3. 启动 Electron（终端 2）
cd electron && npm run dev
```

Electron 的 `main.js` 会自动检测后端 `/health` 就绪后显示窗口。

## 3. 构建生产包

### 3.1 完整构建流程

```bash
# macOS
cd electron && npm run dist:mac

# Windows
cd electron && npm run dist:win
```

构建脚本自动执行：
1. **`prebuild`** — 从 `vermes_cli/__init__.py` 同步版本号到 `electron/version.txt` 和 `electron/package.json`
2. **PyInstaller** — 用 `vermes-backend.spec` 打包 Python 后端为独立可执行文件 → `dist/vermes-backend/`
3. **electron-builder** — 将 Electron 壳 + 后端 + 前端打包为 `.dmg` (macOS) 或 `.exe` 安装包 (Windows)

### 3.2 仅构建目录（调试用）

```bash
cd electron && npm run pack    # 输出到 dist-electron/mac/ 或 dist-electron/win-unpacked/
```

### 3.3 仅构建前端

```bash
cd frontend && npx vite build      # 输出到 frontend/dist/
cp -r frontend/dist vermes_cli/web_dist  # 同步给 Electron
```

## 4. 版本号管理

版本号的唯一来源是 `vermes_cli/__init__.py` 中的 `__version__` 字段。

```bash
# 同步版本号到所有位置
bash scripts/sync-version.sh
```

同步目标：
- `electron/version.txt` — Electron 运行时读取
- `electron/package.json` — npm 包版本
- `frontend/package.json` — 前端包版本
- `frontend/vite.config.js` — 编译时从 `__init__.py` 自动读取

构建前 `prebuild` 脚本会自动调用 `sync-version.sh`。

## 5. 项目结构

```
vermes-electron/
├── electron/                    # Electron 壳
│   ├── main.js                  # 主进程（窗口管理、后端生命周期）
│   ├── preload.js               # 预加载脚本（安全 IPC 桥接）
│   ├── package.json             # Electron 依赖 + electron-builder 配置
│   ├── version.txt              # 版本号（由 sync-version.sh 生成）
│   └── assets/                  # 图标
├── frontend/                    # Vue 3 前端
│   ├── src/
│   │   ├── components/          # Vue 组件
│   │   ├── stores/              # Pinia 状态管理
│   │   ├── services/            # API 服务
│   │   └── config/defaults.js   # 默认配置
│   ├── vite.config.js           # Vite 配置
│   └── package.json
├── vermes_cli/                  # Python 后端
│   ├── web_server.py            # FastAPI 应用
│   ├── web_dist/                # 前端构建产物（由 frontend/dist 复制）
│   └── __init__.py              # 版本号来源
├── backend_main.py              # Electron 后端专用入口
├── vermes-backend.spec          # PyInstaller spec（Electron 用，不含 GUI 依赖）
├── scripts/
│   └── sync-version.sh          # 版本号同步脚本
└── .gitignore
```

## 6. 关键文件说明

### backend_main.py
Electron 专用的后端入口，只启动 FastAPI + uvicorn，接受 `--port` 参数。与旧版 `main.py` 不同，不依赖 pywebview/bottle。

### vermes-backend.spec
PyInstaller 打包配置，关键区别：
- **不打包** pywebview、pyobjc、bottle 等 GUI 依赖
- 输出到 `dist/vermes-backend/vermes-backend`
- Electron 的 `extraResources` 引用该路径

### electron/preload.js
通过 `contextBridge` 暴露安全的 API 给渲染进程：
- `vermes.platform` — 操作系统
- `vermes.version` — 应用版本
- `vermes.wechatLogin(state)` — 微信 OAuth 登录
- `vermes.openExternalBrowser(url)` — 外部链接

## 7. 常见问题

### 构建后打开空白页
确认 `vermes_cli/web_dist/` 存在且包含 `index.html`。前端构建后必须复制到此目录。

### electron-builder 报 Python 路径错误
确认 `.venv` 在项目根目录且 `pyinstaller` 已安装：
```bash
.venv/bin/pip install pyinstaller
```

### macOS 签名/公证
在 `electron/package.json` 的 `build.mac` 中添加：
```json
"mac": {
  "identity": "Developer ID Application: Your Name (TEAMID)",
  "notarize": {
    "teamId": "TEAMID"
  }
}
```

### 调试 DevTools
`electron/main.js` 中 DevTools 已注释（生产关闭）。开发调试时取消注释 `openDevTools()` 即可。
