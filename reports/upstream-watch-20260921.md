# 上游雷达 · Hermes Agent（2026-09-21）

> 由 `scripts/upstream_watch.py watch` 生成。
> **硬约束**：Vermes 与上游无共同祖先（`git merge-base` 为空，非 shallow），
> `git merge` 不可用；本清单用于**能力级取长**，不是可自动合入的补丁队列。

## 1. 概览

| 项 | 值 |
|---|---|
| 基线 | `v2026.9.14` |
| 上游 HEAD | `3b7eda0887` |
| 扫描 commit 数 | 2000（上限 2000） |
| 涉及文件数 | 2637 |
| 红线命中（碰发行版自有资产） | **90** |

## 2. 分区分布

| 分区 | commit 数 | 含义 |
|---|---|---|
| `other` | 1787 | 其他（根文件/配置/构建） |
| `core` | 465 | 同源核心（已 diverge）— 个案评估 |
| `follow` | 305 | 上游跟随区 — 优先跟随，Vermes 侧改动=契约税 |
| `own` | 54 | 发行版自有（红线）— 只参考思路，禁止直接搬运 |

## 3. 价值分布

| 类型 | commit 数 |
|---|---|
| bugfix | 1084 |
| other | 528 |
| chore | 215 |
| feature | 156 |
| perf | 15 |
| security | 2 |

## 4. 取长候选 Top 20（未触碰红线 / 价值优先）

| 分 | 日期 | hash | 主题 | 改动文件 |
|---|---|---|---|---|
| 3 | 2026-09-19 | `940c6109943a` | fix(security): keep _HERMES_PROVIDER_ENV_BLOCKLIST importable from too | 1 |
| 3 | 2026-09-19 | `b534f4b8c8cd` | fix(security): match credential env names case-insensitively | 10 |
| 2 | 2026-04-24 | `efd7bb55fd94` | fix(gateway): preserve ld library path in systemd units | 2 |
| 2 | 2026-05-06 | `f59c451f818d` | fix(feishu): avoid threading regular replies | 2 |
| 2 | 2026-05-12 | `0bbf7b7997ec` | fix(agent): extract residency claims from Codex OAuth JWT for workspac | 2 |
| 2 | 2026-06-25 | `82796e06d7ae` | fix(codex): remove dead gpt-5.3-codex from curated fallback list | 1 |
| 2 | 2026-06-29 | `43127a86ea5d` | fix(cli): bound Azure detect response reads | 2 |
| 2 | 2026-07-02 | `f5fbe9a609c5` | fix(codex): treat leaked Codex-CLI shell JSON as an incomplete turn, n | 2 |
| 2 | 2026-07-13 | `8a55373dbf42` | fix(whatsapp): authorize first-contact LID senders | 3 |
| 2 | 2026-07-16 | `9bb0c4a40f42` | fix: keep Copilot ACP fallbacks on chat completions | 1 |
| 2 | 2026-07-21 | `d4a496373d65` | fix(agent): reject router timeout shim responses | 2 |
| 2 | 2026-07-21 | `e1866bf7a69a` | fix(desktop): create a project from a folder in one step | 1 |
| 2 | 2026-07-22 | `7b81848ce587` | fix(desktop): remove composer input backdrop blur | 4 |
| 2 | 2026-07-24 | `81fd9dc77356` | fix(whatsapp): honor group ingress policy in bridge | 3 |
| 2 | 2026-07-27 | `3926c4209c78` | fix: WhatsApp group messages dropped when LID sender has no lid-mappin | 3 |
| 2 | 2026-07-28 | `0acd96a439b2` | fix(agent): classify Codex account token failures | 2 |
| 2 | 2026-07-28 | `6e1de4850e63` | fix(hindsight): bound the append-mode session turn buffer | 1 |
| 2 | 2026-07-31 | `539b82698ea0` | fix: bound the LSP document cache, delta baselines, TUI fuzzy cache an | 4 |
| 2 | 2026-07-31 | `631baa40c21b` | fix(lsp): honor lsp.wait_timeout in baseline snapshot | 3 |
| 2 | 2026-08-05 | `303d8391335e` | fix(env_loader): isolate external-secret snapshots per HERMES_HOME (#7 | 2 |

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
| `af8e47a699d2` | fix(api-server): a streamed peer turn into a Bot Chat open i | `gateway/platforms/api_server.py` |
| `07405b65ed39` | fix(api-server): a peer run into a Bot Chat open in Desktop  | `gateway/platforms/api_server.py` |
| `07405b65ed39` | fix(api-server): a peer run into a Bot Chat open in Desktop  | `gateway/platforms/api_server_runs.py` |
| `b4b34178b7ac` | fix(api-server): a peer DM into a Bot Chat open in Desktop i | `gateway/platforms/api_server.py` |

_（仅列前 40 条，共 90 条）_

## 6. 高频改动文件 Top 30（漂移热点）

| 文件 | 提交数 | 分区 |
|---|---|---|
| `website/docs/user-guide/configuration.md` | 32 | other |
| `hermes_cli/config_defaults.py` | 29 | other |
| `website/docs/user-guide/bot-mode.md` | 29 | other |
| `agent/auxiliary_client.py` | 29 | core |
| `gateway/run.py` | 26 | core |
| `agent/chat_completion_helpers.py` | 26 | core |
| `agent/codex_runtime.py` | 25 | core |
| `agent/context_compressor.py` | 24 | core |
| `hermes_cli/runtime_provider.py` | 23 | other |
| `website/docs/developer-guide/model-provider-plugin.md` | 23 | other |
| `agent/error_classifier.py` | 22 | core |
| `agent/turn_recovery.py` | 20 | core |
| `tests/agent/test_error_classifier.py` | 20 | other |
| `website/docs/user-guide/desktop.md` | 20 | other |
| `agent/model_metadata.py` | 19 | core |
| `hermes_cli/models.py` | 19 | other |
| `gateway/platforms/api_server.py` | 19 | own |
| `agent/agent_runtime_helpers.py` | 18 | core |
| `apps/desktop/src/i18n/en.ts` | 18 | other |
| `apps/desktop/src/i18n/types.ts` | 17 | other |
| `apps/desktop/src/i18n/zh.ts` | 17 | other |
| `agent/credential_pool.py` | 17 | core |
| `gateway/run_shutdown.py` | 17 | core |
| `agent/agent_init.py` | 16 | core |
| `plugins/platforms/telegram/adapter.py` | 16 | follow |
| `apps/desktop/src/i18n/ar.ts` | 16 | other |
| `apps/desktop/src/i18n/ja.ts` | 16 | other |
| `apps/desktop/src/i18n/zh-hant.ts` | 16 | other |
| `hermes_cli/gateway.py` | 16 | other |
| `website/docs/integrations/providers.md` | 15 | other |

---

## 处置纪律（见 docs/DISTRIBUTION_MANIFEST.md）

1. 候选先过**红线闸门**：碰 `own` 区 = 禁止直接搬运，只参考实现思路
2. 单个取长 = 一个 commit + 登记 ledger（价值 / 冲突面 / 验收 / 回退）
3. 取长后跑 `python3 scripts/check_coexistence.py --deep` 与相关 pytest
4. 本报告的基线写入 `reports/.upstream-baseline.json`，下次只看增量
