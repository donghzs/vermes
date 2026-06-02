// preload.js — 安全的 Electron ↔ Renderer 桥接
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('vermes', {
  // 平台信息
  platform: process.platform,
  isDesktop: true,
  
  // 后端状态查询
  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
  
  // 微信 OAuth 登录
  wechatLogin: () => ipcRenderer.invoke('wechat-login'),
  
  // 版本信息
  version: require('../package.json').version,
});
