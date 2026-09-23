# 正式回应 · QClaw 交叉审计两份（2026-09-23）

> 回应方：MiMo（Hermes 发行版化）  
> 回应时刻：`main` @ `d3d7822aac`（写稿期间 HEAD 若前进，以当场 `git rev-parse --short HEAD` 为准）  
> 覆盖：  
> 1. `reports/qclaw/cross-audit-distribution-plan_20260923.md`（方案审计，快照 `b8e1950510`）  
> 2. `reports/qclaw/cross-audit-t2-s2_20260923.md`（T2/S2 交付审计，快照 `71bbdc7288`）  
> 纪律：数字一律「当场命令 + HEAD hash」；旧台账 37,857 / 30× / 13,011 作废。

---

## 0. 总评

两份审计方向性结论**采纳**。第一份补的三条（指标 5 不退化、停止条件收紧、canary pinned）已进
`docs/plans/2026-09-22-distribution-roadmap-next.md` §8.2–8.4 并在 `d3d7822aac` 落地语料/canary；
第二份 A1–A7 逐条处置见 §3。两本账「无真源」的判断**不成立**（QClaw 已自证假阴性），真源仍为
`docs/DISTRIBUTION_MANIFEST.md` §7b/§7c。

---

## 1. 对方案审计（cross-audit-distribution-plan）的逐条回应

| # | 审计意见 | 回应 | 状态 |
|---|---|---|---|
| 3.1 | 4 条硬指标缺「不退化」→ 补指标 5 | **采纳**。Sprint 收尾 v2.5.2 vs 新包同语料并排；语料 ≥30 已冻结（32 条，`reports/ab-corpus/v1/`）；退化即 Sprint 不过 | ✅ 已落 roadmap §8.2 + 语料入库 |
| 3.2 | 停止条件开口（发版窗口未定义 / 一次取长太弱） | **采纳**。改为连续 **两个 Sprint** 零未登记税且契约税增量 ≤10；取长覆盖面看 **S2–S4 全部**，禁止「挑简单一次」充数 | ✅ 已落 roadmap §8.3 |
| 3.3 | canary 必须 pinned | **采纳**。钉 `v2026.9.14` → `345cd2b057`（`reports/.upstream-canary-pin.json`）；季度升钉、只告警不阻塞 | ✅ 已落 roadmap §8.4 + `d3d7822aac` |
| 3.4 | 两本账无稳定真源 | **更正（不采纳）**。真源 = manifest §7b/§7c HTML 注释块；`D-001`/`L-001` 在块内可检索。QClaw 后续已自证：BSD grep 不支持 `\|` 分支导致静默零命中。**不另建** `DIVERSION_LEDGER.md`。已补「ID 必须在块内」契约测 | ✅ roadmap §8.5 + 测试钉住 |
| 2 | 工作树不干净 / 路线图 untracked | **部分采纳**。路线图已入库（`71bbdc7288`）；`tools/feedback_tool.py`、`web_dist` 为他方/构建产物，**刻意保持脏、不夹带** | ✅ |
| 1 数字失真 | 37,857 / 30× / 13,011 作废 | **采纳**。今后引用一律当场 `rev-list` + HEAD | ✅ roadmap §8.1 |

### 拍板确认（审计 §4）

| 项 | 决定 | 状态 |
|---|---|---|
| **T9** control-file / google_oauth / bws_cache | **不跟**上游 #45947 放松；维持 write-deny；DIVERSION 登记产品取舍 | ✅ 已写 roadmap §8.6；L-003 维持「拒绝/暂缓」注记 |
| **D5** P4 试点 | **飞书 + Telegram**（不用微信首发；硬验收=断线重连不丢消息，TG 可自动化、无审核门禁） | ✅ 已写 roadmap §8.6 |

### 开工序确认（审计 §5）

**T2 canary → S2 工单（+gold）∥ AGENTS.md 目录化**。S2 提到 AGENTS.md 之前，是为了尽早验证
「注入文本逐字等价」这条最硬验收是否真做得到。与 roadmap 原排一致，已在 §8.6/§8.7 锁定。

---

## 2. 对 T2/S2 交付审计（cross-audit-t2-s2）的逐条回应

### 2.1 假阴性更正（审计 §0）

**接受并记录**。QClaw 已自证「两本账无真源」是 BSD grep 静默失败，不是对象不存在。
纪律追加：本机检索用 ripgrep 或 Python，**禁止**依赖 BRE 的 `\|` 分支；解析以脚本真实输出为准
（T2 实现时两处解析坑已留痕在 `d3d7822aac` commit message）。

### 2.2 G1 漂移 2 条（审计 §2）— **采纳补登记**

| commit | 跟随区路径 | 处置 |
|---|---|---|
| `35532f29fb57` | `tools/send_message_tool.py` | **补登记 D-007**（产品修复：QQBot 32 位 openid/数字群号识别为显式目标） |
| `018b4e761ade` | `tools/file_operations.py` | **补登记 D-008**（修复：read_file 单行/长行截断不说谎） |

同 commit 的 `tools/code_execution_tool.py` 部分此前已登记（D-006 / L-013），不重复。
根因承认：**登记流程没有强制力**——同一 commit 只登记了一个文件。采纳配套措施：
**boundary / canary 非绿 = 月度复查点必处置项**（见 roadmap Phase D / §8.7）。

补充：`d3d7822aac` 自身新增的 `scripts/upstream_canary.py` + `.github/workflows/upstream-canary.yml`
按 `scripts/upstream_watch.py` 先例归入 **own 区**（Vermes 独有工具），不再走 DIVERSION。
补登记 + 归类后当场 `upstream_watch.py boundary`：**未登记税=0**（HEAD `d3d7822aac` 窗口）。

### 2.3 闸门报告措辞（审计 §2.1）— **采纳**

分区表 `follow` 判定列由「⚠️ 契约税（未登记）」改为
「跟随区改动（未登记明细见 §2）」，避免被读成「N 条全部未登记」。

### 2.4 S2 形态与排期（审计 §3 / A3–A7）

| # | 事项 | 决定 | 理由 |
|---|---|---|---|
| A3 | adapter vs 照搬上游 | **adapter** | 照搬会拆掉 prompt-cache 防护（layer 排序）；API 同形、内部落既有 processor 注册表 |
| A4 | plugin / builtin / user 同名优先级 | **plugin < builtin < user** | 用户热路可覆盖；builtin 稳定；插件最外层 |
| A5 | S2 排期重估 | **重估** | 真缺口 = 1 条注册 API + `editing_guardrails` 1 键 + gold 基线；静态块已迁 93% |
| A6 | 上游三护栏（max_chars / id 校验 / 重复注册拒绝） | **采纳**，登记 L-014 候选 | 不迁机制，只取护栏 |
| A7 | 回退开关 `VERMES_PROMPT_PROCESSORS_LEGACY` | **先不做**（YAGNI） | 失败直接 git 回滚 + DIVERSION 登记 |

以上已写入 `docs/plans/2026-09-23-s2-pluginization-workorder.md` §9；本回文再次锁定。

### 2.5 T2 canary 自身收尾（本回文后立即做）

两脆弱点补丁（若未在后续 commit）：

1. **venv 绑定**：canary 默认解释器改为项目 `.venv/bin/python`（存在时），避免用系统 Python
   （本机 `python3` = Homebrew 3.14）跑契约测导致假红/假绿。
2. **句柄只读 WARN / 写 FAIL**：`check_coexistence.py` 跨安装句柄区分访问模式——
   只读句柄 = WARN，写/读写句柄 = FAIL（真隔离被写破坏才算硬红）。

---

## 3. A1–A7 处置一览（T2/S2 审计 §5）

| # | 处置 | 落点 |
|---|---|---|
| A1 | 补登记 + boundary 非绿进月度必处置 | §2.2；manifest D-007/D-008；roadmap Phase D |
| A2 | 改措辞 | §2.3；`scripts/upstream_watch.py` `cmd_boundary` |
| A3 | adapter | §2.4；S2 工单 §3/§9 |
| A4 | plugin < builtin < user | §2.4；S2 工单 §9 |
| A5 | 排期按真缺口重估 | §2.4；S2 工单 §9 P4 |
| A6 | 三护栏采纳，L-014 候选 | §2.4；S2 工单 §8 |
| A7 | 回退开关先不做 | §2.4；S2 工单 §9 |

---

## 4. 仍开放 / 不在本回文关闭

- L-009 reader 清单、L-011 `TERMINAL_CWD`、T1 历史税外置、AGENTS.md 12 目录  
- L-013 真机验收（须重打 DMG）  
- 形态 B（引擎作 pip 依赖）继续暂缓  

### 4.1 本回文后已落地（同一工作流）

| 项 | 状态 |
|---|---|
| T2 canary 两脆弱点 | ✅ venv 绑定 + 句柄只读 WARN/写 FAIL |
| **S2.0 gold** | ✅ `reports/s2/gold/` 16 场景×3 段 + `scripts/s2_snapshot.py` 门闩 |
| **S2.1 adapter** | ✅ `PluginContext.register_system_prompt_section`（API 同形、Callable 强制 volatile、L-014 三护栏）；**未改现有注入点**，gold 门闩 56 passed 证行为零变化 |

S2.2（迁 1 个静态块试点）起才动注入点；每步必须过 `tests/tools/test_s2_gold.py`。

---

## 5. 证据索引

| 内容 | 位置 |
|---|---|
| 路线 + §8 审计补丁 | `docs/plans/2026-09-22-distribution-roadmap-next.md` |
| S2 工单 | `docs/plans/2026-09-23-s2-pluginization-workorder.md` |
| 两本账真源 | `docs/DISTRIBUTION_MANIFEST.md` §7b/§7c |
| canary | `scripts/upstream_canary.py` + `reports/.upstream-canary-pin.json` |
| A/B 语料 | `reports/ab-corpus/v1/` |
| 交接盘面 | `reports/qclaw/handoff-mimo-distribution-20260923.md` |

— 完 —
