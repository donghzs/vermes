# S2.4 退役 `_PROCESSOR_FALLBACK` · 2026-09-23

> 状态：**已落地**。分支 `feat/s24-retire-fallback`（worktree `/Users/dongzusheng/Projects/vermes-electron-s24`）。
> 基线 HEAD：`950ea7a5ae`。策略按接力拍板：**以 YAML 为准回写常量 → 再删 map**；`computer_use` 保惰性分支（工单 §9b.1）。

---

## 1. 做了什么

### 1.1 回写 4 键漂移（以 builtin YAML 为准改 `agent/prompt_builder.py` 常量）

| 键 | 常量 | 字节等价 | sha256 前 12 |
|---|---|---|---|
| `task_completion` | `TASK_COMPLETION_GUIDANCE` | ✅ = YAML | `6e063a5d71bf` |
| `scholarforge_workflow` | `SCHOLARFORGE_WORKFLOW_GUIDANCE` | ✅ = YAML | `7b65455bedae` |
| `tool_use_enforcement` | `TOOL_USE_ENFORCEMENT_GUIDANCE` | ✅ = YAML | `a5456fbfbe45` |
| `openai_model` | `OPENAI_MODEL_EXECUTION_GUIDANCE` | ✅ = YAML | `9dd1d7007eff` |

对照未漂移键（本就等价，未动）：`computer_use` `652088a33834` / `identity` `ffc851598814` / `editing_guardrails` `33a5fff380d1`。

`KNOWN_YAML_CONSTANT_DRIFT` 已清为空集（钉扎测保留，再漂移必须显式改表）。

### 1.2 退役 map（`agent/system_prompt.py`）

- 删除整张 `_PROCESSOR_FALLBACK`（14 字符串常量 + 1 个 `computer_use: None` 哨兵键）。
- `_resolve_section` 现来源：user > builtin YAML > plugin > **`computer_use` 显式惰性分支** > `("", "missing", "")` + `logger.warning`。
- §9b.1 处置：**保留显式 `if name == "computer_use"` 惰性分支**（选项②）；契约测钉住「删 YAML 后仍有兜底」。
- 清掉仅被 map 引用的 14 个 `prompt_builder` import；`PLATFORM_HINTS` / `TOOL_USE_ENFORCEMENT_*` 保留。
- `_proc_or_default` 改为薄包装文档（注入点早已走 `_resolve_section`）。

### 1.3 改受影响测试（4 文件，勿漏清单全覆盖）

| 文件 | 改法 |
|---|---|
| `tests/tools/test_s21_register_section.py` | 哨兵断言去掉 map 键；改断言惰性分支（hide → `fallback-lazy` 仍取到 guidance） |
| `tests/tools/test_s22_identity.py` | 删 `_PROCESSOR_FALLBACK["identity"]` / map keys=15；fallback 路径测改为 **missing 路径测**；`computer_use` 断言 `hasattr` False + lazy；keys 来源改 `CONSTANTS` |
| `tests/tools/test_s23_migrate_sections.py` | 漂移表清空；`set(CONSTANTS)==set(map)` 改为 15 键经 processor 可解析；source 分支去掉 `fallback` |
| `tests/agent/test_w_l4_l5_m7_threshold.py` | `assertIs(map.get(...))` → 断言 YAML/常量字节等价 |

---

## 2. 通过门（硬）— 全绿

| 门 | 命令 | 结果 |
|---|---|---|
| gold 全场景 | `PYTHONPATH=<s24-worktree> <main>/.venv/bin/python <s24>/scripts/s2_snapshot.py --check`（**cwd=main**） | **17 场景 × 3 段逐字绿** |
| cache 哨兵 | `pytest tests/tools/test_s2_gold.py` | `test_stable_sha_stable_across_two_builds` **绿**（同套件 5 passed） |
| 契约 + gold 套件 | `pytest test_s21 test_s22 test_s23 test_w_l4_l5_m7 test_s2_gold` | **62 passed** |
| 宽回归（prompt/run_agent） | `pytest test_prompt_builder test_s2_gold test_run_agent` | **460 passed, 5 skipped, 7 failed**（7 红为基线既有，见 §3） |

### 双探针（应命中 + 应不命中）

| 探针 | 操作 | 期望 | 实测 |
|---|---|---|---|
| 1a | hide `load_all_processors` | `computer_use` → `fallback-lazy`，content=`COMPUTER_USE_GUIDANCE`，hash=sha256(content) | ✅ |
| 1b | hide `load_all_processors` | `identity` / `task_completion` → `("", "missing", "")` + warning（**不再** map 兜底） | ✅ |
| 2a | 改 `identity.yaml` content 行 | gold **红**（17 场景 stable 全漂） | ✅ 红 |
| 2b | 还原 `identity.yaml` | gold **绿** | ✅ 17×3 逐字绿，YAML 与备份 byte-equal |

---

## 3. 已知无关红（基线复现，非 S2.4 引入）

stash 掉本批改动后，下列 **同样 7 红**：

- `tests/agent/test_prompt_builder.py::TestEnvironmentHints::test_remote_backend_list_covers_known_sandboxes`
- `tests/run_agent/test_run_agent.py::TestConcurrentToolExecution`（5 条）
- `tests/run_agent/test_run_agent.py::TestMemoryNudgeCounterPersistence::test_counters_not_reset_in_preamble`

属既有 flaky/环境项，**未夹带修复**。

### gold 的 cwd 敏感性（排障备忘）

`build_environment_hints()` 会把 `os.getcwd()` 写进 **stable** 段（`Current working directory: …`）。gold 以 main 路径 `/Users/dongzusheng/Projects/vermes-electron` 为指纹；在 `vermes-electron-s24` worktree 里裸跑 `--check` 会 17 红——**与注入逻辑无关**。复现/门禁请：

```bash
cd /Users/dongzusheng/Projects/vermes-electron   # cwd 对齐 gold
PYTHONPATH=/Users/dongzusheng/Projects/vermes-electron-s24 \
  /Users/dongzusheng/Projects/vermes-electron/.venv/bin/python \
  /Users/dongzusheng/Projects/vermes-electron-s24/scripts/s2_snapshot.py --check
```

（stdin 探针脚本同理：cwd=main 时 `sys.path[0]=''` 会先 import main 的 `agent`，须 `sys.path.insert(0, s24-worktree)`。）

---

## 4. 改动清单（只含本批）

| 文件 | 摘要 |
|---|---|
| `agent/prompt_builder.py` | 4 键常量以 YAML 为准回写 |
| `agent/system_prompt.py` | 删 `_PROCESSOR_FALLBACK`；保留 `computer_use` 惰性；missing 路径 warning；清 map 专用 import |
| `tests/tools/test_s21_register_section.py` | 惰性分支契约 |
| `tests/tools/test_s22_identity.py` | missing 路径 + map 退役断言 |
| `tests/tools/test_s23_migrate_sections.py` | 漂移表空集 + 15 键 processor 解析 |
| `tests/agent/test_w_l4_l5_m7_threshold.py` | YAML/常量等价 |
| `docs/plans/2026-09-23-s2-pluginization-workorder.md` | §5 S2.4 状态 ✅ |
| `reports/qclaw/s24-retire-fallback_20260923.md` | 本报告 |

**未夹带**：`tools/feedback_tool.py`（主仓他人/前会话 WIP）；未改任何 gold；未改注入条件与顺序。

---

## 5. 行为变化（有意）

| 场景 | S2.3 及以前 | S2.4 |
|---|---|---|
| YAML 在场（生产/gold） | processor 内容 | **不变**（gold 逐字绿） |
| YAML 缺失，非 `computer_use` | 回落硬编码常量 | `""` + warning（**不再静默/不再陈旧常量**） |
| YAML 缺失，`computer_use` | map 哨兵 `None` → 惰性 import | **同一语义**，改为显式分支（§9b.1 选项②） |
| 双源漂移 | 4 键已钉扎 | 常量=YAML，钉扎表空集 |

---

## 6. 之后排队（勿抢跑）

| 优先级 | 项 |
|---|---|
| 下下刀 | P3 `VERMES_DISABLE_PROMPT_SECTIONS=id1,id2`（含 Hermes 可发现性/可见性补点） |
| 可选 | push main（ahead 3 + 本切片） |
| 可选 | `tools/feedback_tool.py` 单独收 |
| 可选 | gold cwd 归一（把 `os.getcwd()` 归一为占位符，或生成时钉死 `TERMINAL_CWD`）—— 已知痛点，非本切片范围 |

— 完 —
