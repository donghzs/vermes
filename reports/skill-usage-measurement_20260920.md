# 技能使用度量 · Phase 0-B · 2026-09-20

> **性质**：只读度量，Phase 1（L2 prefetch）立项 gate。
> **脚本**：`scripts/measure_skill_usage.py`
> **生成时间**：2026-09-20T10:04:55.931812+00:00

## 1. 四个硬指标

| 指标 | 数值 | 口径 |
|---|---:|---|
| **加载率** | **2.14%** | 有 `skill_view` 的会话 / 总会话（8/373） |
| **用到率（代理）** | **50.0%** | skill_view **之后**（跳过该次 tool 回包）同会话后续 assistant/tool 文本出现该技能名（4/8 次加载） |
| **重复加载** | 会话 0 次出现重复；多余加载 0；单会话最多 0 | 同会话同名 skill_view >1 |
| **Top8 重尾** | **46.7%**（Gini=0.6308） | `.usage.json` use_count Top8 / 总量 |

## 2. usage.json 库存

| 项 | 值 |
|---|---|
| 记录数 | 91 |
| 运行时技能数 | 245 |
| 覆盖率 | 37.14% |
| 有 view 的技能 | 90 |
| 有 use 的技能 | 90 |
| 总 view / 总 use | 636 / 636 |

### Top8 use_count

| # | 技能 | use_count |
|---:|---|---:|
| 1 | vermes-runtime-diagnostic | 86 |
| 2 | scholarforge-thesis-pipeline | 68 |
| 3 | gateway-telegram | 30 |
| 4 | docx | 28 |
| 5 | qimen-bazi-paipan | 27 |
| 6 | audit-verification-workflow | 21 |
| 7 | ch9329-hid-control | 19 |
| 8 | wechat-official-account | 18 |

### 重复加载 Top

| 技能 | 额外加载次数 |
|---|---:|

## 3. 会话侧

| 项 | 值 |
|---|---|
| state.db | `/Users/dongzusheng/.vermes/state.db` (108765184 bytes) |
| skill_view 事件总数 | 8 |
| 唯一被加载技能数 | 7 |
| error | None |

## 4. Gate 解读（Phase 1 是否立项）

**本机实测（373 会话 / state.db + .usage.json）：**

| 指标 | 值 | 含义 |
|---|---:|---|
| 加载率 | **2.14%**（8/373） | 历史上几乎不主动 `skill_view` |
| 用到率（代理，已扣 tool 回包） | **50%**（4/8） | 加载后约一半在后续消息中被提及 |
| 重复加载 | **0** | 无「反复猜技能」的 thrashing |
| Top8 重尾 | **46.7%**（Gini=0.63） | 使用高度集中 |
| 技能库覆盖 | **37%**（91/245） | 多数技能无 telemetry |

**判定（MiMo，供 QClaw 审计）：**

- 按 QClaw 原 rubric「加载率低且用到率高 → 缓做 L1」：本机 **加载率极低**，更像 **发现不足 / 该用未用**，而不是「加载了很多但选错」。
- 因此 Phase 1 以 **提升发现** 为目标仍然成立：prefetch 在 LLM 调用前注入 top-k 技能提示，不赌模型会主动 list/view。
- **不**据此证明「乱选技能」普遍存在；路由的验收应看 **加载率是否上升且用到率不降**，而非只看命中率。
- 技能库治理（Top8 重尾 + 63% 无记录）仍值得单独排期，与路由并行不冲突。

**Phase 1 实现状态（本分支）：**

- `agent/skill_router.py`：`SkillRouter(MemoryProvider)`，独立 `~/.vermes/rag/skills.db` + `skills_fts`，与记忆 `chunks_fts` 分表
- 注入：≤3 条「技能名+短描述」+ skill_view 提示；**无** `skill_search` 工具
- 开关：`agent.skill_router_enabled`（默认 True）
- 测试：`tests/agent/test_skill_router.py` 等 **54 passed**（相关套件）

## 5. 指针


- 执行方案：`reports/qclaw/skill-management-执行方案_20260920.md`
- 决策简报：`reports/skill-routing-decision-brief_20260920.md`

— scripts/measure_skill_usage.py · 只读 · 未改生产数据
