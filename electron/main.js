const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

// 强制 Chromium 解析微信桌面本地服务域名 → 127.0.0.1
app.commandLine.appendSwitch('host-resolver-rules', 'MAP localhost.weixin.qq.com 127.0.0.1');

let mainWindow = null;
let backendProcess = null;
let gatewayProcess = null;  // Vermes 专属 gateway（com.vermes.gateway 命名空间，VERMES_HOME=~/.vermes）
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
    const exeName = process.platform === 'win32' ? 'vermes.exe' : 'vermes';
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
    // PyInstaller 可执行文件是 vermes_cli.main CLI 入口，需 dashboard 子命令启动 web 后端
    return ['dashboard', '--port', String(BACKEND_PORT)];
  }
  // 开发模式：用 uvicorn
  return ['-m', 'uvicorn', 'vermes_cli.web_server:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT), '--log-level', 'warning'];
}

// ── G3 · 资源路径容错泛化（docs/design-startup-integrity-guards-final.md §G3）──
// 打包后 __dirname 为 app.asar/electron，dev 为项目 electron/；不同布局下资源位置不同。
// 统一用「多候选路径探测」命中第一个存在项，找不到时 console.error 带全部候选，
// 避免静默 undefined（历史上缺 preload.js → window.vermes 为 undefined 导致微信登录跳浏览器）。
function resolveResource(relPath, candidates) {
  const list = (candidates || []).filter(Boolean);
  if (list.length === 0) {
    // 默认候选：覆盖 dev / asar / 解包 三种布局
    list.push(
      path.join(__dirname, relPath),                 // dev: electron/<rel>
      path.join(__dirname, '..', relPath),           // 打包: app.asar/<rel>
      path.join(getAppDir(), relPath),               // dev: 项目根/<rel>
      process.resourcesPath ? path.join(process.resourcesPath, 'app.asar', relPath) : '',   // 打包兜底
      process.resourcesPath ? path.join(process.resourcesPath, 'app', relPath) : '',         // 解包兜底
    );
  }
  const found = list.find(p => { try { return fs.existsSync(p); } catch (_) { return false; } });
  if (!found) {
    console.error(`[Vermes] 资源未找到: ${relPath}，候选路径:\n  ` + list.join('\n  '));
    return null;
  }
  return found;
}

function getIconPath() {
  const iconFile = process.platform === 'win32' ? 'icon.png' : 'vermes.icns';
  // 图标候选：dev 在 electron/assets，打包在 app.asar/assets 或解包 app/assets
  return resolveResource(path.join('assets', iconFile), [
    path.join(__dirname, 'assets', iconFile),
    path.join(process.resourcesPath || '', 'app', 'assets', iconFile),
    path.join(process.resourcesPath || '', 'app.asar', 'assets', iconFile),
  ]);
}

// ── Windows Git Bash 检测与自动安装 ──
// Vermes 工具链依赖 bash（write_file/terminal/search 等），
// Windows 不自带 bash，需要 Git for Windows。
// install.ps1 有完整逻辑但 Electron NSIS 安装不跑它，
// 这里在首次启动时检测：没有可用 bash 就自动下载 PortableGit。
async function ensureGitBash() {
  if (process.platform !== 'win32') return; // Mac/Linux 自带 bash

  const localAppData = process.env.LOCALAPPDATA || '';
  if (!localAppData) return;

  const vermesGitDir = path.join(localAppData, 'Vermes', 'git');
  const portableBash = path.join(vermesGitDir, 'bin', 'bash.exe');
  const envVar = 'VERMES_GIT_BASH_PATH';

  // 1. 已有环境变量且文件存在 → 跳过
  const existing = process.env[envVar];
  if (existing && fs.existsSync(existing)) {
    console.log('[Vermes] Git Bash found via env:', existing);
    return;
  }

  // 2. PortableGit 已安装但环境变量没设 → 设上
  if (fs.existsSync(portableBash)) {
    console.log('[Vermes] PortableGit found, setting env var:', portableBash);
    process.env[envVar] = portableBash;
    return;
  }

  // 3. 系统有 Git for Windows（Program Files\Git\bin\bash.exe）→ 设环境变量
  const systemGitBash = path.join(process.env.ProgramFiles || 'C:\\Program Files', 'Git', 'bin', 'bash.exe');
  if (fs.existsSync(systemGitBash)) {
    console.log('[Vermes] System Git Bash found, setting env var:', systemGitBash);
    process.env[envVar] = systemGitBash;
    return;
  }

  // 4. 都没有 → 下载 PortableGit
  console.log('[Vermes] No bash found, downloading PortableGit...');
  const { execFileSync } = require('child_process');
  const os = require('os');

  // 检测架构
  const arch = process.arch; // 'arm64' | 'x64' | 'ia32'
  const gitVer = '2.54.0';
  const gitTag = `v${gitVer}.windows.1`;

  let assetName;
  if (arch === 'arm64') {
    assetName = `PortableGit-${gitVer}-arm64.7z.exe`;
  } else {
    assetName = `PortableGit-${gitVer}-64-bit.7z.exe`;
  }

  const downloadUrl = `https://github.com/git-for-windows/git/releases/download/${gitTag}/${assetName}`;
  const tmpFile = path.join(os.tmpdir(), assetName);

  try {
    // 下载（PowerShell Invoke-WebRequest，可靠且显示进度）
    console.log(`[Vermes] Downloading ${assetName}...`);
    execFileSync('powershell', [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
      `$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '${downloadUrl}' -OutFile '${tmpFile}' -UseBasicParsing`
    ], { stdio: 'pipe', timeout: 300000 }); // 5 min timeout

    // 解压（PortableGit 是自解压 7z）
    console.log(`[Vermes] Extracting to ${vermesGitDir}...`);
    if (fs.existsSync(vermesGitDir)) {
      fs.rmSync(vermesGitDir, { recursive: true, force: true });
    }
    fs.mkdirSync(vermesGitDir, { recursive: true });
    execFileSync(tmpFile, [`-o"${vermesGitDir}"`, '-y'], {
      stdio: 'pipe',
      timeout: 120000,
      windowsHide: true
    });

    // 清理临时文件
    try { fs.unlinkSync(tmpFile); } catch (_) {}

    // 验证
    if (fs.existsSync(portableBash)) {
      console.log('[Vermes] PortableGit installed successfully:', portableBash);
      process.env[envVar] = portableBash;

      // 持久化环境变量（User scope）
      try {
        execFileSync('powershell', [
          '-NoProfile', '-Command',
          `[System.Environment]::SetEnvironmentVariable('${envVar}', '${portableBash}', 'User')`
        ], { stdio: 'pipe' });
      } catch (_) {}
    } else {
      console.error('[Vermes] PortableGit extraction did not produce bash.exe');
    }
  } catch (err) {
    console.error('[Vermes] Failed to install PortableGit:', err.message);
    // 非致命——后端仍能启动，bash 相关工具不可用
  }
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

    // Bug C: 端口预检——如果 9119 已被占用，直接报错而不是等 15 秒超时
    const { execSync } = require('child_process');
    try {
      if (process.platform === 'win32') {
        execSync(`netstat -ano | findstr :9119 | findstr LISTENING`, { stdio: 'pipe' });
      } else {
        execSync(`lsof -i :9119 -sTCP:LISTEN`, { stdio: 'pipe' });
      }
      // 如果走到这里说明端口已被占用
      console.error('[Vermes] 端口 9119 已被占用');
      resolve({ ok: false, reason: 'port_in_use', detail: '端口 9119 已被其他进程占用，请关闭占用进程或更换端口' });
      return;
    } catch (_) {
      // 端口空闲，正常继续
    }

    const env = { ...process.env };
    // 打包模式下设置 PYTHONPATH
    if (app.isPackaged) {
      env.PYTHONPATH = path.join(process.resourcesPath, 'app');
      env.VERMES_HOME = path.join(require('os').homedir(), '.vermes');
    }

    backendProcess = spawn(backendExe, backendArgs, {
      cwd: app.isPackaged ? path.join(process.resourcesPath, 'backend') : getAppDir(),
      env: env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,  // Windows: 隐藏控制台窗口
    });

    // Bug C: 缓存最后几行 stderr 用于诊断
    let _stderrLines = [];
    let _resolved = false;

    backendProcess.stdout.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log(`[Backend] ${msg}`);
    });

    backendProcess.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) {
        console.error(`[Backend ERR] ${msg}`);
        _stderrLines.push(msg);
        if (_stderrLines.length > 20) _stderrLines.shift();
      }
    });

    backendProcess.on('error', (err) => {
      console.error(`[Vermes] 后端启动失败: ${err.message}`);
      if (!_resolved) {
        _resolved = true;
        resolve({ ok: false, reason: 'spawn_error', detail: err.message });
      }
    });

    backendProcess.on('exit', (code) => {
      console.log(`[Vermes] 后端进程退出, code=${code}`);
      backendProcess = null;
      // Bug C: 后端刚启动就退出 → 立刻报错，不等 15 秒超时
      if (!_resolved && code !== 0) {
        _resolved = true;
        clearInterval(checkReady);
        const stderrTail = _stderrLines.join('\n') || '(无 stderr 输出)';
        let detail = `后端进程异常退出 (code=${code})`;
        if (stderrTail.includes('Address already in use') || stderrTail.includes('已在使用')) {
          detail = '端口 9119 被占用，请关闭其他占用该端口的进程';
        } else if (stderrTail.includes('ModuleNotFoundError') || stderrTail.includes('ImportError')) {
          detail = '后端模块缺失，请重新安装或联系支持';
        } else if (stderrTail.includes('Permission denied')) {
          detail = '权限不足，请检查文件权限';
        }
        resolve({ ok: false, reason: 'crash', code, detail, stderr: stderrTail });
      }
    });

    // 等待后端就绪（最多 60 秒——PyInstaller onefolder 在 Windows 上
    // 冷启动需加载 ~2 万个文件到 _internal/，加上 antivirus 扫描，
    // 15 秒根本不够；2.3.6 时代包小所以 15 秒够用）
    const startTime = Date.now();
    const checkReady = setInterval(async () => {
      try {
        const resp = await fetch(`${BACKEND_URL}/health`);
        if (resp.ok) {
          clearInterval(checkReady);
          console.log('[Vermes] 后端就绪 ✅');
          _resolved = true;
          try {
            const body = await resp.json();
            const integrity = (body && body.integrity) || {};
            const verdict = integrity.state_db;
            // G4 分流（docs/design-startup-integrity-guards-final.md §G4）
            if (verdict === 'corrupt' || verdict === 'missing_with_profile') {
              // 方案 a lockdown：账本已坏/缺失 → 阻断进主界面，splash 硬报错
              resolve({
                ok: false,
                reason: verdict === 'corrupt' ? 'db_corrupt' : 'db_missing_with_profile',
                integrity,
              });
              return;
            }
            resolve({ ok: true, integrity });
            return;
          } catch (_) {
            // /health 返回非 JSON（极端情况）→ 视为就绪，交给后续链路
            resolve({ ok: true, integrity: {} });
            return;
          }
        }
      } catch (_) {
        // 还没就绪
      }
      if (Date.now() - startTime > 60000) {
        clearInterval(checkReady);
        console.warn('[Vermes] 后端启动超时（60s），可能已在外部运行');
        if (!_resolved) {
          _resolved = true;
          resolve({ ok: false, reason: 'timeout' });
        }
      }
    }, 500);
  });
}

// ── A.4.1 后端运行期看门狗 + 自重启 ──────────────────────────────────
// 首启成功后挂一个周期 /health 探测；后端掉线（崩溃/卡死）自动 SIGTERM+重拉，
// 并向渲染进程广播在线状态，根治"后端死了前端永久离线、只能源头重启 App"。
let _backendWatchdogTimer = null;
let _backendOnline = true;
let _backendRestarting = false;
// 连续探测失败计数。单次失败就重启后端太激进：后端任何一次短暂占用（长工具调用、
// 大模型首包慢、GC、磁盘抖动）都会被判死刑并 SIGTERM，前端表现为 "Failed to fetch"
// + "重连中 (n/2)" 的循环。要求连续 N 次不可达才动手。
let _backendMissedProbes = 0;
const BACKEND_PROBE_TIMEOUT_MS = 5000;   // 单次 /health 探测超时（原 2000，过紧）
const BACKEND_MAX_MISSED_PROBES = 3;     // 连续 3 次（≈15s）不可达才判定真的死了

function _broadcastBackendStatus(online, detail) {
  const payload = { online, restarting: _backendRestarting, detail: detail || null };
  try {
    BrowserWindow.getAllWindows().forEach((w) => {
      if (w && w.webContents && !w.webContents.isDestroyed()) {
        w.webContents.send('backend-status', payload);
      }
    });
  } catch (_) {}
}

function _backendHealthCheck() {
  return new Promise((resolve) => {
    const ctrl = new AbortController();
    const to = setTimeout(() => ctrl.abort(), BACKEND_PROBE_TIMEOUT_MS);
    fetch(`${BACKEND_URL}/health`, { signal: ctrl.signal })
      .then((r) => { clearTimeout(to); resolve(!!(r && r.ok)); })
      .catch(() => { clearTimeout(to); resolve(false); });
  });
}

async function _backendWatchdogTick() {
  if (_backendRestarting) return;            // 已有重启在途，避免叠加
  const alive = await _backendHealthCheck();
  if (alive) {
    _backendMissedProbes = 0;
    if (!_backendOnline) {
      _backendOnline = true;
      _broadcastBackendStatus(true, 'recovered');
      console.log('[Vermes] 后端已恢复 ✅');
    }
    return;
  }

  // 探测失败 ≠ 后端死了。先累计，够 N 次再动手，避免把"正忙"误判成"已死"。
  _backendMissedProbes += 1;
  if (_backendMissedProbes < BACKEND_MAX_MISSED_PROBES) {
    console.warn(
      `[Vermes] /health 探测失败 ${_backendMissedProbes}/${BACKEND_MAX_MISSED_PROBES}（尚未判定掉线）`
    );
    return;
  }

  // 掉线
  if (_backendOnline) {
    _backendOnline = false;
    _broadcastBackendStatus(false, 'backend unreachable');
    console.warn('[Vermes] 后端连续不可达，准备自重启…');
  }
  _backendRestarting = true;
  _broadcastBackendStatus(false, 'restarting');
  try {
    // 清掉可能残留的僵尸/旧进程（A.4.2 已确保崩溃进程真正退出，这里是双保险）
    if (backendProcess && !backendProcess.killed) {
      try { backendProcess.kill('SIGTERM'); } catch (_) {}
    }
    backendProcess = null;
    await new Promise((r) => setTimeout(r, 800));  // 等端口释放，降低 bind 竞态
    const r = await startBackend();
    if (r && r.ok) {
      _backendOnline = true;
      _backendRestarting = false;
      _backendMissedProbes = 0;
      _broadcastBackendStatus(true, 'recovered');
      console.log('[Vermes] 后端自重启完成 ✅');
    } else {
      _backendRestarting = false;  // 失败：下一 tick 再试
      console.error('[Vermes] 后端自重启未成功，下一周期重试');
    }
  } catch (e) {
    _backendRestarting = false;
    console.error('[Vermes] 后端自重启异常:', e && e.message);
  }
}

function startBackendWatchdog() {
  if (_backendWatchdogTimer) return;  // 幂等
  _backendOnline = true;
  _backendMissedProbes = 0;
  _backendWatchdogTimer = setInterval(() => { _backendWatchdogTick(); }, 5000);
  console.log(
    `[Vermes] 后端看门狗已启动（5s 探测 / ${BACKEND_PROBE_TIMEOUT_MS}ms 超时 / ` +
    `连续 ${BACKEND_MAX_MISSED_PROBES} 次失败才重启）`
  );
}

function stopBackendWatchdog() {
  if (_backendWatchdogTimer) {
    clearInterval(_backendWatchdogTimer);
    _backendWatchdogTimer = null;
  }
}

// ── 渠道网关管理（Vermes 专属 gateway，独立于官方 Vermes / QClaw 的 gateway）──
// 关键设计：注入 VERMES_HOME=~/.vermes，使 gateway 与桌面后端共享同一份配置与 state.db，
// 从而飞书/TG 等渠道消息可由桌面端「全渠道统一控制」。命名空间隔离避免多 agent 共存冲突。
function getGatewayExe() {
  // 打包后用 Vermes 自带的 python 运行 vermes_cli.main；开发模式直接用系统 vermes CLI
  if (app.isPackaged) {
    return getBackendExe();  // 同一 python，靠 -m 指定模块
  }
  return 'vermes';
}

function getGatewayArgs() {
  if (app.isPackaged) {
    return ['-m', 'vermes_cli.main', 'gateway', 'run', '--replace'];
  }
  return ['gateway', 'run', '--replace'];
}

function startGateway() {
  if (gatewayProcess) return;  // 已启动
  console.log('[Vermes] 启动渠道网关(gateway)...');
  const gatewayExe = getGatewayExe();
  const gatewayArgs = getGatewayArgs();

  const env = { ...process.env };
  // 与桌面后端一致：VERMES_HOME=~/.vermes + PYTHONPATH（保证用 Vermes 自带 vermes_cli）
  env.VERMES_HOME = path.join(require('os').homedir(), '.vermes');
  if (app.isPackaged) {
    env.PYTHONPATH = path.join(process.resourcesPath, 'app');
  }

  const spawnGateway = () => {
    gatewayProcess = spawn(gatewayExe, gatewayArgs, {
      cwd: app.isPackaged ? path.join(process.resourcesPath, 'backend') : getAppDir(),
      env: env,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    });

    gatewayProcess.stdout.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.log(`[Gateway] ${msg}`);
    });
    gatewayProcess.stderr.on('data', (data) => {
      const msg = data.toString().trim();
      if (msg) console.error(`[Gateway ERR] ${msg}`);
    });
    gatewayProcess.on('error', (err) => {
      console.error(`[Vermes] 渠道网关启动失败: ${err.message}`);
      gatewayProcess = null;
    });
    // 崩溃自重启（daemon 守护）；正常退出(exit code 0)不重启
    gatewayProcess.on('exit', (code) => {
      console.log(`[Vermes] 渠道网关进程退出, code=${code}`);
      gatewayProcess = null;
      if (code !== 0 && !app.isQuitting) {
        console.warn('[Vermes] 渠道网关闭常退出，3 秒后重启...');
        setTimeout(() => { if (!gatewayProcess) startGateway(); }, 3000);
      }
    });
  };

  spawnGateway();
}

function stopGateway() {
  if (gatewayProcess) {
    console.log('[Vermes] 关闭渠道网关...');
    const proc = gatewayProcess;
    gatewayProcess = null;
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(proc.pid), '/f', '/t']);
    } else {
      proc.kill('SIGTERM');
      setTimeout(() => { if (!proc.killed) { try { proc.kill('SIGKILL'); } catch {} } }, 3000);
    }
  }
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
/**
 * 首次启动同步 bundled + optional 技能到 ~/.vermes/skills/。
 * install.ps1 原有此逻辑但 Electron NSIS 不执行 install.ps1，
 * 与 PortableGit 同一个问题。
 * 用 Python 跑 skills_sync.py（已打包），非阻塞。
 */
function ensureSkillsSynced() {
  const vermesHome = process.env.VERMES_HOME || path.join(
    process.env.HOME || process.env.USERPROFILE || '', '.vermes'
  );
  const skillsDir = path.join(vermesHome, 'skills');
  const manifestFile = path.join(skillsDir, '.bundled_manifest');

  // 已有 manifest → 之前同步过，跳过
  if (fs.existsSync(manifestFile)) {
    console.log('[Vermes] Skills already synced (manifest exists)');
    return;
  }

  // 找后端可执行文件里的 Python
  const backendExe = getBackendExe();
  const backendDir = path.dirname(backendExe);

  // 打包模式下 skills_sync.py 在 _internal/tools/
  const syncScript = path.join(backendDir, '_internal', 'tools', 'skills_sync.py');
  if (!fs.existsSync(syncScript)) {
    console.log('[Vermes] skills_sync.py not found, skipping skill sync');
    return;
  }

  console.log('[Vermes] Syncing bundled skills to ~/.vermes/skills/...');
  try {
    const { execFileSync } = require('child_process');
    execFileSync(backendExe, [syncScript], {
      stdio: 'pipe',
      timeout: 30000,
      windowsHide: true,
      env: { ...process.env, VERMES_HOME: vermesHome }
    });
    console.log('[Vermes] Skills sync completed ✅');
  } catch (err) {
    console.error('[Vermes] Skills sync failed (non-fatal):', err.message);
  }
}

async function runInitialization() {
  if (_initializing) return
  _initializing = true
  // 0. Windows: 确保 Git Bash 可用（首次启动自动下载 PortableGit）
  if (process.platform === 'win32') {
    sendSplash({ type: 'progress', label: '正在检查运行环境…', percent: 5 });
    await ensureGitBash();
  }
  // 0.5 首次启动同步 bundled 技能（Electron NSIS 不执行 install.ps1）
  sendSplash({ type: 'progress', label: '正在加载技能…', percent: 8 });
  ensureSkillsSynced();
  // 1. 启动后端
  sendSplash({ type: 'progress', label: '正在启动后端服务…', percent: 10 });
  const started = await startBackend();

  if (started && started.ok) {
    // G5：profile 错配仅横幅提醒，不阻断（数据本身未坏）。
    // 横幅由前端主窗口拉 /health 自行判定（update.js 已有 /health 拉取），
    // 无需主进程经 preload 中转——main.js 处于主进程，无 bridge 概念。
    sendSplash({ type: 'progress', label: '后端已就绪', percent: 90 });
    // 渠道网关（飞书/TG 等）随桌面端生命周期启动，注入 VERMES_HOME=~/.vermes 与后端共享配置
    startGateway();
    // A.4.1: 后端首启成功后挂运行期看门狗（掉线自动重启 + 广播在线状态）
    startBackendWatchdog();
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
    // 后端启动失败 / 超时 / 账本损坏或缺失
    const reason = (started && started.reason) || 'unknown';
    let detail;
    let dataProtect = false;
    let diagnostic = null;
    if (reason === 'db_corrupt' || reason === 'db_missing_with_profile') {
      // G4 方案 a：splash 硬报错 + UX 三件套
      const integ = (started && started.integrity) || {};
      const dbPath = integ.db_path || '（未知路径）';
      const verdictText = reason === 'db_corrupt' ? '损坏' : '缺失（但检测到历史数据痕迹）';
      detail =
        `检测到历史数据账本${verdictText}，为保护您的资料已停止启动。\n` +
        `受影响文件：${dbPath}\n\n` +
        `您的聊天记录、记忆与自学习素材未被修改，可安全恢复。\n` +
        `建议：从备份恢复该文件，或用 sqlite3 修复后重试；如无需恢复，重命名/移走该文件可让应用以空账本启动（将丢失旧数据）。`;
      dataProtect = true;
      diagnostic = integ;
    } else if (reason === 'timeout') {
      detail = '后端服务启动超时（60秒），请关闭应用后重新打开。\n如果问题持续，请检查系统资源占用或重新安装。';
    } else if (reason === 'port_in_use') {
      detail = (started && started.detail) || '端口 9119 已被占用，请关闭占用该端口的进程后重试。';
    } else if (reason === 'crash') {
      detail = (started && started.detail) || '后端进程异常退出';
      if (started && started.stderr) {
        detail += `\n\n诊断信息：\n${started.stderr.split('\n').slice(-5).join('\n')}`;
      }
    } else if (reason === 'spawn_error') {
      detail = `后端可执行文件启动失败：${(started && started.detail) || '未知错误'}`;
    } else {
      detail = '后端服务启动失败，请关闭应用后重新打开。\n如果问题持续，请检查系统资源占用或重新安装。';
    }
    sendSplash({
      type: 'error',
      detail,
      dataProtect,
      diagnostic,
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

// ── G0/G2 · 分区清理版本戳门控 ──
// 仅在应用版本变更后的首次启动清理 Electron 分区脏数据（localstorage/serviceworkers 等，
// 防旧版残留导致白屏/黑屏）；同版本启动零清理。任何情况下都不清 indexdb——
// 图片/消息 IDB 库的 schema 自愈由前端 chat-storage.js 按单库粒度处理。
const CLEAN_STAMP_FILE = 'last-clean-version';

function readCleanStamp() {
  try {
    return fs.readFileSync(path.join(app.getPath('userData'), CLEAN_STAMP_FILE), 'utf8').trim();
  } catch (_) {
    return null; // 读失败（不存在/权限）→ 视为版本变更（保守：多清一次 localstorage，不丢 IDB）
  }
}

function writeCleanStamp(version) {
  try {
    fs.writeFileSync(path.join(app.getPath('userData'), CLEAN_STAMP_FILE), version, 'utf8');
    return true;
  } catch (_) {
    return false; // 写失败 → 下次启动仍视为版本变更，行为保守但无害
  }
}

function maybeCleanPartitionStorage(ses) {
  const currentVersion = app.getVersion();
  const lastCleanVersion = readCleanStamp();
  if (lastCleanVersion === currentVersion) {
    console.log(`[Vermes] 分区存储清理跳过（版本未变: ${currentVersion}）`);
    return;
  }
  console.log(`[Vermes] 版本变更（${lastCleanVersion || '无版本戳'} -> ${currentVersion}），清理分区脏数据（不含 indexdb）`);
  Promise.all([
    // G0 修复：storages 永不包含 'indexdb'——历史图片/消息只存 IDB，清了即真丢失。
    // 注意：此处刻意不 .catch 吞错——清理失败必须让 Promise.all 拒绝，
    // 从而跳过写版本戳，下次启动重清（"清理成功后才写戳"的设计不变量）。
    ses.clearStorageData({ storages: ['localstorage', 'shadercache', 'serviceworkers', 'cachestorage'] }),
    (async () => {
      try {
        const reg = await ses.getServiceWorkers?.()
        if (reg?.getAll?.()) {
          for (const sw of reg.getAll()) { await reg.unregister(sw.scope) }
        }
      } catch (_) {} // SW 注销保持 best-effort，不阻塞戳写入
    })(),
  ]).then(() => {
    // 清理成功后才写版本戳；写失败则下次启动重清一次（幂等，无数据损失面）
    writeCleanStamp(currentVersion);
  }).catch(() => {
    console.log('[Vermes] 分区清理未完全成功，版本戳未写入（下次启动将重试清理）');
  })
}

// ── 创建窗口 ──
async function createWindow() {
  const iconPath = getIconPath() || undefined;  // getIconPath 找不到时返回 null → 不设置 icon

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
      preload: resolveResource('preload.js') || path.join(__dirname, 'preload.js'),
      partition: 'persist:vermes',
    },
  });

  // 清除缓存 — 防止旧前端 JS/CSS 被缓存导致白屏
  const ses = mainWindow.webContents.session
  ses.clearCache().catch(() => {})
  // 只缓存运行时数据，不缓存静态资源（后端无 Cache-Control 头）
  ses.setSpellCheckerEnabled(false)

  // ── G0/G2 分区清理版本戳门控（docs/design-startup-integrity-guards-final.md §G0/G2）──
  // 历史 bug（G0）：此处曾无条件 clearStorageData(['indexdb', ...])，而聊天图片仅存
  // IndexedDB（vermes-images，服务端 JSON 只存 _imageKeys 引用无图片字节），导致
  // 每次冷启动静默清空全部历史图片。
  // 现行为：
  //   1. 版本戳（userData/last-clean-version）与 app.getVersion() 一致 → 零清理；
  //   2. 版本变更（或版本戳读写失败，保守视为变更）→ 清 localstorage/sw 等脏数据源，
  //      但 **永不清 indexdb**——IDB schema 问题交前端单库粒度自愈（chat-storage.js）；
  //   3. clearCache() 保留每次执行（上方 L201），保底热修静态资源缓存，无数据副作用。
  maybeCleanPartitionStorage(ses)

  // 先加载启动欢迎页（立即显示，不等后端）
  // splash.html 在打包后位于 app.asar 根目录，dev 模式位于项目根目录；
  // __dirname 在打包后为 app.asar/electron，需向上一级查找，保证两种布局都能命中。
  const splashPath = resolveResource('splash.html');
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

// IPC: 打开文件所在文件夹（WorkBuddy 风格）
ipcMain.handle('shell:showItemInFolder', (e, fullPath) => {
  if (!fullPath || typeof fullPath !== 'string') return { ok: false, err: 'invalid path' }
  try {
    shell.showItemInFolder(fullPath)
    return { ok: true }
  } catch (err) {
    return { ok: false, err: String(err) }
  }
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

// IPC: 渠道网关重启（Settings 页「启动网关」按钮调用）
ipcMain.handle('gateway:restart', async () => {
  console.log('[Vermes] 收到 gateway:restart IPC，重启渠道网关...');
  stopGateway();
  await new Promise(r => setTimeout(r, 1000));
  startGateway();
  return { ok: true };
});

// IPC: G4 诊断信息打包（splash 数据保护错误页「复制诊断」按钮）
// 把 /health 的 integrity 字段 + 版本 + 平台拼成可粘贴文本。
ipcMain.handle('copyDiagnostic', async (event, diagnostic) => {
  try {
    // 主进程 clipboard 模块：不依赖任何窗口/聚焦状态，splash 硬阻断场景
    // （此时 mainWindow 停在 splash 页、navigator.clipboard 在 file:// 下可能
    // 静默失败）也 100% 可靠。谎报「已复制 ✓」在数据保护错误页不可接受。
    const { clipboard } = require('electron');
    const lines = [
      `Vermes ${app.getVersion()}`,
      `Platform: ${process.platform} ${require('os').release()}`,
      `Time: ${new Date().toISOString()}`,
      `Diagnostic: ${JSON.stringify(diagnostic || {}, null, 2)}`,
    ];
    const text = lines.join('\n');
    clipboard.writeText(text);
    // 写后读回校验，确保按钮文案「已复制 ✓」与剪贴板实际状态一致
    const ok = clipboard.readText() === text;
    return { ok, text };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
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
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  app.isQuitting = true;
  stopGateway();
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
    const m = html.match(/window\.__VERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/)
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
    headers['X-Vermes-Session-Token'] = token
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
