# Hermes 收口 · gold cwd 解耦 + missing 可见性 · 2026-09-23

> 状态：**已落地**。分支 `fix/hermes-s24-closeout`（worktree `/Users/dongzusheng/Projects/vermes-electron-hermes-fix`）。
> 基线：`fdefdba929`（S2.4）。拍板：缺陷1 = 归一化占位符；缺陷2 = ①+②+③ 全闭环。

---

## 1. 缺陷1 — `--check` 依赖调用者 cwd（已修）

**根因**：`build_environment_hints()` 把机器指纹写进 **stable** 三行：

```
Host: macOS (26.5.2)
User home directory: /Users/dongzusheng
Current working directory: /Users/dongzusheng/Projects/vermes-electron
```

gold 指纹 = 生成机绝对路径 → 换 worktree / CI / 用户即假红（Hermes 从 `/private/tmp` 实跑复现）。

**修法**（与 volatile 归一同风格）：`scripts/s2_snapshot.py` 新增 `_MACHINE_NORMALIZERS`，三行归一成：

```
Host: {{HOST}}
User home directory: {{HOME}}
Current working directory: {{CWD}}
```

生成与比对共用 `normalize_machine()`；gold 已 `--write-gold` 重写。**任何 cwd 可跑**。

| 门 | 结果 |
|---|---|
| `--check` @ 仓根 | ✅ 17 场景 × 3 段逐字绿 |
| `--check` @ `/tmp` | ✅ 同上（修前此处 17 红） |
| cache 哨兵 `test_stable_sha_stable_across_two_builds` | ✅ 1 passed |

> 附带：`User home directory` / `Host` 一并归一（Hermes「任何 cwd/**机器**都能跑」）。注入文本本身未改，只是 gold 侧指纹占位符化。

---

## 2. 缺陷2 — 退役兜底后 missing 静默消失（①+②+③ 闭环）

### ① doctor 增加 missing 段出口

- `prompt_processor_loader` 新增：
  - `CORE_PROMPT_SECTION_IDS`（15 注入点核心段）
  - `missing_core_sections() -> list[str]`
  - `list_prompt_sections()` 补 `source=missing` 行（此前白名单不含 missing，缺段在 doctor 里根本不显示）
- `vermes_cli/doctor.py` Prompt Sections 区：缺段 `check_warn("N core section(s) MISSING", …)` + 恢复指引

### ② 打包链 37 YAML 完整性断言

- `EXPECTED_PROCESSOR_YAMLS` 金名单 37 个（tripwire：新增 YAML 必须显式进表）
- `scripts/prebuild-check.sh`：源码树 ≥37 + 5 个核心段非空（实跑输出 `prompt processors: 37 个 YAML（≥37）`）
- `scripts/verify-build.sh`：构建产物内 processors ≥37 + 核心段非空（防 PyInstaller datas 漏拷）
- 契约测 `test_expected_processor_yamls_all_present` 钉住金名单

### ③ missing 时极小可见兜底（非原常量全文）

`_resolve_section` missing 路径不再返回 `""`，改为：

```
# [prompt-section missing: editing_guardrails]
# Expected guidance failed to load. Restore vermes_cli/processors/editing_guardrails.yaml
# or reinstall Vermes. This placeholder is intentionally visible.
```

source 仍为 `missing`，`content_hash = sha256(content)`。**空串会让 editing_guardrails 等安全段静默消失；占位符让异常在 prompt 里露出来。**

---

## 3. 测试与探针

| 项 | 结果 |
|---|---|
| 新契约 `test_hermes_missing_visibility.py` | ✅ 7 条（金名单 / 15 键 / missing 探测 / list 出口 / 可见占位） |
| S2 契约 + gold 套件 + 本批 | ✅ **68 passed** |
| 探针 A（完好） | missing=[]，`editing_guardrails` source=builtin，无占位 |
| 探针 B（hide） | missing=15，list 含 source=missing 行，resolve 吐可见占位 |

---

## 4. 改动清单

| 文件 | 摘要 |
|---|---|
| `scripts/s2_snapshot.py` | `_MACHINE_NORMALIZERS` + `normalize_machine`；三段全归一 |
| `reports/s2/gold/*` | 重写为占位符指纹（17 场景 + manifest） |
| `agent/prompt_processor_loader.py` | `CORE_PROMPT_SECTION_IDS` / `EXPECTED_PROCESSOR_YAMLS` / `missing_core_sections` / list 补 missing 行 |
| `agent/system_prompt.py` | missing 可见占位文本 |
| `vermes_cli/doctor.py` | Prompt Sections 增 missing WARN |
| `scripts/prebuild-check.sh` | 源码 37 YAML + 核心段断言 |
| `scripts/verify-build.sh` | 产物 37 YAML + 核心段断言 |
| `tests/tools/test_hermes_missing_visibility.py` | 新契约 7 条 |
| `tests/tools/test_s22_identity.py` | missing 期望从空串改为可见占位 |
| `tests/tools/test_s23_migrate_sections.py` | 文案 + 断言排除占位 |

**未夹带**：`tools/feedback_tool.py`。未改注入条件与顺序；gold 只做机器指纹占位符化。

---

## 5. 测试口径备忘（对齐 Hermes）

以后写「哪几个文件 → N passed」：

```
test_hermes_missing_visibility + test_s21 + test_s22 + test_s23
  + test_w_l4_l5_m7_threshold + test_s2_gold  →  68 passed
```

— 完 —
