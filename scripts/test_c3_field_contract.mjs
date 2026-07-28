// c3 验证 harness —— G4 字段契约（main.js 读取 vs web_server 写入 vs splash 渲染）
// 防止字段名错位导致 db_corrupt 静默不阻断（结构性 bug）。
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const mainJs = readFileSync(path.join(ROOT, 'electron', 'main.js'), 'utf8');
const webServer = readFileSync(path.join(ROOT, 'hermes_cli', 'web_server.py'), 'utf8');
const splash = readFileSync(path.join(ROOT, 'electron', 'splash.html'), 'utf8');

let pass = 0, fail = 0;
function assert(cond, name) { if (cond) { pass++; console.log('PASS:', name); } else { fail++; console.error('❌ FAIL:', name); } }

// 1. web_server 写入的字段集合
const written = new Set();
for (const m of webServer.matchAll(/"state_db"|"profile_mismatch"|"db_path"|"detail"|"lockdown"/g)) written.add(m[0].replace(/"/g, ''));
assert(written.has('state_db'), 'web_server 写入 state_db');
assert(written.has('profile_mismatch'), 'web_server 写入 profile_mismatch');
assert(written.has('db_path'), 'web_server 写入 db_path');
assert(written.has('detail'), 'web_server 写入 detail');

// 2. main.js 读取的字段集合（分流判定用 state_db；G5 横幅交给前端故不读 profile_mismatch，属正确分工）
assert(mainJs.includes('integrity.state_db'), 'main.js 读取 integrity.state_db 用于分流');
assert(mainJs.includes("=== 'corrupt'") && mainJs.includes("=== 'missing_with_profile'"), 'main.js 对 corrupt/missing_with_profile 硬阻断');
assert(mainJs.includes('db_corrupt') && mainJs.includes('db_missing_with_profile'), 'main.js resolve reason 含 db_corrupt / db_missing_with_profile');

// 3. 字段名拼写三方一致（无 typo 导致静默失效）
assert(!mainJs.includes('integrity.stateDB') && !mainJs.includes('integrity.state_db '), 'main.js 无 stateDB 错写');
assert(!splash.includes('integrity.') || splash.includes('msg.diagnostic'), 'splash 用 msg.diagnostic（与 main.js 透传字段名一致）');

// 4. splash showError 渲染结构化字段
assert(splash.includes('msg.dataProtect') && splash.includes('msg.diagnostic'), 'splash 渲染 dataProtect/diagnostic 三件套');
assert(splash.includes("getElementById('errorTitle')"), 'splash 渲染自定义标题（errorTitle）');

// 5. preload 暴露 copyDiagnostic 且 main.js 有对应 IPC handler
const preload = readFileSync(path.join(ROOT, 'electron', 'preload.js'), 'utf8');
assert(preload.includes('copyDiagnostic'), 'preload 暴露 copyDiagnostic');
assert(mainJs.includes("ipcMain.handle('copyDiagnostic'"), 'main.js 注册 copyDiagnostic IPC');

// 6. 前端 G5 横幅自行拉 /health 判定 profile_mismatch（不依赖 main.js 中转）
const appVue = readFileSync(path.join(ROOT, 'frontend', 'src', 'App.vue'), 'utf8');
assert(appVue.includes("fetch('/health')") && appVue.includes('profile_mismatch'), 'App.vue 自行拉 /health 判定 profile_mismatch 横幅');
assert(appVue.includes('v-if="profileMismatch"'), 'App.vue 横幅用 v-if 绑定（非 v-if 错写）');

console.log(`\n${fail === 0 ? '✅' : '❌'} c3 字段契约：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
