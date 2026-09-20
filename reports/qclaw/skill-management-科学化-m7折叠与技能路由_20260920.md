# 技能管理科学化：M7 折叠处置 + 技能路由方向 · 2026-09-20

> 触发：董董「随 agent/AI 发展技能只会越来越多，什么任务什么场景用什么技能直接决定效率与质量，用得精准科学高质才是 agent 实力的决定因素」。
> 结论：把「省 token（折叠）」与「选得准（路由）」两个正交问题分开处理。

---

## 1. 现状盘点（实测，非臆断）

| 能力 | 模块 | 现状 |
|---|---|---|
| 技能发现 | `tools/skills_tool.py:skills_list` | name+description，`_sort_skills` 按 `(category, name)` 字典序，**无热度无语义** |
| 技能加载 | `tools/skills_tool.py:skill_view` | 按需拉正文 + linked_files + dedup（Hermes 的按需加载，**Vermes 已有**） |
| 使用统计 | `tools/skill_usage.py` | use_count / view_count / patch_count |
| 质量管理 | `agent/curator.py` | 自动 stale/archive/consolidate（后台审查 agent） |
| 自建技能 | `skill_manage` | 保存/patch 技能 |
| 语义检索基建 | `agent/rag_provider.py` | FTS5 trigram（中英）+ 可选 sqlite-vec 向量；**只检索 memory chunks，不检索技能** |

**核心缺口**：
1. `_sort_skills` 是字典序，`use_count` 只喂 curator 归档，不喂推荐排序。
2. `rag_provider` 的 FTS5/向量基建完全没接到技能索引上。
3. 现状 = LLM 靠 system prompt 里 231 条 name+description **自己"读描述猜"**用什么技能；技能越多越不准、越耗 context。

---

## 2. 两个正交问题（此前被混在 `resolve_compact_skill_categories`）

- **A. 省 token（负载问题）**：技能索引 41KB 要不要折叠 → 这是 M7 折叠。
- **B. 选得准（路由问题）**：怎么让 agent 精准科学地选技能 → 这才是「agent 实力」。

---

## 3. M7 折叠处置（问题 A）

### 3.1 对齐 Hermes 语义
- 折叠 gate 到显式 `focus`（`off/auto/focus/on`），auto **不折叠**。
- Hermes 官方 docstring 原话：*"index changes under ``auto`` proved too surprising"*（auto 下改索引太出乎意料）。
- 砍掉 `skill_index_compact_threshold_bytes` 独立字节阈值（实测默认 20000 < 实际 41860，auto 恒触发，阈值形同虚设）。

### 3.2 归入 compression_scheduler 统一调度
- 折叠作为「context 接近 70% 阈值（`HARD_THRESHOLD_PCT=0.70`）时的降级策略」之一，与对话压缩同一调度，不另立平行开关。
- 理由：真正的 token 压力来自对话增长（已有 `compression_scheduler` + 疲劳桥 `conversation_compression` 兜底），技能索引是 41KB 固定底座、不增长，现代 128K–200K context 下占比仅 ~8–14%。

### 3.3 结论
- 保留「按需加载」（`skill_view` 已有，不动）。
- M7 折叠：**要么 gate 到 focus（Hermes 原版），要么砍掉独立阈值归入 compression_scheduler**。二者取一，倾向后者（更符合「统一上下文管理」）。

---

## 4. 技能路由方向（问题 B，单独立项）

按投入产出排序，均可复用现有基建：

| 层 | 内容 | 复用 | 量级 |
|---|---|---|---|
| L1 热度排序 | `skills_list` 排序从字典序 → `use_count` 加权（高频前置，LLM 更易选对） | `skill_usage.py` 现成信号 | 极小 |
| L2 语义检索 | 231 SKILL.md description 建 FTS5/向量索引，新增 `skill_search(query)` 工具 | `rag_provider.py` FTS5+vec 基建 | 小-中 |
| L3 意图路由 | 任务→技能推荐（语义匹配 + 历史共现 + 使用频率），`skill_view` 加「加载前确认相关」引导 | L1+L2 合成 | 中-大 |

**核心洞察**：Vermes 缺的不是「省 token 的折叠」，是「选得准的路由」；而路由的原料（usage 统计、语义检索基建、curator 质量信号）**全都在，只是没接起来**。

---

## 5. 建议（待董董拍板）

1. **M7 折叠**：对齐 Hermes focus 语义 + 归入 compression_scheduler（砍独立阈值）——收口现有混乱。
2. **技能路由**：单独立项，先做 L1（热度排序，极小）+ L2（`skill_search` 语义检索，复用 rag_provider）——这是「agent 实力」的真正杠杆。
3. L3 意图路由等 L1+L2 验证价值后再排。

— QClaw 分析 · 2026-09-20
