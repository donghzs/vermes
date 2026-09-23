# 对外数字口径 + A/B 哨兵进 canary · 2026-09-23

> 分支 `fix/numbers-and-ab-sentinels`。基线 `04f4604715`。收 Hermes 排序的两件。

## 1. 对外数字定口径（窗口 + HEAD + 复现命令）

窗口 **2026-08-24 → 2026-09-23**；上游 `5a0c2fb89e`，Vermes `04f4604715`。

| 口径 | 快照值 | 复现命令 |
|---|---|---|
| 上游近 30 天 | **12,603** | `git -C ~/.hermes/hermes-agent log --since='30 days ago' --oneline \| wc -l` |
| 上游总提交 | **37,857** | `git -C ~/.hermes/hermes-agent rev-list --count HEAD` |
| Vermes 近 30 天 | **557** | `git log --since='30 days ago' --oneline \| wc -l` |
| Vermes 总提交 | **1,839** | `git rev-list --count HEAD` |
| 30 天倍率 | **≈22.6×** | 12603/557 |

历史 13,011 / 14,603 / 25× / 30× 均为各自时刻滚动窗口快照，**不得再当「当前值」引用**。已改 4 处：roadmap §8.1（主口径）+ 3 份报告脚注指向 §8.1。

## 2. A/B 哨兵 → canary step 6（指标 5 停止条件）

`step_ab_sentinels`：语料哨兵映射可执行断言，**按哨兵分跑、红因归因**。

| 哨兵 | 映射 | 实测 |
|---|---|---|
| A08 / B07 | `test_s2_gold::test_stable_sha_stable_across_two_builds` | PASS |
| C02 | `TestDetectDangerousRm`（rm -rf） | PASS |
| C03 | `test_file_safety_secret_stores` + ssh_key/shadow deny | PASS |
| C04 | `test_concurrent_threads_do_not_contaminate` + session key | PASS |
| C05 | `test_cron_session_contextvar` 全文件 | PASS |
| D02 / D03 | 无确定性单测 | **WARN 未映射**（不静默当 PASS） |

汇总：**6 绿 / 2 未映射** → WARN。`--skip-ab` 可跳过。

## 3. 顺带真发现（未在本刀修）

`TestCheckSensitivePathMacOSBypass::test_private_var_blocked` 红：
`_check_sensitive_path('/private/var/db/something')` 返回 None —— macOS `/private/var` 旁路未挡（issue #8734 同类）。write-deny 真缺口，另刀修。

## 4. 改动

| 文件 | 摘要 |
|---|---|
| `docs/plans/2026-09-22-distribution-roadmap-next.md` | §8.1 窗口+命令+快照 |
| `reports/qclaw/{cross-audit-distribution-plan,handoff,response-to-cross-audits}` | 脚注指向 §8.1 |
| `scripts/upstream_canary.py` | `AB_SENTINEL_TESTS` + `step_ab_sentinels`（step 6） |

— 完 —
