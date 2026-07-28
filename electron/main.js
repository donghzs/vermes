const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

// 强制 Chromium 解析微信桌面本地服务域名 → 127.0.0.1
app.commandLine.appendSwitch('host-resolver-rules', 'MAP localhost.weixin.qq.com 127.0.0.1');

let mainWindow = null;
let backendProcess = null;
const BACKEND_URL = 'http://127.0.0.1:9119';
const BACKEND_PORT = 9119;

// ── 路径解析 ──
function getAppDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, 'app');
  }
  return path.join(__dirname, '..', '..', 'vermes');
}

function getBackendExe() {
  // 打包模式：PyInstaller 构建的独立可执行文件
  if (app.isPackaged) {
    const exeName = process.platform === 'win32' ? 'vermes-backend.exe' : 'vermes-backend';
    return path.join(process.resourcesPath, 'backend', exeName);
  }
  // 开发模式：用 .venv 的 Python
  const pythonDir = getAppDir();
  if (process.platform === 'win32') {
    return path.join(pythonDir, '.venv', 'Scripts', 'python.exe');
  }
  return path.join(pythonDir, '.venv', 'bin', 'python');
}

function getBackendArgs() {
  if (app.isPackaged) {
    // PyInstaller 可执行文件直接运行，不需要参数
    return ['--port', String(BACKEND_PORT)];
  }
  // 开发模式：用 uvicorn
  return ['-m', 'uvicorn', 'hermes_cli.web_server:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT), '--log-level', 'warning'];
}

function getIconPath() {
  const iconFile = process.platform === 'win32' ? 'icon.png' : 'vermes.icns';
  return path.join(__dirname, 'assets', iconFile);
}

// ── 后端管理 ──
function startBackend() {
  return new Promise((resolve, reject) => {
    console.log('[Vermes] 启动后端...');
    const backendExe = getBackendExe();
    const backendArgs = getBackendArgs();

    // 检查可执行文件是否存在
    if (!fs.existsSync(backendExe)) {
      console.warn(`[Vermes] 后端未找到: ${backendExe}，假设已在外部运行`);
      resolve(false);
      return;
    }

    const env = { ...process.env };
    // 打包模式下设置 PYTHONPATH
    if (app.isPackaged) {
      env.PYTHONPATH = path.join(process.resourcesPath, 'app');
      env.HERMES_HOME = path.join(require('os').homedir(), '.vermes');
    }

    backendProcess = spawn(backendExe, backendArgs, {
      cwd: app.isPackaged ? path.join(process.resourcesPath, 'backend') : getAppDir(),
      env: env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,  // Windows: 隐藏控制台窗口
    });

    backendProcess.stdout.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log(`[Backend] ${msg}`);
    });

    backendProcess.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[Backend ERR] ${msg}`);
    });

    backendProcess.on('error', (err) => {
      console.error(`[Vermes] 后端启动失败: ${err.message}`);
      resolve(false);
    });

    backendProcess.on('exit', (code) => {
      console.log(`[Vermes] 后端进程退出, code=${code}`);
      backendProcess = null;
    });

    // 等待后端就绪（最多 15 秒）
    const startTime = Date.now();
    const checkReady = setInterval(async () => {
      try {
        const resp = await fetch(`${BACKEND_URL}/health`);
        if (resp.ok) {
          clearInterval(checkReady);
          console.log('[Vermes] 后端就绪 ✅');
          resolve(true);
        }
      } catch (_) {
        // 还没就绪
      }
      if (Date.now() - startTime > 15000) {
        clearInterval(checkReady);
        console.warn('[Vermes] 后端启动超时，可能已在外部运行');
        resolve(false);
      }
    }, 500);
  });
}

// ── 启动进度管理（后台初始化 + Splash 通信）──
function sendSplash(msg) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    try {
      mainWindow.webContents.send('splash:message', msg);
    } catch (_) {}
  }
}

let _initializing = false
async function runInitialization() {
  if (_initializing) return
  _initializing = true
  // 1. 启动后端
  sendSplash({ type: 'progress', label: '正在启动后端服务…', percent: 10 });
  const started = await startBackend();

  if (started) {
    sendSplash({ type: 'progress', label: '后端已就绪', percent: 90 });
    // 短暂展现 100% 状态再跳转
    await new Promise(r => setTimeout(r, 400));
    sendSplash({ type: 'progress', label: '加载界面…', percent: 100 });
    await new Promise(r => setTimeout(r, 300));
    // 跳转到主界面
    if (mainWindow && !mainWindow.isDestroyed()) {
      sendSplash({ type: 'ready' });
      mainWindow.loadURL(`${BACKEND_URL}?v=${app.getVersion()}`).catch(err => {
        console.error('[Vermes] 加载主界面失败:', err.message);
      });
    }
  } else {
    // 后端启动失败 / 超时
    sendSplash({
      type: 'error',
      detail: '后端服务启动失败，请关闭应用后重新打开。\n如果问题持续，请检查系统资源占用或重新安装。',
    });
  }
  _initializing = false
}

function stopBackend() {
  if (backendProcess) {
    console.log('[Vermes] 关闭后端...');
    const proc = backendProcess
    backendProcess = null
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t']);
    } else {
      proc.kill('SIGTERM');
      setTimeout(() => {
        if (!proc.killed) {
          try { proc.kill('SIGKILL'); } catch {}
        }
      }, 3000);
    }
  }
}

// ── 创建窗口 ──
async function createWindow() {
  const iconPath = fs.existsSync(getIconPath()) ? getIconPath() : undefined;

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 900,
    minHeight: 650,
    title: 'Vermes',
    icon: iconPath,
    show: false,
    backgroundColor: '#0f172a', // 深色背景减少白闪
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
      partition: 'persist:vermes',
    },
  });

  // 清除缓存 — 防止旧前端 JS/CSS 被缓存导致白屏
  const ses = mainWindow.webContents.session
  ses.clearCache().catch(() => {})
  // 只缓存运行时数据，不缓存静态资源（后端无 Cache-Control 头）
  ses.setSpellCheckerEnabled(false)

  // 强制清除持久化分区的运行时存储（IndexedDB/LocalStorage/SessionStorage）
  // 防止旧版本前端在 Electron 分区里残留的脏数据（如不兼容的 IndexedDB schema）
  // 导致新版本前端初始化时读取到损坏数据而白屏/黑屏。
  // 注意：这仅清除 Electron 渲染进程的缓存，不影响 ~/.<app> 下的用户业务数据。
  Promise.all([
    ses.clearStorageData({ storages: ['indexdb', 'localstorage', 'shadercache', 'serviceworkers', 'cachestorage'] }).catch(() => {}),
    (async () => {
      try {
        const reg = await ses.getServiceWorkers?.()
        if (reg?.getAll?.()) {
          for (const sw of reg.getAll()) { await reg.unregister(sw.scope) }
        }
      } catch (_) {}
    })(),
  ]).catch(() => {})

  // 先加载启动欢迎页（立即显示，不等后端）
  // splash.html 在打包后位于 app.asar 根目录，dev 模式位于项目根目录；
  // __dirname 在打包后为 app.asar/electron，需向上一级查找，保证两种布局都能命中。
  const candidateSplashPaths = [
    path.join(__dirname, 'splash.html'),            // dev: electron/splash.html（若放此处）
    path.join(__dirname, '..', 'splash.html'),      // 打包: app.asar/splash.html
    path.join(getAppDir(), 'splash.html'),          // dev: 项目根/splash.html
    process.resourcesPath ? path.join(process.resourcesPath, 'app.asar', 'splash.html') : '',  // 打包兜底
    process.resourcesPath ? path.join(process.resourcesPath, 'app', 'splash.html') : '',        // 解包兜底
  ].filter(Boolean);
  const splashPath = candidateSplashPaths.find(p => fs.existsSync(p));
  if (splashPath) {
    mainWindow.loadFile(splashPath);
  } else {
    // fallback: 直接加载后端（开发环境 splash 不存在时）
    mainWindow.loadURL(BACKEND_URL);
  }

  // 加载完成后立即显示窗口（不等后端就绪）
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    // 后端不在外部运行时，后台启动
    if (!process.argv.includes('--external-backend')) {
      runInitialization();
    }
    console.log('[Vermes] 窗口已显示（欢迎页）');
  });

  // 自定义菜单栏（macOS 保留默认菜单以支持 Cmd+Q 等）
  if (process.platform !== 'darwin') {
    Menu.setApplicationMenu(null);
  } else {
    // macOS: 简化菜单
    Menu.setApplicationMenu(Menu.buildFromTemplate([
      { label: app.name, submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]},
      { label: '编辑', submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        { role: 'selectAll' }
      ]},
      { label: '显示', submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]},
      { label: '窗口', submenu: [
        { role: 'minimize' },
        { role: 'close' }
      ]}
    ]));
  }

  // 固定窗口标题，防止页面 <title> 覆盖
  mainWindow.on('page-title-updated', (e) => {
    e.preventDefault();
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // 外部链接用系统浏览器打开
  // 拦截 target="_blank" 链接 — 统一由 shell.openExternal 打开系统浏览器
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    safeOpenExternal(url);
    return { action: 'deny' };
  });

  // 拦截所有导航到非本地 URL 的请求（如页面内 bug 导致的跳转）
  // 注意：正常消息链接由 window.vermes.openExternalBrowser() 显式调用
  mainWindow.webContents.on('will-navigate', (e, url) => {
    try {
      const parsed = new URL(url);
      // 允许本地后端访问
      if (parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost') return;
    } catch (_) {}
    e.preventDefault();
  });
}

// ── 微信 OAuth 登录窗口 ──
// 接收前端调 /api/wechat/qrurl 返回的完整 OAuth URL（含 vbit.top 注册好的 state）
function openWechatOAuth(oauthUrl) {
  return new Promise((resolve) => {
    const parent = mainWindow;
    const oauthWidth = 420;
    const oauthHeight = 520;

    // 居中在主窗口上
    const parentBounds = parent.getBounds();
    const x = Math.round(parentBounds.x + (parentBounds.width - oauthWidth) / 2);
    const y = Math.round(parentBounds.y + (parentBounds.height - oauthHeight) / 2);

    const oauthWin = new BrowserWindow({
      width: oauthWidth,
      height: oauthHeight,
      x, y,
      title: '微信登录',
      parent: parent,
      modal: false,
      resizable: true,
      minimizable: false,
      maximizable: false,
      icon: getIconPath(),
      backgroundColor: '#ffffff',
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        webSecurity: false,  // 允许连接 localhost.weixin.qq.com (微信桌面本地服务)
      },
    });

    // P1-7: OAuth 导航域白名单 — 即使 webSecurity=false，也限制可跳转域名
    const OAUTH_ALLOWED_DOMAINS = new Set([
      'vbit.top',
      'weixin.qq.com',
      'open.weixin.qq.com',
      'localhost.weixin.qq.com',
      'localhost',
      '127.0.0.1',
      'wx.tenpay.com',
    ])
    function isOAuthUrlAllowed(url) {
      try {
        const parsed = new URL(url)
        const host = parsed.hostname.replace(/^www\./, '')
        // 允许所有子域名匹配
        for (const allowed of OAUTH_ALLOWED_DOMAINS) {
          if (host === allowed || host.endsWith('.' + allowed)) return true
        }
        return false
      } catch (_) { return false }
    }
    oauthWin.webContents.on('will-navigate', (e, url) => {
      if (!isOAuthUrlAllowed(url)) {
        console.warn(`[Vermes] OAuth 窗口阻止导航: ${url}`)
        e.preventDefault()
      }
    })
    oauthWin.webContents.on('will-redirect', (e, url) => {
      if (!isOAuthUrlAllowed(url)) {
        console.warn(`[Vermes] OAuth 窗口阻止跳转: ${url}`)
        e.preventDefault()
      }
    })

    // 内容加载后自动调整窗口大小
    oauthWin.webContents.on('did-finish-load', () => {
      oauthWin.webContents.executeJavaScript(`
        new ResizeObserver(() => {
          const h = document.documentElement.scrollHeight;
          if (h > 400 && h < 900) {
            document.title = '__resize__' + h;
          }
        }).observe(document.body);
        setTimeout(() => {
          const h = document.documentElement.scrollHeight;
          if (h > 400) document.title = '__resize__' + h;
        }, 500);
      `).catch(() => {});
    });

    oauthWin.on('page-title-updated', (e, title) => {
      if (title.startsWith('__resize__')) {
        e.preventDefault();
        const newHeight = parseInt(title.replace('__resize__', ''));
        if (newHeight > 400 && newHeight < 900) {
          const [currentWidth] = oauthWin.getSize();
          oauthWin.setSize(currentWidth, newHeight);
        }
      }
    });

    let oauthDone = false;

    // 直接使用前端传入的完整 OAuth URL（state 已由 vbit.top 注册）
    // 从 URL 中提取 state 用于回调匹配
    let state = '';
    try { state = new URL(oauthUrl).searchParams.get('state') || ''; } catch (_) {}
    oauthWin.loadURL(oauthUrl);

    // 监听 URL 变化 — 检测回调（标记但不关闭窗口，让 vbit.top 完成处理）
    const markDone = () => {
      if (!oauthDone) {
        oauthDone = true;
        // Electron 不允许 renderer window.close()，主进程 3s 后自动关闭
        setTimeout(() => {
          if (!oauthWin.isDestroyed()) {
            console.log('[Vermes] 回调处理完毕，关闭 OAuth 窗口');
            oauthWin.close();
          }
        }, 3000);
      }
    };

    oauthWin.webContents.on('will-redirect', (e, url) => {
      checkOAuthUrl(url, markDone);
    });

    oauthWin.webContents.on('will-navigate', (e, url) => {
      checkOAuthUrl(url, markDone);
    });

    // 轮询 URL（微信可能不用标准 redirect）
    const pollInterval = setInterval(() => {
      if (oauthWin.isDestroyed()) { clearInterval(pollInterval); return; }
      try {
        const currentUrl = oauthWin.webContents.getURL();
        checkOAuthUrl(currentUrl, markDone);
      } catch (_) {}
    }, 500);

    // 窗口关闭时裁决（vbit.top 成功页会 1.5s 后 window.close）
    oauthWin.on('closed', () => {
      clearInterval(pollInterval);
      if (oauthDone) {
        console.log(`[Vermes] 微信 OAuth 完成，token 已就绪`);
        resolve({ success: true, state });
      } else {
        resolve({ success: false, error: 'cancelled' });
      }
    });

    // 5 分钟超时
    setTimeout(() => {
      if (!oauthWin.isDestroyed()) {
        clearInterval(pollInterval);
        oauthWin.close();
        resolve({ success: false, error: 'timeout' });
      }
    }, 5 * 60 * 1000);
  });
}

function checkOAuthUrl(url, onCallback) {
  if (!url) return;
  try {
    const parsed = new URL(url);
    // 检测回调 URL — 只标记，不关闭窗口，让 vbit.top 完成 token 创建
    if (parsed.hostname === 'vbit.top' && parsed.pathname.includes('callback')) {
      if (parsed.searchParams.get('code')) {
        console.log(`[Vermes] 微信回调已触发，等待 vbit.top 处理…`);
        onCallback();
      }
    }
    // 检测微信确认页面（授权成功后微信会跳转）
    if (parsed.hostname === 'open.weixin.qq.com' && parsed.pathname.includes('connect/confirm')) {
      console.log('[Vermes] 微信扫码确认，等待回调...');
    }
  } catch (_) {}
}

// IPC: 渲染进程传入完整 OAuth URL（调 /api/wechat/qrurl 获得）
ipcMain.handle('wechat-login', async (event, oauthUrl) => {
  return await openWechatOAuth(oauthUrl);
});

// ── URL 安全白名单工具 ──
const ALLOWED_PROTOCOLS = ['https:', 'http:', 'mailto:'];
function safeOpenExternal(url) {
  try {
    const parsed = new URL(url);
    if (!ALLOWED_PROTOCOLS.includes(parsed.protocol)) {
      console.warn('[Vermes] 拒绝打开危险协议:', parsed.protocol, url);
      return;
    }
  } catch (_) {
    console.warn('[Vermes] 拒绝打开无效 URL:', url);
    return;
  }
  shell.openExternal(url);
}

// IPC: 渲染进程调用打开外部链接
ipcMain.handle('shell:openExternal', (e, url) => {
  safeOpenExternal(url);
});

// IPC: Splash 重试初始化
ipcMain.on('splash:retry', () => {
  console.log('[Vermes] 用户点击重试初始化');
  runInitialization();
});

// IPC: 后端状态查询
ipcMain.handle('backend:status', () => {
  return { running: !!backendProcess, pid: backendProcess?.pid || null };
});

// ── 单实例锁 ──
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  // ── App 生命周期 ──
  app.whenReady().then(createWindow);
}

app.on('window-all-closed', () => {
  stopBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  stopBackend();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

// ── Agent 框架更新 IPC ──────────────────────────────────────────────
const AGENT_CHECK_URL = 'http://127.0.0.1:9119/api/agent/check';
const AGENT_UPDATE_URL = 'http://127.0.0.1:9119/api/agent/update';

// 获取 session token（从后端 HTML 注入）
let _cachedToken = null
async function getSessionToken() {
  if (_cachedToken) return _cachedToken
  try {
    const res = await fetch('http://127.0.0.1:9119/')
    const html = await res.text()
    const m = html.match(/window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/)
            || html.match(/window\.__OPENCLAW_SESSION_KEY__\s*=\s*"([^"]+)"/)
    if (m && m[1]) {
      _cachedToken = m[1]
      return _cachedToken
    }
  } catch {}
  return null
}

// 带认证的 fetch
async function fetchWithAuth(url, opts = {}) {
  const token = await getSessionToken()
  const headers = { ...opts.headers }
  if (token) {
    headers['X-Hermes-Session-Token'] = token
  }
  return fetch(url, { ...opts, headers })
}

ipcMain.handle('agent:check', async () => {
  try {
    const res = await fetchWithAuth(AGENT_CHECK_URL, {
      headers: { 'Accept': 'application/json' }
    })
    if (res.status === 401) {
      return { error: 'Unauthorized', status: 401 };
    }
    return await res.json();
  } catch (err) {
    console.error('[Vermes] 检查 Agent 更新失败:', err.message);
    return { error: err.message };
  }
});

ipcMain.handle('agent:download', async (event, opts) => {
  try {
    const { version, url, sha256, mirror_url } = opts;
    const res = await fetchWithAuth(AGENT_UPDATE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ version, url, sha256, mirror_url })
    });
    
    if (res.status === 401) {
      mainWindow?.webContents.send('agent:update-error', 'Unauthorized');
      return { error: 'Unauthorized' };
    }
    
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      mainWindow?.webContents.send('agent:update-error', err.detail || 'Download failed');
      return { error: err.detail || 'Download failed' };
    }
    
    // SSE 流式处理
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            mainWindow?.webContents.send('agent:update-progress', data);
            
            if (data.status === 'done') {
              mainWindow?.webContents.send('agent:update-complete', data);
            } else if (data.status === 'error') {
              mainWindow?.webContents.send('agent:update-error', data.message);
            }
          } catch {}
        }
      }
    }
    
    return { success: true };
  } catch (err) {
    console.error('[Vermes] Agent 更新失败:', err.message);
    mainWindow?.webContents.send('agent:update-error', err.message);
    return { error: err.message };
  }
});
