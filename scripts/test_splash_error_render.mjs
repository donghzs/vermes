// c3 验证 harness —— splash.html showError 结构化渲染（G4 UX 三件套）
// 范式（同 c1）：从磁盘 splash.html 抽取真实 showError 函数体注入 mock DOM 执行，非逻辑副本。
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const splashPath = path.join(ROOT, 'electron', 'splash.html');
const html = readFileSync(splashPath, 'utf8');

// 抽取真实 showError 函数源码（从 `function showError(msg) {` 到匹配的右括号闭合）
const start = html.indexOf('function showError(msg)');
if (start < 0) { console.error('❌ 未在 splash.html 找到 showError 定义'); process.exit(1); }
// 简单括号配对截取
let depth = 0, i = html.indexOf('{', start), end = -1;
for (; i < html.length; i++) {
  if (html[i] === '{') depth++;
  else if (html[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
}
const fnSrc = html.slice(start, end);
if (!fnSrc.includes('dataProtect') || !fnSrc.includes('diagnostic')) {
  console.error('❌ showError 未含 G4 三件套逻辑（dataProtect/diagnostic）'); process.exit(1);
}

// ── mock DOM + bridge ──
function makeEl() {
  return {
    style: {}, textContent: '', _onclick: null,
    set onclick(fn) { this._onclick = fn; }, get onclick() { return this._onclick; },
    classList: { add() {}, remove() {} },
  };
}
const els = {
  loadingState: makeEl(), errorState: makeEl(), errorDetail: makeEl(),
  errorTitle: makeEl(), diagBtn: makeEl(), retryBtn: makeEl(),
};
global.document = {
  getElementById: (id) => els[id] || makeEl(),
};
let copyDiagCalled = null;
const bridge = { copyDiagnostic: (d) => { copyDiagCalled = d; return Promise.resolve({ ok: true }); } };
global.bridge = bridge;

// 注入真实函数（用 new Function 包裹源码，引用全局 document/bridge）
const realShowError = new Function(fnSrc + '\nreturn showError;')();

let pass = 0, fail = 0;
function assert(cond, name) { if (cond) { pass++; console.log('PASS:', name); } else { fail++; console.error('❌ FAIL:', name); } }

// 场景1：字符串（向后兼容旧调用）—— 仅填 detail
els.errorDetail.textContent = '';
realShowError('旧版错误文本');
assert(els.errorDetail.textContent === '旧版错误文本', '场景1 字符串兼容 → detail 文本');
assert(els.errorTitle.textContent === '初始化失败', '场景1 字符串兼容 → 默认标题');
assert(els.diagBtn.style.display === 'none', '场景1 字符串兼容 → 无诊断按钮');

// 场景2：db_corrupt 结构化 → 三件套（标题/详情/诊断按钮可见 + 复制诊断携带 integrity）
const diagObj = { state_db: 'corrupt', db_path: '/Users/x/.vermes/state.db', detail: 'quick_check: ...' };
els.diagBtn.style.display = 'none';
realShowError({ detail: '损坏提示', title: '数据账本已损坏', dataProtect: true, diagnostic: diagObj });
assert(els.errorTitle.textContent === '数据账本已损坏', '场景2 db_corrupt → 自定义标题');
assert(els.errorDetail.textContent === '损坏提示', '场景2 db_corrupt → 详情文本');
assert(els.diagBtn.style.display === 'inline-flex', '场景2 db_corrupt → 诊断按钮可见');
copyDiagCalled = null;
els.diagBtn.onclick && els.diagBtn.onclick();
await Promise.resolve();
assert(copyDiagCalled === diagObj, '场景2 db_corrupt → 诊断按钮复制携带 integrity');

// 场景3：普通错误（无 dataProtect）→ 诊断按钮隐藏
els.diagBtn.style.display = 'inline-flex';
realShowError({ detail: '后端超时', title: '初始化失败' });
assert(els.diagBtn.style.display === 'none', '场景3 普通错误 → 诊断按钮隐藏');
assert(els.errorTitle.textContent === '初始化失败', '场景3 普通错误 → 默认标题');

console.log(`\n${fail === 0 ? '✅' : '❌'} splash showError 渲染： ${pass} 通过 / ${fail} 失败`);
process.exit(fail === 0 ? 0 : 1);
