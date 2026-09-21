# 发行版边界闸门（2026-09-21）

> 区间 `888bf8a344..main`（Vermes 侧 25 commits）。
> 冻结锚 `888bf8a344`（非发版 tag）。
> 判据：改动落在**上游跟随区**（`plugins/ tools/ harness/ cron/ .github/ docs/ scripts/`）
> 且 **两账都未登记** = 契约税。已登记（DIVERSION_LEDGER）= 有意偏离，单列不税。

## 1. 分区分布

| 分区 | 文件改动数 | 判定 |
|---|---|---|
| `own` | 87 | ✅ 发行版自有，正常 |
| `other` | 29 | — |
| `follow` | 16 | ⚠️ 契约税（未登记） |
| `core` | 1 | 🔍 核心 diverge，个案评估 |

## 2. 未登记契约税明细（follow 区改动 && 两账未登记）

_无。当前 Vermes 在跟随区零未登记改动 —— 边界干净。_

## 3. 已登记偏离（两账：DIVERSION + TAKEALONG，不算税）

| hash | 主题 | 路径 |
|---|---|---|
| `f9d6a8e56127` | fix(security): strip profile authorization gates from cross- | `docs/DISTRIBUTION_MANIFEST.md` |
| `f9d6a8e56127` | fix(security): strip profile authorization gates from cross- | `tools/env_passthrough.py` |
| `9eaf328a3ddf` | fix(distribution): MiMo 三点残留注意项收口 | `docs/DISTRIBUTION_MANIFEST.md` |
| `9eaf328a3ddf` | fix(distribution): MiMo 三点残留注意项收口 | `tools/approval.py` |
| `127ebb7d61cb` | fix(security): approval 队列 pop/提交同临界区 + boundary 两账合并免税 | `docs/DISTRIBUTION_MANIFEST.md` |
| `127ebb7d61cb` | fix(security): approval 队列 pop/提交同临界区 + boundary 两账合并免税 | `tools/approval.py` |
| `da614a818861` | fix(distribution): own 红线不被精确映射放行 + 账本补登记 + 独有工具外置 | `docs/DISTRIBUTION_MANIFEST.md` |
| `c1e2d3248c4e` | fix(distribution): intake 证据口径拧紧 — 映射收窄 + 报告头写全参数 + 文件名防覆盖 | `docs/DISTRIBUTION_MANIFEST.md` |
| `78664dc3f225` | fix(security): write-deny vault/ and browser-profile/ secret | `docs/DISTRIBUTION_MANIFEST.md` |
| `936969148d7e` | docs(distribution): walking skeleton 评审 + manifest 待办更新（T6/T | `docs/DISTRIBUTION_MANIFEST.md` |
| `52676b3badce` | fix(distribution): 修 DIVERSION_LEDGER 匹配精度 — 文件条目不再升格父目录免税 | `docs/DISTRIBUTION_MANIFEST.md` |
| `9cb4992bebfa` | feat(distribution): 机制纠偏三件 — 冻结锚 + G1零未登记税 + 两本账 | `docs/DISTRIBUTION_MANIFEST.md` |
| `a48811769d2a` | fix(security): match credential env names case-insensitively | `tools/env_passthrough.py` |
| `a48811769d2a` | fix(security): match credential env names case-insensitively | `tools/environments/docker.py` |
| `a48811769d2a` | fix(security): match credential env names case-insensitively | `tools/environments/local.py` |
| `91bb3807c1a5` | docs+tools(distribution): 上游雷达 + 边界闸门 + 发行版契约（v2.5.1 后启动发行版化 | `docs/DISTRIBUTION_MANIFEST.md` |

## 4. 闸门结论

**PASS** —— 零未登记契约税。已登记偏离单列，不阻碍跟随。
