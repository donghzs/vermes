// test_update_orphan_removal.mjs — P1 更新断路修复验证（方案 B：删 preload 孤儿 API）
//
// 范式同 c1/c3/c4/c5：从磁盘读取真实源码，非逻辑副本。
// 杀手点：用 preload.js 真实暴露的 key 集合构造 window.vermes，
// 再跑 update.js 真实 checkUpdate 分支 —— 测的是两文件间的真实契约，
// 防止未来任何一侧改动让"存在性判断永真"断路悄悄复活。

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const preloadSrc = readFileSync(join(root, 'electron/preload.js'), 'utf8');
const updateSrc = readFileSync(join(root, 'frontend/src/stores/update.js'), 'utf8');

let pass = 0, fail = 0;
function check(name, cond, detail = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); }
  else { fail++; console.log(`  ✗ ${name}${detail ? ' — ' + detail : ''}`); }
}

// ── 1. 静态断言：preload 不再有任何壳更新 IPC 通道 ──
console.log('【1】preload.js 孤儿 IPC 通道已清除');
check('无 invoke(update:*)', !/ipcRenderer\.invoke\(\s*['"]update:/.test(preloadSrc));
check('无 on(update:*) 事件监听', !/ipcRenderer\.on\(\s*['"]update:/.test(preloadSrc));
check('Agent 更新通道保留 (agent:check)', /ipcRenderer\.invoke\(\s*['"]agent:check['"]/.test(preloadSrc));
check('Agent 更新通道保留 (agent:download)', /ipcRenderer\.invoke\(\s*['"]agent:download['"]/.test(preloadSrc));
check('splash 通道未误伤 (copyDiagnostic)', /copyDiagnostic/.test(preloadSrc));

// ── 2. 从真实 preload.js 解析暴露给渲染进程的 key 集合 ──
console.log('【2】解析 preload 真实暴露面');
const exposeBlock = preloadSrc.slice(
  preloadSrc.indexOf('exposeInMainWorld'),
  preloadSrc.lastIndexOf('});')
);
const exposedKeys = [...exposeBlock.matchAll(/^\s{2}(\w+)\s*:/gm)].map(m => m[1]);
check('key 集合非空', exposedKeys.length > 0, `parsed=${exposedKeys.length}`);
const orphans = ['checkForUpdates', 'downloadUpdate', 'installUpdate',
  'onUpdateAvailable', 'onUpdateNotAvailable', 'onUpdateProgress',
  'onUpdateDownloaded', 'onUpdateError'];
const leaked = orphans.filter(k => exposedKeys.includes(k));
check('6+2 个壳更新孤儿 key 全部消失', leaked.length === 0, `残留: ${leaked.join(',')}`);
check('checkAgentUpdate 仍暴露', exposedKeys.includes('checkAgentUpdate'));

// ── 3. 行为断言：真实 checkUpdate 在"新 preload 契约"下走 web 分支 ──
console.log('【3】update.js 真实 checkUpdate 分支行为');
const fnStart = updateSrc.indexOf('async function checkUpdate()');
const fnSrc = updateSrc.slice(fnStart, updateSrc.indexOf('/** Electron 原生更新检查 */'));
check('checkUpdate 源码抽取成功', fnSrc.includes('checkUpdateWeb'), 'anchor miss');

function runCheckUpdate(vermesObj) {
  const calls = [];
  const harness = new Function('window', 'isDesktop', 'checked', 'Promise',
    'checkUpdateElectron', 'checkAgentUpdate', 'checkUpdateWeb',
    `return (${fnSrc.replace('async function checkUpdate()', 'async function ()')})();`
  );
  return harness(
    { vermes: vermesObj }, true, { value: false },
    Promise,
    async () => calls.push('electron'),
    async () => calls.push('agent'),
    async () => calls.push('web'),
  ).then(() => calls);
}

// 3a. 用真实 preload key 集合构造 window.vermes（修复后的真契约）
const realVermes = Object.fromEntries(exposedKeys.map(k => [k, () => {}]));
const callsNow = await runCheckUpdate(realVermes);
check('修复后：桌面端走 web 检查', callsNow.includes('web'), `calls=${callsNow}`);
check('修复后：不再进 electron 死路', !callsNow.includes('electron'), `calls=${callsNow}`);
check('修复后：Agent 更新检查保留', callsNow.includes('agent'), `calls=${callsNow}`);

// 3b. 反证：若孤儿 API 复活（旧契约），断路立即回归 —— 证明本测试能抓住它
const oldVermes = { ...realVermes, checkForUpdates: () => {} };
const callsOld = await runCheckUpdate(oldVermes);
check('反证：孤儿 API 复活时确实断路（走 electron 不走 web）',
  callsOld.includes('electron') && !callsOld.includes('web'), `calls=${callsOld}`);

console.log(`\n${fail === 0 ? '✅' : '❌'} 更新断路修复：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
