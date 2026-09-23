# S2.3 迁剩余 14 键 · 交付报告（供交叉审计）

> 交付方：MiMo · 时刻：2026-09-23 · 范围：**editing_guardrails 补 YAML + 15 注入点统一走 `_resolve_section`**
> 对照真源：`docs/plans/2026-09-23-s2-pluginization-workorder.md` §5 S2.3 / §9b
> 纪律：gold 门闩先于一切；**不改注入文本**；只 add 本批文件；数字带当场命令 + HEAD hash。

---

## 0. 结论先行

**S2.3 通过门**：15 键全部走统一注入入口 `_resolve_section`；`editing_guardrails` 补 YAML 且与常量**字节等价**。gold 17 场景 × 3 段**逐字相同**。双探针变异实证门闩有牙。

**关键情报（S2.4 前置）**：4 键的 builtin YAML **已领先**硬编码常量（`task_completion` / `scholarforge_workflow` / `tool_use_enforcement` / `openai_model`）。gold 走 YAML，故 gold 仍绿；退役 `_PROCESSOR_FALLBACK` 前必须先对齐或显式废弃常量侧。集合已钉进契约测 `KNOWN_YAML_CONSTANT_DRIFT`。

---

## 1. 改了什么（最小面）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `vermes_cli/processors/editing_guardrails.yaml` | **新增**，content 与 `EDITING_GUARDRAILS_GUIDANCE` 字节等价（408B） | S2.3：唯一残留硬编码键补 YAML |
| `agent/system_prompt.py` | 14 个 `_proc_or_default(...)` 调用点 → `_resolve_section(...)[0]` | S2.3：统一入口，15 键全覆盖 |
| 同上 | `_resolve_section` 改单次 `load_all_processors` 遍历（去掉 `_get_processor` 双查） | 审计性能瑕疵；语义不变 |
| 同上 | 去掉未使用的 `compute_manifest_hash` import | hash 用 `p.content_hash`（parse 时已 canonical） |
| `tests/tools/test_s23_migrate_sections.py` | **新增** 20 条（15 逐键 + YAML 等价 + 漂移钉扎 + 调用点静态检） | §5 通过门「每个键单独一次比对」 |
| `tests/tools/test_s22_identity.py` | hide 策略改 patch `load_all_processors`；fallback 测适配 | `_resolve_section` 不再依赖 `_get_processor` |

**没改**：任何 gold 文件 / `prompt_processor_loader.py` / `_PROCESSOR_FALLBACK` 常量内容 / 注入条件与顺序。

### 1.1 `_proc_or_default` 去留

`build_system_prompt_parts` 体内 **0 处** 调用（静态检钉住）。函数保留为薄包装（测试与外部兼容）；S2.4 退役常量时一并处置。

---

## 2. 逐字等价证据

### 2.1 gold 门闩（硬门槛）

```
$ PYTHONPATH=. .venv/bin/python scripts/s2_snapshot.py --check
[s2] 比对通过：17 场景 × 3 段与 gold 逐字相同
```

迁移前后同一次 `--check` 均绿。**S2.3 不改注入文本**。

### 2.2 逐键一次比对（工单 §5 通过门）

契约测 `test_each_key_content_matches_source_of_truth` 参数化 15 键：每键断言
`_resolve_section(name)[0] == _proc_or_default(name)` 且等于其生效源（processor.content 或常量）。

当场字段读数（15 键全 `builtin`）：

| name | source | content_hash（前 16） | == 常量 |
|---|---|---|---|
| identity | builtin | `sha256:0732f72807040` | True |
| help_guidance | builtin | `sha256:efbce5a7493f9` | True |
| task_completion | builtin | `sha256:db385c7078250` | **False** |
| editing_guardrails | builtin | `sha256:38ddeb194ad9c` | True |
| memory_guidance | builtin | `sha256:245b47151c933` | True |
| session_search | builtin | `sha256:c818b314d1da0` | True |
| skills_guidance | builtin | `sha256:6270b7e2c483e` | True |
| image_generate | builtin | `sha256:ca9b694c6ca69` | True |
| academic_search | builtin | `sha256:13253e4772f5d` | True |
| scholarforge_workflow | builtin | `sha256:b6a0465fc5d72` | **False** |
| kanban | builtin | `sha256:22679313744f8` | True |
| computer_use | builtin | `sha256:d5a6b24f08c50` | True |
| tool_use_enforcement | builtin | `sha256:a25b7adf1134f` | **False** |
| google_model | builtin | `sha256:df684ccb71921` | True |
| openai_model | builtin | `sha256:3d373340baecd` | **False** |

---

## 3. 契约测（`tests/tools/test_s23_migrate_sections.py`）

| # | 测 | 为什么 |
|---|---|---|
| 1 | editing_guardrails.yaml 存在且 content 字节等价常量 | 补 YAML 不改字 |
| 2 | source 变 `builtin` 且 hash==proc.content_hash | 走 YAML 路径 |
| 3–17 | 15 键逐键 == 生效源 且 == `_proc_or_default` | §5「每个键单独一次比对」 |
| 18 | 漂移集合 == `KNOWN_YAML_CONSTANT_DRIFT`（4 键） | S2.4 情报钉扎 |
| 19 | 15 键无 missing | 可解析性 |
| 20 | `build_system_prompt_parts` 0 处 `_proc_or_default` 调用 | 调用点迁移完成 |

加上 S2.2 八条（hide 后 fallback 路径已适配）—— 本批合计自测 **28 passed**；宽套件 **111 passed**。

### 3.1 双探针变异（对照组判别力，已还原）

| 变异 | 期望 | 实测 |
|---|---|---|
| A：`editing_guardrails.yaml` 改一词 | gold **红** | 17 处 `S*.stable` 全 diff ✅ |
| B：`_resolve_section` 改成常量优先 | source 变 `fallback` 可抓 | 3 键 source 判别 ✅ |
| 还原后 | gold **绿** + source=`builtin` | ✅ |

---

## 4. 覆盖口径（勿误读）

- gold：17 场景 / `unique_stable_fingerprints=14` / `unique_context_fingerprints=2`（manifest 自带）。
- S2.3 **不新增场景**、**不改 gold 文件** —— 换路径 + 补 YAML（内容等价），不换字。
- 「每个键单独一次比对」= 参数化契约测对 15 键逐一断言生效源等价，不是 15 次 gold 进程。
- 33 个 prompt 段仍 **100% stable**；context 非空仅 S11 探针（S2.2 报告口径沿用）。

---

## 5. 边界 / 未做（诚实清单）

| 项 | 状态 |
|---|---|
| 删 `_PROCESSOR_FALLBACK` | **未做**（S2.4；先处理 4 键双源漂移 + 读 §9b.1） |
| 4 键 YAML/常量对齐 | **未做** —— 只钉扎集合；对齐策略留给 S2.4 拍板（以 YAML 为准回写常量，或废弃常量侧） |
| `VERMES_DISABLE_PROMPT_SECTIONS` | **未做**（P3：S2.2 首次动注入点后的下一刀；可发现性 `list_prompt_sections` 已在） |
| plugin Callable 真渲染进 prompt | 未接（S2.1 登记面，后续） |
| 改 gold / 改 `prompt_processor_loader` 优先级 | **未动** |

---

## 6. 自测读数（当场命令）

| 项 | 读数 |
|---|---|
| `s2_snapshot.py --check` | 17 场景 × 3 段逐字通过 |
| `pytest test_s23 + test_s22` | **28 passed**（7.21s） |
| `pytest … + gold + s21 + prompt_processors` | **111 passed**（70.46s） |
| gold 相对 origin/main | 本批前无 diff；本批亦未改 gold |
| HEAD | commit 本批 |

---

## 7. 请审计重点核（不采信声称）

1. `editing_guardrails.yaml` content 是否与 `EDITING_GUARDRAILS_GUIDANCE` **字节**等价（不是「大意相同」）。
2. gold 有无被偷改：`git diff origin/main -- reports/s2/gold/` 应为空。
3. 15 个调用点是否全部 `_resolve_section`；`_proc_or_default` 是否只在定义/测试出现。
4. 4 键漂移集合是否与 `KNOWN_YAML_CONSTANT_DRIFT` 一致；有没有第 5 键漏报。
5. 双探针可复现（A 必红、还原必绿；B source 必可抓）。
6. `_resolve_section` 单次查找后，plugin < builtin < user 语义是否仍由 `load_all_processors` 承担（不在 resolve 内重复实现）。

— 完 —
