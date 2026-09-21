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

1. **TUI gateway 是否内嵌 cron ticker？** 若 TUI gateway 进程内也跑 cron 线程，则 `_enable_gateway_prompts` 的进程级置位会直接影响 cron 线程（当前 L-006 只防御了 gateway/run.py 的 cron，TUI 侧 cron 若有则未覆盖）。
2. **cron/scheduler 的 workdir job 是否串行化？** 确认 TERMINAL_CWD 的 finally restore 在并发下是否安全。
3. **`VERMES_SESSION_ID`（acp_adapter）** 的完整 writer/reader 清单——它也是「会话语义」变量，与 INTERACTIVE 同批处理。

---

## 五、L-009+ 实施清单（本报告输出，不改代码）

| ID | 项 | 依赖 | 优先级 |
|---|---|---|---|
| L-009 | `gateway/run.py:770` module-level `VERMES_EXEC_ASK` 源头治理（确认 L-006 覆盖后，评估是否保留 module-level 或改为 start_gateway 内受控设置） | 待查 1 | 中 |
| L-010 | `acp_adapter/server.py` 的 INTERACTIVE/VERMES_SESSION_ID 改 contextvar（复用 `set_edit_approval_requester` 同款基建） | 无 | 中 |
| L-011 | `TERMINAL_CWD` cron 串行化边界确认，必要时改 contextvar | 待查 2 | 低 |
| T14-查证 | 待查 1/2/3 三项实证（TUI 是否内嵌 cron、workdir 串行化、VERMES_SESSION_ID 全景） | 无 | 高（先做） |

---

## 六、结论

- `VERMES_CRON_SESSION`（L-007+L-008）已闭环，是「cron 标记」问题，用 contextvar + 启动 sanitize 解决。
- presence 变量（INTERACTIVE/EXEC_ASK/GATEWAY_SESSION）与 TERMINAL_CWD 是**下一类问题**：它们多数是「进程策略」（合法保留），少数是「并发串味」（ACP save+restore、gateway module-level EXEC_ASK、cron TERMINAL_CWD）。
- 按 MiMo 口径：**本报告只审计，不改代码**。先做 T14-查证三项实证，再定 L-009/L-010/L-011 的实施顺序与范围。
