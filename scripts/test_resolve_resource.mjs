// c4 验证 harness —— main.js resolveResource 资源路径容错泛化
// 范式（同 c1/c3）：从磁盘 main.js 抽取真实 resolveResource 函数注入 mock 执行，非逻辑副本。
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const mainJs = readFileSync(path.join(ROOT, 'electron', 'main.js'), 'utf8');

// 抽取真实 resolveResource 函数体
const start = mainJs.indexOf('function resolveResource(relPath, candidates)');
if (start < 0) { console.error('❌ 未在 main.js 找到 resolveResource 定义'); process.exit(1); }
let depth = 0, i = mainJs.indexOf('{', start), end = -1;
for (; i < mainJs.length; i++) {
  if (mainJs[i] === '{') depth++;
  else if (mainJs[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = mainJs.slice(start, end);
if (!fnSrc.includes('console.error') || !fnSrc.includes('.find(')) {
  console.error('❌ resolveResource 未含容错逻辑（找不到报错/候选探测）'); process.exit(1);
}

// ── mock 环境 ──
let existsMap = new Set();
let errors = [];
const mockFs = { existsSync: (p) => existsMap.has(p) };
const mockProcess = { resourcesPath: '/rsrc' };
// path 用真实 path 模块（path.join 行为稳定）
const mockModule = {
  path, fs: mockFs, process: mockProcess, console: { error: (...a) => errors.push(a.join(' ')) },
};
// 注入（函数体内引用 path/fs/process/console/__dirname 为自由变量，逐个传入）
const resolveResource = new Function('path', 'fs', 'process', 'console', '__dirname',
  fnSrc + '\nreturn resolveResource;')(path, mockFs, mockProcess, mockModule.console, '/proj/electron');

let pass = 0, fail = 0;
function assert(cond, name) { if (cond) { pass++; console.log('PASS:', name); } else { fail++; console.error('❌ FAIL:', name); } }

function makeResolve(dir) {
  // getAppDir 在 main.js 中定义为项目根；此处 mock 为 dir 的上一级（与打包布局一致即可）
  const mockGetAppDir = () => path.dirname(dir);
  return new Function('path', 'fs', 'process', 'console', '__dirname', 'getAppDir',
    fnSrc + '\nreturn resolveResource;')(path, mockFs, mockProcess, mockModule.console, dir, mockGetAppDir);
}

// 场景1：默认候选命中第一个存在项（dev 布局 __dirname/splash.html）
existsMap = new Set(['/proj/electron/splash.html']);
errors = [];
const r1 = makeResolve('/proj/electron')('splash.html');
assert(r1 === '/proj/electron/splash.html', '场景1 默认候选命中 dev 布局');

// 场景2：打包布局命中 app.asar/splash.html（__dirname 候选不存在时跳过）
existsMap = new Set(['/app.asar/splash.html']);
errors = [];
const r2 = makeResolve('/app.asar/electron')('splash.html');
assert(r2 === '/app.asar/splash.html', '场景2 默认候选命中打包 asar 布局');

// 场景3：自定义候选优先于默认
existsMap = new Set(['/custom/preload.js']);
errors = [];
const r3 = makeResolve('/proj/electron')('preload.js', ['/custom/preload.js']);
assert(r3 === '/custom/preload.js', '场景3 自定义候选生效');

// 场景4：找不到 → 返回 null 且 console.error 带全部候选路径
existsMap = new Set();
errors = [];
const r4 = makeResolve('/proj/electron')('missing.xyz');
assert(r4 === null, '场景4 找不到返回 null');
assert(errors.length === 1 && errors[0].includes('missing.xyz') && errors[0].includes('/proj/electron/missing.xyz'), '场景4 报错带资源名 + 候选路径');

// 场景5：existsSync 抛错（权限问题）不崩溃，视为不存在继续探测
existsMap = new Set();
const throwingFs = { existsSync: (p) => { throw new Error('EACCES'); } };
const resolveThrowing = new Function('path', 'fs', 'process', 'console', '__dirname', 'getAppDir',
  fnSrc + '\nreturn resolveResource;')(path, throwingFs, mockProcess, mockModule.console, '/proj/electron', () => '/proj');
errors = [];
const r5 = resolveThrowing('splash.html');
assert(r5 === null, '场景5 existsSync 抛错 → 安全返回 null（不崩溃）');

console.log(`\n${fail === 0 ? '✅' : '❌'} c4 resolveResource：${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
