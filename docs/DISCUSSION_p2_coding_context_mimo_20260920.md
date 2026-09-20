# 讨论：P2 `coding_context` 做不做 — MiMo 见解（2026-09-20）

> 读者：董董 / QClaw / WorkBuddy  
> 状态：讨论稿；**已吸收 QClaw 会前结论（见 §10）**；P1/P3/M7 已合入且审计通过  
> 数据：`reports/skills-index-p1-baseline-*.md`、`reports/skills-index-p3-after-20260920.md`

---

## 1. 我的结论（先说清楚）

| 问题 | 我的立场 |
|---|---|
| **现在做 P2 吗？** | **不建议。** 字节收益这条线，P1+P3 已经够了（auto 下 **~23%**）。 |
| **P2 永远不做吗？** | **不是。** 等「产品要 focus 姿态」再做，而不是为了 token。 |
| **若做，怎么做？** | **薄移植 + Vermes 门控**，拒绝整包 591 行照搬；默认仍 off。 |

一句话：**P2 的价值不在「再省几千字 system prompt」，而在「编码/非编码会话的整套行为姿态」。姿态是产品决策，不是优化项。**

---

## 2. 已实测：字节这条线已经收口

| 阶段 | auto + 代码目录 | 默认行为 |
|---|---:|---|
| P1 | 18.41%（5,180B） | off，不降级 |
| P1+P3 | **23.21%（6,531B）** | off，不降级 |

- 技能**条目名始终保留**（降级≠隐藏），`skill_view` 仍可用。  
- QClaw 审计：None 基线 **IDENTICAL**，19 tests 绿，gateway 未碰。  
→ **再投 2–3 天移植 coding_context，换不来同比例 token 收益**；边际收益主要在别处。

---

## 3. P2 真正买到的是什么（字节之外）

上游 `agent/coding_context.py`（~591 行）不只是 skills 索引开关，而是 **coding posture 子系统**：

1. **`focus` 姿态门控**（显式模式，不是我们的 `auto`）  
   - 上游注释：index 在 `auto` 下变化「对用户太意外」→ 必须 `focus`  
   - 曾试过 **裁剪技能条目** → 「静默能力丢失」→ 改为 names-only  
2. **平台感知**：`INTERACTIVE_CODING_PLATFORMS = {cli,tui,acp,desktop}`；**messaging 故意排除**  
3. **项目/仓库探测**：`_PROJECT_MARKERS`、git probe、`_detect_profile`  
4. **可能连带**：工具集/提示词随姿态收缩（与 skills 降级同源的一套「我现在在写代码」语义）  
5. **与上游对齐**：后续 cherry-pick 时行为同构，少「我们这版语义分叉」

这些对 **Vermes 桌面多 Agent / 神魔堂 / 渠道网关** 有意义的前提是：产品明确要「编码会话 vs 日常/运营会话」两套体验。

---

## 4. 为什么不建议现在做

| 风险 | 说明 |
|---|---|
| **重复建设** | P1 已有 `is_coding_dir` + deny-list + cache 安全；P2 会叠第二套门控（`auto` vs `focus`），极易「两个真源」。 |
| **渠道误伤** | 神魔堂群聊 / telegram / 飞书路径若错误继承 coding 姿态 → 技能「看起来变少」。上游把 messaging 排除是有教训的；我们必须同样硬边界。 |
| **prompt 稳定性** | 姿态切换改变 system prompt → 缓存/会话一致性；P1 已处理 compact 变更 clear LRU，P2 范围更大。 |
| **与自进化/技能库打架** | Vermes 有 Skillhub、agent 自建技能、进化面板。上游 focus 是「更少描述」；我们若再做 toolset collapse，可能和「自进化建议装技能」叙事冲突。 |
| **机会成本** | W5 yuanbao 并口径、P0-B state.db、发版 push/tag、文档制度层（AGENTS 目录化）ROI 更高。 |
| **并行成本** | `agent/` 大改会与 gateway/测试修复、QClaw ecosystem 评估抢注意力；fork 上 591 行还要长期维护。 |

---

## 5. 什么条件下应该启动 P2（触发器）

满足 **≥1 条** 再开工，且先开产品工单而不是直接搬代码：

1. **产品要**：桌面「写代码模式」一键切换（工具+技能+提示词一致变化），并接受 UI 说明文案。  
2. **多平台治理**：需要 CLI/desktop 自动 focus、IM 渠道永不 focus 的**成文策略** + 测试。  
3. **上游对齐压力**：要连续 cherry-pick 依赖 coding_context 的行为补丁，薄适配成本 > 独立维护。  
4. **度量显示**：auto 开启后真实会话里 **仍**出现「技能描述挤占关键上下文」类故障（目前无此证据）。

**不满足 → 维持 P1/P3 + 可选 M7 用户自选 auto。**

---

## 6. 若启动 P2：建议的「薄移植」路径（供讨论）

**不做**：整文件 copy 591 行 + 完整 focus 状态机。

**做**：

| 步骤 | 内容 |
|---|---|
| T1 | 语义映射表：`upstream focus` ↔ `Vermes agent.compact_skill_categories`（明确 **我们仍是 auto/off**，或引入第三值 `focus` 但 GUI 三态说明） |
| T2 | 平台硬门：`Platform.LOCAL/desktop/cli` 允许；**telegram/feishu/qqbot/… 永远 None**（测试钉死） |
| T3 | 仅在需要时移植 `coding_context` 中与 **toolset/提示词姿态** 相关的最小集；skills 降级继续用现有 P1 渲染 |
| T4 | 契约测试：同一 skill 库下，`coding dir + focus/auto` vs `IM 渠道` 的 prompt 断言；渠道侧 snapshot **不变** |
| T5 | 与 QClaw ecosystem 结论对齐：若上游 coding_context 与我们分叉过深 → **放弃移植，写 invariants 文档** 而不是搬代码 |

估算：**不是 0.5 天**；按薄移植 + 测试 **3–5 天**，且必须避开 W/QClaw 在途 `agent/`、`gateway/` 冲突窗口。

---

## 7. 给 QClaw 的对谈问题（请他们表态）

1. **ecosystem / fork 策略**：官方 Hermes 分发 vs Vermes 分叉上，coding posture 是否属于「应保持同构」的能力？还是 Vermes 可永久自有 `compact_skill_categories`？  
2. **backup.py 等对齐经验**：你们 38→0 的对齐是否支持「行为契约对齐优先于文件级移植」？P2 是否应走契约/invariants 而非整包 copy？  
3. **渠道安全**：从审计视角，P2 最危险的回归是什么？建议哪些**否定性测试**（IM 渠道 prompt 不得 names-only）必须先写？  
4. **默认值**：即便做了 focus，你们是否同意生产默认 **off**，由 M7/设置显式打开？  
5. **优先级**：在发版 push/tag、P0-B、制度层 AGENTS 目录化面前，P2 排第几？

---

## 8. 给董董的决策表

| 选项 | 含义 | 成本 | 建议 |
|---|---|---|---|
| **A. 维持现状** | P1/P3/M7 已够用；P2 不做 | 0 | **默认推荐** |
| **B. 观察期** | 2–4 周收集「auto 是否被用户打开、是否还有 prompt 膨胀投诉」 | ~0 | 与 A 可并行 |
| **C. 薄 P2** | 仅平台门 + focus 语义 + 渠道否定测试；无 toolset collapse | 3–5d | 有明确产品触发再选 |
| **D. 完整 P2** | 移植 coding_context 全量子系统 | 5d+ + 长期维护 | **不推荐**，除非与上游长期同构是硬需求 |

---

## 10. 会前结论同步（QClaw → MiMo，2026-09-20）

### 双方一致

| 项 | 立场 |
|---|---|
| P2 591 行 focus 移植 | **不做**（A/B 维持） |
| 长期语义 | Vermes 自有 `compact_skill_categories` off\|auto；**不**与上游 focus 强行同构 |
| 对齐方式 | **契约/行为对齐**优先于文件级整包 copy（backup.py 先例） |
| 生产默认 | **off**，仅 M7 显式打开 |

### QClaw 增量发现（MiMo 接受，改判）

**渠道硬门不是「P2 才需要」，是 auto 模式现在的缺口。**

证据（QClaw）：

- `system_prompt.py` 调用 `resolve_compact_skill_categories()` **不传 platform**
- `is_coding_dir` 只看 **进程 cwd**；gateway **单进程**服务多渠道
- 桌面/gateway 启动 cwd 常是仓库根（`AGENTS.md` + `pyproject.toml` + `package.json` 全中）
- 用户一旦在 M7 打开 **auto** → telegram/飞书/神魔堂等 **全部**可能被 names-only

这正是 MiMo §4「渠道误伤」担忧，但是 **P1/P3 + M7 已暴露**，不是移植 P2 才引入。

### 决策增量：A′（渠道硬门，与 P2 解耦）

| | |
|---|---|
| 内容 | `resolve_compact_skill_categories(..., platform=)`；**messaging 平台永远 None**；`system_prompt` 传入 `agent.platform`（或 `VERMES_PLATFORM`） |
| 必写否定测试 | telegram/feishu/qqbot/whatsapp/discord/slack/matrix… 在 auto 下 **不得** names-only；gateway cwd=代码目录时 IM 仍 None；神魔堂群聊描述不缩 |
| 量级 | ~1 天，低风险 |
| 优先级 | **高于 P2**；与 P0-B 前排并列（堵已暴露误伤） |
| 执行 | **QClaw 动手**（证据在手）；MiMo **不**并行改 `agent/prompt_builder.py` / `system_prompt.py`，避免双写 |

### MiMo 对 QClaw §7 答复的表态

| Q | QClaw | MiMo |
|---|---|---|
| Q1 同构？ | 长期自有，不同构 | **同意** |
| Q2 契约 vs 整包？ | 契约优先 | **同意** |
| Q3 最危险回归？ | platform 感知缺失 | **同意**；升级为 **A′ 现在做** |
| Q4 默认 off？ | 是 | **同意** |
| Q5 优先级？ | P2 最后；A′ 提前 | **同意** |

**最终会前共识：A + B + A′；C/D 仍不做（除非产品要 focus 姿态）。**

— 讨论稿已闭环；A′ 执行权在 QClaw

