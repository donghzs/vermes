const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const { autoUpdater } = require('electron-updater');

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

function stopBackend() {
  if (backendProcess) {
    console.log('[Vermes] 关闭后端...');
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
      setTimeout(() => {
        if (backendProcess && !backendProcess.killed) {
          backendProcess.kill('SIGKILL');
        }
      }, 3000);
    }
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
    // mainWindow.webContents.openDevTools(); // 生产环境关闭
    console.log('[Vermes] 窗口已显示');
  });

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
    shell.openExternal(url);
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

// IPC: 渲染进程调用打开外部链接
ipcMain.handle('shell:openExternal', (e, url) => {
  shell.openExternal(url);
});

// ── 自动更新 (electron-updater) ──

// 配置更新源：vbit.top 发布服务器
// 生产环境从 GitHub Releases 或自定义服务器拉取更新
autoUpdater.autoDownload = false;           // 手动控制下载时机
autoUpdater.autoInstallOnAppQuit = true;    // 退出时自动安装
autoUpdater.allowDowngrade = false;

// 根据平台设置更新 feed URL
const updateFeedBase = 'https://vbit.top/vermes/updates';
const platform = process.platform === 'darwin' ? 'mac' : 'win';
const arch = process.arch === 'arm64' ? 'arm64' : (process.platform === 'darwin' ? 'x64' : 'x64');
autoUpdater.setFeedURL({
  provider: 'generic',
  url: `${updateFeedBase}/${platform}/${arch}/`
});

// 事件：发现有更新
autoUpdater.on('update-available', (info) => {
  console.log('[Vermes] 发现新版本:', info.version);
  mainWindow?.webContents.send('update:available', {
    version: info.version,
    releaseNotes: info.releaseNotes,
    releaseDate: info.releaseDate,
  });
});

// 事件：没有更新
autoUpdater.on('update-not-available', () => {
  console.log('[Vermes] 已是最新版本');
  mainWindow?.webContents.send('update:not-available');
});

// 事件：下载进度
autoUpdater.on('download-progress', (progress) => {
  mainWindow?.webContents.send('update:download-progress', {
    percent: progress.percent,
    bytesPerSecond: progress.bytesPerSecond,
    transferred: progress.transferred,
    total: progress.total,
  });
});

// 事件：下载完成，准备安装
autoUpdater.on('update-downloaded', (info) => {
  console.log('[Vermes] 更新已下载:', info.version);
  mainWindow?.webContents.send('update:downloaded', {
    version: info.version,
  });
});

// 事件：更新出错
autoUpdater.on('error', (err) => {
  console.error('[Vermes] 更新出错:', err.message);
  mainWindow?.webContents.send('update:error', {
    message: err.message,
  });
});

// IPC: 检查更新
ipcMain.handle('update:check', async () => {
  try {
    const result = await autoUpdater.checkForUpdates();
    return { success: true, updateInfo: result?.updateInfo || null };
  } catch (err) {
    console.error('[Vermes] 检查更新失败:', err.message);
    return { success: false, error: err.message };
  }
});

// IPC: 开始下载更新
ipcMain.handle('update:download', async () => {
  try {
    await autoUpdater.downloadUpdate();
    return { success: true };
  } catch (err) {
    console.error('[Vermes] 下载更新失败:', err.message);
    return { success: false, error: err.message };
  }
});

// IPC: 安装更新并重启
ipcMain.handle('update:install', async () => {
  autoUpdater.quitAndInstall();
  return { success: true };
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
