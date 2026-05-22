/**
 * QClaw IMA JSAPI SDK v1.0.0
 *
 * 运行环境：IMA iframe 内部
 * 通信方式：通过 window.parent.postMessage 向宿主（QClaw）发送请求，
 *           宿主处理后通过 postMessage 回传结果。
 *
 * IMA 前端引入本 SDK 后，通过 window.QClawBridge 调用各 JSAPI 方法。
 *
 * 使用方式：
 *   <script src="ima-jsapi-sdk.js"></script>
 *   window.QClawBridge.getToken().then(res => { console.log(res.token); });
 */
;(function (global) {
  'use strict';

  // ==================== 配置 ====================
  var SDK_VERSION = '1.0.0';
  var MESSAGE_TYPE = 'qclaw-ima-jsapi';
  var RESPONSE_TYPE = 'qclaw-ima-jsapi-response';
  var DEFAULT_TIMEOUT = 30000; // 30s 超时

  // ==================== 内部状态 ====================
  var callbackMap = {}; // callbackId → { resolve, reject, timer }
  var callbackIdCounter = 0;

  // ==================== 工具函数 ====================

  function generateCallbackId() {
    return 'cb_' + (++callbackIdCounter) + '_' + Date.now();
  }

  /**
   * 向宿主发送请求并等待响应
   * @param {string} method - JSAPI 方法名
   * @param {object} params - 请求参数
   * @param {number} [timeout] - 超时时间（ms）
   * @returns {Promise<object>} 宿主响应数据
   */
  function invoke(method, params, timeout) {
    timeout = timeout || DEFAULT_TIMEOUT;
    params = params || {};

    return new Promise(function (resolve, reject) {
      var callbackId = generateCallbackId();

      // 超时处理
      var timer = setTimeout(function () {
        if (callbackMap[callbackId]) {
          delete callbackMap[callbackId];
          reject({ code: -1, msg: 'timeout: ' + method + ' (' + timeout + 'ms)' });
        }
      }, timeout);

      // 注册回调
      callbackMap[callbackId] = {
        resolve: resolve,
        reject: reject,
        timer: timer,
      };

      // 发送消息给宿主
      var message = {
        type: MESSAGE_TYPE,
        callbackId: callbackId,
        method: method,
        params: params,
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

  // ==================== 监听宿主回传消息 ====================

  function handleMessage(event) {
    var data = event.data;
    if (!data || data.type !== RESPONSE_TYPE) return;

    var callbackId = data.callbackId;
    if (!callbackId || !callbackMap[callbackId]) return;

    var cb = callbackMap[callbackId];
    clearTimeout(cb.timer);
    delete callbackMap[callbackId];

    // 响应给调用方
    if (data.error) {
      cb.reject(data.error);
    } else {
      cb.resolve(data.result || {});
    }
  }

  window.addEventListener('message', handleMessage, false);

  // ==================== 公开 API ====================

  var QClawBridge = {
    /** SDK 版本 */
    version: SDK_VERSION,

    /**
     * 获取设备信息
     * @returns {Promise<{code: number, deviceInfo: {q36: string, qua: string}}>}
     */
    getDeviceInfo: function () {
      return invoke('getDeviceInfo');
    },

    /**
     * 获取 IMA access_token
     * @returns {Promise<{code: number, token: string}>}
     */
    getToken: function () {
      return invoke('getToken');
    },

    /**
     * 刷新 IMA access_token
     * @returns {Promise<{code: number}>}
     */
    refreshToken: function () {
      return invoke('refreshToken');
    },

    /**
     * 添加知识到对话附件
     * @param {{medias: Array<{id: string, mediaType: number, title: string}>}} params
     * @returns {Promise<{code: number, msg?: string}>}
     */
    addKnowledgeTask: function (params) {
      return invoke('addKnowledgeTask', params);
    },

    /**
     * 下载文件
     * @param {{url: string}} params
     * @returns {Promise<{code: number, msg?: string}>}
     */
    download: function (params) {
      return invoke('download', params);
    },

    /**
     * 打开外部浏览器
     * @param {{url: string}} params
     * @returns {Promise<{code: number, msg?: string}>}
     */
    openBrowser: function (params) {
      return invoke('openBrowser', params);
    },

    /**
     * 打开文件查看器
     * @param {{id: string, mediaType: number}} params
     * @returns {Promise<{code: number, msg?: string}>}
     */
    openMedia: function (params) {
      return invoke('openMedia', params);
    },

    /**
     * 打开 IMA App（schema）或官网（url）
     * @param {{schema: string, url: string}} params
     * @returns {Promise<{code: number, msg?: string}>}
     */
    openApp: function (params) {
      return invoke('openApp', params);
    },

    /**
     * 检测 IMA 桌面应用是否已安装
     * @returns {Promise<{code: number, installed: boolean}>}
     */
    checkAppInstalled: function () {
      return invoke('checkAppInstalled');
    },

    /**
     * 获取鉴权信息（QClaw 专用文档中的 getAccountInfo）
     * @returns {Promise<{code: number, accountInfo: object}>}
     */
    getAccountInfo: function () {
      return invoke('getAccountInfo');
    },

    /**
     * 加密请求 body
     * @param {{data: string}} params - 需要加密的明文字符串
     * @returns {Promise<{code: number, data: {data: string, x_ima_cm: number, x_ima_ckey?: string, x_ima_ctk?: string}}>}
     */
    encryptData: function (params) {
      return invoke('encryptData', params);
    },

    /**
     * 解密响应 body
     * @param {{data: string}} params - 密文字符串
     * @returns {Promise<{code: number, msg: string, data: string}>}
     */
    decryptData: function (params) {
      return invoke('decryptData', params);
    },

    /**
     * 保存加密 Token
     * @param {{token: string, expire: number}} params
     * @returns {Promise<{code: number, msg: string}>}
     */
    setCryptoToken: function (params) {
      return invoke('setCryptoToken', params);
    },

    /**
     * 清除加密会话
     * @returns {Promise<{code: number, msg: string}>}
     */
    clearCryptoSession: function () {
      return invoke('clearCryptoSession');
    },

    /**
     * 通知授权码（notifyAuthCode）
     * @param {{authCode: string}} params
     * @returns {Promise<{code: number}>}
     */
    notifyAuthCode: function (params) {
      return invoke('notifyAuthCode', params);
    },

    /**
     * 获取支持的文件格式（预览/问答能力）
     * @returns {Promise<{code: number, formats: Record<number, {supportChat: boolean, supportPreview: boolean}>}>}
     */
    getSupportFileFormats: function () {
      return invoke('getSupportFileFormats');
    },

    /**
     * 底层调用方法（供扩展使用）
     * @param {string} method
     * @param {object} params
     * @param {number} [timeout]
     * @returns {Promise<object>}
     */
    invoke: invoke,
  };

  // ==================== 挂载到全局 ====================
  global.QClawBridge = QClawBridge;

  // 兼容性：如果 IMA 用 QClawJSBridge 这个名字
  global.QClawJSBridge = QClawBridge;

})(typeof window !== 'undefined' ? window : this);
