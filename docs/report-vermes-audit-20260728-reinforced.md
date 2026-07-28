# Vermes 家底复合审计（加固版）— 2026-07-28，c1–c4 上线后

> 本文在 2026-07-28 初版草稿基础上，对每条量化论断做**真实代码/仓库取证**，修正口径错误与事实偏差，并输出带证据的优化优先级，为下一步优化方案做准备。
> 取证环境：本地工作树 `feature/c5-g6-image-evict`（含 c5 提交 171fa7e12），排除 `.venv / node_modules / dist / dist-electron / .git` 后统计生产源码。

---

## 0. 综合评分（修正后）

**8.1 / 10**（与初稿一致）——功能面惊人、安全底盘扎实、测试密度罕见，但架构债（上帝文件、迁移稀疏）与桌面 UX 打磨（更新断路、i18n、无障碍）有清晰欠账。

> 评分未变，但**支撑评分的数字多数需校准**（见 §2 证据表）。结论稳健，论据需诚实地改。

---

## 1. 家底硬数据（全部重新取证）

| 指标 | 初稿声称 | 实测（代码取证） | 备注 |
|---|---|---|---|
| 生产源码 Python 总行数 | ~42 万（hermes_cli/agent/gateway/tools 四目录） | **544,006 行**（861 个 .py，纯源码树，排除 tests/dist/依赖） | 初稿口径漏算 `cli.py`(13.7k)、`run_agent.py`(5k)、`tui_gateway`(6.6k) 等顶层/旁支；若含 tests 则总 ~100 万行 |
| 测试总量 | 46 万行 / 1309 文件 | **458,563 行 / 1309 文件**（仅 `tests/`） | 精确吻合 |
| 测试:生产比 | ≈1:1 | **≈0.85:1**（45.9万 / 54.4万） | 仍极罕见，但非严格 1:1 |
| commit 总数 | 889 | **890**（本地含 1 个 c5 提交） | 吻合 |
| 7 月 commit 数 | 401 | **402** | 吻合 |
| 渠道 adapter | 35 个 | **25 个** `BasePlatformAdapter` 子类 | 初稿高估 10 个 |
| 上帝文件(>3000行) | 20 个 | **纯源码树 25+ 个**（含 cli.py 13681 / hermes_cli/main.py 13193） | 初稿低估 |
| except Exception | 5456 处 | **5301 处**（纯源码） | 吻合 |
| 静默吞(except:pass) | 247 处 | **8 处**（裸 `except:` 6 + `except X: pass` 2） | **初稿严重高估**；"247"应为把 `except: logger.warn(...)` 等"记日志但吞异常"也算入，需分两级（见 §3） |
| 前端 i18n | 零，locales 16 种只服务 CLI | frontend 下 vue-i18n/`$t(`/locales 引用 **0 处**；根 `locales/` 含 11+ yaml（af/de/en/es/fr/ga/hu/it/ja/ko…）服务 CLI | 精确吻合 |
| migrations 机制 | 缺位（靠内联 ALTER） | **`migrations/` 目录存在**（`2.0.3_to_2.0.5.py` 5336行 + README），`hermes_cli/update_manager.py:620 run_migrations()` 驱动，`claw.py` 有 `cmd_migrate` 入口；但 `hermes_state.py: SCHEMA_VERSION=14` 仍内联 ALTER 兜底 | **初稿"缺位"论断错误**；实为"双轨并存、正式迁移文件稀疏" |

---

## 2. 五维分项（基于取证）

### 安全与数据完整性 — 9.0（最强项，原 8.9 上调）
- `tools/approval.py` 分层审批 + `HARDLINE` 红线（grep 命中 31 处字面，含连 YOLO 也不可绕的守卫）；无硬编码密钥；`osv-scanner` + 供应链审计 + secret 扫描三重 CI 兜底。
- **c1–c4 启动守卫是点睛**：双进程（main.js / gateway）写熔断、splash 硬阻断、G0 自毁 bug 已死、只读 `mode=ro` 哨兵零写入。
- 唯一已知潜伏口径：RAG `rag_provider.py` 向量未 L2 归一化（依赖 OpenAI 系归一化向量侥幸正确），~5 行加固，非紧急。

### 功能广度 — 8.8（维持）
- **25 个渠道 adapter**（非 35）：discord/telegram/feishu/yuanbao 四大成熟（各 ~5 千行，yuanbao 实际 4500+），其余 21 个覆盖 slack/line/whatsapp/matrix/weixin/dingtalk/zalo/nostr/irc/email/sms/webhook 等。
- agent 侧：自进化模块群 + 四层记忆 + RAG + 多模型路由 + 83 工具文件；前端 ScholarForge / Studio / 专家目录 / MCP / 技能 / 进化面板全家桶。

### 工程质量 — 8.2（原 8.4 微调）
- 亮点：`conftest` 气密不变量（无凭证 env / 隔离 HOME / 固定种子）+ 595 fixture；19 个核心依赖 `==` 精确锁版本。
- 扣分：CI `ci-quality-gate.yml:40` **Python 测试 `-x || true # 初期不阻断，只报告`**——46 万行测试价值被软门禁弱化；全仓无 ruff/mypy 门禁；e2e/integration 仅 18 文件（虽比初稿多，仍偏薄）。
- 误报修正：静默吞非 247 处，真实硬静默仅个位数（见 §3）。

### 架构 — 7.3（原 7.4 微调）
- 分层意图清晰，但债务具体：**25+ 个 >3000 行上帝文件**（cli.py 13681 / hermes_cli/main.py 13193 / auth.py 7419 / tui_gateway/server.py 6651 / kanban_db.py 6179 / conversation_loop.py 4902 等）。
- `SessionDB` 85 方法职责过重；agent→gateway 反向依赖 7 处。
- **migrations 并非缺位**：机制存在但稀疏（仅 1 个迁移文件），`SCHEMA_VERSION=14` 仍靠内联 ALTER 演进。优化方向是"把内联 ALTER 追缴成正式迁移文件"，而非从零建机制。

### 用户体验 — 7.2（维持，最弱项）
- 好的：流式渲染 + 骨架屏 + 结构化错误页 + 图片老化淘汰（c5）+ 启动守卫硬阻断，错误 UX 同类顶级。
- 欠的：前端零 i18n（中文全硬编码）、无障碍近乎空白、无托盘/全局快捷键/deep-link、无对话分支。

---

## 3. P1 活 bug 复核（重点）

### P1-① 桌面壳自动更新断路 —— **成立，但根因比初稿描述的更隐蔽**

**取证链：**
1. `git show 4c2cbcbc6` 确认：该 commit 因"打包时 electron-updater 未装导致启动崩溃"摘除 `main.js` 顶部 `require('electron-updater')` 及整块 autoUpdater 逻辑（95 行 → 6 行），**同时删除了所有 `ipcMain.handle('update:*')` handler**。
2. 当前 `main.js` IPC handler 全表（grep `ipcMain.handle(`）：`wechat-login / shell:openExternal / backend:status / copyDiagnostic / agent:check(L731) / agent:download(L746)`。**无任何 `update:*` handler** → 壳更新 IPC 确断路。
3. `preload.js:20-48` 仍暴露 `checkForUpdates/downloadUpdate/installUpdate + 4 个 onUpdate*` → 全部孤儿（invoke 无 handler → reject）。
4. `frontend/src/stores/update.js:73-77`：
   ```js
   if (isDesktop && window.vermes?.checkForUpdates) {   // ← 存在性判断永远为真（preload 暴露了）
     await checkUpdateElectron()
     await checkAgentUpdate()
     return                                          // ← 进入即 return，永不尝试 web 分支
   }
   return Promise.all([checkUpdateWeb(), checkAgentUpdate()])  // ← 桌面端永远走不到
   ```
5. `checkUpdateElectron()` L136 `await window.vermes.checkForUpdates()` → invoke `update:check` → 无 handler → reject → L137 `catch(e)` 仅 `logger.warn` 吞掉。

**结论修正：**
- 初稿说"catch 吞错且不回退 web 检查"——**不准确**。代码架构上桌面分支 `return` 后根本不尝试 web；且 `window.vermes?.checkForUpdates` 存在性判断**永远为真**，导致桌面端**永远卡在注定失败的 electron 路径、永不降级 web**。这比"catch 后没回退"更隐蔽：即使 catch 里想回退，分支结构也不允许。
- **Agent 框架更新不受影响是事实，但理由不是"走 web 分支"**——而是 `agent:check` / `agent:download` handler **根本没被删**（L731/L746 健在），桌面 Agent 更新 IPC 直连可用。
- 后果：**桌面用户永远收不到"壳更新"通知**（Agent 框架更新正常）。修法二选一（均 ~20 行）：
  - **A（推荐，治本）**：把 `electron-updater` 真正装进 `package.json` dependencies，恢复 `update:*` IPC handler + autoUpdater 配置（feed URL `https://vbit.top/vermes/updates/{platform}/{arch}/`）。需 A11 重新出包验证。
  - **B（治标，最快）**：删除 `preload.js` 孤儿 `update:*` API → `window.vermes?.checkForUpdates` 变 `undefined` → 桌面端自动走 `checkUpdateWeb()` 轮询 `version.json`（与 web 同链路，已验证可用）。代价：桌面壳更新走"下载页手动更新"而非"应用内自动更新"。

### P1-② CI 门禁转硬 —— **已落地（commit 227928f7b），非待办**

原判断"需要去 `|| true` 硬化门禁"已不成立：实测 `ci-quality-gate.yml` 旧版确用 `|| true` 软化（`test-baseline` 自创建起跑零测试，CI 日志 run 30403827843 实证），但该问题已在 P1 落地时修复——现 `test-baseline` 已硬（无 `|| true`、跑真实 `tests/gateway/`、103 预存失败进 QUARANTINE 显式 deselect）。

**真正未解的 CI 问题不是"门禁软"，而是"覆盖窄"**（详见 §6 三层守门模型）。故本项**降级为 P2 重定义：扩大 CI 测试覆盖**——gateway 门禁已硬，剩余工作是唤醒 75% 的死资产测试（nightly 全量 + 分目录 L1 路由 + 启动守卫 harness 进 L0），而非再"硬化"。

---

## 4. 静默吞异常的分级（修正初稿"247 处"误判）

初稿"247 处静默吞"口径过宽。实测分级：

| 级别 | 定义 | 数量（纯源码） | 风险 |
|---|---|---|---|
| L0 硬静默 | `except: pass` / `except X: pass` | **8 处** | 真·零可见，需逐个看是否掩盖 bug |
| L1 日志吞 | `except: logger.warn(...)` 等记日志但不上抛 | 数百处（未精确计数） | 可接受，但关键路径（启动/数据写入）应上抛 |
| L2 正常捕获 | `except Exception as e` + 处理/返回错误 | 主体（~5000） | 正常 |

**建议**：下一步只针对 **L0 的 8 处** + **启动/数据写入路径上的 L1** 做排查，而非"247 处"泛扫。

---

## 5. 优化优先级（带证据，为下一步方案准备）

### P1（低成本、高回报，建议本迭代做）
1. **修更新断路（P1-①）**：用户在增长，发不出去壳更新等于白发版。已落地方案 B（删 preload 孤儿 API，commit d32a8df2c，桌面走 web 轮询，零打包改动，已端到端验证）。

### P2（中期：CI 覆盖扩展 + 架构债）
2. **CI 测试覆盖扩展（原 P1-② 降级重定义）**：gateway 门禁已硬，真正缺口是覆盖仅 25%。按 §6 三层守门模型落地——P1 最低垂动作"加 nightly 全量 job"已落地（`ci-quality-gate.yml` 的 `nightly-full`），其余（分目录 L1 路由、清偿 QUARANTINE 103、启动守卫 harness 进 L0）逐步推进。
3. **migrations 追缴**：把 `hermes_state.py` 内联 ALTER（SCHEMA_VERSION 14）逐步抽成 `migrations/` 下的正式文件，统一由 `update_manager.run_migrations` 驱动。机制已存在，只需补文件。
4. **上帝文件分治**：先拆 `cli.py`(13681) / `hermes_cli/main.py`(13193) 两个 1.3 万行巨物（按职责抽 commands / lifecycle / config 子模块）。

### P3（出海 / 体验前置）
5. **i18n**：前端零 i18n，locales 11+ 种 yaml 已服务 CLI。出海前需把前端硬编码中文抽到 i18n（现在动手成本最低，越晚越贵）。
6. **无障碍 + 桌面增强**：托盘/全局快捷键/deep-link/对话分支，按用户增长节奏排。
7. **启动守卫 harness 进 L0**（scripts/test_*.mjs 那批 c1-c5）：从磁盘抽真实函数体的范式，天然适合快门；现已接 `preload-contract` job 守护 P1-① 修复，其余 c1-c5 harness 逐步接入。

---

## 6. CI 定位：三层守门（快速反馈守门员，非全量正确性证明）

**核心判断**：CI 是"快速反馈的守门员"，不是"全量正确性证明"。基于本仓实证——

- 7 月 402 个 commit、迭代烈度极高；若每次 PR 跑全量 45 万行测试会拖死节奏（仅 `tests/gateway/` 11 万行已要 30min timeout）。
- 但当前 CI 在另一极端：只跑 `tests/gateway/`（约 25%，262/≈1500 测试文件），其余 75% 测试是"仓库里有、CI 不跑"的死资产。
- gRPC 式"全量测试门禁"在单仓 + 多渠道 + Electron 壳 + 重后端形态下既不可行也没必要。

**正确位置 = 三层守门（按"反馈速度 × 阻断强度"分层，而非"要么全跑要么不跑"）：**

| 层 | 触发 | 跑什么 | 阻断？ | 预算 |
|---|---|---|---|---|
| **L0 快门** | 每次 PR/push | 核心链路 + 启动守卫 harness（`scripts/test_*.mjs` c1-c5）+ `tests/gateway/` | 硬阻断 | <10min |
| **L1 影响域门** | PR 合并前 | 按改动目录路由：动 `agent/` 跑 `tests/agent/`、动 `frontend/` 跑 playwright、动 `hermes_cli/` 跑对应 | 硬阻断 | <15min |
| **L2 全量门** | 定时/手动 | `tests/` 全量（含 stress/integration/e2e） | 报告不阻断 | 不限时 |

**关键认知**：75% 长尾测试不该每次 PR 阻断，但也不能睡死——放进 L2 nightly，既保住资产价值又不挡迭代。这正是"适应开发进度"的解法：快门永远快，重活放到夜里。

**当前诊断（基于取证）**：

- 只有 L0 一层，且只占 25%：`ci-quality-gate.yml` 的 pytest 调用只有 `tests/gateway/` 一个目录。门禁本身已硬（无 `|| true`），但覆盖面太窄。
- 配置债：`pyproject.toml` 的 `[tool.pytest.ini_options]` 写了 `testpaths = ["tests"]`（本应全量），但 workflow 显式传 `tests/gateway/` 把默认全覆盖能力主动废掉。
- 103 个预存失败（`QUARANTINE-D2b.txt`）只在 gateway 隔离，没在全量层做趋势追踪。

**落地优先级（已按性价比排序）**：

- **P1（最低垂，已落地）**：修配置债 + 加 L2 nightly——`ci-quality-gate.yml` 新增 `nightly-full` job：`python -m pytest tests/ --tb=short -q --timeout=120`，`if: schedule`，失败只进 summary 不阻断。零风险，~15 行，唤醒死资产。
- **P2**：分目录接入 L1 影响域路由（先接 `tests/agent/` 自进化核心 + `tests/hermes_cli/` 启动链路）；接前先清偿 QUARANTINE 103 项（"只许缩短"做趋势管理）。
- **P3**：把 `scripts/test_*.mjs` 启动守卫 harness 正式接进 L0（已接 `preload-contract` 守 P1-①）。

## 7. 一句话总结（修正版）

底盘（安全 9.0 / 测试 0.85:1 / 功能 25 渠道 + 全家桶）是 9 分的料，被 7 分的架构债（上帝文件、迁移稀疏）和桌面细节（更新断路、i18n）拖到 8.1。修完**更新断路（P1-①）** 这一低成本项即可站上 8.5；CI 门禁已硬（P1-② 已落地），下一步是按 §6 三层模型把覆盖从 25% 扩到"快门+影响域+nightly"的全资产守护，为出海（i18n）与架构分治扫清决策干扰。

---

## 附：取证命令摘要（可复现）
- 生产源码行数：`find . \( -path ./.venv -o -path ./dist -o -path ./dist-electron -o -path ./node_modules -o -path ./.git -o -path ./tests \) -prune -o -name "*.py" -print | xargs wc -l`
- 上帝文件：`... | awk '$1>3000'`
- 更新断路：`git show 4c2cbcbc6`、`grep -n "ipcMain.handle(" electron/main.js`、`sed -n '60,160p' frontend/src/stores/update.js`
- 静默吞：`grep -nE "except[ A-Za-z_]*:\s*pass\s*$" --include="*.py"`
- migrations：`ls migrations/`、`grep -n "run_migrations" hermes_cli/update_manager.py`
- 渠道 adapter：`grep -rhn "^class .*Platform" gateway/platforms/*.py`
