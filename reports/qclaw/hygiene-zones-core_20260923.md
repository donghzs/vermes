# 卫生刀：假税归 own + core 登记 + 防呆 · 2026-09-23

> 状态：**已落地**。分支 `chore/hygiene-zones-core`（worktree `/Users/dongzusheng/Projects/vermes-electron-hygiene`）。
> 基线：`cc97079a13`。收 Hermes 连带发现 + 交接报告入库 + 工单 P3 措辞对齐。

---

## 1. 假税 3 条归 `ZONES.own`（Hermes 连带 🔴）

| 路径 | 上游同名 | 处置 |
|---|---|---|
| `tools/feedback_tool.py` | 无 | → own |
| `scripts/prebuild-check.sh` | 无 | → own |
| `scripts/verify-build.sh` | 无 | → own |

`classify()` 实测：三者 → `own`；`tools/registry.py` 仍 `follow`（正确对照）。

**边界闸门复跑**：`未登记税=0`、`core 登记率 100%（9/9）` —— 假税清零。

## 2. `agent/prompt_builder.py` 进 §7d（C-009）

S2.4 四键回写 + skill-routing 渠道门 + W-L4/L5/M7 常量 = 有意分叉。契约测
`EXPECTED_CORE_FILES` 同步加 `prompt_builder.py`（9 个 core 文件全覆盖）。

## 3. boundary 新增文件首选处置（机制断根）

`git diff --diff-filter=A` 区分「新增 vs 改既有」；未登记税表加类型列 +
首选提示：**新增 ⇒ 先查上游同名，无则归 `ZONES.own`，有则登记 DIVERSION / 外置插件**。
防止「交付即欠税」第四次复发。

## 4. 零碎卫生

| 件 | 处置 |
|---|---|
| `feedback_tool.py` docstring | `like/dislike` 笔误改为 `thumbs_up/thumbs_down` |
| `s2_snapshot.py` | `sys.version_info < (3,10)` 直接报「请用 `.venv/bin/python`」并 exit 2（Hermes 第三次踩 3.9） |
| `s24-handover-20260923.md` | 入库（主仓最后一条未跟踪） |
| 工单 P3 措辞 | 对齐策略分层定稿（去掉已废弃的「load_all 过滤 / 拒登」） |

## 5. 测试口径

```
test_core_diverge_ledger + test_feedback_learning + test_w_l4_l5  →  25 passed
upstream_watch.py boundary                                       →  未登记税=0，core 100%
s2_snapshot --check                                              →  17×3 逐字绿
```

## 6. 改动清单

| 文件 | 摘要 |
|---|---|
| `scripts/upstream_watch.py` | ZONES.own +3；`_added_files_in_range`；boundary 新增列/提示 |
| `docs/DISTRIBUTION_MANIFEST.md` | C-009 `prompt_builder.py` |
| `tests/tools/test_core_diverge_ledger.py` | EXPECTED_CORE_FILES +prompt_builder |
| `tools/feedback_tool.py` | docstring 笔误 |
| `scripts/s2_snapshot.py` | Python ≥3.10 防呆 |
| `docs/plans/2026-09-23-s2-pluginization-workorder.md` | P3 措辞对齐 |
| `reports/qclaw/s24-handover-20260923.md` | 交接报告入库 |

— 完 —
