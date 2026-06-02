const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

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
      cwd: app.isPackaged ? path.join(process.resourcesPath, 'app') : getAppDir(),
      env: env,
      stdio: ['ignore', 'pipe', 'pipe']
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

function stopBackend() {
  if (backendProcess) {
    console.log('[Vermes] 关闭后端...');
    backendProcess.kill('SIGTERM');
    // 3 秒后强制杀
    setTimeout(() => {
      if (backendProcess && !backendProcess.killed) {
        backendProcess.kill('SIGKILL');
      }
    }, 3000);
    backendProcess = null;
  }
}

// ── 创建窗口 ──
async function createWindow() {
  // 先尝试启动后端
  await startBackend();

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

  mainWindow.loadURL(BACKEND_URL);

  // 加载完成后显示
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    console.log('[Vermes] 窗口已显示');
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // 外部链接用系统浏览器打开
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

// ── 微信 OAuth 登录窗口 ──
function openWechatOAuth(event) {
  return new Promise((resolve) => {
    const parent = mainWindow;
    const oauthWidth = 420;
    const oauthHeight = 620;

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
      },
    });

    // 微信 OAuth URL
    const state = Date.now().toString();
    const oauthUrl = `https://open.weixin.qq.com/connect/qrconnect?appid=wxfd680141e93226be&redirect_uri=${encodeURIComponent('https://vbit.top/api/wechat/callback')}&response_type=code&scope=snsapi_login&state=${state}#wechat_redirect`;

    oauthWin.loadURL(oauthUrl);

    // 监听 URL 变化 — 检测回调
    oauthWin.webContents.on('will-redirect', (e, url) => {
      checkOAuthUrl(url, oauthWin, resolve, state);
    });

    oauthWin.webContents.on('will-navigate', (e, url) => {
      checkOAuthUrl(url, oauthWin, resolve, state);
    });

    // 轮询 URL（微信可能不用标准 redirect）
    const pollInterval = setInterval(() => {
      if (oauthWin.isDestroyed()) { clearInterval(pollInterval); return; }
      try {
        const currentUrl = oauthWin.webContents.getURL();
        checkOAuthUrl(currentUrl, oauthWin, resolve, state);
      } catch (_) {}
    }, 500);

    // 用户关闭窗口
    oauthWin.on('closed', () => {
      clearInterval(pollInterval);
      resolve({ success: false, error: 'cancelled' });
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

function checkOAuthUrl(url, oauthWin, resolve, state) {
  if (!url) return;
  try {
    const parsed = new URL(url);
    // 检测回调 URL (vbit.top/api/wechat/callback?code=xxx&state=yyy)
    if (parsed.hostname === 'vbit.top' && parsed.pathname.includes('callback')) {
      const code = parsed.searchParams.get('code');
      const returnedState = parsed.searchParams.get('state') || state;
      if (code) {
        console.log(`[Vermes] 微信 OAuth code 获取成功`);
        if (!oauthWin.isDestroyed()) oauthWin.close();
        resolve({ success: true, code, state: returnedState });
      }
    }
    // 检测微信确认页面（授权成功后微信会跳转）
    if (parsed.hostname === 'open.weixin.qq.com' && parsed.pathname.includes('connect/confirm')) {
      // 用户已扫码确认，等待回调
      console.log('[Vermes] 微信扫码确认，等待回调...');
    }
  } catch (_) {}
}

// IPC: 渲染进程调用微信登录
ipcMain.handle('wechat-login', async () => {
  return await openWechatOAuth();
});

// ── App 生命周期 ──
app.whenReady().then(createWindow);

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
