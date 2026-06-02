// preload.js — 安全的 Electron ↔ Renderer 桥接
const { contextBridge, ipcRenderer } = require('electron');
const path = require('path');

let appVersion = '2.0.6';
try {
  // 开发模式：直接 require package.json
  appVersion = require(path.join(__dirname, '..', 'package.json')).version;
} catch (_) {
  // 打包模式：从 app.asar 内读取失败，用默认值
}

contextBridge.exposeInMainWorld('vermes', {
  // 平台信息
  platform: process.platform,
  isDesktop: true,
  
  // 后端状态查询
  getBackendStatus: () => ipcRenderer.invoke('backend:status'),
  
  // 微信 OAuth 登录
  wechatLogin: () => ipcRenderer.invoke('wechat-login'),
  
  // 版本信息
  version: appVersion,
});
