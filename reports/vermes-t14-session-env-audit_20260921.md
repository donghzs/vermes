# T14 进程级会话状态变量审计（纸面，2026-09-21）

> 目的：为「cron 标记」之后的下一类问题（presence 变量 + TERMINAL_CWD 串味）建立
> 全仓 writer/reader/泄漏路径清单，输出 L-009+ 实施清单。**本报告只审计，不改代码。**

---

## 一、四个核心变量全景

### 1. `VERMES_INTERACTIVE`

| 角色 | 位置 | 语义 | 进程模型 | 判定 |
|---|---|---|---|---|
| **W** | `cli.py:13436` `os.environ["VERMES_INTERACTIVE"]="1"` | CLI 交互主进程启动时置位 | 单进程串行（CLI） | ✅ 合法进程策略（CLI 整个进程都是交互的） |
| **W** | `doctor.py:444` `setdefault` | doctor 诊断进程 | 单进程 | ✅ 合法（幂等 setdefault） |
| **W** | `tui_gateway/server.py:715`（`_enable_gateway_prompts`） | TUI gateway 进程 | **多线程并发** | ⚠️ 见 §二 |
| **W** | `tui_gateway/slash_worker.py:58` | slash worker 子进程 | 子进程 | ✅ 合法（worker 自身交互） |
| **W** | `acp_adapter/server.py:1447`（请求内 set + finally restore） | ACP 会话 | 多线程（executor 复用） | ⚠️ 见 §三（有 restore 但非 contextvar） |
| **R** | `tools/approval.py:1543/1797` `is_cli` | 审批判定 | — | 消费者 |
| **R** | `tools/terminal_tool.py:873` sudo prompt | — | 消费者 |
| **R** | `tools/cronjob_tools.py:803` `check_cronjob_requirements` | — | 消费者 |

### 2. `VERMES_EXEC_ASK`

| 角色 | 位置 | 语义 | 进程模型 | 判定 |
|---|---|---|---|---|
| **W** | `gateway/run.py:770`（**module-level**） | gateway 进程 import 即置位 | gateway 主进程 | ⚠️ 见 §二 |
| **W** | `tui_gateway/server.py:714`（`_enable_gateway_prompts`） | TUI gateway | 多线程 | ⚠️ 见 §二 |
| **R** | `tools/approval.py:1545/1799` `is_ask` | 审批判定 | — | 消费者 |
| **R** | `tools/cronjob_tools.py:805` | — | 消费者 |

### 3. `VERMES_GATEWAY_SESSION`

| 角色 | 位置 | 语义 | 进程模型 | 判定 |
|---|---|---|---|---|
| **W** | `tui_gateway/server.py:713`（`_enable_gateway_prompts`） | TUI gateway | 多线程 | ⚠️ 见 §二 |
| **R** | `tools/approval.py:131` `_is_gateway_approval_context` | 审批判定（legacy 兼容） | — | 消费者 |
| **R** | `tools/terminal_tool.py:364` | — | 消费者 |
| **R** | `tools/skills_tool.py:369` | — | 消费者 |
| **R** | `tools/cronjob_tools.py:804` | — | 消费者 |

### 4. `TERMINAL_CWD`

| 角色 | 位置 | 语义 | 进程模型 | 判定 |
|---|---|---|---|---|
| **W** | `gateway/run.py:780`（module-level bridge，config terminal.cwd → env） | gateway 启动时一次 | 单线程启动 | ✅ 合法进程策略 |
| **W** | `cron/scheduler.py:1548`（job 内 set + finally restore，:1937-1944） | cron job 设 workdir | gateway 内 cron 线程 | ⚠️ 见 §二（有 restore，但非 contextvar） |
| **W** | `vermes_cli/main.py:1238` `env["TERMINAL_CWD"]=...` | 子进程 env dict（非 os.environ） | 子进程 | ✅ 合法（不污染父进程） |
| **R** | `gateway/slash_handlers/session_handlers.py:248` | cwd 解析 | — | 消费者 |
| **R** | `gateway/message_handler_mixin.py:1716/2510` | — | 消费者 |
| **R** | `gateway/runtime_footer.py:116` | — | 消费者 |
| **R** | `tools/file_tools.py:84/91` | 相对路径解析 | — | 消费者 |

---

## 二、核心发现

### 发现 1：`_enable_gateway_prompts()` 在 TUI gateway 是「进程级永久置位」，与多线程并发模型冲突

- `tui_gateway/server.py` 用 `ThreadPoolExecutor`（:164）+ 每 session 一个 daemon thread，是**多线程并发**处理。
- `_enable_gateway_prompts()`（:711-715）把 `GATEWAY_SESSION`/`EXEC_ASK`/`INTERACTIVE` 三个变量写进 `os.environ`，且**从不清理**（session 结束不 pop）。
- 调用点：`session.create`（:2122）、`session.reopen`（:2282）——**每次创建/重开会话都调**，是「进程启动后永久置位」。
- **串味面**：TUI gateway 是多会话并发的。但——这三个变量的语义是「整个 TUI gateway 进程就是 gateway 会话环境」（区别于独立 CLI），所以「进程级永久置位」对 TUI gateway 内部**语义上是自洽的**（所有 session 都走 gateway 审批路径，没有「用户 vs cron」的区分）。
- **真正的串味风险不在 TUI gateway 内部**，而在：如果 TUI gateway 进程又 spawn 子进程（slash_worker 已单独设 INTERACTIVE），或 cron 线程在这个进程内跑（TUI gateway 是否内嵌 cron ticker 需确认——**待查**）。

### 发现 2：`gateway/run.py:770` module-level `VERMES_EXEC_ASK=1` 是 gateway 主进程的「永久置位」

- 与 L-006/L-007 的修复直接相关：**这就是「cron worker 继承 VERMES_EXEC_ASK 泄漏」的源头**。
- L-006 已经在 `check_dangerous_command`/`check_all_command_guards` 里对 cron 做了 `is_ask=False` 强制覆盖（防御住了），所以 cron 侧目前安全。
- 但这是「防御在消费方」而非「源头治理」。module-level 置位意味着 gateway 进程里 `VERMES_EXEC_ASK=1` 永远为真，任何读它的路径（除 approval 已防御的 cron 分支）都会看到「交互 exec」语义。

### 发现 3：`acp_adapter/server.py` 的 INTERACTIVE/VERMES_SESSION_ID 用「save+restore 进程级 env」而非 contextvar

- :1446-1471 在请求内 set `VERMES_INTERACTIVE=1`、`VERMES_SESSION_ID=session_id`，finally 里 restore。
- 注释明确承认「executor 线程复用会泄漏 session id」，所以 save+restore。
- **问题**：ACP server 若并发跑多个 session（executor 多线程），`os.environ` 是进程级的，两个并发 session 的 save+restore 会**互相覆盖**（线程 A restore 时可能把线程 B 刚 set 的值 pop 掉）。
- 这正是「应该用 contextvar」的场景——ACP 已经为 `edit_approval_requester` 用了 contextvar（:1440 `set_edit_approval_requester`），但 INTERACTIVE/SESSION_ID 还在用进程级 env。

### 发现 4：`TERMINAL_CWD` 在 cron/scheduler 是「job 内 set + finally restore」，非 contextvar

- :1546-1548 job 开始 set，:1937-1944 finally restore。
- 但 scheduler 对「workdir job」做了**串行化**（:2099 注释「profile jobs use a context-local」，:1527-1535 注释说明 workdir job 串行）。
- **串味面**：如果存在「workdir job」与「无 workdir job」并发，无 workdir job 会读到前一个 workdir job 设的 TERMINAL_CWD（因为 restore 是 finally，但并发窗口内仍是进程全局）。
- 需确认：scheduler 的 tick 是否对 workdir job 串行化（`_run_job_impl` 是否有锁）——**待查**。

---

## 三、风险分级

| 变量 | 位置 | 风险 | 处置建议 |
|---|---|---|---|
| `VERMES_EXEC_ASK` | `gateway/run.py:770` module-level | **中**（已由 L-006 消费方防御，但源头未治；cron 线程内若未来有别的 reader 会误判） | L-009：考虑改为「启动时 setdefault 但不影响 cron」或确认 L-006 覆盖足够后暂缓 |
| `VERMES_GATEWAY_SESSION` | `tui_gateway/server.py:713` | **低**（TUI gateway 内语义自洽，无 user/cron 区分） | 暂缓，属进程策略 |
| `VERMES_INTERACTIVE` | `acp_adapter/server.py:1447` | **中**（并发 session 会互相覆盖 save+restore） | L-010：改 contextvar（ACP 已有 contextvar 基建 `set_edit_approval_requester`） |
| `VERMES_INTERACTIVE` | `tui_gateway/server.py:715` | 低（同 GATEWAY_SESSION） | 暂缓 |
| `TERMINAL_CWD` | `cron/scheduler.py:1548` | **待查**（串行化是否覆盖所有并发路径） | L-011：先确认 workdir job 串行化边界，再决定是否改 contextvar |

---

## 四、待查（本报告未闭环，需下轮实证）

> **【2026-09-21 20:08 查证完毕，三项全部闭环，结论如下】**

### 查证 1：TUI gateway 是否内嵌 cron ticker？—— **否**
- `tui_gateway/server.py` 里对 cron 的引用仅是 `cron.manage` RPC（:6502）→ `tools.cronjob_tools.cronjob` 的增删改查，**不启动 cron ticker**。
- cron ticker 的唯一启动点：`gateway/run.py:4115`（`target=_start_cron_ticker`），`_start_cron_ticker` 定义在 `gateway/run.py:3569`，内部 `from cron.scheduler import tick`（:3583）。
- `vermes_cli/cron.py:132 cron_tick()` 是独立 CLI `vermes cron` 命令的入口（`main.py:11240` 注册 `tick` 子命令），非 TUI。
- **结论**：`_enable_gateway_prompts()` 的进程级置位**不会**影响 cron 线程（TUI 内无 cron 线程），串味风险只存在于「TUI gateway 自身多 session 并发」——但 TUI 内所有 session 都是 gateway 会话（无 user/cron 区分），语义自洽。**TUI 侧无新增风险。**

### 查证 2：cron workdir job 是否串行化（TERMINAL_CWD 并发安全）？—— **是，严格串行**
- `cron/scheduler.py tick()`（:2095-2113）把 due jobs 分区：
  - `sequential_jobs` = 有 `workdir` 或 `profile` 的 job（它们 mutate 进程全局状态）→ **串行跑**（`for job in sequential_jobs: _ctx.run(_process_job, job)`）
  - `parallel_jobs` = 无 workdir 无 profile 的 job（不改 TERMINAL_CWD）→ 并行跑
- **且 sequential 与 parallel 是先后两批**（sequential 全部跑完才跑 parallel），所以并行 job 永远不会与「正在改 TERMINAL_CWD 的 workdir job」并发。
- `TERMINAL_CWD` 的 set（:1548）+ finally restore（:1937-1944）在串行化保障下**安全**，无实际串味。
- **结论**：`TERMINAL_CWD` 当前实现**安全**，无急迫改造必要；唯一残留是「进程全局」的**理论**缺陷（若未来有人绕过 tick() 分区直接并发调 _run_job_impl 才会暴露），可降级为「可选加固」。

### 查证 3：VERMES_SESSION_ID 全景？—— **contextvar 基建已建但未贯通**
- `gateway/session_context.py:58` 已有 `_SESSION_ID: ContextVar`，`:86` 已进 `_VAR_MAP`，`get_session_env()`（:150）三级解析（contextvar→os.environ→default）能读它。
- **但 `set_session_vars()`（:200-227）不含 `_SESSION_ID`**，也没有独立的 `set_session_id()` 入口。
- 所以 ACP（`acp_adapter/server.py:1453-1474`）只能用进程级 `os.environ["VERMES_SESSION_ID"]=session_id` + save/restore，这正是并发串味风险。
- reader 侧（`tools/kanban_tools.py:125/688`）也直接读 `os.environ.get("VERMES_SESSION_ID")`，**没走 `get_session_env()`**——contextvar 基建与 session_id 消费未贯通。
- **结论**：`VERMES_SESSION_ID` 是**最该改 contextvar 的场景**，且基建已就绪（只需 ①`set_session_vars` 加 `session_id` 参数 / 或新增 `set_session_id()`，②reader 改 `get_session_env()`，③ACP 改调 setter）。

### 【补充查证 2026-09-21 20:15，MiMo 反馈后】

#### 查证 A：cronjob() 是否就地 run_job/tick？—— **否**
- `tools/cronjob_tools.py:337 cronjob()` 的 action 分支含 `run`/`run_now`/`trigger`（:511），调 `trigger_job(job_id)`（来自 `cron.jobs`）。
- `cron/jobs.py:855 trigger_job()` 只把 job 状态改 `scheduled` + `next_run_at=now`，**不在调用方进程就地跑 job**。
- **结论**：TUI/ACP/gateway 任意线程通过 `cron.manage` RPC 触发 cron 时**不会**吃到进程级 presence（job 只是被排进下次 tick）。「TUI presence + 内嵌 cron」串味面彻底排除。

#### 查证 B：VERMES_SESSION_ID reader 全表（比查证 3 更强——挖出 setter 缺失真 bug）
- **`set_current_session_id` 全仓 6 处调用、0 处定义**：
  - `agent/agent_init.py:997/999`、`agent/conversation_compression.py:616/618/662/663` 均 `from gateway.session_context import set_current_session_id` + `set_current_session_id(agent.session_id)`。
  - 但 `gateway.session_context` **没有 `set_current_session_id` 定义**（grep 确认 0 处）。
- **后果**：writer 想走 contextvar，但 setter 缺失 → import 抛 ImportError → 全部静默回落 `os.environ["VERMES_SESSION_ID"]`。这是「想改 contextvar 但半途而废」的 pre-existing 缺陷，也是 L-010 的核心。
- writer 全景（3 处）：`acp_adapter/server.py:1454`（进程级 save/restore）、`agent/agent_init.py:999`（想走 contextvar 但回落 os.environ）、`agent/conversation_compression.py:618/663`（同上）。
- reader 全景：`tools/kanban_tools.py:125/688`（直读 os.environ）、skill 模板 `${VERMES_SESSION_ID}`（`agent/skill_preprocessing.py:10` 替换 token）、`get_session_env()` 有 env 回落。
- **结论**：L-010 不是「只改 ACP」，而是「补 setter 入口 + 3 处 writer 统一走 contextvar + reader 改 get_session_env」——否则「改一处、漏两处」。

#### 查证 C：TERMINAL_CWD 用户路径 reader（cron↔用户线程串味面）
- 用户路径 reader 三处：`tools/file_tools.py:84-91`（`_resolve_path_for_task` 相对路径解析）、`gateway/message_handler_mixin.py:1716`（`os.environ.get("TERMINAL_CWD", ...)`）、`gateway/runtime_footer.py:116`（`_home_relative_cwd`）。
- 这三处都直读 `os.environ.get("TERMINAL_CWD")`，**无 cron-aware 判断、无 contextvar**。
- **串味窗口**：cron workdir job 线程在 tick 内 `os.environ["TERMINAL_CWD"]=_job_workdir`（:1548）→ 若同一时刻用户消息线程跑 `file_tools._resolve_path_for_task` 或 `message_handler_mixin` 的 `@` 上下文解析，会读到 cron job 的 workdir 而非用户会话 cwd。
- 注：tick 已把 workdir job 串行化（不与其它 workdir/parallel job 并发），但**没隔离「cron 线程 vs gateway 用户消息线程」**（这是 gateway 进程内的两个独立线程）。
- **结论**：L-011 的真实风险是「cron 线程 vs 用户消息线程」的 cwd 串味，不是「workdir 与 parallel job 并发」（后者已由 tick 分区解决）。

---

（以下为原始待查，已全部由上面查证闭环取代）

1. ~~**TUI gateway 是否内嵌 cron ticker？**~~ → **已查证：否**（tui_gateway 仅 cron.manage RPC，ticker 只在 gateway/run.py:4115）
2. ~~**cron/scheduler 的 workdir job 是否串行化？**~~ → **已查证：是**（tick 分区，workdir/profile job 严格串行，TERMINAL_CWD 安全）
3. ~~**`VERMES_SESSION_ID` 完整 writer/reader 清单**~~ → **已查证：contextvar 基建已建未贯通**（_SESSION_ID ContextVar + _VAR_MAP 有，但 set_session_vars 无 session_id 入口，ACP/kanban_tools 仍用进程级 env）

---

## 五、L-009+ 实施清单（已据三项查证修订）

| ID | 项 | 依赖 | 优先级（已修订） |
|---|---|---|---|
| L-009 | `gateway/run.py:770` module-level `VERMES_EXEC_ASK` 源头治理（确认 L-006 覆盖后，评估是否保留 module-level 或改为 start_gateway 内受控设置） | 查证 1（TUI 无 cron，风险仅 gateway 主进程） | 中 |
| L-010 | **`VERMES_SESSION_ID` 改 contextvar 贯通**：①`set_session_vars` 加 `session_id` 参数（或新增 `set_session_id()`）②`tools/kanban_tools.py` reader 改 `get_session_env()` ③`acp_adapter/server.py` 改调 setter（弃用进程级 save+restore）。**基建已就绪，成本最低收益最实** | 查证 3（已确认基建存在但未贯通） | **高（首选）** |
| L-011 | `TERMINAL_CWD` cron 串行化边界确认，必要时改 contextvar | 查证 2（已确认串行化安全） | **降级为可选加固**（当前安全，无急迫性） |
| T14-查证 | ~~三项实证~~ | **已完成**（见 §四查证 1/2/3） | ✅ 完成 |

---

## 六、结论

- `VERMES_CRON_SESSION`（L-007+L-008）已闭环，是「cron 标记」问题，用 contextvar + 启动 sanitize 解决。
- presence 变量（INTERACTIVE/EXEC_ASK/GATEWAY_SESSION）与 TERMINAL_CWD 是**下一类问题**：它们多数是「进程策略」（合法保留），少数是「并发串味」（ACP save+restore、gateway module-level EXEC_ASK、cron TERMINAL_CWD）。
- 按 MiMo 口径：**本报告只审计，不改代码**。先做 T14-查证三项实证，再定 L-009/L-010/L-011 的实施顺序与范围。
