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
| **W2** | E 类 4 条 reconnect 失败根因判定 | `gateway/lifecycle_mixin.py`、`gateway/watcher_mixin.py` | ✅ **已修（2026-09-20 08:45）：判定为「真 bug」×2，非噪声**。①**主因**：`_connect_one` 失败时**整体替换** `_failed_platforms[platform]` entry，而 watcher 在 `watcher_mixin.py:235` 已持有旧引用 → 递增写进孤儿 dict → attempts 永不累积（circuit breaker `_PAUSE_AFTER_FAILURES=10` 永远不触发）+ 指数退避被重置为恒定 30s。②**死分支**：watcher 的 non-retryable 移除分支探测 `self.adapters.get(platform)`，但失败 adapter 只在**成功**连接时才注册 → 恒为 None → 不可重试的平台被**无限重试**。③`_create_adapter` 返回 None（插件缺失）同样无限重试。**修法**：entry 原地更新 + result 携带 `retryable` 标记 + no-adapter 时出队。验收：4 条全绿（29 passed），回归 139 passed / 2 failed（均为 pre-existing 环境缺依赖）。**变异测试**：改回整体替换 → #2#3 立刻红（非侥幸） | ✅ 完成 |
| **W3** | G 类 email self-message 过滤（**疑真 bug**） | `gateway/platforms/email.py:437` | ✅ **已判定 + 已修（2026-09-20 09:10）：真缺陷，但不是活跃生产 bug**。根因：`if sender_addr == self._address.lower()` —— **左侧未归一化、右侧归一化**的不对称比较（同方法内 `_is_automated_sender:94`、allowlist `:453` 都做了 `.lower()`，只有 437 漏了）。真实链路唯一调用方 `_check_inbox:363 → _fetch_new_messages:414 → _extract_email_address:182/183`（已 lower），所以**生产上不会漏过滤**；测试绕过归一化层直接喂混合大小写地址，正好暴露该脆弱点。**修法**：两侧都 `.strip().lower()` 后比较（真实链路是 no-op）。验收：`tests/gateway/test_email.py` **60 passed**（修复前该用例 1 failed）。**未隔离、未改测试** | ✅ 完成 |
| **W4** | A3 提示去重（独立 `notices` 域） | **新增** `gateway/notices.py`（落点 `~/.vermes/notices.json`）+ `gateway/message_handler_mixin.py` 抽出 `_maybe_prompt_missing_home_channel()` | ✅ **已完成（2026-09-20 09:25）**。落点订正：`~/.vermes/gateway_state.json` **就是** `gateway/status.py` 写的 pid/健康文件（实读含 `pid/kind/gateway_state/platforms`）→ 路线图 A3 原落点正是 E7 警告的污染点，故按 W4 走**独立文件**。语义：**按 (platform, key) 持久化去重**，取代 `not history`（会话级节流，每次新会话都重弹）；跨重启仍生效。实现要点：mtime 感知的内存缓存（避免每轮消息读一次盘）、`os.replace` 原子写、**投递失败不标记**（下次仍提示）、文件损坏视为未提示且不抛。验收：`tests/gateway/test_notices_dedup.py` **15 passed**，含**真子进程**跨重启用例。**变异测试 ×2**：①只写内存不落盘 → 3 红（含跨重启用例）；②去掉去重判定 → `test_prompts_once_and_only_once` 红 | ✅ 完成 |

> **跑测试的环境坑（双方共用，2026-09-20 实测）**：WorkBuddy/沙箱环境下 pytest 默认 tmpdir（`/private/var/...`）与 `/tmp`（→`/private/tmp`）会被 shim 拦 mkdir，报 `PermissionError: EEXIST`。
> 且 `--basetemp` 目录**已存在时同样报错**，必须每次给新鲜路径。可用配方：
> `BT=~/wb-tmp/bt-$RANDOM; TMPDIR=~/wb-tmp .venv/bin/python -m pytest <paths> -q -p no:xdist -o addopts="" --basetemp=$BT`
> （若你在非沙箱终端跑，`TMPDIR` 这段可省略；`-p no:xdist -o addopts=""` 仍建议保留，xdist scheduler 在本仓会崩。）

> **W1 口径澄清（重要）**：全仓 `VERMES_` 大写残留 **768 行**，但绝大多数是**环境变量前缀**（本该大写，如 `VERMES_HOME`），
> **不是 bug**。本工单只处理 irc 那两处真实不一致，**禁止全仓 sweep**（会制造 768 行无意义 diff）。

---

## §3 mimo 工单（仓库外围 / 零代码风险侧）

| ID | 工单 | 落点 | 验收 | 状态 |
|---|---|---|---|---|
| **M1** | A4 分歧度量脚本落地 | `scripts/diverge_metrics.py`（自 Hermes skill 搬入并适配） | 基线已出：上游核心工具 60 / Vermes 49；Jaccard 0.09–0.15；静默失败 runtime≈935 vs 243、近 90 天新增 3 行/3 提交 → `reports/diverge-baseline-20260920.md` | ✅ |
| **M2** | A5 重写 `UPSTREAM_SYNC.md` | 仓库根 `UPSTREAM_SYNC.md` | 实测：Vermes **2.5.0**（version.txt+三 package.json）、上游 **v0.21.3**（tag `v2026.9.14`）、remote `upstream/upstream2`→`NousResearch/hermes-agent`；**未改 remote**；旧 0.18/v2.3 数字已废弃 | ✅（WorkBuddy 交叉审计已复核四处版本号；**工单板原文写的 2.4.9 才是错的，已订正**） |
| **M3** | A6 补 3 条 CI lane | `.github/workflows/js-tests.yml`（真跑 vitest）`tests-os.yml` / `install-e2e.yml`（**`if: false` 占位**，防误伤） | 三文件在盘；不重复 `uv-lockfile-check.yml`；占位 lane 不会在 PR 上自动开跑 | ✅（占位待评估后启用） |
| **M4** | A7 GUI 设置入口 + home channel | `frontend/src` Settings 移动接入 + `vermes_cli/gateway_channels.py` + `vermes_cli/blueprints/gateway_channels.py`（HTTP 面） | GUI 可设「默认通知频道」；读侧只走 `resolve_home_channel_chat_id`。**交叉审计返工已合入**：① config.yaml 改 `utils.atomic_roundtrip_yaml_update`（ruamel 保注释 + 原子写）② `save_env_value` 失败显式 `ok=false`/`env_error`，且不写 environ ③ 列表接口复用已加载 config（P3）。注释保留 / 吞错 / 双写均有真行为测试 | ✅ 返工完成 |

> **M1 提示**：脚本已存在于 Hermes skill root，**先读再搬，别从零写**（0.5d → 0.25d）。
> **M2 提示**：`git remote -v` 实测 `upstream` / `upstream2` → `NousResearch/hermes-agent`，**管道没坏，烂的是文档**。
> **M3 提示**：上游 35 条，本地原有 14 条；本切片 +3 文件（其中 2 条占位）。
> **M4 点验（WorkBuddy 2026-09-20 09:50）**：`e697aa9066` 复跑 22 passed；helper 真实存在（`utils.py:203`）；两条新用例是**真行为测试**（回读文件断言注释、monkeypatch 抛错断言 `ok=False` 且 environ 未被污染）；**变异测试 ×2** 均已复红（↩ 换回 `yaml.dump` → 注释用例红；↩ 吞掉 env 错误 → 吞错用例红）。详见 `docs/CROSS_AUDIT_M_20260920.md` 末节。

### §3b 新增工单（交叉审计派生，待领）

| ID | 工单 | 落点 | 为什么现在要做 | 状态 |
|---|---|---|---|---|
| **M5** | `_save_config_yaml` 也换 ruamel round-trip | `utils.py` + `vermes_cli/blueprints/gateway_channels.py`（PUT `:337` / DELETE `:374` / toggle `:399`） | **已完成（mimo）**：新增 `load_roundtrip_yaml` / `atomic_roundtrip_yaml_dump` / `atomic_roundtrip_yaml_mutate`；写路径改 `_load_config_rt` + 原子 dump。三条端点写后注释仍在；`yaml.dump(data, f` 残留 grep 为零。真行为测试 `test_m5_config_yaml_roundtrip.py` | ✅ mimo |

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
| **WorkBuddy 审 mimo** | M1–M4（`9bbf2f1ece`） | ① M2 数字是否实测 ② M1 能否真跑 ③ M3 是否误伤现有流水线 ④ M4 是否另起 home channel 口径 |

### WorkBuddy → mimo 审计结果（2026-09-20 09:40，已出 `docs/CROSS_AUDIT_M_20260920.md`）

| ID | 判定 | 要点 |
|---|---|---|
| M1 | ✅ | 脚本实跑复现；唯一差异「近 90 天新增」实跑 **5 行 / 4 提交**（基线写 3/3），属 HEAD 时间漂移，非错报 |
| M2 | ✅ | 四处版本均 2.5.0 实测通过；**工单板 2.4.9 是错的，已订正** |
| M3 | ✅ | 两 lane `if: false` 确认；`js-tests` 依赖齐备（`vitest run` + lock 在）真能跑；未重复 `uv-lockfile-check.yml` |
| M4 | ⚠️ P1×1 / P2×3 | 读侧单一口径 ✅、写读路径同源 ✅、blueprint↔api.js 一致 ✅、20 passed 复核通过；**P1：`yaml.dump` 全量重写 config.yaml —— 实证注释 21 行→0（681→661 行）** |

**给 mimo 的返工项（均在 `vermes_cli/` 内，WorkBuddy 不动）：**
- **P1** config.yaml 注释丢失：改用 `ruamel.yaml` round-trip（本机已装 `0.18.17`），或退而用仓库已有的 `from utils import atomic_yaml_write`
- **P2** 非原子写（裸 `write_text`，半途被杀会截断 config.yaml）
- **P2** `except Exception: pass` 吞掉 `save_env_value` 失败，但返回体读的是刚 set 的 `os.environ` → 「没落盘却报 ok」的静默失败（与 M1 自己度的口径应一致对待）
- **P3** `_schema_to_dict` 每 schema 调一次 `read_home_channel` → 列表接口约 30 次配置加载

> **复跑测试前必读**：沙箱下 pytest 会在默认 tmpdir 报 `PermissionError: EEXIST ... pytest-of-root`，看起来像测试挂了其实是环境问题。
> 配方：`BT=~/wb-tmp/bt-$RANDOM; TMPDIR=~/wb-tmp .venv/bin/python -m pytest <paths> -q -p no:xdist -o addopts="" --basetemp=$BT`（`--basetemp` 必须每次新鲜）。
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
