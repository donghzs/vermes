# 上游雷达 · Hermes Agent（2026-09-21）

> 由 `scripts/upstream_watch.py watch` 生成。
> **硬约束**：Vermes 与上游无共同祖先（`git merge-base` 为空，非 shallow），
> `git merge` 不可用；本清单用于**能力级取长**，不是可自动合入的补丁队列。

## 1. 概览

| 项 | 值 |
|---|---|
| 基线 | `v2026.9.14` |
| 上游 HEAD | `3b7eda0887` |
| 扫描 commit 数 | 300（上限 300） |
| 涉及文件数 | 810 |
| 红线命中（碰发行版自有资产） | **36** |

## 2. 分区分布

| 分区 | commit 数 | 含义 |
|---|---|---|
| `other` | 293 | 其他（根文件/配置/构建） |
| `follow` | 69 | 上游跟随区 — 优先跟随，Vermes 侧改动=契约税 |
| `core` | 56 | 同源核心（已 diverge）— 个案评估 |
| `own` | 15 | 发行版自有（红线）— 只参考思路，禁止直接搬运 |

## 3. 价值分布

| 类型 | commit 数 |
|---|---|
| bugfix | 218 |
| chore | 43 |
| other | 25 |
| feature | 11 |
| security | 2 |
| perf | 1 |

## 4. 取长候选 Top 20（未触碰红线 / 价值优先）

| 分 | 日期 | hash | 主题 | 改动文件 |
|---|---|---|---|---|
| 3 | 2026-09-19 | `940c6109943a` | fix(security): keep _HERMES_PROVIDER_ENV_BLOCKLIST importable from too | 1 |
| 3 | 2026-09-19 | `b534f4b8c8cd` | fix(security): match credential env names case-insensitively | 10 |
| 2 | 2026-09-11 | `ae37fac2ac69` | fix(desktop): local-graph fallback when the update compare API 404s a  | 3 |
| 2 | 2026-09-18 | `7473088b4d84` | fix(desktop): honor proxy env for update API checks | 5 |
| 2 | 2026-09-18 | `bd2b8124f432` | fix(auth): prevent repeated copilot raw token exchange warnings (#1147 | 4 |
| 2 | 2026-09-18 | `5f97bb0fa70c` | fix(desktop): keep un-acked optimistic messages across a resync | 3 |
| 2 | 2026-09-18 | `f8d479b69ba9` | fix(tools): resolve skills.sh skills whose SKILL.md sits at the repo r | 3 |
| 2 | 2026-09-18 | `c28a0f74bb67` | fix(desktop): an env-pinned remote can sign in again from Gateway sett | 2 |
| 2 | 2026-09-18 | `0a37960b2e4c` | fix(mcp): re-mint an ended dashboard OAuth flow instead of parking the | 6 |
| 2 | 2026-09-18 | `1cf8a9fb417f` | fix(tui): count streamed frames as heartbeat liveness | 4 |
| 2 | 2026-09-18 | `bfaa0492d2af` | fix(bot-relay): peer gateways name each machine by its label, not its  | 3 |
| 2 | 2026-09-18 | `9a46df0a21c9` | fix(process): verify tree death before writing killed receipt | 3 |
| 2 | 2026-09-18 | `7ba1b4361aea` | fix(desktop): pin transcript viewport while text is selected (#115464) | 2 |
| 2 | 2026-09-19 | `03973bd02b87` | fix(delegate): child_timeout_seconds bounds inactivity, not total runt | 6 |
| 2 | 2026-09-19 | `5171ea18dc4b` | fix(desktop): scope plugin specifier scanning to code, not strings/com | 2 |
| 2 | 2026-09-19 | `09b72bc6d2f0` | fix(compression): track commit fences as a registration stack | 2 |
| 2 | 2026-09-19 | `522e121e90a0` | fix(desktop): pin the update-check proxy deps exactly and document the | 3 |
| 2 | 2026-09-19 | `00c0ea6cbe58` | fix(desktop): "Open containing folder" is offered only for a session o | 8 |
| 2 | 2026-09-19 | `d15b17adf56f` | fix(lsp): log at INFO when a request is skipped because its root is ma | 3 |
| 2 | 2026-09-19 | `d03b5f3770a8` | fix(auth): skip the Copilot token exchange while copilot is only an am | 4 |

## 5. 红线告警（上游改动落在发行版自有资产同名路径）

| hash | 主题 | 命中红线路径 |
|---|---|---|
| `86a599cbaebc` | fix(gateway): human-delay pacing comes from each profile's h | `gateway/platforms/base.py` |
| `5a074a02cb47` | fix(gateway): busy-text debounce/hard-cap come from per-prof | `gateway/platforms/base.py` |
| `b6a5b41e2331` | fix(gateway): validate human delay bounds | `gateway/platforms/base.py` |
| `36ede56a66ca` | fix(gateway): share the drift-tolerant start-time comparator | `gateway/platforms/api_server_runs.py` |
| `85564321be7b` | fix(windows): route every bare-bash spawn through _find_bash | `gateway/platforms/webhook_filters.py` |
| `fd94fe9b93bb` | fix(gateway): every adapter send_voice accepts the dispatch' | `gateway/platforms/weixin.py` |
| `bcdb1decaf3c` | fix(gateway): suspend typing refresh before final delivery ( | `gateway/platforms/base.py` |
| `5e6b504bf92b` | fix: shutdown-killed live-owned peer run reports interrupted | `gateway/platforms/api_server_runs.py` |
| `4179dc9b090e` | fix(bot-mode): every transport into a live Bot Chat waits on | `gateway/platforms/api_server.py` |
| `4179dc9b090e` | fix(bot-mode): every transport into a live Bot Chat waits on | `gateway/platforms/api_server_runs.py` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/af.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/ar.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/de.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/en.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/es.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/fr.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/ga.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/hu.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/it.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/ja.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/ko.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/pt.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/ru.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/tr.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/uk.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/zh-hant.yaml` |
| `dbcbd9d9db94` | feat(gateway): /branch opens a sibling thread by default; -- | `locales/zh.yaml` |
| `92b4f1993bdf` | fix: a failed executor submission releases the API worker co | `gateway/platforms/api_server.py` |
| `92b4f1993bdf` | fix: a failed executor submission releases the API worker co | `gateway/platforms/api_server_runs.py` |
| `589d4d05d43f` | fix(shutdown): track API-server worker lifetime past handler | `gateway/platforms/api_server.py` |
| `589d4d05d43f` | fix(shutdown): track API-server worker lifetime past handler | `gateway/platforms/api_server_runs.py` |
| `bd3eb4132259` | fix(gateway): a busy redirect re-anchors the running turn's  | `gateway/platforms/base.py` |
| `bd3eb4132259` | fix(gateway): a busy redirect re-anchors the running turn's  | `gateway/platforms/event.py` |
| `3c6952b33cb1` | fix(api): expose shutdown drain on durable run status | `gateway/platforms/api_server.py` |
| `3c6952b33cb1` | fix(api): expose shutdown drain on durable run status | `gateway/platforms/api_server_runs.py` |
| `587bb1057501` | fix(platforms): Discord/WhatsApp/DingTalk gates honour allow | `gateway/platforms/whatsapp_common.py` |

## 6. 高频改动文件 Top 30（漂移热点）

| 文件 | 提交数 | 分区 |
|---|---|---|
| `website/docs/user-guide/bot-mode.md` | 10 | other |
| `hermes_constants.py` | 6 | other |
| `gateway/platforms/api_server_runs.py` | 6 | own |
| `tests/tools/test_skills_guard.py` | 6 | other |
| `tools/skills_guard.py` | 6 | follow |
| `tests/agent/test_context_references.py` | 6 | other |
| `apps/desktop/src/plugins/hermes-bots/group-turns.ts` | 6 | other |
| `gateway/platforms/base.py` | 5 | own |
| `website/docs/user-guide/configuration.md` | 5 | other |
| `hermes_cli/kanban_db.py` | 5 | other |
| `tools/environments/local.py` | 5 | follow |
| `agent/lsp/client.py` | 5 | core |
| `gateway/run.py` | 4 | core |
| `agent/agent_runtime_helpers.py` | 4 | core |
| `apps/desktop/electron/main.ts` | 4 | other |
| `package-lock.json` | 4 | other |
| `hermes_cli/plugins_cmd.py` | 4 | other |
| `hermes_cli/kanban_db_dispatch.py` | 4 | other |
| `tests/hermes_cli/test_kanban_db.py` | 4 | other |
| `agent/context_references.py` | 4 | core |
| `cron/scheduler.py` | 4 | follow |
| `cron/scheduler_prompt.py` | 4 | follow |
| `apps/desktop/src/plugins/hermes-bots/group-turns.test.ts` | 4 | other |
| `tools/process_registry.py` | 4 | follow |
| `tests/agent/lsp/test_client_e2e.py` | 4 | other |
| `gateway/platforms/api_server.py` | 4 | own |
| `apps/desktop/src/plugins/hermes-bots/group-chat.ts` | 4 | other |
| `apps/desktop/src/plugins/hermes-bots/plugin.tsx` | 4 | other |
| `gateway/run_shutdown.py` | 4 | core |
| `tests/tui_gateway/test_bot_relay_methods.py` | 4 | other |

---

## 处置纪律（见 docs/DISTRIBUTION_MANIFEST.md）

1. 候选先过**红线闸门**：碰 `own` 区 = 禁止直接搬运，只参考实现思路
2. 单个取长 = 一个 commit + 登记 ledger（价值 / 冲突面 / 验收 / 回退）
3. 取长后跑 `python3 scripts/check_coexistence.py --deep` 与相关 pytest
4. 本报告的基线写入 `reports/.upstream-baseline.json`，下次只看增量
