# S2.2 identity walking skeleton · 交付报告（供 WorkBuddy 交叉审计）

> 交付方：MiMo · 时刻：2026-09-23 · 范围：**只迁 `identity` 一个块** + 统一注入入口  
> 对照真源：`docs/plans/2026-09-23-s2-pluginization-workorder.md` §5 S2.2 / §9b  
> 纪律：gold 门闩先于一切；**不改注入文本**；只 add 本批文件；数字带当场命令 + HEAD hash。

---

## 0. 结论先行

**S2.2 通过门**：`identity` 已走统一注入入口 `_resolve_section()`，与迁移前
**字节等价**；stable 层 `content_hash` 有 canonical 值且可解释。gold 17 场景 × 3 段
**逐字相同**。双探针变异实证门闩有牙。

**未动**：其余 14 个键仍走 `_proc_or_default`（内部已改为薄包装，调用点零改动）；
`_PROCESSOR_FALLBACK` 未删（S2.4）；`editing_guardrails` 未补 YAML（S2.3）。

---

## 1. 改了什么（最小面）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `agent/system_prompt.py` | 新增 `_resolve_section(name) → (content, source, content_hash)`；`_proc_or_default` 改为 `return _resolve_section(name)[0]` | walking skeleton 的唯一注入入口（工单 §5） |
| 同上 | `build_system_prompt_parts` 的 identity 分支改调 `_resolve_section("identity")` | S2.2 试点：只迁这一个块 |
| 同上 | import 补 `compute_manifest_hash` | content_hash 要 canonical，不能瞎 sha |
| `tests/tools/test_s22_identity.py` | **新增** 8 条契约测 | 见 §3 |

**没改**：`prompt_processor_loader.py` / `plugins.py` / 任何 YAML / gold 文件 / 其余 14 个注入点的调用形式。

### 1.1 `_resolve_section` 语义（审计请对表）

| 返回 | 含义 |
|---|---|
| `content` | 实际进 prompt 的文本。processor 在场用 processor；否则 `_PROCESSOR_FALLBACK`；否则 `computer_use` 惰性；否则 `""` + warning |
| `source` | `user` \| `builtin` \| `plugin` \| `fallback` \| `fallback-lazy` \| `missing` —— **不进 prompt**，只诊断 |
| `content_hash` | processor：`compute_manifest_hash(p)`（canonical）；fallback：`sha256(content)` |

来源优先级 = A4 拍板 **plugin < builtin < user**，但合并发生在 `load_all_processors()`
（plugin 先入、builtin 覆盖 plugin、user 覆盖两者），`_resolve_section` 只查一次
`_get_processor`，不重复实现优先级。

---

## 2. 逐字等价证据

### 2.1 gold 门闩（硬门槛）

```
$ PYTHONPATH=. .venv/bin/python scripts/s2_snapshot.py --check
[s2] 比对通过：17 场景 × 3 段与 gold 逐字相同
```

迁移前后同一次 `--check` 均绿。**identity 迁移不改注入文本** —— 这正是 S2.2 的门。

### 2.2 字段实测（当场）

| name | source | content_hash（前 16） | 备注 |
|---|---|---|---|
| `identity` | `builtin` | `sha256:0732f728…`（`compute_manifest_hash`） | S2.2 试点 |
| `help_guidance` | `builtin` | 同上 | 未迁，仍走 `_proc_or_default` 包装 |
| `editing_guardrails` | `fallback` | `sha256(常量)` 64hex | 仍硬编码（S2.3 补 YAML） |
| `computer_use` | `builtin`（有 YAML）；hide 后 `fallback-lazy` | canonical / `sha256(COMPUTER_USE_GUIDANCE)` | 哨兵坑，见工单 §9b.1 |
| `openai_model` | `builtin` | canonical | model_affinity 分支 |

`_proc_or_default(n)` 与 `_resolve_section(n)[0]` 对全部 15 键**逐字相同**（契约测钉住）。

---

## 3. 契约测（`tests/tools/test_s22_identity.py`，8 passed）

| # | 测 | 为什么 |
|---|---|---|
| 1 | `_resolve_section("identity")` 三元组形状 + hash 为 `sha256:64hex`（processor）或 `64hex`（fallback） | 入口契约 |
| 2 | content == `_proc_or_default("identity")` | **字节等价** |
| 3 | source==`builtin` 且 hash==`compute_manifest_hash` | 工单 §5「canonical 值且可解释」 |
| 4 | fallback 路径 hash==`sha256(content)`（hide YAML） | 可解释性双侧（对照组判别力） |
| 5 | 缺失键 → `( "", "missing", "")` 且 warning | 不静默 |
| 6 | `computer_use` 键仍在 map 且值为 `None`；hide YAML 后走 `fallback-lazy` | §9b.1 防删键（有 YAML 时 source=`builtin`，惰性路径在无 processor 时） |
| 7 | `build_system_prompt_parts` 的 stable 含 identity 内容 | 真注入路径，不只库函数 |
| 8 | `_proc_or_default` 15 键全部 == `_resolve_section` content | 13 个调用点零回归 |

### 3.1 双探针变异（对照组判别力，已还原）

| 变异 | 期望 | 实测 |
|---|---|---|
| A：`identity.yaml` 内容改一个词 | gold **红** | 17 处 `S*.stable` 全 diff ✅ |
| B：`_resolve_section` 改成 fallback 优先 | `source` 变 `fallback` 可抓 | `source=fallback` 判别 ✅ |
| 还原后 | gold **绿** | `--check` 通过 + `source=builtin` ✅ |

---

## 4. 覆盖口径（勿误读）

- gold：17 场景 / `unique_stable_fingerprints=14` / `unique_context_fingerprints=2`（manifest 自带）。
- S2.2 **不新增场景**、**不改 gold 文件** —— 本切片是「换路径、不换字」。
- 33 个 prompt 段仍 **100% stable**（WorkBuddy doctor 实测）；context 非空仅 S11 探针。
  迁块进 context 层仍是 S2.3+ 的前置条件（工单 §9c）。

---

## 5. 边界 / 未做（诚实清单）

| 项 | 状态 |
|---|---|
| 其余 14 键迁入 `_resolve_section` 语义 | 入口已统一，但**调用点仍是 `_proc_or_default`**（内容等价，显式 source/hash 只在 identity 分支暴露） |
| `editing_guardrails` 补 YAML | 未做（S2.3） |
| 删 `_PROCESSOR_FALLBACK` | 未做（S2.4，先读 §9b.1） |
| `VERMES_DISABLE_PROMPT_SECTIONS` | 未做（绑 S2.3 首次真动注入点后的下一刀；可见性打印同） |
| plugin Callable 真渲染进 prompt | 未接（S2.1 只建登记面，工单 §3 明示 S2.2+ 才接） |
| 改 gold / 改 YAML / 改 `prompt_processor_loader` | **未动** |

---

## 6. 自测读数（当场命令）

| 项 | 读数 |
|---|---|
| `s2_snapshot.py --check` | 17 场景 × 3 段逐字通过 |
| `pytest tests/tools/test_s22_identity.py` | **8 passed**（4.98s） |
| `pytest test_s22 + test_s2_gold + test_s21` | **29 passed**（54.67s） |
| `pytest test_s22 + test_s2_gold + test_s21 + test_prompt_processors` | **91 passed**（64.08s） |
| gold 目录相对 origin/main | **无 diff**（未偷改） |
| HEAD | commit 本批 |

---

## 7. 请 WorkBuddy 重点核（不采信声称）

1. **字节等价**是否真成立：抽几个 key 比 `_proc_or_default` vs 迁移前 git show 的内容哈希。  
2. **gold 有无被偷改**：`git diff origin/main -- reports/s2/gold/` 应为空。  
3. **双探针**是否可复现（变异 A 必红、还原必绿）。  
4. **`_resolve_section` 优先级**是否与 `load_all_processors` 一致（plugin < builtin < user）。  
5. **`computer_use` 哨兵**是否仍 `fallback-lazy` 且有测。  
6. 13 个 `_proc_or_default` 调用点是否真的零改动（只应看到 identity 一处显式换成 `_resolve_section`）。

— 完 —
