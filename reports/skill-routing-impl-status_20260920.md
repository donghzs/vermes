# 技能路由 · 实现状态（round2 复审计就绪）· 2026-09-20

> **分支**：`feat/skill-routing`  
> **Worktree**：`/Users/dongzusheng/Projects/vermes-electron-skill-routing`  
> **状态**：round2 指令已修完，**未 commit**，待 QClaw 收口  
> **测试**：**56 passed**  
> **说明**：上一轮工具调用误循环，已中断；本轮只补未完成项。

---

## Round2 指令对照

| # | 项 | 状态 | 证据 |
|---|---|---|---|
| **B** | 否定语境 + 真实 fixture | ✅ | `_hit_score` 剥离 `NOT for:` / 不支持 等否定范围；测试用真实 weather 文案 |
| **C-1** | 删 `estimate_skills_index_bytes` 死代码 | ✅ | `hasattr(pb, ...)=False`；prompt_builder 源码无该符号 |
| **C-2** | 重建 web_dist | ✅ | `vite build` → `Settings-Ck9tTcrL.js`；产物中 **无** `skill_index_compact_threshold` |
| A / D | （上轮已过） | ✅ | trigger 命中「写论文」；`skill_router_enabled=False` |

---

## 真实技能库回归（`~/.vermes`，231 技）

| 查询 | 结果 |
|---|---|
| `写论文` | `scholarforge-thesis-pipeline` ✅ |
| `data analysis` | `['codebase-audit']` — **无 weather** ✅ |
| 死代码 | `estimate_skills_index_bytes` / `_model_context_window` 均不存在 |
| 默认配置 | `skill_router_enabled=False`；无 ratio/threshold 键 |

**真实 weather description（测试 fixture 已对齐）：**  
`... NOT for: historical weather data, severe weather alerts, or detailed meteorological analysis.`  
否定范围内的 `data`/`analysis` 不再计入 score。

---

## auto 终局语义（拍板 C）

```text
auto: platform ∈ {cli,web,desktop,tui,acp,local,api,api_server} → 降级
      其他（IM/未知/空）→ 永不降级
on:   跨渠道始终降级
off:  默认，不降级
```

无成本面：已删 `_model_context_window`、`skill_index_compact_ratio_pct`、`skill_index_compact_threshold_bytes`。

---

## web_dist

- 源：`frontend/src/components/Settings.vue`（已改「纯渠道门」文案）
- 构建：`cd frontend && npm run build`（vite outDir → `vermes_cli/web_dist`）
- 新产物：`vermes_cli/web_dist/assets/Settings-Ck9tTcrL.js`
- 旧 stale `Settings-Bj1C0yel.js` 不再被 index.html 引用

---

## 复现

```bash
cd /Users/dongzusheng/Projects/vermes-electron-skill-routing
PYTHONPATH=. /Users/dongzusheng/Projects/vermes-electron/.venv/bin/python -m pytest \
  tests/agent/test_a1_channel_gate.py \
  tests/agent/test_w_l4_l5_m7_threshold.py \
  tests/agent/test_skills_index_p1.py \
  tests/agent/test_p3_m7_skills_deny_gui.py \
  tests/agent/test_skill_router.py \
  tests/tools/test_sort_skills_heat.py -q
# 56 passed

# 真实库检索
PYTHONPATH=. /Users/dongzusheng/Projects/vermes-electron/.venv/bin/python -c "
import os; from pathlib import Path
os.environ['VERMES_HOME']=str(Path.home()/'.vermes')
from agent import skill_router as sr
sr._index_state['built_at']=0; sr._conn_cache.clear(); sr.rebuild_index(force=True)
print(sr.search_skills('data analysis'))  # 不得含 weather
print(sr.search_skills('写论文'))           # 含 scholarforge
"
```

**请 QClaw 收口验证：**  
① 真实库 `data analysis` 无 weather ② 无 `estimate_skills_index_bytes` / ratio / threshold 残留 ③ web_dist 新 Settings 无阈值文案 ④ 56 passed ⑤ 默认 router off。

— MiMo · 待 QClaw 收口 · 未 commit
