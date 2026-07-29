# 启动期数据完整性守卫体系 — 最终设计稿（定稿）

> 合并来源：`design-startup-guardian.md`（主会话初稿）+ `design-startup-integrity-guards.md`（WorkBuddy 稿）
> 日期：2026-07-28
> 状态：**已定稿（2026-07-28）——用户裁定修正 D 采用方案 a（lockdown 熔断），进入实施；实施顺序 c1(G0/G2) → c2(G1+lockdown+/health) → c3(P1) → c4(P2)，G6 独立 PR**
> 审计轮次：2026-07-28 第二轮代码事实审计已并入（4 处【审计修正】标记；修正 D 已裁定：方案 a）
> 核心哲学（用户裁定）：**数据丢失零容忍**——用户数据是无价资产，是涌现自进化/自学习的素材基底；账本已坏=阻断，账本可能写错位置=提醒不阻断。

---

## 0. 设计前核验挖出的活 bug（P0，先于体系修）

**G0 · 无条件清 IndexedDB 导致每次启动丢历史图片（正在发生，非隐患）。**

事实链（已逐行验证）：
1. `electron/main.js:210` 每次启动**无条件** `clearStorageData({storages:['indexdb','localstorage',...]})`；
2. 图片剥离后**只存 IndexedDB**（`frontend/src/stores/chat-storage.js:4` `IMAGE_DB='vermes-images'`），`saveMessagesToAPI` 上送 lean 消息（仅 `_imageKeys` 引用，无图片字节）；
3. 服务端 `~/.vermes/messages/*.json` **永远无图片副本**。

→ 结论：每次冷启动 `clearStorageData` 全清 `vermes-images`，图片彻底丢失、`_imageKeys` 变死引用。文本因服务端 JSON 兜底无恙，**图片是真数据丢失、不可找回**。

**用户裁定：G0 修复 = P0，立即修。** 理由：数据丢失不仅毁用户体验，更截断自进化引擎的输入素材——零容忍。

---

## 1. 设计铁律（不可妥协）

1. **探测→报错→等人工，绝不静默新建 / 静默删除 / 自动修复**。任一守卫失败必须 **fail-visible**（弹窗/横幅），绝不 fail-silent。
2. **零容忍分级**：`db_corrupt` / `missing_with_profile` → **留在 splash 硬报错，阻断进入主界面**（用户裁定：数据丢失零容忍，不因"无关功能废掉"而放宽）；`profile_mismatch` → **进主界面 + 顶部持久横幅**（仅提醒，数据本身未坏）。
3. **不引入新依赖**。纯 Node/Electron API + SQLite PRAGMA 只读探测。
4. **不做自动修复黑魔法**：不 rename / 不删坏库 / 不 REINDEX / 不自动 `.recover` / 不启动期迁移 / 不 VACUUM。恢复动作一律留人工（报错文案给 `sqlite3 state.db ".recover"` 建议）。
5. **复用既有资产，不重写**：现有 `clearStorageData`、`splash` 五候选路径、`startBackend` 健康检查、`_set_last_init_error`、`MSG_DB_VERSION` 全部复用/增强，不推翻。
6. **G6 独立 PR**：图片服务端落盘是新后端功能（新端点+内容寻址存储），不混入本防御性发版，避免回归面扩大。

---

## 2. 现状家底（启动链路事实，带出处）

```
Electron main (electron/main.js)
 ├─ L195 partition 'persist:vermes'
 ├─ L210 无条件 clearStorageData indexdb/localstorage/...      ← G0/G2
 ├─ L224-231 splash 五候选路径探测（已部分完成）             ← G3
 └─ runInitialization → startBackend → /health 轮询 15s(L102) ← G4
Python 后端
 ├─ vermes_constants.py:72-104 get_hermes_home：profile 错配仅 stderr 一次性警告（桌面不可见）← G5b
 ├─ hermes_state.py SessionDB.__init__：文件不存在→静默新建空库；打开失败→_set_last_init_error+raise ← G1/G5a
 └─ /health 端点【审计修正 A】真身在 vermes_cli/web_server.py:2753（@app.get("/health")），
    不在 status.py——status.py 是 /api/status 富状态端点（其 L218 会实例化 SessionDB()）。
    G4 的 integrity 字段应加在 web_server.py:2753 的 /health 上（main.js L102 轮询的就是它）。← G4/G5 输出口
前端 (frontend/src/stores/chat-storage.js)
 ├─ 消息：IDB(主) + 服务端 JSON(副本, lean) 双写
 └─ 图片：仅 IDB（vermes-images），无服务端副本               ← G0/G6
```

---

## 3. 守卫体系总览

| 守卫 | 层 | 一句话 | 优先级 | 决策 |
|---|---|---|---|---|
| **G0** 图片丢失修复 | Electron+前端 | 清 IDB 从"每次"改"版本戳门控"+ IDB 单库自愈 | **P0（立即）** | 用户裁定 |
| **G1** state.db 完整性哨兵 | Python | 启动 quick_check，四态判定，坏库→报错等人工，绝不重建 | P0 | — |
| **G2** 分区清理版本戳门控 | Electron | = G0 的 Electron 侧实现（去 indexdb、改版本戳触发） | P0 | 与 G0 同批 |
| **G3** 资源路径容错泛化 | Electron | splash 五候选抽 `resolveResource()`，覆盖 icon/preload | P2 | — |
| **G4** 启动失败诊断分级 | Electron+Python | splash 错误分类 + /health 透传 integrity | P1 | db_corrupt 留 splash |
| **G5** profile 错配可见告警 | Python | stderr 警告 → /health 字段 + UI 横幅 | P1 | 走横幅不阻断 |
| **G6** 图片服务端落盘 | 前端+后端 | 图片副本上送 /api/gui/images，消除仅 IDB 单点 | P1（**独立 PR**） | 拆出本体系 |

---

## 4. 各守卫详细设计

### G0/G2 · 分区清理版本戳门控（修活 bug，P0）

**现状问题**：`clearStorageData` 防"旧版前端不兼容 IDB schema"，只应发生在**版本变更后首启**，却在每次启动执行，误伤图片库。

**设计（electron/main.js:210 改造）**：
1. `userData/last-clean-version` 存上次清理时的 `app.getVersion()`；
2. 版本一致 → **跳过 clearStorageData**（保留 `clearCache()`，无副作用）；
3. 版本变更 → 执行清理，但 `storages` **去掉 `indexdb`**，改为前端 schema 级自愈 + 写新版本戳；
4. 前端自愈（`chat-storage.js`）：给 `openImageDB`/`openMsgDB` 补 `onerror → indexedDB.deleteDatabase(该库) → 重开一次` 的**单库粒度**自愈——坏哪个删哪个，图片库好好的绝不动。
5. 版本戳文件读写失败 → 视为版本变更（保守=多清一次 localstorage，不丢 IDB 图片）。

**兼容性论证（实现阶段需实测验证）**：黑屏根因是脏 localstorage/旧 serviceworker + 不兼容 IDB schema。localstorage/sw 清理保留在版本变更时；IDB 交单库自愈——防护面不缩，误伤面归零。`clearCache()` 每次执行保底热修缓存；splash:retry 可加"深度清理"人工入口。

### G1 · state.db 完整性哨兵（P0）

**探测时机**：web_server 与 gateway 进程启动早期各跑一次（非 per-request）。

**新函数 `hermes_state.startup_integrity_probe() -> dict`**（hermes_state.py 新增）：
1. `db_path.exists() & size>0 & 打不开/quick_check≠ok` → **corrupt**；
2. 文件不存在但 `~/.vermes/` 下有历史痕迹（`messages/*.json`、`sessions/`、`config.yaml`）→ **missing_with_profile**；
3. 文件不存在且目录近空 → **fresh_install**（合法，静默新建）；
4. 正常 → **ok**。

**【审计修正 B】目录口径必须动态取自 `get_hermes_home()`，禁止硬编码 `~/.vermes`**：
本机实测同时存在 `~/.hermes/state.db`（508MB，历史遗留）与 `~/.vermes/state.db`（82MB，现役）。
`get_hermes_home()` 现默认 `~/.vermes`，但 HERMES_HOME env / profile 模式可改指向。probe 的
db_path 与"痕迹目录"必须同源于 `get_hermes_home()`，否则 profile/env 用户会被误判 fresh_install
——这恰是 G1 要防的"探测目标本身错位"事故。痕迹清单同时加 `state.db-wal`/`state.db-shm`
（主库被删但 WAL 残留 = 强 missing_with_profile 信号）。

**【审计修正 C】probe 必须在任何 `SessionDB()` 实例化之前跑完判定**：
`SessionDB.__init__` 第一步就是 `mkdir(parents=True)` + `sqlite3.connect`（缺库即静默新建空文件）。
若启动链任何早于 probe 的代码路径先碰了 `SessionDB()`（如 blueprint 模块级 import、status.py 的
/api/status 被前端提前轮询），missing_with_profile 会被"洗白"成一个 0 字节新库 → probe 误判 ok/corrupt。
实施要求：probe 在 web_server 入口（create_app / __main__ 最早处）同步执行**判定与快照**，仅
quick_check 大库场景才允许后台线程续跑；且 probe 用只读 `sqlite3.connect(f"file:{p}?mode=ro", uri=True)`
探测，自身绝不触发建库。

**失败分支**：
- corrupt / missing_with_profile：结果写入模块级 `_integrity_status`，由 `/health` 暴露；`SessionDB()` 照常尝试（不改既有无 schema 触碰语义）。**绝不做**：rename/删除/REINDEX/自动恢复。

**【审计修正 D · 设计级矛盾，需用户拍板】"照常尝试" 与 "未创建新库" 的承诺互斥**：
G4 的 splash 文案向用户承诺"已停止写入，未创建新库"，但本节"`SessionDB()` 照常尝试"保留了
既有语义 = **缺库即静默新建**。missing_with_profile 场景下，后端只要活着服务 /health，任何一处
API/blueprint 触碰 `SessionDB()`（含 status.py:218 的 /api/status）都会立即建出空库——splash 挡住的
只是 UI，账本位置上已经躺了一个 0 字节新库，"未创建新库"的承诺被打破，且下次启动 probe 会把
这个空库误判为 ok。两个方案：
- **方案 a（推荐，符合零容忍裁定）**：probe 判定 corrupt/missing_with_profile 时置模块级
  `_integrity_lockdown=True`，`SessionDB.__init__` 在 lockdown 且目标缺库/坏库时 **raise 而非新建**
  （fresh_install 与正常路径零影响）。后端保持存活仅服务 /health 与静态资源，写入面全面熔断——
  文案与行为一致。
- **方案 b（弱化）**：不动 SessionDB，splash 文案改为"UI 已阻断，后端可能已生成空库占位"——
  与零容忍哲学冲突，不推荐。
**【裁定 2026-07-28】用户拍板采用方案 a**（lockdown 熔断）。排除逻辑存档：
- 方案 b 为**原则性排除**（非 UX 取舍）：空库占位后下次启动 probe 判 ok = 守卫体系自我洗白/撒谎，与"数据零容忍"哲学正面冲突；
- 方案 c 收益窄（仅 corrupt 场景有增益，missing 场景退化为 a）、成本高（SessionDB 全写路径挂 ro 分支，漏一处即带病写入），作为 P0 防御性改动不划算，**留作 v2**——待 G1 上线后有误报率数据再评估；
- 方案 a 软肋（误报锁门）以工程手段压制：quick_check 仅在文件存在且非零时才可能判 corrupt、missing_with_profile 依赖强信号组合，**c2 测试重点 = 误报率**。
- fresh_install：零动作静默放行。
- 性能：仅 `quick_check`（非 integrity_check）；4MB 以下全量，以上只验 header + `PRAGMA schema_version` 可读；哨兵放后台线程，`/health` 先返回 `integrity:"probing"`。

**与 §3.5 惰性哲学同构**：哨兵只读探测，对旧库零写入、零 schema 触碰（须通过 `test_topic_mode_schema_is_not_auto_migrated_on_open` 不变量）。

### G3 · 资源路径容错泛化（P2）

`electron/main.js:224-231` 五候选探测抽成 `resolveResource(relPath): string|null`，用于 splash.html/icon/preload.js；找不到时 `console.error` 带全部候选路径（当前 icon 找不到静默 undefined）。

### G4 · 启动失败诊断分级 + db_corrupt 分流（P1，用户决策版）

**分流方案选型（a/b/c 对比，2026-07-28 审定）**

db_corrupt / missing_with_profile 发生后，系统还剩什么能力？三方案本质是在「可用性」与「事故现场保全」间取舍：

| 维度 | 方案 a：lockdown 熔断 | 方案 b：弱化文案（新建空库继续） | 方案 c：只读降级 |
|---|---|---|---|
| state.db 写入 | ❌ 熔断(raise) | ✅ 新建空库继续写 | ❌ 熔断 |
| 渠道会话读取 | ❌ 拒读坏库 | ⚠️ 读到空库（假象） | ⚠️ corrupt 时尽力读 |
| web 对话(messages/*.json) | ❌ 被 splash 挡住 | ✅ 可用 | ✅ 可用 |
| gateway 渠道收发 | ❌ 同一 lockdown | ⚠️ 新消息写进空库→分叉 | ❌ 写熔断 |
| 事故可恢复性 | ✅ 磁盘零写入，随时可换盘/恢复 | ❌ 空库占位+新数据写入，恢复要三方合并 | ✅ 保住 |
| 下次启动判定 | ✅ 仍是 corrupt/missing | ❌ 空库被判 ok，事故自我洗白 | ✅ 仍正判 |

**排除理由**：
- **b 排除**：用可用性换现场，与「数据丢失零容忍、数据是自进化素材基底」直接冲突；且「下次启动误判 ok」意味着守卫体系自己撒谎，结构性不可接受。
- **c 排除**：收益（corrupt 时还能看历史）真实但窄；成本（SessionDB 全部写方法挂 ro 分支，漏一个即带病写入；missing_with_profile 退化成 a；corrupt 库 mode=ro 读可能中途崩）与 P0 防御性改动定位不符。留作 v2：等 G1 上线跑一段、误报率有数据后再评估是否补只读通道。
- **a 采纳**：唯一软肋是误报锁门，但可用工程手段压到极低（quick_check 仅在文件存在且非零时才可能判 corrupt；missing_with_profile 的痕迹清单是强信号组合）。**误报率是 c2 测试重点**（目录同源 + 只读连接，见 §7）。

**UX 补丁（splash 错误页三件套，采纳）**：刚性阻断的挫败感用「它在保护我」的感知抵消——
1. 受影响文件的**完整路径**；
2. **「你的数据未被修改」明确承诺**（方案 a 在任意写入前 raise/阻断，承诺成立）；
3. 一键**「打包诊断信息」按钮**（复制 probe 结果 + 路径到剪贴板，便于发支持）。

`startBackend` resolve 改 `{ok, reason}`；splash 错误按 reason 分级：

| reason | 文案要点 | 分流 |
|---|---|---|
| `exe_missing` | 安装包不完整 | splash 报错 |
| `spawn_error` | 后端无法启动（附 err.message） | splash 报错 |
| `timeout_port_busy` | 9119 被**非 Vermes 进程**占用 | splash 报错（校验响应体 vermes 标识） |
| `timeout` | 纯超时 | splash 报错 + 重试按钮 |
| **`db_corrupt` / `db_missing_with_profile`** | **/health 透传 integrity：明确告知"检测到历史数据但账本无法读取，已停止写入，未创建新库"+ 恢复命令** | **⛔ 留在 splash 硬报错，阻断进主界面（用户零容忍裁定）** |

`/health`（**vermes_cli/web_server.py:2753**，【审计修正 A】非 status.py）响应体扩展：`{status:"ok", app:"vermes", integrity:{state_db:"ok|corrupt|missing_with_profile|fresh_install", profile_mismatch:bool, detail:str}}`。main.js 轮询处（L102）现只判 `resp.ok`——需改为解析 body 后分流（integrity 异常时 splash 转报错而非 resolve(true) 进主界面）。

### G5 · profile 错配升级为可见告警（P1，横幅不阻断）

`vermes_constants.py:72-104` 的一次性 stderr 警告 → 同时写模块级 `_profile_fallback_active=True`（import-safe，零 IO）；web_server /health 并入 `integrity.profile_mismatch`；**UI 顶部持久横幅**（非 splash 阻断）："当前激活 profile 为 X，但进程正在使用默认 profile——数据可能写错位置"。不改变回退行为本身（30+ module-level caller 约束不变）。

### G6 · 图片服务端落盘（P1，**独立 PR**）

`persistMessages` 剥离图片后，除 `saveImage`(IDB) 外增 `POST /api/gui/images/{key}`（幂等、内容寻址、落 `~/.vermes/images/`）；`loadImage` miss 回源 `GET /api/gui/images/{key}` 并回填 IDB。历史已丢图片无法找回（G0 已清），`_imageKeys` 死引用优雅降级为占位图。`存量死引用不做批量清扫`——读到时降级，符合惰性哲学。

---

## 5. 失败分支行为矩阵（体系灵魂）

| 场景 | 现状 | 守卫后（用户裁定版） |
|---|---|---|
| state.db 损坏 | 静默 raise→各调用方降级，UI 无感知 | /health 报警→**splash 硬报错+恢复指引；不新建不删除** |
| 旧 profile 在、state.db 没了 | 静默新建空库（"升级丢会话"观感） | **splash 硬报错**"检测到历史数据但账本缺失"；渠道读自然降级 |
| 全新安装 | 新建空库 | 不变（fresh_install 静默放行） |
| 版本未变启动 | 清空 IDB（**每次丢图**） | **零清理**（G0 修） |
| 版本变更首启 | 同上 | 清 localstorage/sw；IDB 单库自愈 |
| 某个 IDB 库 schema 坏 | 全部 IDB 被清 | 只删重建坏的那个库 |
| 9119 被外进程占用 | "可能已在外部运行"误判 | 校验响应体 vermes 标识，非我方→明确报错 |
| profile 错配 | stderr 警告（不可见） | /health + **UI 横幅（不阻断）** |

---

## 6. 非目标（明确不做）

- ❌ 自动修复/恢复损坏 DB（`.recover` 留人工）
- ❌ 启动期 schema 迁移或 VACUUM（§3.5 惰性铁律）
- ❌ state.db 自动备份轮转（另行讨论）
- ❌ gateway 进程 UI 告警通道（仅日志 + 让 web /health 代述）
- ❌ G6 混入本防御性发版（独立 PR）

---

## 7. 实施拆分与测试

| Commit | 内容 | 量级 | 文件 |
|---|---|---|---|
| **c1 (P0)** | G0/G2 版本戳门控 + IDB 单库自愈（修活 bug） | main.js ~30 + chat-storage ~20 | `electron/main.js:210`、`frontend/src/stores/chat-storage.js` |
| **c2 (P0)** | G1 哨兵（含修正 B/C/D：get_hermes_home 同源 + probe 先行 + lockdown）+ /health integrity 字段 + G5 标志 | hermes_state ~80 + web_server ~15 | `hermes_state.py`、`vermes_cli/web_server.py:2753`、`vermes_constants.py` |
| **c3 (P1)** | G4 splash 诊断分级 + db_corrupt 留 splash + G5 横幅 | main.js/splash ~40 + 前端组件 | `electron/main.js:102,146`、`splash.html`、前端横幅组件 |
| **c4 (P2)** | G3 resolveResource 重构 | 纯重构 | `electron/main.js` |
| **c5 (P1, 独立 PR)** | G6 图片服务端落盘 | 前后端各 ~40 | `vermes_cli/web_server.py` + `chat-storage.js` |

**测试要点**：
- G1：pytest 构造坏库/空目录/带痕迹目录三态断言 probe 返回；**必须**跑 `test_topic_mode_schema_is_not_auto_migrated_on_open` 证明哨兵只读；新增断言：①probe 自身（mode=ro）不落任何新文件；②lockdown 下 `SessionDB()` raise 且磁盘无 0 字节新库（修正 C/D 回归）；③HERMES_HOME 指向自定义目录时 probe 探测同一目录（修正 B 回归）。
- G0/G2：两次启动模拟（版本戳同/异）断言 `clearStorageData` 调用与否 + 图片库不被清。
- G4：mock /health 四种 integrity 状态，断言 splash 分流正确（corrupt→阻断，mismatch→横幅）。
- 回归：clearStorageData 自愈、splash 显示、后端健康检查仍正常。

---

## 8. 风险登记

| 风险 | 缓解 |
|---|---|
| quick_check 超大库拖慢启动 | 4MB 阈值分级；哨兵后台线程，/health 先返 `probing` |
| 版本戳门控后同版本内前端热修脏缓存不被清 | `clearCache()` 保留每次；splash:retry 加"深度清理"人工入口 |
| 横幅造成新装用户恐慌 | fresh_install 严格静默；文案仅 corrupt/missing/mismatch 三态出现 |
| G0 修复后旧版用户已丢的图片无法找回 | 文档明示；G6 落盘后新图不再丢；死引用降级占位图 |

---

## 9. 与 OpenSquilla #2 的对齐 / 差异

| OpenSquilla #2 | 本体系 |
|---|---|
| 升级前验证 active workspace 存在性 | G1（四态 probe） |
| 防止升级后误建空库覆盖 | G1（missing_with_profile→报错不新建） |
| 卸载保留 profile | 天然保留（不删 `~/.hermes`） |
| 清理动作明示删什么 | G2（版本戳门控，清什么显式） |

**差异（我们更严）**：G0（正在发生的图片丢失，它没提）、G1 四态（它两态）、G4 db_corrupt 零容忍阻断（它默认进主界面）、G5 profile 错配可见（它未覆盖）、G6 图片单点消除（它未覆盖）。
