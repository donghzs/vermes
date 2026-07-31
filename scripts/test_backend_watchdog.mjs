// A.4.1 后端运行期看门狗验证 harness —— main.js watchdog 函数
// 范式同 c1/c3/c4/gateway_lifecycle：从磁盘 main.js 抽取真实函数体注入 mock 执行，非逻辑副本。
import { readFileSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const mainJs = readFileSync(path.join(ROOT, 'electron', 'main.js'), 'utf8');

// 抽取函数体（含 async/普通），支持嵌套花括号配对；保留 async 修饰符
function extractFn(name) {
  const re = new RegExp('(async\\s+)?function\\s+' + name.replace(/[_$]/g, '\\$&') + '\\s*\\(', 'g');
  re.lastIndex = 0;
  const m = re.exec(mainJs);
  if (!m) throw new Error(`未在 main.js 找到 ${name} 定义`);
  const declStart = m.index;
  let depth = 0, i = mainJs.indexOf('{', m.index), end = -1;
  for (; i < mainJs.length; i++) {
    if (mainJs[i] === '{') depth++;
    else if (mainJs[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  return mainJs.slice(declStart, end);
}

let pass = 0, fail = 0;
function assert(cond, name) { if (cond) { pass++; console.log('PASS:', name); } else { fail++; console.error('❌ FAIL:', name); } }

// ── mock BrowserWindow（记录广播）──
const sent = [];
const fakeWin = { webContents: { isDestroyed: () => false, send: (ch, p) => sent.push({ ch, p }) } };
const BrowserWindow = { getAllWindows: () => [fakeWin] };

// ── mock fetch（可控健康状态）──
let healthAlive = true;
const fetchMock = async (url, opts) => {
  if (!healthAlive) throw new Error('connection refused');
  return { ok: true };
};

// ── mock startBackend（可控重启结果）──
let startBackendResult = { ok: true };
let startBackendCalls = 0;
const startBackend = async () => { startBackendCalls++; return startBackendResult; };

// ── mock setInterval / clearInterval（捕获回调，不自动跑）──
let intervalCb = null;
let intervalRegCount = 0;
const setIntervalMock = (cb, ms) => { intervalCb = cb; intervalRegCount++; return 1; };
const clearIntervalMock = () => { intervalCb = null; };

// ── 抽取真实函数体 ──
const fns = ['_broadcastBackendStatus', '_backendHealthCheck', '_backendWatchdogTick', 'startBackendWatchdog', 'stopBackendWatchdog']
  .map(extractFn).join('\n');

const scope = new Function(
  'BrowserWindow', 'BACKEND_URL', 'fetch', 'startBackend', 'setInterval', 'clearInterval',
  `let _backendWatchdogTimer = null;
   let _backendOnline = true;
   let _backendRestarting = false;
   let backendProcess = null;
   ${fns}
   return {
     _backendHealthCheck, _backendWatchdogTick, startBackendWatchdog, stopBackendWatchdog,
     getState: () => ({ _backendOnline, _backendRestarting, timer: _backendWatchdogTimer, backendProcess }),
   };`
)(BrowserWindow, 'http://127.0.0.1:9119', fetchMock, startBackend, setIntervalMock, clearIntervalMock);

const tick = () => scope._backendWatchdogTick();

console.log('=== 场景1：健康 → 在线，不重启、不广播离线 ===');
healthAlive = true; startBackendCalls = 0; sent.length = 0;
const h1 = await scope._backendHealthCheck();
assert(h1 === true, '场景1 健康检查返回 true');
await tick();
assert(scope.getState()._backendOnline === true, '场景1 仍在线');
assert(startBackendCalls === 0, '场景1 未触发重启');
assert(sent.slice().length === 0, '场景1 无广播');

console.log('=== 场景2：后端掉线 → 广播离线 + 自动重启 + 恢复广播 ===');
healthAlive = false; startBackendCalls = 0; startBackendResult = { ok: true }; sent.length = 0;
await tick();  // 检测到掉线，重启（含 800ms 等端口释放），成功后恢复
assert(startBackendCalls === 1, '场景2 触发了一次自重启(startBackend)');
assert(scope.getState()._backendOnline === true, '场景2 重启后恢复在线');
const s2 = sent.slice();
assert(s2.some((m) => m.p.online === false), '场景2 广播过 offline');
assert(s2.some((m) => m.p.online === true && m.p.detail === 'recovered'), '场景2 广播过 recovered');
assert(s2[0].p.online === false, '场景2 先 offline 后 recovered');

console.log('=== 场景3：自重启失败（startBackend 返回 ok:false）→ 下一 tick 再试 ===');
healthAlive = false; startBackendCalls = 0; startBackendResult = { ok: false }; sent.length = 0;
await tick();
assert(startBackendCalls === 1, '场景3 尝试了一次重启');
assert(scope.getState()._backendOnline === false, '场景3 仍未在线（等下次重试）');
assert(scope.getState()._backendRestarting === false, '场景3 重启失败后解除锁，允许重试');

console.log('=== 场景4：startBackendWatchdog 幂等（多次调用只注册一次）===');
intervalRegCount = 0;
scope.startBackendWatchdog();
scope.startBackendWatchdog();
scope.startBackendWatchdog();
assert(intervalRegCount === 1, '场景4 只注册了一个 interval');
assert(typeof intervalCb === 'function', '场景4 回调已捕获');

console.log('=== 场景5：手动跑一次看门狗 tick（通过捕获的 interval 回调）===');
healthAlive = true; sent.length = 0;
if (intervalCb) await intervalCb();  // 同场景1 路径
assert(scope.getState()._backendOnline === true, '场景5 tick 后保持在线');

console.log('=== 场景6：stopBackendWatchdog 清空 ===');
scope.stopBackendWatchdog();
assert(intervalCb === null, '场景6 interval 已清空');

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
