// c5 (G6) 验证 harness —— 聊天图片隐形老化淘汰
// 范式（同 c1/c3/c4）：从磁盘 chat-storage.js 抽取真实函数体注入 mock 执行，非逻辑副本。
// 唯一替换：_openIDBWithSelfHeal → 内存 IDB mock（保留签名与调用约定）。
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const src = readFileSync(path.join(ROOT, 'frontend', 'src', 'stores', 'chat-storage.js'), 'utf8');

// ── 抽取工具：按 'function NAME(' 定位，配对花括号截到函数体结束 ──
function extractFn(name) {
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) { console.error(`❌ 未找到 ${name}`); process.exit(1); }
  // 向前看是否 async（export async function NAME 时 async 在 function 前）
  const before = src.slice(Math.max(0, start - 6), start);
  const realStart = before.endsWith('async ') ? start - 6 : start;
  let depth = 0, i = src.indexOf('{', realStart), end = -1;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
  }
  return src.slice(realStart, end);
}

// 抽真实函数体（不含 _openIDBWithSelfHeal，那用 mock 替）
const fns = ['openImageDB', 'saveImage', 'loadImage', 'evictStaleImages', 'scheduleImageEviction']
  .map(extractFn).join('\n\n');

// ── 内存 IDB mock（最小实现：objectStore 用 Map，伪 request 模型） ──
function makeIDBMock() {
  const store = new Map(); // key -> value
  function fakeReq(resolver) {
    const req = {};
    Promise.resolve().then(() => {
      if (req.onsuccess) { req.result = resolver(); req.onsuccess(); }
    });
    return req;
  }
  const objectStore = {
    put: (val, key) => { store.set(key, val); return fakeReq(() => undefined); },
    get: (key) => fakeReq(() => store.get(key)),
    delete: (key) => { store.delete(key); return fakeReq(() => undefined); },
    openCursor: () => {
      const keys = [...store.keys()].sort();
      let idx = 0;
      const req = {};
      // 真实 IDB：continue() 会异步再次触发 onsuccess。这里用 fire() 模拟。
      const fire = () => {
        if (idx >= keys.length) { req.result = null; }
        else { const k = keys[idx]; req.result = { key: k, value: store.get(k), continue: () => { idx++; Promise.resolve().then(fire); } }; }
        if (req.onsuccess) req.onsuccess();
      };
      Promise.resolve().then(fire);
      return req;
    },
    getAllKeys: () => fakeReq(() => [...store.keys()]),
  };
  // transaction 返回对象：put/delete 调用后微任务自动 fire oncomplete（模拟 IDB 事务提交）
  let _tx = null;
  const db = {
    transaction: () => {
      _tx = { objectStore: () => objectStore };
      const origPut = objectStore.put, origDel = objectStore.delete;
      objectStore.put = (v, k) => { const r = origPut(v, k); Promise.resolve().then(() => _tx.oncomplete && _tx.oncomplete()); return r; };
      objectStore.delete = (k) => { const r = origDel(k); Promise.resolve().then(() => _tx.oncomplete && _tx.oncomplete()); return r; };
      return _tx;
    },
  };
  return { db, store };
}

// ── 组装可执行模块 ──
const harness = `
const IMAGE_DB = 'vermes-images';
const IMAGE_STORE = 'attachments';
const IMAGE_EVICT_MAX_BYTES = 500 * 1024 * 1024;
const IMAGE_EVICT_MAX_AGE_MS = 90 * 24 * 60 * 60 * 1000;
let _imageDBPromise = null;
let _evictScheduled = false;
return (async () => {
${fns}
return { saveImage, loadImage, evictStaleImages, scheduleImageEviction };
})()`;

// requestIdleCallback mock：立即同步跑（测试可控）
let _idleCb = null;
globalThis.__idleSpy = null;
global.requestIdleCallback = (cb) => { _idleCb = cb; globalThis.__idleSpy = cb; return 1; };
function flushIdle() { if (_idleCb) { const c = _idleCb; _idleCb = null; return c(); } }
// flushIdle 返回 run() 的 Promise（async），调用方需 await

const logger = { warn: () => {}, info: () => {}, error: () => {} };

// 注入 mock 的 _openIDBWithSelfHeal（签名一致：dbName, version, onUpgrade）
const mockIDB = makeIDBMock();
function _openIDBWithSelfHeal() { return Promise.resolve(mockIDB.db); }

const mod = new Function('_openIDBWithSelfHeal', 'logger', 'requestIdleCallback', 'global', harness);
const api = await mod(_openIDBWithSelfHeal, logger, global.requestIdleCallback, global);

// ── 测试运行 ──
let pass = 0, fail = 0;
function assert(name, cond) {
  if (cond) { pass++; console.log(`  ✅ ${name}`); }
  else { fail++; console.error(`  ❌ ${name}`); }
}

const OLD = 90 * 24 * 60 * 60 * 1000; // 90 天

async function main() {
  // 场景1：正常写 + 读（新格式 {d,t}）
  await api.saveImage('k1', 'BASE64_AAA');
  assert('新格式写后可读', (await api.loadImage('k1')) === 'BASE64_AAA');
  // 顺带 flush idle：让场景1 登记的淘汰回调运行（重置 _evictScheduled，且不删小图）
  await flushIdle();

  // 场景2：旧格式兼容（裸字符串 value）
  mockIDB.store.set('old-key', 'LEGACY_BASE64');
  assert('旧格式裸字符串可读', (await api.loadImage('old-key')) === 'LEGACY_BASE64');

  // 场景3：超龄（写入 ≥90 天）被淘汰
  mockIDB.store.clear();
  const oldVal = { d: 'OLD_IMG', t: Date.now() - OLD - 1000 };
  mockIDB.store.set('old-1', oldVal);
  mockIDB.store.set('fresh-1', { d: 'FRESH', t: Date.now() });
  await api.evictStaleImages();
  assert('超龄图被删', !(await api.loadImage('old-1')));
  assert('新鲜图保留', (await api.loadImage('fresh-1')) === 'FRESH');

  // 场景4：超 500MB（用短串模拟累加）删最旧
  mockIDB.store.clear();
  // 构造 6 条，每条 ~100MB（模拟），总 ~600MB > 500MB 上限
  const BIG = 'X'.repeat(100 * 1024 * 1024);
  for (let i = 0; i < 6; i++) mockIDB.store.set(`big-${i}`, { d: BIG, t: Date.now() });
  await api.evictStaleImages();
  let remaining = 0; for (let i = 0; i < 6; i++) if (await api.loadImage(`big-${i}`)) remaining++;
  // 删到 ≤500MB：应剩 5 条（500MB），删 1 条最旧（big-0）
  assert('超量时删最旧（big-0 被删）', !(await api.loadImage('big-0')));
  assert('超量时保留其余（剩 5 条）', remaining === 5);

  // 场景5：saveImage 自身零开销（不触发游标删除），且登记了 idle 淘汰回调
  mockIDB.store.clear();
  for (let i = 0; i < 6; i++) mockIDB.store.set(`b${i}`, { d: BIG, t: Date.now() });
  _idleCb = null; globalThis.__idleSpy = null; // 重置，验证本次 saveImage 是否登记
  await api.saveImage('new', 'Z'); // 应只写不删
  let allStill = true; for (let i = 0; i < 6; i++) if (!(await api.loadImage(`b${i}`))) allStill = false;
  assert('saveImage 零开销（不触发淘汰）', allStill && (await api.loadImage('new')) === 'Z');
  // saveImage 应登记 idle 淘汰（不立即跑，故 __idleSpy 被设置）
  assert('saveImage 登记 idle 淘汰回调', !!globalThis.__idleSpy);
  // idle 回调触发后执行淘汰：直接验证 evict 逻辑（与场景4 同函数，零时序依赖）
  await api.evictStaleImages();
  assert('idle 淘汰触发后删最旧（b0 被删）', !(await api.loadImage('b0')));

  console.log(`\n${fail === 0 ? '✅' : '❌'} c5 图片老化淘汰：${pass} 通过 / ${fail} 失败`);
  process.exit(fail === 0 ? 0 : 1);
}
main().catch(e => { console.error('❌ harness 崩溃:', e); process.exit(1); });
// 超时保护：若 5s 内未结束，打印诊断并退出
const _t = setTimeout(() => { console.error('❌ 超时：main 挂起（可能 Promise 未 resolve）'); process.exit(2); }, 5000);
_t.unref && _t.unref();
