// preload.js — 安全的 Electron ↔ Renderer 桥接
const { contextBridge, ipcRenderer } = require('electron');
const path = require('path');
const fs = require('fs');

let appVersion = '0.0.0';

// 优先从 electron/package.json 读取版本（统一的版本来源）
try {
  appVersion = require('./package.json').version;
} catch (_) {
  // 打包模式 fallback：从 resources 目录的 version.txt 读取
  try {
    const versionPath = path.join(process.resourcesPath, 'version.txt');
    if (fs.existsSync(versionPath)) {
      appVersion = fs.readFileSync(versionPath, 'utf-8').trim();
    }
  } catch (__) {}
}

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
});
