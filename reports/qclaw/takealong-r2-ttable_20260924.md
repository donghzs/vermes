# 取长第 2 轮 · T 表直修 + intake 判定（2026-09-24）

> 分支：`feat/takealong-r2-intake` → main
> 真源：`reports/upstream-intent-20260924-v2026.9.14-max8000.md` + `reports/qclaw/takealong-r2-intake-verdict_20260924.md`
> 数字口径（当场）：`python3 scripts/upstream_watch.py boundary` @ HEAD 见 commit · 报告日 2026-09-24

## 1. Intake 重跑

| 口径 | 值 |
|---|---|
| 命令 | `python3 scripts/upstream_watch.py intake --since v2026.9.14 --max 8000 --upstream-repo ~/.hermes/hermes-agent` |
| 上游 | `v2026.9.14` → `5a0c2fb89e`，3154 commits |
| 候选 | 220（GHSA 1 / fixsec 6 / 有对应物 **21** / 红线 7） |
| 判定表 | `reports/qclaw/takealong-r2-intake-verdict_20260924.md`（每条含**上游后续变更次数**） |

## 2. T 表三类分派 — 本轮落地

| 类 | 项 | 结果 | 契约测 |
|---|---|---|---|
| 产品正确性 | T5 `sync-version.sh` 静默失败 | **L-015** Python ast/json 重写 | 3 passed |
| 产品正确性 | T10 profile 门控三条 | **L-016** 去 default 豁免 + fail-closed | 5 passed |
| 产品正确性 | T12①③ 会话状态 | **L-017** presence sanitize + TERMINAL_CWD contextvar | 4+5 passed |
| UX（配额） | T16 三条 | **L-018** 审批按会话 / 路径段匹配 / 测试现名 | frontend 23 passed |
| 取长续期 | T8 `file_safety` | **L-027**（原误用 L-014，已换号）`_guard_homes` multi-home | 5+3 passed |

**用户可见配额（本周）**：T16① 审批计数按本会话 + ③ 中间产物误标修复 — **≥1 条达标**（可在 GUI 感知）。

## 3. 新增取长/修复（§7c L-015~L-027；L-014 为历史 max_chars 行，本轮找回）

| id | 来源 | 类型 | 人时 | 上游后续变更 |
|---|---|---|---|---|
| L-027 | `7c478ac257a3` | 重写 | ~0.5h | 2 |
| L-015 | — | 修复（自有） | ~0.4h | — |
| L-016 | `3ed40556cea1` 续期 | 部分采纳/重写 | ~0.4h | 0 |
| L-017 | `b6b7802447f4` 语义 + 自有 | 修复（自有） | ~0.6h | 27 |
| L-018 | — | 修复（自有 UX） | ~0.5h | — |
| L-019 | `c0362da9a6e9` | 重写 | ~0.4h | 4 |
| L-020 | `2dfb`+`1e2c`+`6332` | 重写 | ~0.5h | 8 |
| L-021~L-026 | 9 条 | 拒绝/暂缓（各行有后续变更次数） | — | 4~33 |

**对外数字口径（Hermes 更正 + 收口后实测）**：截至 2026-09-24，**取长登记 26 条（L 行，含 L-014 历史 max_chars 与 L-027）/ 偏离登记 9 条（D 行）/ core 登记 9 条（C 行）**。此前写「12→28」口径不准（混入非 L 行、L-014 曾误用）。命令：`grep -c '^| L-' docs/DISTRIBUTION_MANIFEST.md` 同类实测于收口 commit。

## 4. 验收

| 闸门 | 结果 |
|---|---|
| boundary | **未登记税 1 → 收口后 0**（税条= `scripts/sync-version.sh` 改既有；上游无同名，已归 `ZONES.own`）· 已登记偏离 71 · core 登记率 100% (9/9) |
| 契约测 | Python 相关 **53–98 passed**（分布机制 + 本批新测） |
| frontend | **23 passed**（T16 三测文件） |
| 纪律 | 每步一条分支；数字带当场命令 + HEAD；双探针；发现即刀（T5 pipefail 根因当场加固） |

## 6. Hermes 交叉审计收口（2026-09-24 同日）

| 项 | 处置 |
|---|---|
| 🔴 boundary 税=1（`sync-version.sh` 改既有） | 归 `ZONES.own`（上游 `git ls-tree` 无同名，当场查证）+ 机制：「改既有」也出「查上游同名→归 own」提示 |
| 🟠 L-014 ID 回收 | file_safety 换 **L-027**；历史 max_chars 找回为 **L-014 行**；账本立「ID 一经使用不再回收」+ 契约测钉唯一性 |
| 数字口径 | 更正为「取长 25 / 偏离 9，截至 2026-09-24」 |

## 5. 明确不做（沿用交接）

- 形态 B / P5：等本轮 + 一次真实上游升级数据
- gold 瞬态：未复现，不耗注意力
- 再加治理机制：无新痛点

— 完 —
