# 技能路由 · QClaw 收口验证（第三轮·最终）

> **对象**：MiMo 第二轮修复后的五点收口
> **分支**：`feat/skill-routing`，worktree `/Users/dongzusheng/Projects/vermes-electron-skill-routing`，未 commit
> **方式**：逐条独立实测 + 源码 grep + 测试复现
> **时间**：2026-09-20 19:30–19:45 GMT+8

---

## 五点收口结论

| # | 收口点 | 判定 |
|---|---|---|
| 1 | A′ 渠道门（auto 下 IM/未知/空永不降级） | ✅ 通过 |
| 2 | 比例阈值修死开关（删成本面） | ✅ 通过（后端已删） |
| 3 | SkillRouter cache-safe（prefetch 注入） | ✅ 通过 |
| 4 | 技能索引与记忆分表 | ✅ 通过 |
| 5 | 相关测试通过 | ✅ 56 passed |

## 修复项验证

| 项 | 判定 | 证据 |
|---|---|---|
| A trigger 入索引 | ✅ | 实测「写论文」→ `scholarforge-thesis-pipeline` |
| B 否定语境 | ✅ | 实测真实库 `data analysis` → `['codebase-audit']`（无 weather） |
| C-1 删死代码 | ✅ | `estimate_skills_index_bytes` 全仓仅剩测试断言（`test_a1_channel_gate.py` 反证已删），无定义 |
| C-2 web_dist 重建 | ⚠️ **残留 1 处** | 阈值文案已清，但「基线见 reports/skills-index-p1-baseline-20260920.md」仍在（见下） |
| D 默认 off | ✅ | `skill_router_enabled: False` |

---

## 两个遗留问题（不阻塞功能，但 commit 前建议处理）

### 🟡 遗留 1：前端 GUI「基线见」文案残留（C-2 未收干净）

- **现象**：`frontend/src/components/Settings.vue:2668` 仍渲染：
  ```
  · auto=纯渠道门（交互式降级，IM/群聊不降级）
  · 基线见 reports/skills-index-p1-baseline-20260920.md
  ```
- **问题**：`auto 阈值` / `skill_index_compact_threshold_bytes` 文案已删，但「基线见 reports/skills-index-p1-baseline-20260920.md」这行**引用了 P1 字节基线报告**，而成本面已删、字节基线已无意义 → 语义残留
- **产物侧**：`web_dist/assets/Settings-Ck9tTcrL.js` 同步含 `reports/skills-index-p1-baseline-20260920.md`
- **判定**：非功能阻塞，但属「删成本面」的语义尾巴，应一并清掉（这行基线提示对用户无意义了）

### 🟡 遗留 2：`frontend/node_modules` 是 symlink，会被误提交

- **现象**：`git status` 显示 `?? frontend/node_modules`
- **根因**：`.gitignore:36` 写的是 `node_modules/`（匹配目录），但 worktree 里 `frontend/node_modules` 是一个 **symlink**（→ 主仓库 `vermes-electron/frontend/node_modules`），symlink 是文件不是目录，不被 `node_modules/` 规则匹配
- **风险**：`git add -A` 会把这个 65 字节的 symlink 提交进去
- **修复**：commit 前确认不 `git add frontend/node_modules`，或 `.gitignore` 补 `node_modules`（不带斜杠，同时匹配文件和目录）

---

## 总体判定

**五点全绿 + A/B/C-1/D 四项修复全部落实，可收口。** 剩两个非阻塞遗留（GUI 基线文案 + node_modules symlink），建议 commit 前一并处理：

1. 删 `Settings.vue:2668` 的「基线见 reports/skills-index-p1-baseline-20260920.md」行（或改成无意义的描述），并重建 web_dist
2. `git add` 时排除 `frontend/node_modules`（symlink），或 `.gitignore` 补 `node_modules`

处理完即可 commit 到 `feat/skill-routing`，然后走 §12 冻结流程（不 push）。

— QClaw 收口验证 · 2026-09-20
