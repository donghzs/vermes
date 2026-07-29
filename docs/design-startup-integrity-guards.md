# 设计稿：启动期数据完整性守卫体系（Startup Integrity Guards）

> 日期：2026-07-28
> 状态：设计稿，待审
> 来源：OpenSquilla 竞品调研借鉴点 #2 的体系化扩展（用户建议 A：做成体系而非零散补）
> 原则：**探测→报错→等待人工，绝不静默新建/静默降级/自动删除**；与 §3.5 惰性列同构——不急切改写任何旧数据。

---

## 0. 一个设计前核验中发现的活 bug（P0，先于本设计修）

**G0 · 无条件清 IndexedDB 导致每次启动丢历史图片。**

事实链（已逐行验证）：

| # | 事实 | 位置 |
|---|---|---|
| 1 | `main.js` 每次启动**无条件** `clearStorageData({storages:['indexdb', ...]})` | `electron/main.js:210` |
| 2 | 用户消息中的 base64 图片被剥离后**只存 IndexedDB**（`vermes-images`），`saveMessagesToAPI` 上送的是 lean 消息（`_imageKeys` 引用，无图片数据） | `chat-session.js:138-151`、`chat-storage.js:42-48` |
| 3 | 服务端 `~/.vermes/messages/*.json` 因此**永远没有图片副本** | 同上 |

→ 结论：黑屏修复（清脏 IDB）的代价是**每次启动全量清空 `vermes-images`**，历史会话中的图片全部变成死引用（`_imageKeys` 指向已删除的键）。消息文本无恙（服务端 JSON 兜底），**图片是真数据丢失**。这不是"可能发生"，是当前每次启动都在发生。

修复方向见 G2（版本戳门控清理）+ G6（图片服务端落盘，中期）。

---

## 1. 背景：三个已知启动期坑 + 借鉴点

历史上已撞过的同类问题（全部是"启动期读不到正确数据 → 静默退化/新建"）：

1. **persist:vermes 分区脏数据 → 黑屏**（修复方式=无条件清分区，引出上面的 G0）；
2. **splash 路径错位 → 测试模式**（已修：五候选路径探测，`main.js:224-231`）；
3. **VERMES_HOME 回退错 profile → 数据写错库**（现状=一次性 stderr 警告，`vermes_constants.py:72-104`，但 stderr 在打包桌面端用户不可见）。

OpenSquilla 借鉴点 #2 的核心思想："探测到旧 profile 存在但打不开 → **报错等待人工**，而非新建空库继续跑"。本设计将其扩展为覆盖 Electron 层、Python 后端层、前端层的完整守卫体系。

## 2. 现状家底（启动链路事实，逐条带出处）

```
Electron main (main.js)
 ├─ createWindow → partition 'persist:vermes' (L195)
 ├─ 无条件 clearStorageData indexdb/localstorage/... (L210)   ← G0/G2
 ├─ splash 五候选路径探测，找不到→直接 loadURL 后端 (L224-236) ← G3 已部分完成
 └─ runInitialization → startBackend
      ├─ spawn python，打包时 env.VERMES_HOME=~/.vermes (L68)
      ├─ /health 轮询 15s (L98-116)
      └─ 失败分支：单一泛化文案"后端服务启动失败…" (L152-155) ← G4
Python 后端 (web_server / VERMES_state)
 ├─ get_vermes_home()：profile 错配仅 stderr 一次性警告 (vermes_constants.py:72-104) ← G5b
 ├─ SessionDB.__init__：mkdir + connect + _init_schema (VERMES_state.py:472-511)
 │    · 文件不存在 → 静默新建空库（sqlite 语义）                ← G5a
 │    · 打开失败 → _set_last_init_error + raise，调用方各自降级
 └─ /health：仅存活探测，不暴露数据面健康 (web_server.py:2754)  ← G4/G5 的输出口
前端 (chat-storage / chat-session)
 ├─ 消息：IDB(主) + 服务端 JSON(副本，lean) 双写 (chat-session.js:149-151)
 └─ 图片：仅 IDB，无服务端副本 (chat-session.js:141)           ← G0/G6
```

关键既有资产（守卫要复用，不重建）：
- `VERMES_state._set_last_init_error / _last_init_error`——DB 打不开的原因已被捕获，只是没有出口到 UI；
- `MSG_DB_VERSION`（chat-storage.js:10）——前端消息库已有 schema 版本机制；
- splash 的 `splash:message` / `splash:retry` IPC 通道——错误展示与重试 UI 已存在。

## 3. 守卫体系总览

| 守卫 | 层 | 一句话 | 优先级 |
|---|---|---|---|
| **G1** state.db 完整性哨兵 | Python | 启动时 `PRAGMA quick_check`，坏库→报错等待人工，绝不重建 | P0 |
| **G2** 分区清理版本戳门控 | Electron | 清 IDB 从"每次启动"改为"仅版本变更时"，并豁免图片库 | P0（修 G0） |
| **G3** 资源路径容错泛化 | Electron | splash 五候选模式抽成 `resolveResource()`，覆盖 icon/preload | P2 |
| **G4** 启动失败诊断分级 | Electron+Python | splash 错误从单一文案改为分类诊断（含 G1/G5 透传） | P1 |
| **G5** profile 错配升级为可见告警 | Python | VERMES_HOME 回退错 profile：stderr 警告 → /health 字段 + UI 横幅 | P1 |
| **G6** 图片服务端落盘 | 前端+后端 | 图片副本上送 `/api/gui/images`，消除"仅 IDB"单点 | P1（中期） |

## 4. 各守卫详细设计

### G1 · state.db 完整性哨兵（OpenSquilla #2 的直接对应物）

**探测时机**：web_server 与 gateway 进程启动早期各跑一次（非每次 `SessionDB()`——blueprints 是 per-request 构造，哨兵必须一次性）。

**探测逻辑**（新函数 `VERMES_state.startup_integrity_probe() -> dict`）：
1. `db_path.exists()` 且 `size > 0` 且 sqlite3 打不开 / `PRAGMA quick_check` ≠ ok → **corrupt**；
2. 文件不存在但 `~/.vermes/` 下存在其他历史痕迹（`messages/*.json`、`sessions/`、`config.yaml`）→ **missing_with_profile**（旧 profile 在、账本没了——最接近 OpenSquilla 防的"升级后误新建空库"场景）；
3. 文件不存在且目录也近空 → **fresh_install**（合法，正常新建）；
4. 正常 → **ok**。

**失败分支行为**：
- **corrupt / missing_with_profile**：结果写入模块级 `_integrity_status`，由 `/health` 暴露（见 G4）；`SessionDB()` 照常尝试（不改 §472-511 的语义）。**绝不做的事**：不 rename、不删除、不 `REINDEX`、不自动从 WAL 恢复——一切恢复动作留给人工（报错文案给出 `sqlite3 state.db ".recover"` 建议命令）。
- **fresh_install**：零动作，静默放行。首次安装不该看到任何警告。
- quick_check 对大库有成本 → 仅 `quick_check`（非 `integrity_check`），且 4MB 以下才跑全量、以上只验 header + `PRAGMA schema_version` 可读。

**与 §3.5 一致性**：哨兵是**只读探测**，对旧库零写入、零 schema 触碰，与惰性列"不急切改写旧库"完全同构。

### G2 · 分区清理版本戳门控（修 G0）

**现状问题**：`clearStorageData` 防的是"旧版本前端留下的不兼容 IDB schema"——这只可能发生在**版本变更后首启**，却在每次启动执行，误伤图片库。

**设计**：
1. `userData/last-clean-version` 文件存上次清理时的 `app.getVersion()`；
2. 启动时版本一致 → **跳过全部 clearStorageData**（clearCache 保留，成本低无副作用）；
3. 版本变更 → 执行清理，但 storages 里**去掉 `indexdb`**，改为通知前端做 schema 级自愈（见下）+ 写入新版本戳；
4. 前端自愈替代粗暴清库：`openMsgDB` 的 `onupgradeneeded`/`onerror` 已是 schema 门；给 `openImageDB`/`openMsgDB` 补 `onerror → indexedDB.deleteDatabase(该库) → 重开一次` 的**单库粒度**自愈——坏哪个删哪个，图片库好好的就绝不动。

**失败分支**：版本戳文件读写失败 → 视为版本变更（保守方向=多清一次 localstorage，也不丢 IDB 图片）。

**兼容性论证**：黑屏根因是"脏 localstorage/旧 serviceworker + 不兼容 IDB schema"。localstorage/serviceworker 清理保留在版本变更时；IDB 交给单库自愈——防护面不缩，误伤面归零。

### G3 · 资源路径容错泛化（P2，顺手）

splash 的五候选探测（`main.js:224-231`）抽成 `resolveResource(relPath): string|null`，用于 splash.html、icon、preload.js。找不到时 `console.error` 带全部候选路径（当前 icon 找不到是静默 undefined）。纯重构，行为不变。

### G4 · 启动失败诊断分级

**现状**：`startBackend` 三种失败（exe 不存在 / spawn error / 15s 超时）都 resolve(false)，splash 只有一句泛化文案。

**设计**：`startBackend` resolve 改为 `{ok, reason}`；splash 错误文案按 reason 分级：

| reason | 文案要点 | 可行动建议 |
|---|---|---|
| `exe_missing` | 安装包不完整 | 重新安装 |
| `spawn_error` | 后端无法启动（附 err.message） | 查权限/杀毒软件 |
| `timeout_port_busy` | 15s 超时且 9119 端口被**非 Vermes 进程**占用（fetch /health 通了但响应体不含 vermes 标识） | 关闭占用进程 |
| `timeout` | 纯超时 | 重试按钮（已有 splash:retry） |
| `db_corrupt` / `db_missing_with_profile` | /health 通了但 G1 报警（health 响应体透传 `integrity` 字段） | **明确告知"检测到历史数据存在但账本无法读取，已停止写入，未创建新库"** + 恢复命令 |

`/health` 响应体扩展：`{status:"ok", app:"vermes", integrity:{state_db:"ok|corrupt|missing_with_profile|fresh_install", profile_mismatch:bool, detail:str}}`。main.js 轮询处（L102）读取并分流。**注意**：`db_corrupt` 时 /health 仍返回 200（进程活着），只是 integrity 字段报警——Electron 层决定是进主界面+横幅，还是留在 splash 报错。**决策：进主界面 + 顶部持久横幅**（web 会话走 messages/*.json 不依赖 state.db，一刀切挡在 splash 会把无关功能全废掉）。

### G5 · profile 错配升级为可见告警

`vermes_constants.py:72-104` 的一次性 stderr 警告，桌面端用户永远看不到。设计：警告触发时**同时**把标志写入模块级 `_profile_fallback_active = True`（import-safe，零 IO），web_server 的 /health 把它并进 `integrity.profile_mismatch`。UI 横幅文案："当前激活 profile 为 X，但进程正在使用默认 profile——数据可能写错位置"。不改变回退行为本身（30+ module-level caller 约束不变，与上游 #18594 注释一致）。

### G6 · 图片服务端落盘（中期，消除单点）

`persistMessages` 剥离图片后，除 `saveImage`(IDB) 外增加 `POST /api/gui/images/{key}`（幂等，内容寻址，落 `~/.vermes/images/`）；`loadImage` miss 时回源 `GET /api/gui/images/{key}` 并回填 IDB。历史已丢图片无法找回（G0 已清），但 `_imageKeys` 死引用要优雅降级为占位图而非破图标。**存量死引用不做批量清扫**——读到时降级即可，符合惰性哲学。

## 5. 失败分支行为矩阵（体系的灵魂）

| 场景 | 现状行为 | 守卫后行为 |
|---|---|---|
| state.db 损坏 | 静默 raise→各调用方降级，UI 无感知直到功能坏 | /health 报警→UI 横幅+恢复指引；**不新建不删除** |
| 旧 profile 在、state.db 没了 | 静默新建空库（"升级丢会话"的观感） | 横幅告警"检测到历史数据但账本缺失"；渠道读功能自然降级 |
| 全新安装 | 新建空库 | 不变（fresh_install 静默放行） |
| 版本未变启动 | 清空 IDB（**每次丢图**） | 零清理 |
| 版本变更首启 | 同上 | 清 localstorage/sw；IDB 单库自愈 |
| 某个 IDB 库 schema 坏 | 连带全部 IDB 被清 | 只删重建坏的那个库 |
| 9119 被外进程占用 | "可能已在外部运行"误判直接连 | 校验响应体 vermes 标识，非我方→明确报错 |
| profile 错配 | stderr 警告（不可见） | /health + UI 横幅 |

## 6. 非目标（明确不做）

- ❌ 自动修复/自动恢复损坏 DB（`.recover` 留人工）；
- ❌ 启动期做任何 schema 迁移或 VACUUM（与 §3.5 惰性铁律冲突）;
- ❌ state.db 自动备份轮转（另行讨论，不混入本体系）；
- ❌ gateway 进程的 UI 告警通道（gateway 无 UI，仅日志 + 让 web /health 代述）。

## 7. 实施拆分与测试

| Commit | 内容 | 量级 |
|---|---|---|
| c1 (P0) | G2 版本戳门控 + IDB 单库自愈（修 G0 活 bug） | main.js ~30 行 + chat-storage ~20 行 |
| c2 (P0) | G1 哨兵 + /health integrity 字段 + G5 标志 | VERMES_state ~60 行 + web_server ~15 行 |
| c3 (P1) | G4 splash 诊断分级 + 前端横幅 | main.js/splash ~40 行 + 前端组件 |
| c4 (P1) | G6 图片服务端落盘 | 前后端各 ~40 行 |
| c5 (P2) | G3 resolveResource 重构 | 纯重构 |

测试要点：G1 用 pytest 构造坏库/空目录/带痕迹目录三态断言 probe 返回；**必须**跑 `test_topic_mode_schema_is_not_auto_migrated_on_open` 证明哨兵只读（quick_check 不改 schema_version）；G2 用两次启动模拟（版本戳同/异）断言 clearStorageData 调用与否。

## 8. 风险登记

| 风险 | 缓解 |
|---|---|
| quick_check 在超大库上拖慢启动 | 4MB 阈值分级；哨兵放后台线程，/health 先返回 `integrity:"probing"` |
| 版本戳门控后，同版本内前端热修的脏缓存不再被清 | `clearCache()` 保留每次执行；splash:retry 可加"深度清理"入口（人工触发全清） |
| 横幅告警造成新装用户恐慌 | fresh_install 严格静默；文案只在 corrupt/missing_with_profile/mismatch 三态出现 |
