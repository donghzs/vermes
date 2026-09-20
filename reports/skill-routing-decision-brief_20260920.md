# 技能路由决策简报（终版）· M7 定位 + 技能路由 · 2026-09-20

> **读者**：董董（拍板）/ QClaw / Hermes  
> **性质**：**三方收敛后的最终决策简报**；不改生产代码，拍板后再开实现分支  
> **综合链**：QClaw 报告 → Hermes 核验 → MiMo 本机复核 → **QClaw 认账与排序修正（终）**  
> **分支**：`chore/skill-routing-decision-brief`（仅文档，待 commit）

---

## 0. 终版一句话

**方向：选得准 > 省 token（QClaw 对）。**  
**落地顺序（QClaw 终版排序）：先 20 分钟度量 → 纠 A′ 渠道门 → 用比例阈值修 M7 auto → L2 prefetch 路由 → L1 仅 tie-breaker。**  
**设计根问题只有一个：`auto` 到底该是什么**（死开关是阈值面，渠道门被摘是语义面，别打两个补丁）。

---

## 1. 问题拆分（三方一致）

| 问题 | 本质 | 处置 |
|---|---|---|
| **A. 省 token** | 系统 prompt 技能索引固定开销 | 产品门控；默认 `off`；**不是** agent 实力 |
| **B. 选得准** | 任务→技能发现/选择/路由 | **agent 实力**；先度量，再 L2 |

对话增长压力已有 `compression_scheduler`（70%）+ 疲劳桥；技能索引是构建期固定底座。折叠与路由正交。

---

## 2. 事实钉死（本机可复现）

### 2.1 索引体量与 auto 死开关

| 口径 | 数值 | 判定 |
|---|---|---|
| **生产门控估算** | **41,860 B** | `estimate_skills_index_bytes()`（name+description，245 技）— **权威口径** |
| P1 渲染段模拟 | 26,453 B | 历史测量，非门控所用 |
| QClaw | ~41KB | ✅ 与生产一致 |
| Hermes 引用 | 98,198 B | ❌ **无来源，且易误导**；疑把 SKILL.md **正文**算入。M7 省的是 description，正文本就按需加载、不进 system prompt |
| 默认阈值 | 20,000 B | `skill_index_compact_threshold_bytes` |

**死开关成立（用正确数字）**：`41860 ≥ 20000` → 打开 `auto` 则**永远降级**，`auto ≡ on`。  
默认仍为 `off`，生产未触发；死开关针对「配置里打开 auto」。  
**阈值纠偏应用比例**（索引 ÷ 模型 context window，如 >2%），**不要**按错误的 98KB 绝对值拍板。

### 2.2 A′ 渠道门：auto 路径已被摘掉（QClaw 交代）

| 时间线 | 内容 |
|---|---|
| `034d41bb69`（QClaw A′） | messaging / 未知 / 空 platform **永不降级**（白名单） |
| `60654e0aac`（M7 落地） | `auto` 改为**与 platform/cwd 解耦**；docstring：「IM 超限同样降级」；**不再走** `_INTERACTIVE_CODING_PLATFORMS` |
| 现状 | 仅 `on` 分支仍可理解为保留 A′ 语义（显式要求、跨渠道合理）；**auto 上 A′ 已失效** |
| 测试 | `test_a1_channel_gate.py` 只测「未超限不降级」，**不再断言超限时 messaging 永不降级** |
| **QClaw 说明** | 知情但未当作冲突上报，**属疏漏**；非其主动设计推翻 A′ |

**拍板含义**：是否在 IM 降级 = **产品语义选择**，不是已决共识。群聊 names-only 对第三方可见。

### 2.3 usage 稀疏（三方复核一致）

| 指标 | 值 |
|---|---|
| usage 记录 / 运行时技能 | 91 / 245 → **37.1%** |
| use_count Top8 占比 | **~47%** |

→ 全局热度排序会压死 63%「无数据」技能。curator 纪律（`agent/curator.py:353-354`）：*use=0 is absence of evidence*。  
L1 **仅可**作同分类 tie-breaker / 有记录优先。

### 2.4 全链路（三方一致）

- `_sort_skills` 字典序，**与上游逐字相同** → 新能力，非 Vermes 独有缺口（QClaw 措辞已认账）。
- `skill_view` + `bump_use` 按需加载与统计已有。
- `rag_provider`：`MemoryProvider` + `prefetch()`，只索引记忆，未接技能。
- 工具仅 `skills_list` / `skill_view`，无 `skill_search`。

---

## 3. 三方修正账本（收敛完毕）

| # | 原主张 | 修正 | 认账 |
|---|---|---|---|
| 1 | 「字典序是 Vermes 缺口」 | 与上游相同；做排序是**新能力** | QClaw ✅ |
| 2 | L1 全局热度排序 | 稀疏反噬；降为 tie-breaker | QClaw ✅ |
| 3 | L2 = 新增 `skill_search` | 应复用 **prefetch 注入**（cache-safe，不赌模型调用） | QClaw ✅ |
| 4 | Hermes 索引 98KB | **数字错**；权威为 **41860** | QClaw / MiMo ✅ |
| 5 | A′ 被摘需追溯 | QClaw 知情未上报；**终版收成「auto 语义」根问题** | QClaw ✅ |

**L2 架构共识**：技能 description → FTS5（与记忆**分表**）→ `prefetch`/`pre_llm_call` 注入 top-k → 细节仍 `skill_view`。

---

## 4. 终版优先级（QClaw 排序，MiMo 采纳）

| 序 | 事项 | 量级 | 说明 |
|---|---|---|---|
| **P0** | **20 分钟度量**（先做） | 极小 | ①任务加载技能占比 ②加载后是否被用 ③重复加载率；顺带 Top8 重尾→是否**裁技能库**。无此数，「选不准」可能不存在 |
| **P0** | **A′ 渠道门找回** | 半小时级 | 拆回正交：①是否降级（成本）②哪些渠道降级（**IM 默认不降**）。与 auto 语义一并拍板 |
| **P1** | **M7 auto 死开关** | 半小时级 | **比例阈值**（索引/context）或废止 auto；**勿用 98KB 绝对值** |
| **P1** | **L2 prefetch 路由** | 小-中 | 真正「选得准」一刀；等 P0 度量支持后再写产品代码 |
| **P2** | L1 热度 | 极小 | 仅 tie-breaker；禁止全局重排 |
| **P3** | L3 意图路由 | 中-大 | 等度量 + L2 命中率 |
| **Open** | 折叠并入 `compression_scheduler` | 大 | 中途重建 vs prefix cache 未解；**本轮不做** |

**根问题（QClaw 补充，采纳）**：  
Hermes 的「死开关」与「渠道门」是 **`auto` 语义**的两面——  
`auto` = 按什么条件降级？在哪些渠道降级？→ **一次设计、一次拍板**，不要分打两个补丁。

---

## 5. `auto` 语义设计选项（P0 拍板核心）

| 方案 | 成本条件 | 渠道条件 | 效果 |
|---|---|---|---|
| **A. 比例阈值 + IM 白名单不降**（推荐） | `index_bytes / context_window > r`（如 2%） | messaging/未知 platform 永不降 | auto 真自适应 + 保住 A′ |
| **B. 废止 auto，仅 off/on** | 无 auto | 无 auto | 概念最简；折叠永远显式 |
| **C. 比例阈值 + 全渠道（含 IM）** | 同 A | 超限则 IM 也降 | 接受「长度优先」；推翻旧 A′ |
| **D. 维持字节 20KB + 全渠道** | 本机必触发 | IM 超限也降 | 现状；auto≡on，概念不收口 |

静态判定：模型/context 已知时，会话**开始**即可定 auto 结果，**不中途翻转**（与 scheduler 降级不同）。

---

## 6. L2 prefetch 设计要点（度量支持后再立项）

1. 每技能一条：name + description + category（**不要**正文切块）。
2. 独立 FTS 表（如 `skills_fts`），**勿**并入记忆 `chunks_fts`。
3. 主路径：`MemoryProvider.prefetch` / `pre_llm_call` 注入；`skill_search` 工具至多可选增强。
4. 注入形态：top-k 名 + 一句描述 +「细节 skill_view(name)」；控字节。
5. 系统 prompt 索引保留；prefetch 是补充不是替代。
6. 评估：任务→期望技能对照；全表猜 vs prefetch 命中/误载。
7. 若 P0 显示主要是「索引吵」→ 先治理/裁剪技能库，后路由。

---

## 7. 明确不做什么（本轮）

- 不改生产默认 `compact_skill_categories`（保持 `off`），除非 §8 勾选。
- 未解 cache 前不并入 `compression_scheduler`。
- 不做全局 use_count 重排。
- 不砍 `skill_view` 按需加载。
- 无 P0 度量不启动 L3；无度量结论不默认 L2 必做。
- **不以 Hermes 98KB 作为阈值设计依据。**

---

## 8. 待董董拍板（终版勾选）

### 8.1 P0 — 度量

| 选项 | |
|---|---|
| ☐ **先做 20 分钟度量**（推荐） | 决定 L2 是否立项、是否先裁技能库 |
| ☐ 跳过度量，按现判断推进 | |

### 8.2 P0 — `auto` 语义（死开关 + 渠道门一次定）

| 选项 | |
|---|---|
| ☐ **A. 比例阈值 + IM 不降级**（推荐） | 真自适应 + 保住 A′ |
| ☐ **B. 废止 auto，仅 off/on** | |
| ☐ **C. 比例阈值 + IM 超限也降** | 接受长度优先，正式推翻旧 A′ |
| ☐ D. 维持现状（字节阈值 + 全渠道） | |
| ☐ 其他 | |

### 8.3 P1/P2 — 路由（度量后）

| 选项 | |
|---|---|
| ☐ **度量支持后：L2 prefetch 为主，L1 仅 tie-breaker**（推荐） | |
| ☐ 度量后只做 L2 | |
| ☐ 度量后只做 L1 tie-breaker | |
| ☐ 暂不立项路由 | |
| ☐ 其他 | |

### 8.4 签字与知情

| 项 | 内容 |
|---|---|
| 拍板日期 | |
| 董董 | |
| QClaw | 已认账：三处修正 + A′ 疏漏 + 98KB 数字不采信 |
| Hermes | 数字以 41860 为准；98KB 勿作阈值依据 |
| 拍板后分支 | （例：`fix/auto-semantics-ratio-and-channel`、`chore/skill-usage-metrics`、`feat/skill-prefetch-route`） |

---

## 9. 指针

| 材料 | 路径 |
|---|---|
| 本简报（终版真源） | `reports/skill-routing-decision-brief_20260920.md` |
| QClaw 技能管理报告 | `reports/qclaw/skill-management-科学化-m7折叠与技能路由_20260920.md` |
| QClaw M7 审计 | `reports/qclaw/audit-w-l4-l5-m7_20260920.md` |
| P1 基线 | `reports/skills-index-p1-baseline-20260920.md` |
| 门控实现 | `agent/prompt_builder.py` |
| 渠道门测试 | `tests/agent/test_a1_channel_gate.py` |
| 配置默认 | `vermes_cli/config.py` |

---

## 10. 状态

| 项 | 内容 |
|---|---|
| 代码 | **未改**生产逻辑 |
| 关键数 | `estimate_skills_index_bytes()=41860`；usage 91/245（37.1%）；Top8 ~47% |
| 三方 | 方向与优先级已收敛；A′ 由 QClaw 认账后并入 `auto` 语义一次拍板 |
| 下一步 | 董董 §8 勾选 → 度量 / `auto` 纠偏分支 → 再定 L2 |

— MiMo 终版综合 · QClaw 认账修正后 · 2026-09-20
