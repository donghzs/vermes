# 启动守卫体系设计（Vermes 桌面端）

> 设计者：主会话（独立设计，待与 WorkBuddy 版本对比）
> 日期：2026-07-28
> 启发来源：OpenSquilla #2（升级 profile 守卫）+ 我们已知的 3 个启动期真实坑
> 定位：**防御性、零风险、不阻塞主流程的启动期探针层**

---

## 0. 设计原则（铁律）

1. **守卫只"探测+报错"，不"静默退化"**。当前最大漏洞是"读不到旧数据就静默新建空库"——守卫的本职是把这个静默失败变成**可见的、可恢复的错误**。
2. **不重复造轮子**。现有 `main.js` 已有三处相关机制（清缓存、splash 路径兜底、后端健康检查），守卫是**补它们缺的"探测+报错"切面**，不是重写。
3. **零风险优先**。任何守卫失败都必须**fail-visible**（弹窗/错误页），绝不 fail-silent（静默空库/黑屏）。
4. **不碰用户业务数据**。守卫只检查"能否打开旧 profile"，不替用户删、不替用户迁（迁移是另一个 PR）。
5. **可观测**。每次守卫的运行结果写入启动日志（便于远程排查"用户说升级后数据没了"的工单）。

---

## 1. 现状核实（代码事实，非假设）

| 坑 | 现有代码 | 现状判定 |
|---|---|---|
| 脏 storage 黑屏 | `main.js:210` `clearStorageData({storages:[indexdb,localstorage,...]})` | ✅ 已有**事后自愈**（无条件清空 persist:vermes 的 IDB/LS），但**无探测**、**无区分脏/净** |
| splash 路径错位 | `main.js:222-231` 候选路径列表兜底 | ✅ 已有**路径容错**（dev/打包/app.asar 多候选） |
| 后端不可达 | `main.js:104-117` `startBackend` 15s /health 健康检查 | ✅ 已有**可达性探测**（超时→错误页） |
| **profile（state.db）读不到静默新建空库** | **无任何代码** | ❌ **缺口**：SQLite 打开失败/路径权限问题 → 静默建空库 → 用户视角"数据丢了" |

**核心缺口 = 第 4 行**。前 3 个坑 OpenSquilla 的 #2 思路能补的，正是"profile 探测防空库覆盖"——我们完全没做。

---

## 2. 四切面设计

### 切面 A：Profile 存在性 + 可读性探测（核心，补最大缺口）

**时机**：`app.whenReady()` 之后、`createWindow()` 之前（最早入口）。

**逻辑**：
```
profileDir = path.join(app.getPath('userData'), '..', '.vermes')  // 或读 VERMES_HOME env
stateDb    = path.join(profileDir, 'state.db')

if (fs.existsSync(stateDb)):
    // 旧 profile 存在 → 验证能否以 WAL/只读模式打开并查一张核心表
    try:
        open read-only → PRAGMA schema_version → SELECT count(*) FROM sessions
        成功 → profile OK，继续
    catch (e):
        // 旧 profile 存在但打不开（权限/损坏/磁盘满）
        → FAIL VISIBLE: 弹窗"检测到旧数据但无法读取：{e}。请检查文件权限或磁盘空间，不要直接删除。"
        → 阻止启动（或提供"以空库启动（将丢失旧数据）"的高级选项，默认不选）
else:
    // 无旧 profile → 首次启动，正常新建（这不是漏洞）
    继续
```

**防什么**：路径迁移/权限变更/磁盘满/DB 损坏时，SQLite 默认 `open()` 会**新建空文件**。守卫在"存在但打不开"时拦截，避免覆盖假象。

**与现有代码关系**：完全新增，不改动 `clearStorageData`（那是渲染进程缓存，与 state.db 是两回事）。

---

### 切面 B：旧库 schema 版本校验（防"新版读旧 schema 崩"）

**时机**：切面 A 通过后，读取 `state.db` 的 `user_version`（PRAGMA）。

**逻辑**：
```
expected = 14  // VERMES_state.SCHEMA_VERSION（从源码/常量读，不硬编码魔法数）
actual    = PRAGMA user_version

if (actual < expected):
    // 旧版 DB，需要迁移
    → 检查 migrations 目录是否有对应升级脚本
      有 → 提示"正在升级数据格式…"（自动 migrate，不拦）
      无 → FAIL VISIBLE: "数据格式过旧且无可用的升级脚本，请联系支持"
elif (actual > expected):
    // 新版 DB 被旧版客户端打开（降级运行场景）
    → FAIL VISIBLE: "此数据由更高版本创建，请升级 Vermes"
```

**防什么**：我们前面审计知道 `VERMES_state.SCHEMA_VERSION=14`，上游已 23。若用户用旧版客户端开新版 DB（或反之），会崩或静默建错。这正好是 OpenSquilla "升级前验证 active workspace" 的等价物。

**注意**：迁移本身不在这 PR 做（只校验+报错），但**校验失败必须可见**。

---

### 切面 C：渲染进程存储脏数据探测（升级现有自愈为"探测+条件清"）

**时机**：`createWindow()` 内、`clearStorageData` 之前。

**现状**：`main.js:210` 无条件清空 persist:vermes 的 IDB/LS。这是"宁可错杀"策略——每次启动都清，用户前端状态（如未同步的草稿）会被清掉。

**改进（可选、低风险）**：
```
// 不无条件清，而是探测"是否有已知不兼容标记"
if (hasKnownBadSchemaMarker()):   // 例如旧版写入的某个 IndexedDB store 名已废弃
    clearStorageData(...)         // 只清脏的
else:
    // 正常，不清（保留用户前端状态）
```

**判定**：这是**优化项非必需**。现有"无条件清"虽然粗暴但能用（我们就是靠它修黑屏的）。建议**本期不做 C 的精细化**，保持现有自愈，只补 A/B/D。C 留作后续优化，避免改动已验证能用的黑屏修复。

---

### 切面 D：路径/资源容错强化（splash 已做，补充 preload + backend 资源）

**时机**：窗口创建前后。

**现状**：splash 路径已有候选列表（`main.js:222-231`）。但 `preload.js` 也曾出过"build.files 漏配 → asar 内无 preload → window.vermes undefined → 微信登录跳浏览器+测试模式"的坑（我们 v2.3.6 修过）。

**补充守卫**：
```
// preload 必须存在且可读，否则明确报错而非降级到 window.open
if (!fs.existsSync(path.join(__dirname, 'preload.js'))):
    → FAIL VISIBLE: "preload.js 缺失，应用配置损坏，请重新安装"
    → 阻止启动（而非静默走 window.open 降级）
```

**防什么**：build.files 漏配导致"功能残缺但看起来能跑"的隐性退化。

---

## 3. 失败语义统一（关键设计点）

所有切面失败时，统一走一个 `failVisible(reason, recoverable)` 出口：

- **recoverable=false**（如 preload 缺失、磁盘满）：弹窗"应用配置损坏/环境异常，请重新安装或联系支持"，提供"退出"按钮。
- **recoverable=true**（如旧 profile 打不开但用户可选择空库启动）：弹窗说明理由 + 两个按钮「检查权限后重试」「以空库启动（将丢失旧数据）」，默认聚焦"重试"。
- **绝不**：`console.error` 后继续静默新建空库（当前 state.db 的隐患）。

与现有 `runInitialization` 的 `sendSplash({type:'error',...})` 打通——守卫失败也走 splash 错误页，UX 一致。

---

## 4. 不做什么（边界，避免范围蔓延）

1. **不做数据迁移逻辑**。只校验 schema 版本、报错，迁移是独立 PR。
2. **不重写现有 clearStorageData / splash 路径兜底 / 后端健康检查**。它们是好的，守卫补的是它们缺的"探测+报错"切面（A/B/D 的 profile 部分）。
3. **不碰 RAG 归一化（#5）**。那是另一份 PR，只是"可同批发版"，设计上独立。
4. **不做自动修复/自愈黑魔法**。守卫的哲学是"可见地失败"，不是"偷偷修好"。修复动作（迁移/清缓存）已是别的机制。
5. **不引入新依赖**。纯 Node/Electron API（fs/sqlite3 只读 PRAGMA），不装新包。

---

## 5. 改动清单（预估）

| 文件 | 改动 | 切面 |
|---|---|---|
| `electron/main.js` | `app.whenReady()` 后新增 `runStartupGuards()` 调用（切面 A+B+D 入口） | A/B/D |
| `electron/main.js` | 新增 `verifyProfileReadable()` / `verifySchemaVersion()` / `verifyPreloadPresent()` | A/B/D |
| `electron/main.js` | 新增 `failVisible(reason, recoverable)` 统一出口（复用 splash 错误页） | 统一 |
| `electron/main.js` | `SCHEMA_VERSION` 常量从 `VERMES_state` 同步（避免硬编码魔法数） | B |
| （不动） | `clearStorageData` / splash 路径兜底 / 后端健康检查 | — 保留 |

**预估代码量**：~80-120 行（纯守卫函数 + 一个统一失败出口），零新依赖，零业务数据写入。

---

## 6. 验证计划

1. **单元/集成（mock）**：
   - 模拟"state.db 存在但权限 000" → 断言守卫 FAIL VISIBLE、不新建空库。
   - 模拟"state.db user_version=99（未来版）" → 断言守卫报错"请升级"。
   - 模拟"preload.js 缺失" → 断言守卫拦截、不走 window.open 降级。
2. **手动回归**：
   - 正常升级（有旧 profile）→ 启动无碍、数据在。
   - 首次安装（无 profile）→ 正常新建。
   - 故意 chmod 000 state.db → 弹窗报错而非黑屏/空库。
3. **不破坏现有**：clearStorageData 自愈、splash 显示、后端健康检查仍正常。

---

## 7. 与 OpenSquilla #2 的对齐 / 差异

| OpenSquilla #2 | 我们的设计 |
|---|---|
| 升级前验证 active workspace 存在性 | 切面 A（profile 可读性探测） |
| 防止升级后误建空库覆盖身份/记忆/聊天 | 切面 A（存在但打不开→拦截，不新建） |
| 卸载保留 profile | 我们卸载走系统，不删 `~/.vermes`，天然保留（无需额外代码） |
| 清理动作明示删什么 | 切面 C 是"条件清"思路，但本期不做（保留现有无条件清） |

**差异**：我们比它多做了**切面 B（schema 版本校验）**和**切面 D（preload 资源守卫）**——因为我们的实际坑里有"新版读旧 schema 崩"和"preload 漏配隐性退化"两个它没提的特有风险。

---

## 8. 与 #5（RAG 归一化）的关系

独立 PR，但**同批发版**：两者都是"鲁棒性加固、低风险、零业务影响"。守卫体系在 `main.js`（Electron 层），#5 在 `rag_provider.py`（后端层），互不影响，可各自 PR 后同一次出包。
