# 技能管理科学化 · 可执行路线方案（交给 MiMo 落实）

> **作者**：QClaw（董董拍板后出方案）  
> **执行**：MiMo  
> **审计**：QClaw（最后负责，追求质的提升）  
> **分支**：`feat/skill-routing`（从 `chore/skill-routing-decision-brief` 切出）  
> **性质**：本方案是**执行规格**，非决策简报。含文件清单、函数签名、测试清单、验收标准。

---

## 0. 目标与验收（什么是「质的提升」）

**一句话**：让「选对技能」从「LLM 读 245 条描述纯猜」升级为「任务语义驱动的精准召回 + 热度辅助 + 成本自适应」，同时守住「IM 群聊不降级、默认 off 不打扰、cache-safe 不破缓存」三条底线。

**验收硬指标**（P0 度量脚本产出，作为 P1 立项依据与 P1 完成后对比）：
1. **用到率**（加载的技能中实际被引用的占比）
2. **命中率**（P1 路由注入 top-k 中，真正需要的技能命中率）
3. **重复加载率**（同一技能同会话重复 skill_view 的次数）
4. **Top8 重尾**（是否需技能库裁剪的判据）

---

## 1. Phase 0-A：M7 auto 语义纠偏（半小时级，纠偏）

### 1.1 决策（拍板）

`auto` 最终语义 = **两个正交条件同时满足才降级**：

| 条件 | 配置 | 默认 | 语义 |
|---|---|---|---|
| **成本**（是否降级） | `compact_skill_ratio_pct` | `2` | 索引字节 ÷ 模型 context window > 2% 才降级 |
| **渠道**（哪些渠道降级） | 复用 `_INTERACTIVE_CODING_PLATFORMS` | IM 排除 | messaging / 未知 / 空 platform **永不降级** |

### 1.2 改动清单

**文件 1：`agent/prompt_builder.py`**
- `resolve_compact_skill_categories(cwd, platform)` 的 `auto` 分支改为：
  1. 渠道门先短路：`platform not in _INTERACTIVE_CODING_PLATFORMS` → 返回 `None`（**恢复 A′ 语义**）
  2. 比例阈值：`est / context_window > ratio_pct / 100` 才降级（**修死开关**）
- 新增 helper：`_model_context_window()`（从 config 读当前模型 context，已知量，会话开始即可静态判定）

**文件 2：`vermes_cli/config.py`**
- 新增 `agent.skill_index_compact_ratio_pct`（默认 `2`）
- `agent.skill_index_compact_threshold_bytes` **标记废弃**（保留字段避免报错，但 auto 分支不再读它）

**文件 3：`tests/agent/test_a1_channel_gate.py`**
- 补回断言：`test_auto_over_ratio_never_demotes_on_messaging`（超比例阈值时 messaging 仍不降级）
- 补：`test_auto_under_ratio_never_demotes_even_interactive`（未超比例，交互式也不降级）

### 1.3 验收
- `test_a1_channel_gate.py` 全绿
- 手工：`auto` + 本机 41860B 索引 + 默认 2% 比例 → 需确认 context 多大才触发（如 200K context 则 2% = 4096B，41860 > 4096 → 交互式会降级；IM 不降级）

---

## 2. Phase 0-B：20 分钟度量脚本（极小，决策前置）

### 2.1 产出
新文件 `scripts/measure_skill_usage.py`（只读，不写生产数据），统计并打印：

1. **加载率**：有 skill_view 调用的任务 ÷ 总任务数
2. **用到率**：skill_view 后该技能名出现在后续 tool 调用/消息里的占比
3. **重复加载率**：同会话内同名技能多次 skill_view 的次数分布
4. **Top8 重尾**：`.usage.json` 的 use_count 分布、Top8 占比、Gini 系数

数据源：`~/.vermes/skills/.usage.json` + `~/.vermes` 会话/消息存储（若可读）。

### 2.2 验收
- 脚本可跑出四个数字
- 数字写入 `reports/skill-usage-measurement_20260920.md`，作为 P1 立项 gate

---

## 3. Phase 1：L2 技能路由（prefetch 型，小-中，核心能力）

### 3.1 设计（复用 rag_provider 模式，不造 skill_search 工具）

技能 description 进**独立 FTS5 表**，复用 `MemoryProvider.prefetch` / `pre_llm_call` 的 **cache-safe 注入通道**。

### 3.2 改动清单

**文件 1：新建 `agent/skill_router.py`**
- `class SkillRouter(MemoryProvider)`（继承 `agent/memory_provider.py:MemoryProvider`）
- `prefetch(query, session_id)`：FTS5 检索技能 description → 返回 top-k 技能提示文本
- `sync_turn` / `queue_prefetch`：no-op（技能不需存 turn）
- `get_tool_schemas()`：返回空（不新增工具，纯注入）

**文件 2：`agent/skill_router.py` 内部索引构建**
- 技能源：`~/.vermes/skills` + 仓内 `skills/`（复用 `_find_all_skills()` / `_sort_skills()` 的技能发现逻辑）
- 索引表：独立 SQLite `skills_fts`（**与记忆 `chunks_fts` 分表**）
- 粒度：**每技能一条**（name + description + category），非正文切块

**文件 3：`agent/memory_manager.py` 注册**
- 把 `SkillRouter` 加入 provider 列表（与 rag_provider 并列），`prefetch_all` 自动聚合注入

### 3.3 注入形态（关键）
```
[相关技能]
- 论文写作 (scholarforge-thesis-pipeline)：学术论文全链路写作
- 文档处理 (docx)：创建/编辑 Word 文档
（细节用 skill_view(name) 查看）
```
控制注入 ≤ 3 条、每条 ≤ 60 字符描述。

### 3.4 验收
- 新增 `tests/agent/test_skill_router.py`：
  - 检索命中：任务描述「写论文」→ 命中 scholarforge 类技能
  - 分表隔离：技能索引不污染记忆检索
  - prefetch 失败 fail-open（返回空，不阻断）
  - 注入条数 ≤ 3、字节上限
- 手工：模拟「写论文」任务，确认 prefetch 注入相关技能提示

---

## 4. Phase 2：L1 热度 tie-breaker（极小，展示增强）

### 4.1 决策
**不做全局 use_count 重排**（稀疏数据反噬）。仅：
- `_sort_skills` 在**同 category 内**，有 usage 记录的排前（无记录保持原位）
- `skills_list` 输出可选附 `use_count`（仅「有记录」的技能显示）

### 4.2 改动清单
- `tools/skills_tool.py:_sort_skills`：category 相同 → 有记录的按 use_count 降序，无记录按 name
- 依赖 `tools/skill_usage.py:load_usage()`

### 4.3 验收
- 新增测试：同 category 内高频技能排前、无记录技能不被打乱全局顺序

---

## 5. 明确不做（本轮）

- ❌ 全局 use_count 重排（Phase 2 只做 tie-breaker）
- ❌ L3 意图路由（等 Phase 0-B 度量 + Phase 1 命中率数据）
- ❌ 折叠并入 compression_scheduler（未解 cache 前不做）
- ❌ 新增 `skill_search` 工具（Phase 1 走 prefetch 注入，非模型主动调用）
- ❌ 改生产默认 `compact_skill_categories`（保持 `off`）

---

## 6. 执行顺序与依赖

```
Phase 0-A（auto 纠偏） ──┐
                         ├─→ Phase 0-B（度量） ──→ 数据 gate
Phase 2（L1 tie-breaker，独立小件）─┘                      │
                                                     ┌────┴─────┐
                                              支持路由   不立项
                                                     │          │
                                               Phase 1（L2）   结束
```

**建议 MiMo 执行顺序**：0-A → 0-B → 2（并行小件）→ 视度量结果定 Phase 1。

---

## 7. 审计权责（QClaw）

QClaw 最后负责审计，重点：
1. **A′ 语义是否真恢复**（auto 下 messaging 永不降级）
2. **比例阈值是否真修死开关**（不再 ≡ on）
3. **Phase 1 是否 cache-safe**（prefetch 注入，非 model 必调工具）
4. **技能索引与记忆索引分表**（不互相污染）
5. 回归：`tests/agent/` 全绿 + gateway 渠道不退化

---

## 8. 指针

| 项 | 路径 |
|---|---|
| 决策简报（三方合成终版） | `reports/skill-routing-decision-brief_20260920.md` |
| 门控实现 | `agent/prompt_builder.py` `resolve_compact_skill_categories` |
| 渠道门测试 | `tests/agent/test_a1_channel_gate.py` |
| 配置默认 | `vermes_cli/config.py` `agent.*` |
| prefetch 通道 | `agent/memory_manager.py` `prefetch_all` / `agent/rag_provider.py` |

— QClaw 执行方案 · 2026-09-20
