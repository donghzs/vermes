# P3 `VERMES_DISABLE_PROMPT_SECTIONS` · 2026-09-23

> 状态：**已落地**。分支 `feat/p3-disable-prompt-sections`（worktree `/Users/dongzusheng/Projects/vermes-electron-p3`）。
> 基线：`5dcff00d91`。拍板：**load_all 出口统一过滤**（user/builtin/plugin 一视同仁）+ register 侧拒登。

---

## 1. 行为

```bash
export VERMES_DISABLE_PROMPT_SECTIONS=identity,kanban
```

| 路径 | 行为 |
|---|---|
| `load_all_processors` 出口 | 命中 `effective_id`/`name` 的段不进 cache、不进 prompt |
| `register_plugin_processor` | 命中即拒登 + WARNING（不进注册表） |
| `_resolve_section` | 被禁用段走 missing 可见占位（非空串） |
| `missing_core_sections` | **禁用 ≠ 缺失**（不报 MISSING） |
| `list_prompt_sections` | 补 `disabled_by_env=True` 行（`path=env:VERMES_DISABLE_PROMPT_SECTIONS`） |
| 启动/诊断打印 | 「已按 env 禁用 N 段: id1,id2」（每 generation 一次 + doctor 常显） |
| 默认（env 未设） | 全开，与 S2.4 行为一致，gold 不受影响 |

---

## 2. 实现要点

- `parse_disabled_sections()` / `DISABLE_ENV` / `_apply_disabled_filter` / `_announce_disabled`（`agent/prompt_processor_loader.py`）
- 过滤点唯一：`load_all_processors` 3b 步；register 侧只是顺手拒登，不是第二套语义
- `test_core_diverge_ledger.py` 措辞钉从「不得写成已生效」翻转为「P3 已落地，不得再写尚未实现」

---

## 3. 测试口径

```
test_p3_disable_prompt_sections (7)
+ test_core_diverge_ledger
+ test_hermes_missing_visibility
+ test_s21 + test_s22 + test_s23          →  59 passed
test_s2_gold::cache 哨兵 + test_w_l4_l5   →  14 passed
```

### 双探针

| 探针 | 期望 | 实测 |
|---|---|---|
| A 默认（env 未设） | 15 核心段全在，missing=[] | ✅ |
| B `identity,kanban` | 两段被滤；list 有 disabled 行；missing 不含 identity；日志「已按 env 禁用 2 段: identity,kanban」 | ✅ |

### 门禁

| 门 | 结果 |
|---|---|
| `s2_snapshot --check` @ 仓根 | ✅ 17×3 逐字绿 |
| `s2_snapshot --check` @ `/tmp` | ✅ 同上 |
| cache 哨兵 | ✅ 1 passed |

---

## 4. 改动清单

| 文件 | 摘要 |
|---|---|
| `agent/prompt_processor_loader.py` | DISABLE_ENV / 过滤 / 显式打印 / register 拒登 / list 补 disabled 行 / missing 排除禁用 |
| `vermes_cli/doctor.py` | 「尚未实现」→「已生效」+ 禁用列表 |
| `tests/tools/test_p3_disable_prompt_sections.py` | 新契约 7 条 |
| `tests/tools/test_core_diverge_ledger.py` | 措辞钉翻转（P3 已落地） |
| `docs/plans/2026-09-23-s2-pluginization-workorder.md` | P3 状态 ✅ |

**未夹带**：`tools/feedback_tool.py`。默认路径 gold 不变。

---

## 5. 之后排队

| 项 | 备注 |
|---|---|
| push main（ahead 6+） | `git -c http.version=HTTP/1.1 push` |
| `tools/feedback_tool.py` 单独收 | 与 S/P 系列无关 |
| P3 退役 | `v3.0.0-distribution` 前按工单收回该 env |

— 完 —
