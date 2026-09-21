# Vermes 发行版化 walking skeleton 评审（纸面，不写迁移代码）

日期：2026-09-21
状态：**仅评审，三方通过前不动 agent/ 红线实现**
前置：P0 账本精度已修（52676b3bad）、P1 巡检脚本已落地（b25021d044）

## 0. 为什么需要这个评审

P1 插件化 walking skeleton 是「治本」，也是唯一能验证「发行版化到底值不值」的实验。
但它动的是红线资产（agent/、gateway/），风险级别与纯机制层（脚本/文档/测试）完全不同。

**本评审的唯一目的**：在写任何迁移代码之前，回答「迁什么、走哪条 hook、验收能否字节级、
回退怎么退、ROI 怎么量」——回答不了就不开工。

## 1. 候选试点（单选，首期只允许一个）

| 候选 | 证明什么 | 成本 | 风险 | 首期判定 |
|---|---|---|---|---|
| **ContextEngine** | 「删/迁本地实现 → 跟随上游 ABC」是否真降冲突 | 2–3 天 | **低** | ✅ **推荐** |
| 自进化静态块 → register_system_prompt_section | 提示词段注入与 v2.5.1 逐字相同 | 数天 | 中（提示词回归） | 备选 |
| 记忆织物 → MemoryProvider 大块红线迁插件 | 召回 A/B 不退化 | 2–3 周 | **高** | ❌ 排除首期 |
| 中文平台 17 个 | — | — | — | ❌ 排除（需上游开口） |
| workflow DAG | — | — | — | ❌ 排除（需上游开口） |

**为什么首选 ContextEngine：**

实测同源度：
- Vermes `agent/context_engine.py` = 226 行；上游 = 242 行（规模接近，明确同源）
- 上游比 Vermes 多 5 个方法：`should_compress_info` / `prune_tool_results_only` /
  `select_context` / `on_turn_complete` / `get_automatic_compaction_status_message`
  + 2 个模块级函数 `sanitize_memory_context` / `automatic_compaction_status_message`
- 这正好是「上游领先、Vermes 落后」的最小、最干净样本 —— 是验证「跟随上游 ABC
  能否降冲突」的完美试金石，而不是一个巨大的 diverge 区。

## 2. 扩展口（hook/ABC）现状核实

Vermes 已有现成的提示词段注入 hook，无需新造：

```
agent/system_prompt.py:
  _PROCESSOR_FALLBACK = { 13 个 guidance 常量映射 }
  _proc_or_default(name) → processor 优先 + 常量 fallback
  13 个注入点已统一走 _proc_or_default()
```

这意味着：提示词段的「processor（YAML 可覆盖）→ 常量（兜底）」双轨已经存在。
ContextEngine 的 ABC（`agent/context_engine.py:32 class ContextEngine(ABC)`）也是现成的。

**评审要确认的关键问题**：上游 ABC 多出的 5 个方法，Vermes 的调用方（compression_scheduler /
conversation_compression / run_agent）是否已经假设了它们存在？还是说这些方法是「可选、
缺了走默认」的？——这决定「跟随上游 ABC」是「加方法即可」还是「要改调用方」。

## 3. 必须回答的六问（评审门槛）

1. **迁哪一块**：ContextEngine 单文件 + 其调用方（compression_scheduler / conversation_compression）。
2. **走哪条 hook/ABC**：`ContextEngine(ABC)` + `_PROCESSOR_FALLBACK` 双轨。上游新增方法
   → 加到 Vermes ABC + 默认实现；不删 Vermes 独有方法（保向后兼容）。
3. **验收能否字节级**：压缩触发/上下文裁剪的输入-输出，与 v2.5.1 基线 diff = 0；
   任何非零 diff = 失败即停（不「差不多就行」）。
4. **回退开关**：环境变量 `VERMES_CONTEXT_ENGINE_UPSTREAM=0` 或配置开关，
   一键回退到 Vermes 原生实现。
5. **取长人时是否下降**：拿 T3 基线（~0.5h）对比——跟随上游 ABC 后，
   同路径的上游改动从「人工考古 + 逐行 diff」降为「版本/配置级 diff」，人时 < 0.5h。
6. **ROI 度量**：该路径的 follow 契约税 / 取长耗时，插件化后是否下降——
   用 `boundary` 和巡检脚本的数字说话，不是感觉上的架构更干净。

## 4. 不做什么（红线）

- 评审通过前，**禁止**改 `agent/capability_evolver.py`、`agent/memory_fabric.py` 等红线实现
- 禁止「顺手」把整目录登记进 DIVERSION_LEDGER 图省事
- 禁止把巡检脚本候选直接当补丁合入（必须人工读）

## 5. 通过标准

- 三方（MiMo / Hermes / 百度搭子 / QClaw）看过本评审并同意开工
- 若任一评审方对「验收能否字节级」或「回退开关」有异议，停在文档，不写代码

## 6. 结论（本轮）

**ContextEngine 是 walking skeleton 的正确首选**：同源、规模小、上游领先样本清晰、
风险低、有现成 hook。但「跟随上游 ABC」到底是「加方法」还是「改调用方」，需要一次
精确的源码 diff（Vermes vs 上游的 ContextEngine 逐方法 + 调用方）才能定——这是评审
通过后的第一步动作，不是评审本身。

**本评审不改变任何生产行为，纯纸面。**
