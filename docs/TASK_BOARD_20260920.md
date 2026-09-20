# Vermes 上游对齐 · 并行工单板（2026-09-20）

> **用法**：本文件是并行分工的**唯一契约**。接手者先读 §0 红线，再领自己那一栏。
> 工单状态自行更新（`⏳ 未领 / 🔨 进行中 / ✅ 已完成 / ⛔ 阻塞`），**改完 commit，不要 push**。

---

## §0 红线（所有人必读，违反即停）

1. **不碰 `vermes_state.py`** —— 2.5 的 B4 `272caee7fa` / B8 `204f96d9cd` 正在改它（route_ledger 新表）。
   P0-B state.db 六件套**必须等 2.5 A2A/kanban 收口并过 route_ledger 回归闸门**。
2. **不碰别人的在途文件** —— 动手前 `git status --short` 看在途改动是谁的。
3. **不 push** —— 本地提交由执行者完成，**审计 + push 由董董负责**。
4. **不覆盖 Vermes 领先项**：workflow DAG / memory fabric / 自进化 / 中文平台 / 245 技能 / 打包链 / ScholarForge / cadir_build。
5. **否定性结论禁用 shell `grep`** —— 本会话有实证假阴性。用 Grep 工具或 `ls -1` 全量列举。
6. **「已落盘」必须回读验证**：`ls -la` + `wc -c`，Edit 回执 ≠ 落盘。
7. **只在自己那份文件足迹内动手**，越界先打招呼。

---

## §1 文件足迹划分（隔离依据）

| 执行者 | 独占目录/文件 | 严禁触碰 |
|---|---|---|
| **WorkBuddy** | `gateway/`、`cron/`、`tests/gateway/` | `frontend/`、`scripts/`、`.github/`、`UPSTREAM_SYNC.md` |
| **mimo** | `scripts/`、`UPSTREAM_SYNC.md`、`.github/workflows/`、`frontend/src`、`vermes_cli/gateway_channels.py` | `gateway/`、`cron/`、`vermes_state.py` |

> 两侧**零交集**。若某工单需要跨界，先在 §4 记一笔，不要直接动。

---

## §2 WorkBuddy 工单（gateway / cron / 诊断判定侧）

| ID | 工单 | 落点 | 验收 | 状态 |
|---|---|---|---|---|
| **W1** | 品牌大小写不一致**定点**判定（irc） | `tests/gateway/test_irc_adapter.py:223/379`、源码 `plugins/platforms/irc/adapter.py:409-424` | **已定性（2026-09-20 08:30）：测试期望过时，源码正确**。实测 `AssertionError: assert 'Vermes_' == 'VERMES_'`；源码 `:112` 默认 `nickname="Vermes-bot"`（新品牌），`:414` 注释仍是旧的 `VERMES_` 但代码品牌自适应（`self.nickname + "_"`）。**修法：改测试期望 `VERMES_`→`Vermes_`（约 5 处），注释 `:414` 顺手订正。禁止全仓 sweep** | 🔨 定性完成，待修 |
| **W2** | E 类 4 条 reconnect 失败根因判定 | `tests/gateway/test_platform_reconnect.py` | 逐条给出根因 + 分类（真 bug / 测试过时 / 环境） | ⏳ 未领 |
| **W3** | G 类 email self-message 过滤（**疑真 bug**） | `gateway/` email 适配侧 | 判定是否真 bug；**若是，不得隔离，须修** | ⏳ 未领 |
| **W4** | A3 提示去重（独立 `notices` 域） | 新增独立域/文件（**不塞 `gateway/status.py`**，那是运行时健康诊断文件） | 同一会话只提示一次；重启后仍生效 | ⏳ 未领 |

> **W1 口径澄清（重要）**：全仓 `VERMES_` 大写残留 **768 行**，但绝大多数是**环境变量前缀**（本该大写，如 `VERMES_HOME`），
> **不是 bug**。本工单只处理 irc 那两处真实不一致，**禁止全仓 sweep**（会制造 768 行无意义 diff）。

---

## §3 mimo 工单（仓库外围 / 零代码风险侧）

| ID | 工单 | 落点 | 验收 | 状态 |
|---|---|---|---|---|
| **M1** | A4 分歧度量脚本落地 | `scripts/diverge_metrics.py`（自 Hermes skill 搬入并适配） | 基线已出：上游核心工具 60 / Vermes 49；Jaccard 0.09–0.15；静默失败 runtime≈935 vs 243、近 90 天新增 3 行/3 提交 → `reports/diverge-baseline-20260920.md` | ✅ |
| **M2** | A5 重写 `UPSTREAM_SYNC.md` | 仓库根 `UPSTREAM_SYNC.md` | 实测：Vermes **2.5.0**（version.txt+三 package.json）、上游 **v0.21.3**（tag `v2026.9.14`）、remote `upstream/upstream2`→`NousResearch/hermes-agent`；**未改 remote**；旧 0.18/v2.3 数字已废弃 | ✅ |
| **M3** | A6 补 3 条 CI lane | `.github/workflows/js-tests.yml`（真跑 vitest）`tests-os.yml` / `install-e2e.yml`（**`if: false` 占位**，防误伤） | 三文件在盘；不重复 `uv-lockfile-check.yml`；占位 lane 不会在 PR 上自动开跑 | ✅（占位待评估后启用） |
| **M4** | A7 GUI 设置入口 + home channel | `frontend/src` Settings 移动接入 + `vermes_cli/gateway_channels.py` + `vermes_cli/blueprints/gateway_channels.py`（HTTP 面） | GUI 可设「默认通知频道」；`write_home_channel` 双写 config.yaml + .env + 进程 environ；**读侧只走** `resolve_home_channel_chat_id`（不另起口径）；测试 20 passed | ✅（首条 DM 自动设定：后端双写已备，gateway 侧 auto-set 属 WorkBuddy W 域，见 §4） |

> **M1 提示**：脚本已存在于 Hermes skill root，**先读再搬，别从零写**（0.5d → 0.25d）。
> **M2 提示**：`git remote -v` 实测 `upstream` / `upstream2` → `NousResearch/hermes-agent`，**管道没坏，烂的是文档**。
> **M3 提示**：上游 35 条，本地原有 14 条；本切片 +3 文件（其中 2 条占位）。

---

## §4 跨界请求（需双方确认后才动）

| 时间 | 提出方 | 内容 | 处置 |
|---|---|---|---|
| 2026-09-20 | mimo | M4 需要 HTTP 面：触碰 `vermes_cli/blueprints/gateway_channels.py`（schema 模块 `gateway_channels.py` 本身在足迹内，blueprint 是其路由壳）。**未改** `gateway/`、`cron/`、`vermes_state.py` | 已落地 home-channel GET/PUT；请 WorkBuddy 交叉审计时知悉 |
| 2026-09-20 | mimo | A7「首条 DM 自动设定」需在 gateway 消息链路写 config——落在 WorkBuddy 独占 `gateway/` | **未做**；请 W 侧在 W4/提示层一并评估 auto-set（用 `write_home_channel` 或共享解析器同口径） |

---

## §5 交叉审计（产出后必做）

| 审计方 | 被审计对象 | 审计要点 |
|---|---|---|
| **mimo 审 WorkBuddy** | W1–W4 | ① 判定是否有源码行号证据 ② W3 若判真 bug 是否真修了（不是隔离了事） ③ W4 去重是否真持久化 ④ 有无越界改 frontend/scripts |
| **WorkBuddy 审 mimo** | M1–M4 | ① M2 版本数字是否实测（`git remote -v`、`version.txt`、GitHub API）② M1 脚本能否真跑出数字 ③ M3 lane 是否会误伤现有流水线 ④ M4 是否引入 home channel 新口径（与 A1/A2 共享解析器冲突） |

**审计纪律**：审计"已完成"声称时，① commit 用 `git cat-file` 验真 ② 文件用 `find` + `git diff` 验落地 ③ 测试实跑 ④ 专门找"新引入了什么"。
详见 `~/.workbuddy/skills/audit-completion-report/`。

---

## §6 已知基线（2026-09-20 实测，可直接引用）

- 本地 HEAD 段：`b7305707d4` → `a2fde2ba95` → `92f5134df0` → `d8c6f8fa1c`（**均在主线，未 push**）
- 本地版本：`version.txt` = `2.4.9`（三源一致）
- 上游最新：v0.21.3（tag `v2026.9.14`）；v0.21.0 = "Pantheon/Bot Mode"
- 上游 `AGENTS.md` **12** 个 / CI **35** 条；本地 **1** / **14**
- gateway 全量：**13 failed / 5820 passed / 13 skipped**，13 条已用对照实验（`git checkout eaa63411a2`）证明 **pre-existing**
- 契约测试：`tests/vermes_cli/test_home_channel_resolution.py` **12 passed**

## §7 关联

- 路线图正文（唯一真源）：`reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md`
- 已知失败清单：`reports/known-failures-gateway-20260920.md`
- 跨 agent 技能索引：`docs/AGENT_SKILLS_INDEX.md`（**已登记 MiMo 多 root**：`~/.config/mimocode/skills/` 等；workbuddy 12 / hermes 33 / vermes-engine 76）
