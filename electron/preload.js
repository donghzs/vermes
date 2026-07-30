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
  version: appVersion,

  // 壳更新（shell update）：
  // electron-updater 已于 4c2cbcbc6 从 main.js 摘除（打包缺依赖导致启动崩溃），
  // 主进程不存在 update:check/download/install 任何 handler。
  // 此处曾暴露的 6 个孤儿 API 使 update.js:74 的存在性判断
  // `isDesktop && window.vermes?.checkForUpdates` 永真 → 桌面端卡死在
  // 注定失败的 electron 分支、永不降级 web 轮询（更新断路 P1）。
  // 删除后判断自然转假，桌面端零改动滑入 web 分支：
  // 检查=vbit.top version.json 轮询，下载/安装=后端 /api/update/* SSE。
  // 若未来重接 autoUpdater，须先在 electron/package.json 真正声明依赖。

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
  // splash.html 触发重试
  retryInit: () => ipcRenderer.send('splash:retry'),
  // splash.html 「复制诊断信息」按钮（G4 数据保护错误页三件套之一）
  copyDiagnostic: (diagnostic) => ipcRenderer.invoke('copyDiagnostic', diagnostic),
});
