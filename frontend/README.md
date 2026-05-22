# Vermes 前端（Vue 3 + Vite）

功能完整的 Vermes AI Agent 前端，对标 QClaw/OpenClaw 界面。

## 功能特性

- ✅ 三栏布局（侧边栏 | 聊天主界面 | 响应式）
- ✅ 会话管理（新建/切换会话）
- ✅ 模型选择（6个预置模型）
- ✅ Markdown 渲染（代码高亮）
- ✅ SSE 流式输出（打字效果）
- ✅ 深色/浅色主题切换
- ✅ 响应式设计（移动端适配）

## 快速开始

### 1. 安装依赖

```bash
cd ~/Projects/vermes/frontend
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

前端会自动代理 `/api` 请求到 `http://127.0.0.1:9120`（Vermes 后端）。

### 3. 构建生产版本

```bash
npm run build
```

构建产物在 `dist/` 目录，可以复制到 `~/Projects/vermes/hermes_cli/web_dist/` 替换旧前端。

## 项目结构

```
frontend/
├── index.html              # 入口 HTML
├── package.json            # 依赖配置
├── vite.config.js          # Vite 配置（代理设置）
├── tailwind.config.js      # Tailwind CSS 配置
├── postcss.config.js       # PostCSS 配置
└── src/
    ├── main.js            # Vue 应用入口
    ├── App.vue            # 根组件（布局）
    ├── style.css          # 全局样式（Tailwind + Markdown）
    ├── stores/
    │   └── chat.js       # Pinia 状态管理
    ├── components/
    │   ├── Sidebar.vue   # 侧边栏（会话列表）
    │   └── ChatView.vue  # 主聊天界面
    └── services/
        └── api.js        # API 服务层（SSE 流式）
```

## 技术栈

- **Vue 3**（Composition API + `<script setup>`）
- **Vite**（极速 HMR）
- **Pinia**（状态管理）
- **Tailwind CSS**（实用优先的 CSS）
- **Markdown-it**（Markdown 渲染）
- **Highlight.js**（代码高亮）

## 下一步

1. **安装依赖**：`npm install`
2. **启动后端**：确保 Vermes 后端在 `9120` 端口运行
3. **启动前端**：`npm run dev`
4. **浏览器访问**：`http://localhost:5173/`

## 常见问题

**Q: 前端连不上后端？**
A: 确保 Vermes 后端在跑：`ps aux | grep python | grep dashboard`

**Q: Markdown 不渲染？**
A: 检查 `markdown-it` 是否正确安装：`npm list markdown-it`

**Q: 如何部署到生产环境？**
A: 运行 `npm run build`，然后将 `dist/` 目录内容复制到 `~/Projects/vermes/hermes_cli/web_dist/`
