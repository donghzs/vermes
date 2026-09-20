# 技能路由 · QClaw 复审计报告（第二轮）

> **对象**：MiMo 按拍板修的 4 项 P1（A trigger 入索引 / B 精度阈值 / C 删成本面 / D 默认 off）
> **分支**：`feat/skill-routing`，worktree `/Users/dongzusheng/Projects/vermes-electron-skill-routing`，未 commit
> **方式**：逐条读源码 + 独立实测 + 测试 fixture 与真实数据交叉验证
> **时间**：2026-09-20 19:14–19:35 GMT+8

---

## 结论速览

| # | 修复项 | 判定 | 说明 |
|---|---|---|---|
| A | trigger 入索引 | ✅ **通过** | 实测「写论文」→ `scholarforge-thesis-pipeline` 命中 |
| B | 精度阈值 | ❌ **未真修复** | 算法对，但**测试 fixture 与真实数据脱节**，真实 weather 仍漏 |
| C | 删成本面 | ⚠️ **通过但 2 残留** | auto=纯渠道门正确；`estimate_skills_index_bytes` 成死代码 + web_dist 构建产物 stale |
| D | 默认 off | ✅ **通过** | `skill_router_enabled: False` 实测确认 |

测试复现：`56 passed in 6.05s` ✅

---

## 一、A · trigger 入索引（✅ 通过）

- `_discover_skills()` 已读 `trigger`（含 `triggers` / `trigger_phrases` 别名），经 `_normalize_trigger` 归一化
- FTS5 表新增 `trigger` 列，且带 schema 迁移（`ALTER TABLE skills ADD COLUMN trigger` + `DROP TABLE skills_fts` 重建）
- `_hit_score` 的 blob 已含 trigger
- **实测**：
  ```
  search_skills('写论文')   -> ['scholarforge-thesis-pipeline']  ✅
  search_skills('生成视频')  -> ['agnes-video-generation', ...]  ✅
  search_skills('网络诊断')  -> ['network-diagnosis']  ✅
  ```
- trigger 归一化处理了 list/tuple/YAML folded scalar 三种形态，稳健。

## 二、B · 精度阈值（❌ 未真修复，测试脱节）

### 算法本身是对的
`_required_score(tokens)`：多 token 需 `>=2` 个不同 token 命中，单 token 长度 <3 拒绝。逻辑正确。

### 但测试 fixture 与真实数据脱节，真实场景仍漏

**测试 fixture 的 weather**（`test_skill_router.py:139`）：
```
"Local weather forecast using meteorological analysis models"
```
只含 `analysis`（不含 `data`）→ score=1 < 2 → 被正确拒绝。

**真实 weather 的 description**（`~/.vermes/skills/weather/SKILL.md`）：
```
"NOT for: historical weather data, severe weather alerts, or detailed meteorological analysis."
```
**否定句里同时含 `data` 和 `analysis` 两个词** → `_hit_score = 2` → 通过阈值。

**实测真实数据**：
```
search_skills('data analysis') -> ['codebase-audit', 'security-audit', 'weather']  ❌ weather 仍混入
```

### 根因
weather 的否定语境（"NOT for: ... data ... analysis"）恰好两个词都命中，score=2 达标。测试用简化 description 复现不出这个边界 → **假绿**。

### 修复方向（二选一，需 MiMo 定）
1. **否定语境剥离**：索引/计分时把 `NOT for:` / `不支持` / `不适用于` / `not intended for` 之后的文本排除或降权
2. **至少让测试复现**：`test_like_fallback_rejects_single_word` 的 weather fixture 改用真实 description（含 "NOT for: ... data ... analysis"），先暴露再针对性修

## 三、C · 删成本面（⚠️ 通过，2 个残留）

### 主体正确 ✅
- `resolve_compact_skill_categories` 的 auto 分支 = 纯渠道门（`platform in _INTERACTIVE_CODING_PLATFORMS → 降级，否则 None`）
- `_model_context_window`、`skill_index_compact_ratio_pct`、`skill_index_compact_threshold_bytes` 均已删（测试 `test_no_model_context_window_helper` 断言无残留）
- **实测**：强制 `mode=auto` 下 `auto/cli→DENY(33类)`、`auto/telegram→None`、`auto/空→None`、`auto/None→None` ✅

### 残留 1：`estimate_skills_index_bytes` 成死代码 🟡
- `agent/prompt_builder.py:1193` 仍定义，但全仓 `grep` 无任何调用点（仅定义处）
- 成本面删了，这个字节估算函数失去唯一消费方 → 死代码，应一并删除（或明确保留原因）

### 残留 2：web_dist 构建产物 stale 🟡
- `vermes_cli/web_dist/assets/Settings-Bj1C0yel.js` 仍含旧文案：
  - `"auto 阈值：agent.skill_index_compact_threshold_bytes"`
  - `"基线见 reports/skills-index-p1-baseline-20260920.md"`
- 这是 M7 的 GUI 文案，与后端「删成本面」矛盾 → **发布后 GUI 会显示已删除的字段**
- `git log` 显示 M7 有专门 `build: web_dist for M7 threshold settings copy` commit → 说明 web_dist 是发布链路必需产物，必须重建

## 四、D · 默认 off（✅ 通过）

- `vermes_cli/config.py:585` `skill_router_enabled: False` ✅
- 注释明确「需要时显式打开」，与 M7 `compact_skill_categories=off` 默认不打扰哲学一致

---

## 给 MiMo 的修复指令

**B 是硬伤，必须修；C 两个残留顺手清掉。修完再报。**

1. **B**：处理否定语境过召回——`_hit_score` 或 `_discover_skills` 剥离 "NOT for:" / "不支持" / "不适用于" 等否定段；并让测试 fixture 用真实 weather description 复现（先红后绿）
2. **C-残留1**：删 `estimate_skills_index_bytes`（死代码）
3. **C-残留2**：重建 web_dist（跑前端构建），让 GUI 文案与后端一致

修完重跑 56 passed + B 的真实数据回归（`search_skills('data analysis')` 不返回 weather）。

— QClaw 复审计 · 2026-09-20
