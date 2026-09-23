# 交接接力报告 · MiMo `ses_ffe5f3d570e87ffe3XqSVLCQj2`

> 时间：2026-09-23 · 状态：工具调用连环失败中断，**业务未写坏、未半改代码**  
> 目的：另一会话直接接手「Hermes 发行版化」后续，不必重挖上下文。  
> 版本：v2（更新至 HEAD `d3d7822aac`，含 T2/S2/A-B 落地后盘面）

---

## 1. 中断时在干什么

上一条用户消息是 **QClaw 交叉审计**（`reports/qclaw/cross-audit-distribution-plan_20260923.md`）：

- 现状核实：大体属实，2 处要更正（工作树脏、上游数字旧）
- 方案要补：**指标 5 不退化 A/B**、停止条件收紧、两本账真源、canary pinned  
- 拍板项：**T9 不跟** control-file 放松；**D5 = 飞书+Telegram**  
- 开工序：**T2 canary → S2 工单 →（并行）AGENTS.md**；S2 提前是为了尽早验证「注入逐字等价」

**已部分落地**（commit `d3d7822aac`）：T2 pinned canary + S2 工单 + A/B 语料 32 条已提交推送。  
**尚未完成**：正式回应 QClaw 审计正文（§4 即立场）、T2 canary 脆弱点补丁、S2.0 gold。

（期间又陷入 bash 少传 `description` 的空转，已多次发生，见 §6。）

---

## 2. 仓库 / 工作树（接手必读）

| 项 | 值 |
|---|---|
| 分支 | `main`，HEAD **`d3d7822aac`**（`feat(distribution): T2 pinned canary + S2 插件化工单 + A/B 语料 v1`） |
| 与 origin | `rev-list --left-right --count origin/main...main` = **0 / 0**（已 push） |
| 脏文件 | `tools/feedback_tool.py`（他方/勿夹带）；`vermes_cli/web_dist/**` 大量 D/M（构建产物） |
| untracked | `reports/canary/*.md`、`reports/.canary-state.json`、`reports/dist-boundary-*.md`、`reports/qclaw/*.md`、`reports/upstream-intent-*.md` 等（可择要入库，勿夹 web_dist） |
| 纪律 | **只 add 本次任务文件**；禁止 `git add -A`；发布相关勿夹 `web_dist` |

近期主线提交（新→旧）：

| commit | 内容 |
|---|---|
| `d3d7822aac` | **T2 pinned canary + S2 工单 + A/B 语料 v1**（roadmap §8.4/§8.7） |
| `71bbdc7288` | docs：路线图入库 + 交叉审计补丁（指标5 A/B、停止条件、canary pinned）+ T9 拍板 + 账本 ID 契约测 |
| `1db6ec8053` | B 方案：SystemExit 退出码透传 + gui.spec 渠道 hiddenimports |
| `b8e1950510` | 日志三刀：token 脱敏 / http_debug 默认关 / 日期日志轮转 |
| `f8200bff44` | **L-013** frozen `execute_code`：内嵌/PATH 解析 + 硬失败禁 GUI exe |
| `bea2c1acf9` | T15 类型免税（TAKEALONG 仅 移植/重写/部分采纳） |
| `aac578109b` | UX 打扰治理 merge（onDelivery 唯一弹出等） |

---

## 3. 发行版化盘面（精简）

**已闭环**：冻结锚 `888bf8a344`、两本账、G1 未登记税=0（有报告）、T15、L-001~L-010 取长/自有修复、L-013 frozen 解释器、**T2 pinned canary（已入库）**、**S2 工单 + A/B 语料 32 条（已入库）**、UX 轨、日志/网关 B 方案首刀。

**真源**：

- 分区/账本/G1：`docs/DISTRIBUTION_MANIFEST.md`（§7b DIVERSION、§7c TAKEALONG，HTML 注释块）  
- 路线：`docs/plans/2026-09-22-distribution-roadmap-next.md`（含 §8 交叉审计补丁）  
- S2 工单：`docs/plans/2026-09-23-s2-pluginization-workorder.md`  
- canary：`scripts/upstream_canary.py` + `reports/.upstream-canary-pin.json` + `.github/workflows/upstream-canary.yml`  
- 语料：`reports/ab-corpus/v1/corpus.yaml`  

**挂起**：L-009/L-011、T1 历史税外置、T8/T9/T10/T16、S2 迁码（**等 caller/等价验收**）、AGENTS.md 12 目录、形态 B 暂缓。

---

## 4. 对 QClaw 审计的建议立场（未正式发出，供接手直接用）

| 议题 | 建议 |
|---|---|
| 指标 5 A/B | **采纳**。≥30 条语料已冻结（32 条）；每 Sprint 收尾 v2.5.2 vs 新包并排；退化即停 |
| 停止条件 | **采纳**：连续 **两个 Sprint**；取长覆盖面看 S2–S4 全部，不许「挑简单一次」 |
| canary pinned | **采纳**（已落 pin 文件 + `d3d7822aac`）；季度升钉、只告警不阻塞 |
| 两本账「无真源」 | **更正**：真源=manifest §7b/§7c；`D-001`/`L-001` 可检索。可补「ID 必须在块内」测试 |
| **T9** | **不跟** #45947 / google_oauth 放松；DIVERSION 登记产品取舍 |
| **D5** | **飞书 + Telegram**（硬验收可自动化；微信审核重） |
| 开工序 | **T2 canary → S2 工单（+gold）∥ AGENTS.md**；S2 优先是为了「逐字等价」排雷 |

数字口径：报告里的 37,857 / 30× 等作废；**当场 `rev-list` + HEAD**。

---

## 5. 建议接力顺序（下一刀做什么）

1. **正式回应/归档 QClaw 审计**（指标 5、T9、D5、开工序写进 roadmap §8.6–8.7，或单独回文）  
2. **T2 收尾**：canary 两脆弱点（venv 绑定、句柄只读 WARN / 写 FAIL）若未在后续 commit 里，要补  
3. **S2.0 gold**：`v2.5.2` → `reports/s2/gold/`，**动注入代码前门闩**  
4. S2.1 adapter（API 同形、禁 Callable 进 stable）  
5. 并行：T14 遗留 L-009 清单、T1 历史税、T12① presence、**AGENTS.md 12 目录**  

**L-013 已在 main**（frozen 解释器）；真机 execute_code 需 **重打 DMG** 后验收（QClaw 曾接 DMG）。

---

## 6. 工具纪律（本会话两次翻车根因）

- `bash` **必须**带 `description`；缺参会无限空转。  
- 一次 bash 里不要塞几十个命令；分步、带描述。  
- 写码前若 worktree 告警：用户说「直接处理/接力」= 继续 main；不要擅自建 worktree。

---

## 7. 本会话未竟事项

- 未正式回复 QClaw 审计正文（上面 §4 即立场）  
- 未改 canary 脆弱点 / 未做 S2.0 gold / 未写 AGENTS.md  
- 未动 `tools/feedback_tool.py`、`web_dist`（保持脏）  
- 记忆：`ses_ffe5f3d570e87ffe3XqSVLCQj2`；本文件即主交接源  

**接手第一句建议**：读本文件 + `docs/plans/2026-09-22-distribution-roadmap-next.md` §8 + `reports/qclaw/cross-audit-distribution-plan_20260923.md`，然后按 §5 开工。
