# 技能路由 · QClaw 独立审计报告（feat/skill-routing）

> **审计对象**：MiMo 在 `/Users/dongzusheng/Projects/vermes-electron-skill-routing`（worktree，`feat/skill-routing` 未 commit）的 Phase 0-A/0-B/1/2 交付
> **审计方式**：逐条读源码 + 独立复现测试 + 实测检索质量（非采信交付清单）
> **审计时间**：2026-09-20 18:19–19:00 GMT+8

---

## 一、五条硬指标核验（全部达标 ✅）

| # | 审计点 | 结论 | 实测证据 |
|---|---|---|---|
| 1 | A′ 语义恢复（auto 下 messaging 永不降级） | ✅ | 强制 `mode=auto` + patch config 实测：`auto/cli→DENY(33类)`、`auto/telegram→None`、`auto/空→None`、`auto/None→None` |
| 2 | 比例阈值修死开关 | ⚠️ 半修 | 字节阈值已废弃不读、渠道门已恢复；但成本面在 128K 回落下**形同虚设**（见发现 C） |
| 3 | Phase 1 cache-safe | ✅ | `prefetch` 注入走 `conversation_loop.py:579-580` 的 `_ext_prefetch_cache` → `build_memory_context_block`，注入 **user message**，非 system prompt；`get_tool_schemas()==[]` 无必调工具 |
| 4 | 技能索引与记忆分表 | ✅ | 独立 `~/.vermes/rag/skills.db` 表 `skills`/`skills_fts`，与 `documents.db`/`chunks_fts` 无交集（测试 `test_index_isolated_from_memory_rag_db` 已断言） |
| 5 | 相关测试通过 | ✅ | 独立复现 `54 passed in 6.45s` |

**FTS SQL 别名疑点已排除**：`FROM skills_fts f JOIN skills s ... WHERE skills_fts MATCH` 用原名 MATCH 是合法写法（别名 `f MATCH` 反而报 no such column），实测查询正常。

---

## 二、新发现（4 个，2 个 P1 需修，1 个 P1 待产品拍板，1 个 P2）

### 🔴 发现 A（P1，检索核心硬伤）：`trigger` 字段被完全忽视，中文口语短查询大面积漏召回

**实测**：
```
search_skills('写论文')  -> [agent-framework-audit, docx-thesis-modification, office-collaboration]  ❌ 没有 scholarforge
search_skills('academic thesis writing') -> [scholarforge-thesis-pipeline, ...]  ✅ 英文才命中
```

**根因**：`agent/skill_router.py:_discover_skills()` 只索引 `name + description + category`，**完全没读 `trigger` 字段**。

- `scholarforge-thesis-pipeline` 的 frontmatter 明确写了 `trigger: 写论文, 学位论文, thesis, 论文评审, 学术写作, 扩写论文`
- 但它的 `description` 是英文（"End-to-end academic thesis workflow..."），中文查询「写论文」的 trigram/2-gram 都匹配不上
- 实测全库 245 个 SKILL.md，**7 个有非空 trigger**，且恰恰是最需要精准触发的：写论文/修改论文/生成视频/网络诊断/Windows 部署等

**这 7 个 trigger 是现成的、高价值的意图信号，却被丢弃了**。已核实上游 Hermes 也不消费 trigger（该字段是 Vermes/skill 作者自加），因此接入 trigger 是纯增量、不偏离上游，且正中「选得准」的核心目标。

**修复建议**：`_discover_skills` 把 `trigger` 字段解析出来并入索引列（如 `description` 后追加 trigger 文本，或单列 `trigger` 进 FTS5），让「写论文」能命中 scholarforge。

### 🔴 发现 B（P1，MiMo 标的待裁决点 3 成立）：LIKE 回退过召回真实存在

**实测**：
```
search_skills('data analysis') -> [codebase-audit, weather, security-audit]  ❌ weather 混入
```

**根因**：`weather` 的 description 含 "meteorological **analysis**"，LIKE 2-gram 回退把 "analysis" 作为 ASCII token 匹配，`score=1` 即返回，无最小阈值。

**修复建议**：LIKE 回退加最小 score 阈值（建议 `score >= 2` 才返回，或 ASCII 词要求精确 token 边界匹配），避免单词级误中。

### 🟡 发现 C（P1，已拍板 ✅ 2026-09-20 18:54）：成本面形同虚设 —— 半个死开关残留

**实测**：
```
est = 41860 B，context_window = 128000（真实 config 无 context_length，回落 128K）
2% 阈值 = 2560 B，est 远超 → 交互式渠道 auto 恒降级
```

**关键洞察**：比例阈值是「伪动态」。成本面原始动机是「prompt 太长才省 token」（随对话增长动态变化），但实现是「索引字节 ÷ 模型窗口」——分子固定（41KB 索引不变）、分母固定（模型 context 固定档位），相除在给定模型下是常数，表达不了「prompt 变长」。调阈值是死路：调低恒触发、调高永不触发，中间没有合理区间。

**董董拍板（18:54）**：选项 1 —— **删成本面，auto = 纯渠道门**。
- 语义：交互式（cli/桌面/web/tui/acp/local/api…）降级省 token；IM/未知/空 永不降级
- 删除 `skill_index_compact_ratio_pct`、`_model_context_window()`（若不再被引用）、废弃 `skill_index_compact_threshold_bytes` 一并清理
- 承认「渠道」是当前唯一真实信号，不叠假动态

### 🟢 发现 D（P2）：`_model_context_window` 回落 128K 可接受，但可复用现成函数

- `vermes_cli/config.py:3449` 已有 `get_custom_provider_context_length(model, base_url, ...)`，但需要 model+base_url 参数
- prompt 构建时（`resolve_compact_skill_categories`）拿不到具体 model，回落 128K 是 fail-safe 正确选择
- 不阻塞，记录即可

---

## 三、MiMo 标的 4 个待裁决点 · QClaw 裁定

| # | 待裁决点 | 裁定 | 依据 |
|---|---|---|---|
| 1 | `_model_context_window` 回落 128K 是否可接受 | **可接受** | prompt 构建时拿不到 model+base_url；回落 fail-safe 正确。但连带发现 C |
| 2 | SkillRouter 默认 `enabled=True` | **改为默认 `off`** | ① 与 M7 `compact_skill_categories=off`（默认不打扰）哲学不一致 ② 检索质量未达标（发现 A/B），默认开会让坏结果先曝光 ③ 行为变更应保守默认 |
| 3 | LIKE 回退过召回 | **成立，需加阈值** | 实测 `data analysis→weather`，见发现 B |
| 4 | gateway 渠道度量 | **需另测，不阻塞** | 当前 CLI/本机样本已足够支撑「发现不足」结论；gateway 加载率可后续补 |

---

## 四、总体判定

**方向正确、结构干净、测试真实（5 条硬指标全达标），但有 3 个 P1 需在 commit 前处理。**

**必须修（P1，commit 前）**：
1. **发现 A**：`trigger` 字段接入检索索引（检索核心硬伤，否则「选得准」目标打折）
2. **发现 B**：LIKE 回退加最小 score 阈值（防过召回污染）
3. **发现 D 待裁决点 2**：SkillRouter 默认改 `off`
4. **发现 C（已拍板 ✅）**：删成本面，auto = 纯渠道门（删 `skill_index_compact_ratio_pct` + `_model_context_window` + 废弃字节阈值）

**不阻塞（P2）**：
5. `_model_context_window` 可复用 `get_custom_provider_context_length`（发现 C 拍板后此函数可能被删，本项作废）

---

## 五、给 MiMo 的下一步指令

**先不要 commit**。请按以下顺序修：

1. `agent/skill_router.py:_discover_skills()` 读 `trigger` 字段并入索引（追加进 description 或独立列进 FTS5），让中文口语短查询命中对应技能
2. `agent/skill_router.py:search_skills()` LIKE 回退加 `score >= 2` 最小阈值（或 ASCII token 精确匹配）
3. `vermes_cli/config.py` + `agent/agent_init.py`：`skill_router_enabled` 默认改 `False`
4. **发现 C（已拍板）**：`agent/prompt_builder.py` 删成本面——`resolve_compact_skill_categories` 的 auto 分支只留渠道门；删 `_model_context_window()`、`skill_index_compact_ratio_pct`、`skill_index_compact_threshold_bytes` 废弃字段；同步注释与测试
5. 补测试：`test_search_hits_trigger_short_query`（「写论文」→ scholarforge）、`test_like_fallback_rejects_single_word`（「data analysis」不返回 weather）、auto 纯渠道门测试（删成本面后 `auto/cli` 降级、`auto/telegram` 不降级）

修完重跑 `54 passed` + 新增测试，再报我复审计。

— QClaw 审计 · 2026-09-20
