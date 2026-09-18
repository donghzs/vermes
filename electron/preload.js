// preload.js — 安全的 Electron ↔ Renderer 桥接
const { contextBridge, ipcRenderer } = require('electron');

let appVersion = '0.0.0';

// 从 electron/package.json 读取版本（asar 内始终可用）
try {
  appVersion = require('./package.json').version;
} catch (_) {}

contextBridge.exposeInMainWorld('vermes', {
  platform: process.platform,
  isDesktop: true,
  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
  restartGateway: () => ipcRenderer.invoke('gateway:restart'),
  wechatLogin: (state) => ipcRenderer.invoke('wechat-login', state),
  openExternalBrowser: (url) => ipcRenderer.invoke('shell:openExternal', url),
  openTerminal: (command) => ipcRenderer.invoke('shell:openTerminal', command),
  showItemInFolder: (fullPath) => ipcRenderer.invoke('shell:showItemInFolder', fullPath),
  saveAs: (srcPath, defaultName) => ipcRenderer.invoke('shell:saveAs', srcPath, defaultName),
  setTrayUnread: (n) => ipcRenderer.send('tray:unread', Number(n) || 0),
  version: appVersion,

  // ── 应用更新（electron-updater，双平台统一）──
  // 主进程 setupAutoUpdater 广播事件，这里桥接给前端 update.js 的 Electron 分支。
  // 名称须与 frontend/src/stores/update.js 的 Electron 分支完全一致：
  // checkForUpdates / downloadUpdate / installUpdate /
  // onUpdateAvailable / onUpdateNotAvailable / onUpdateProgress /
  // onUpdateDownloaded / onUpdateError。
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),
  onUpdateAvailable: (cb) => {
    const handler = (_e, info) => cb(info)
    ipcRenderer.on('update:available', handler)
    return () => ipcRenderer.removeListener('update:available', handler)
  },
  onUpdateNotAvailable: (cb) => {
    const handler = (_e, info) => cb(info)
    ipcRenderer.on('update:not-available', handler)
    return () => ipcRenderer.removeListener('update:not-available', handler)
  },
  onUpdateProgress: (cb) => {
    const handler = (_e, p) => cb(p)
    ipcRenderer.on('update:progress', handler)
    return () => ipcRenderer.removeListener('update:progress', handler)
  },
  onUpdateDownloaded: (cb) => {
    const handler = (_e, info) => cb(info)
    ipcRenderer.on('update:downloaded', handler)
    return () => ipcRenderer.removeListener('update:downloaded', handler)
  },
  onUpdateError: (cb) => {
    const handler = (_e, err) => cb(err)
    ipcRenderer.on('update:error', handler)
    return () => ipcRenderer.removeListener('update:error', handler)
  },

  // Agent 框架更新 (IPC)
  checkAgentUpdate: () => ipcRenderer.invoke('agent:check'),
  downloadAgentUpdate: (opts) => ipcRenderer.invoke('agent:download', opts),

  // Agent 更新事件监听
  onAgentUpdateProgress: (cb) => {
    const handler = (_e, progress) => cb(progress);
    ipcRenderer.on('agent:update-progress', handler);
    return () => ipcRenderer.removeListener('agent:update-progress', handler);
  },
  onAgentUpdateComplete: (cb) => {
    const handler = (_e, info) => cb(info);
    ipcRenderer.on('agent:update-complete', handler);
    return () => ipcRenderer.removeListener('agent:update-complete', handler);
  },
  onAgentUpdateError: (cb) => {
    const handler = (_e, err) => cb(err);
    ipcRenderer.on('agent:update-error', handler);
    return () => ipcRenderer.removeListener('agent:update-error', handler);
  },

  // ── 启动欢迎页（Splash）IPC ──
  // splash.html 通过此通道接收初始化进度
  onSplashMessage: (cb) => {
    const handler = (_e, msg) => cb(msg);
    ipcRenderer.on('splash:message', handler);
    return () => ipcRenderer.removeListener('splash:message', handler);
  },

  // ── 后端连接状态（A.4.1 看门狗广播 → 渲染进程全局 store）──
  onBackendStatus: (cb) => {
    const handler = (_e, status) => cb(status);
    ipcRenderer.on('backend-status', handler);
    return () => ipcRenderer.removeListener('backend-status', handler);
  },
  // ── 窗口可见性（主进程显式下发）──
  // 为什么不能只用 Page Visibility API：主窗口为让长任务在「关窗挂托盘」后继续跑，
  // 设了 backgroundThrottling: false。Electron 文档明确：一旦禁用后台节流，
  // document.visibilityState 会**永久保持 visible**（最小化/遮挡/隐藏都不再翻转）。
  // 因此隐藏与否只能由主进程按 win 的 show/hide/minimize/restore 显式广播。
  // 用途：暂停纯 UI 轮询（会话列表/状态徽标/看板），隐藏时刷了也没人看；
  // 流式输出与长任务轮询不受影响，继续跑。
  onWindowVisibility: (cb) => {
    const handler = (_e, hidden) => cb(hidden);
    ipcRenderer.on('window:visibility', handler);
    return () => ipcRenderer.removeListener('window:visibility', handler);
  },

  // splash.html 触发重试
  retryInit: () => ipcRenderer.send('splash:retry'),
  // splash.html 「复制诊断信息」按钮（G4 数据保护错误页三件套之一）
  copyDiagnostic: (diagnostic) => ipcRenderer.invoke('copyDiagnostic', diagnostic),
});
