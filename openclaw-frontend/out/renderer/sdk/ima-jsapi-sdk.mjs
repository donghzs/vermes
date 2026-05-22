/**
 * QClaw IMA JSAPI SDK v1.0.1 — ES Module 入口
 *
 * 使用方式:
 *   import { getToken, getDeviceInfo, openMedia, ... } from '@tencent/qclaw-ima-jsapi-sdk'
 *   const res = await getToken();
 */

const SDK_VERSION = '1.0.1';
const MESSAGE_TYPE = 'qclaw-ima-jsapi';
const RESPONSE_TYPE = 'qclaw-ima-jsapi-response';
const DEFAULT_TIMEOUT = 30000;

const callbackMap = {};
let callbackIdCounter = 0;

function generateCallbackId() {
  return 'cb_' + (++callbackIdCounter) + '_' + Date.now();
}

/**
 * 向宿主发送请求并等待响应
 */
function invoke(method, params, timeout) {
  timeout = timeout || DEFAULT_TIMEOUT;
  params = params || {};

  return new Promise((resolve, reject) => {
    const callbackId = generateCallbackId();

    const timer = setTimeout(() => {
      if (callbackMap[callbackId]) {
        delete callbackMap[callbackId];
        reject({ code: -1, msg: 'timeout: ' + method + ' (' + timeout + 'ms)' });
      }
    }, timeout);

    callbackMap[callbackId] = { resolve, reject, timer };

    const message = {
      type: MESSAGE_TYPE,
      callbackId,
      method,
      params,
      sdkVersion: SDK_VERSION,
    };

    try {
      window.parent.postMessage(message, '*');
    } catch (e) {
      clearTimeout(timer);
      delete callbackMap[callbackId];
      reject({ code: -2, msg: 'postMessage failed: ' + e.message });
    }
  });
}

// 监听宿主回传
function handleMessage(event) {
  const data = event.data;
  if (!data || data.type !== RESPONSE_TYPE) return;
  const callbackId = data.callbackId;
  if (!callbackId || !callbackMap[callbackId]) return;
  const cb = callbackMap[callbackId];
  clearTimeout(cb.timer);
  delete callbackMap[callbackId];
  if (data.error) {
    cb.reject(data.error);
  } else {
    cb.resolve(data.result || {});
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('message', handleMessage, false);
}

// ==================== 导出 API ====================

export const version = SDK_VERSION;

export function getDeviceInfo() {
  return invoke('getDeviceInfo');
}

export function getToken() {
  return invoke('getToken');
}

export function refreshToken() {
  return invoke('refreshToken');
}

export function getAccountInfo() {
  return invoke('getAccountInfo');
}

export function addKnowledgeTask(params) {
  return invoke('addKnowledgeTask', params);
}

export function download(params) {
  return invoke('download', params);
}

export function openBrowser(params) {
  return invoke('openBrowser', params);
}

export function openMedia(params) {
  return invoke('openMedia', params);
}

export function openApp(params) {
  return invoke('openApp', params);
}

export function checkAppInstalled() {
  return invoke('checkAppInstalled');
}

export function encryptData(params) {
  return invoke('encryptData', params);
}

export function decryptData(params) {
  return invoke('decryptData', params);
}

export function setCryptoToken(params) {
  return invoke('setCryptoToken', params);
}

export function clearCryptoSession() {
  return invoke('clearCryptoSession');
}

export function notifyAuthCode(params) {
  return invoke('notifyAuthCode', params);
}

export function getSupportFileFormats() {
  return invoke('getSupportFileFormats');
}

// 底层方法导出
export { invoke };

// 同时挂载到 window（兼容 IIFE 使用方式）
if (typeof window !== 'undefined') {
  window.QClawBridge = {
    version: SDK_VERSION,
    getDeviceInfo,
    getToken,
    refreshToken,
    getAccountInfo,
    addKnowledgeTask,
    download,
    openBrowser,
    openMedia,
    openApp,
    encryptData,
    decryptData,
    setCryptoToken,
    clearCryptoSession,
    notifyAuthCode,
    getSupportFileFormats,
    invoke,
  };
  window.QClawJSBridge = window.QClawBridge;
}
