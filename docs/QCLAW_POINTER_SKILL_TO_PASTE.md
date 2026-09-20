---
name: vermes-collab-redlines
description: "Use before editing the Vermes repo: red lines + owners."
tags: [vermes, collaboration, redlines, pointer]
---

# Vermes 协作红线（指针 skill）

改动 Vermes 仓库（`~/Projects/vermes-electron`）之前先读这个。
**全文以 `docs/TASK_BOARD_20260920.md` §0 为准**；本文件只放最关键的摘要 + 入口，不复制正文。

1. **不碰 `vermes_state.py`** —— 2.5 的 B4/B8 正在改它（route_ledger 新表）。
2. **动手前 `git status --short` 看在途改动是谁的**；不碰别人的在途文件。**QClaw 的足迹尚未声明** → 先到工单板 §1 补一行。
3. **不 push** —— 本地提交由执行者完成，审计 + push 由董董负责。
4. **不覆盖 Vermes 领先项**：workflow DAG / memory fabric / 自进化 / 中文平台 / 技能库 / 打包链 / ScholarForge / cadir_build。
5. **否定性结论必须双方法交叉验证**（`ls -1` 全量列举 + Grep 工具各一次）；单靠一次 shell `grep`/`find` 的"找不到"不得当结论（本项目已有两次此类误判）。
6. **声称"已落盘"必须回读验证**：`ls -la` + `wc -c`，粘贴真实输出（Edit 回执 ≠ 落盘）。
7. **路径坑**：`~/projects` 与 `~/Projects` 是同一物理目录（软链）；`find ~` 会超时截断（137），不要用全盘搜索下否定结论。

## 协作面（唯一真源，不要另起方案）

| 用途 | 路径 |
|---|---|
| 工单板（红线 + 分工 + 足迹划分） | `docs/TASK_BOARD_20260920.md` |
| 上游对齐路线图（正文真源，含 errata） | `reports/vermes-upstream-catchup-roadmap_FINAL_20260920.md` |
| 跨 agent 技能索引 | `docs/AGENT_SKILLS_INDEX.md`（QClaw root 已代登，**请自行更正/补全**） |
| gateway 已知失败台账（别重跑全量测试） | `reports/known-failures-gateway-20260920.md` |
| 发行版化评估工单（**已外发，勿重复做**） | `reports/vermes-ecosystem-assessment-TASK_20260920.md` |

## 落地位置

- 建到自己的 root：`~/.qclaw/skills/vermes-collab-redlines/SKILL.md`（工作区场景可放 `~/.qclaw/workspace/skills/…`）
- 建完在 `docs/AGENT_SKILLS_INDEX.md` **追加一行自己的登记**（规则：谁的 skill 谁维护自己的行）
- 另请到 `docs/TASK_BOARD_20260920.md` §1 声明你的文件足迹，避免与 WorkBuddy（`gateway/`、`cron/`、`tests/gateway/`）和 mimo（`scripts/`、`.github/`、`frontend/src`、`vermes_cli/gateway_channels.py`）撞车
