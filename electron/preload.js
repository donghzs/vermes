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
  wechatLogin: (state) => ipcRenderer.invoke('wechat-login', state),
  openExternalBrowser: (url) => ipcRenderer.invoke('shell:openExternal', url),
  version: appVersion,

  // 自动更新 (electron-updater)
  checkForUpdates: () => ipcRenderer.invoke('update:check'),
  downloadUpdate: () => ipcRenderer.invoke('update:download'),
  installUpdate: () => ipcRenderer.invoke('update:install'),

  // 更新事件监听
  onUpdateAvailable: (cb) => {
    const handler = (_e, info) => cb(info);
    ipcRenderer.on('update:available', handler);
    return () => ipcRenderer.removeListener('update:available', handler);
  },
  onUpdateNotAvailable: (cb) => {
    const handler = () => cb();
    ipcRenderer.on('update:not-available', handler);
    return () => ipcRenderer.removeListener('update:not-available', handler);
  },
  onUpdateProgress: (cb) => {
    const handler = (_e, progress) => cb(progress);
    ipcRenderer.on('update:download-progress', handler);
    return () => ipcRenderer.removeListener('update:download-progress', handler);
  },
  onUpdateDownloaded: (cb) => {
    const handler = (_e, info) => cb(info);
    ipcRenderer.on('update:downloaded', handler);
    return () => ipcRenderer.removeListener('update:downloaded', handler);
  },
  onUpdateError: (cb) => {
    const handler = (_e, err) => cb(err);
    ipcRenderer.on('update:error', handler);
    return () => ipcRenderer.removeListener('update:error', handler);
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
  // splash.html 触发重试
  retryInit: () => ipcRenderer.send('splash:retry'),
  // splash.html 「复制诊断信息」按钮（G4 数据保护错误页三件套之一）
  copyDiagnostic: (diagnostic) => ipcRenderer.invoke('copyDiagnostic', diagnostic),
});
