# @tencent/qclaw-ima-jsapi-sdk

QClaw IMA JSAPI SDK，运行在 IMA iframe 内部，通过 `postMessage` 与 QClaw 宿主端通信。

## 安装

```bash
npm install @tencent/qclaw-ima-jsapi-sdk --registry=https://mirrors.tencent.com/npm/
```

## 使用

### Script 标签引入

```html
<script src="node_modules/@tencent/qclaw-ima-jsapi-sdk/ima-jsapi-sdk.js"></script>
<script>
  // 全局变量 window.QClawBridge 自动可用
  window.QClawBridge.getToken().then(res => {
    console.log(res.token);
  });
</script>
```

### 直接 CDN / 本地引入

```html
<script src="./ima-jsapi-sdk.js"></script>
```

## API

| 方法 | 参数 | 返回值 | 说明 |
|------|------|--------|------|
| `getDeviceInfo()` | 无 | `{code, deviceInfo: {q36, qua}}` | 获取设备信息 |
| `getToken()` | 无 | `{code, token}` | 获取 IMA access_token |
| `refreshToken()` | 无 | `{code}` | 刷新 token |
| `getAccountInfo()` | 无 | `{code, accountInfo}` | 获取鉴权信息 |
| `addKnowledgeTask(params)` | `{medias: [{id, mediaType, title}]}` | `{code, msg}` | 添加知识到对话附件 |
| `download(params)` | `{url}` | `{code, msg}` | 下载文件 |
| `openBrowser(params)` | `{url}` | `{code, msg}` | 打开外部浏览器 |
| `openMedia(params)` | `{id, mediaType}` | `{code, msg}` | 打开文件查看器 |
| `openApp(params)` | `{schema, url}` | `{code}` | 打开 IMA App |
| `encryptData(params)` | `{data}` | `{code, data: {data, x_ima_cm, ...}}` | 加密请求 body |
| `decryptData(params)` | `{data}` | `{code, msg, data}` | 解密响应 body |
| `setCryptoToken(params)` | `{token, expire}` | `{code, msg}` | 保存加密 Token |
| `clearCryptoSession()` | 无 | `{code, msg}` | 清除加密会话 |
| `notifyAuthCode(params)` | `{authCode}` | `{code}` | 通知授权码 |

## 通信协议

- iframe → 宿主: `{ type: 'qclaw-ima-jsapi', callbackId, method, params }`
- 宿主 → iframe: `{ type: 'qclaw-ima-jsapi-response', callbackId, result/error }`

## 版本

- v1.0.0 - 初始版本
