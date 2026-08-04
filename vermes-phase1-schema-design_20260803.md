# Vermes Phase 1 — Processor Schema 定稿 + f26352f82 评审

日期：2026-08-03
范围：乐高式自进化 Phase 1（prompt/行为层外置为声明式 Processor 积木）
关联：Phase 0 = `7498f0f18`(module reload) + `bb8b147e2`(watcher 安全网)，已运行态闭环、DMG 2.3.7 发布
被评审对象：`f26352f82` "Phase 1 — Prompt Processor YAML 外置 + 热加载"（作者 donghzs）

---

## 0. 结论速览

| 项 | 判定 |
|---|---|
| GLM5.2 四点拍板方向 | ✅ 全部成立，采纳 |
| `f26352f82` 加载器骨架（缓存/watcher/fallback 三层） | ✅ 值得保留，升级而非重写 |
| **内置 processor 加载** | ❌ **P0 阻断 bug，实测加载 0 个** |
| `replaceable=False` 保护 | ❌ 被同一 bug 连带击穿 |
| 8 个无 fallback 调用点 | ❌ 静默丢 guidance |
| processor.yaml 的审批归类 | ❌ 被判 config_level → YOLO 放行，爆炸半径错配 |
| 混合目录（内置平铺 / 用户子目录） | ⚠️ 可行，但 override 键必须与路径解耦 |
| mustache 手写不引依赖 | ✅ 采纳，但需补 3 条规范防破坏性变更 |

**时间窗口**：Phase 1 尚未进 DMG（2.3.7 打的是 Phase 0），运行态**未被污染**，在打包前修完即可。

---

## 1. P0 — 内置 processor 实际加载数 = 0

### 1.1 根因

`agent/prompt_processor_loader.py:111-115`

```python
def _get_builtin_dir() -> Path:
    # In frozen bundle: _internal/vermes_cli/processors/
    # In dev: vermes_cli/processors/
    return Path(__file__).resolve().parent / "vermes_cli" / "processors"
```

`__file__` = `<repo>/agent/prompt_processor_loader.py` → `.parent` = `<repo>/agent/`
→ 拼出 `<repo>/agent/vermes_cli/processors`，**该目录不存在**（真实位置是 `<repo>/vermes_cli/processors`）。

### 1.2 实测取证

```
$ .venv/bin/python -c "from agent.prompt_processor_loader import load_all_processors, _get_builtin_dir; \
                       print(_get_builtin_dir(), _get_builtin_dir().exists()); \
                       print('COUNT =', len(load_all_processors()))"
/Users/.../vermes-electron/agent/vermes_cli/processors  False
COUNT = 0
```

`ls vermes_cli/processors/*.yaml | wc -l` → **32**（文件确实都导出了，只是加载不到）。

### 1.3 为什么「20 passed」没抓到

`tests/agent/test_prompt_processors.py` 的 **全部** 加载类用例都 monkeypatch 了这个函数：

```
line 114/141/168/183/196/302/314/342:
    mock.patch("agent.prompt_processor_loader._get_builtin_dir", return_value=tmp_processors)
```

→ 被测的路径解析逻辑本身从未被执行。这是典型的「mock 掉了唯一会出错的那行」。

### 1.4 后果分级（静态判定每个调用点）

`agent/system_prompt.py` 共 13 个 `_get_processor()` 调用点：

**A. 有 `or CONSTANT` 兜底 → 行为不变（5 个）**

| 行号 | processor | 兜底常量 |
|---|---|---|
| 201 | identity | `DEFAULT_AGENT_IDENTITY` |
| 264 | computer_use | `COMPUTER_USE_GUIDANCE` |
| 296 | tool_use_enforcement | `TOOL_USE_ENFORCEMENT_GUIDANCE` |
| 302 | google_model | `GOOGLE_MODEL_OPERATIONAL_GUIDANCE` |
| 310 | openai_model | `OPENAI_MODEL_EXECUTION_GUIDANCE` |

**B. 无兜底，`if _proc: append` → 内容静默从系统提示词中消失（8 个）**

| 行号 | processor | 丢失影响 |
|---|---|---|
| 213 | help_guidance | 自助能力说明消失 |
| 218 | task_completion | **任务完成纪律消失** |
| 225 | memory_guidance | **记忆读写纪律消失（north star 核心）** |
| 229 | session_search | 历史检索纪律消失 |
| 233 | skills_guidance | **技能积累/纠错纪律消失（north star 核心）** |
| 237 | image_generate | 图像生成约束消失 |
| 241 | academic_search | 学术检索约束消失 |
| 253 | kanban | 看板 worker 生命周期消失（rare 路径） |

即：这次提交在「外置」的名义下，**实质删除了 8 段行为指令**，其中 3 段直接命中产品 north star（记忆纪律、技能积累、任务完成）。

### 1.5 连带击穿：`replaceable=False` 保护失效

`load_all_processors()` 的保护逻辑依赖 `by_name` 里**先有内置项**：

```python
existing = by_name.get(proc.name)
if existing and not existing.replaceable:   # existing 恒为 None
    continue                                 # 永不触发
```

内置加载 0 个 → `existing` 恒为 `None` → 用户/AEGIS 写 `~/.vermes/processors/identity.yaml` 会被**无条件采纳**。GLM5.2 设计的三级保护（`replaceable:false` / `L1` / `L2`）当前防护力为 **0**。

### 1.6 修复（最小 diff）

```diff
 def _get_builtin_dir() -> Path:
-    return Path(__file__).resolve().parent / "vermes_cli" / "processors"
+    # agent/prompt_processor_loader.py -> repo root (or _internal/) -> vermes_cli/processors
+    return Path(__file__).resolve().parent.parent / "vermes_cli" / "processors"
```

**必须同时加一个不 mock 的真实回归测试**（否则同类错误会再犯）：

```python
def test_builtin_processors_actually_load_from_real_bundle():
    """No mocking: the real built-in dir must resolve and be non-empty."""
    invalidate_cache()
    d = _get_builtin_dir()
    assert d.exists(), f"built-in processor dir missing: {d}"
    procs = load_all_processors()
    assert len(procs) >= 30, f"expected >=30 built-in processors, got {len(procs)}"
    assert any(p.name == "memory_guidance" for p in procs)
```

**并且**：PyInstaller 侧需确认 `vermes_cli/processors/*.yaml` 进 `datas`（`vermes-backend.spec` 的 `vermes_cli` 若是整目录拷贝则自动包含，需实测冻结包内 `_internal/vermes_cli/processors/` 存在）。

### 1.7 无 fallback 的 8 点：策略选择

修好路径后 B 组会恢复，但**架构上仍不安全**——任何一次 YAML 解析失败/文件误删都会静默丢指令。定稿要求：

> **所有 processor 注入点必须走「processor 优先，常量兜底」的统一形态**，禁止裸 `if _proc:`。

建议引入统一取值函数，把兜底表内建：

```python
def _proc_or_default(name: str) -> str:
    return _get_processor(name) or _PROCESSOR_FALLBACK.get(name, "")
```

`_PROCESSOR_FALLBACK` 已在 `system_prompt.py:57` 存在，当前只覆盖 6 项 → 补齐到 13 项，调用点全部改用该函数。

---

## 2. 治理归类错配 — processor.yaml 会被 YOLO 直接放行

### 2.1 现状取证

`tools/approval.py:612` / `:615-642` / `:645-671`

```python
_CONFIG_LEVEL_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".ini", ".env"}

def is_config_level_target(target_path):
    ...
    return os.path.splitext(tp)[1].lower() in _CONFIG_LEVEL_SUFFIXES

def classify_component_swap(target_path):
    if is_module_hot_path(target_path): return "module_hot_path"
    if is_config_level_target(target_path): return "config_level"
    return "source_level"
```

`~/.vermes/processors/xxx.yaml`：不在 modules 路径 → 后缀 `.yaml` → 判 **`config_level`** → L1 自动应用、YOLO 放行、无弹窗。

### 2.2 为什么这是错的

`config_level` 的豁免理由写在 docstring 里：*"a config dial is a single quantified value ... offline-measurable blast radius"*。

但 processor 的 `content` **就是系统提示词正文**——改它 = 改 agent 的行为宪法。爆炸半径远大于一个标量旋钮，却拿到了最低审批档。这正是审批分层阶段修掉的那类反例（危险的没拦住）的**重新出现**，只是换了个文件后缀。

### 2.3 定稿要求：加第三类判据，且不靠后缀

```python
def classify_component_swap(target_path: str) -> str:
    if not target_path:
        return "source_level"
    if is_module_hot_path(target_path):
        return "module_hot_path"
    if is_processor_hot_path(target_path):      # NEW，必须在 config_level 之前
        return "processor_hot_path"
    if is_config_level_target(target_path):
        return "config_level"
    return "source_level"
```

`processor_hot_path` 的档位**不由路径决定，由 manifest 决定**：读目标 YAML 的 `governance.risk_tier`（读不到 → fail-closed 取 `L2`）。这样：

- 调 `priority` / `enabled` 之类 → 作者在 manifest 里声明 `L0/L1`
- 改 `identity` / 安全类 content → 声明 `L2`，必弹窗
- manifest 损坏或缺字段 → `L2`（与 `_source_modify_always_confirm` 的 fail-safe 一致）

> 这条对应此前记录的「乐高约束②」：**路径后缀判据在乐高化后必然失效，必须改用声明式爆炸半径判据。** 现在它已经从预测变成实测事实。

---

## 3. Processor Schema v1（定稿）

```yaml
api: vermes.processor/v1        # 版本化契约；未知次要字段宽松忽略，未知 api 主版本拒绝加载
kind: prompt_fragment           # prompt_fragment | injection | strategy_dial
                                # behavior_rule / lifecycle_hook = RESERVED（Phase 2 启用）
id: memory_guidance             # 全局唯一稳定 ID —— override / hash / 追踪的唯一键
name: 记忆读写纪律               # 仅展示用，可随意改，不参与任何匹配
version: 1.0.0
enabled: true
priority: 100                   # 同 layer 内升序拼接
layer: stable                   # stable | context | volatile —— 保 prefix-cache 命中率

model_affinity:                 # 对应 TOOL_USE_ENFORCEMENT_MODELS / EXCLUDED_MODELS
  operator: any_of              # any_of | all_of | none_of
  match: []                     # 空 = 不限

conditions:                     # 对应现有 if "memory" in agent.valid_tool_names
  require_tools: ["memory"]
  require_capabilities: []
  platform: ["*"]
  config_flag:                  # 对应 _task_completion_guidance 之类开关
    key: ""
    default: true

content: |
  ...正文...

render:
  engine: none                  # none(默认) | mustache —— 默认 none 是刻意的，见 §4
  on_missing: keep              # keep(默认,原样保留 {{var}}) | empty | error
  inputs:
    memory_budget: context.memory_budget

governance:
  risk_tier: L1                 # L0 | L1 | L2 —— classify 的唯一依据，缺失/损坏 → L2
  replaceable: true             # false = 用户与 AEGIS 均不可覆盖（如 identity）
  mutable_by_aegis: true
  rollback: enabled
  critic_guarded: false         # strategy_dial 类置 true
  hash: auto                    # 见 §5 计算规范

lifecycle:
  hooks: []                     # 取值必须 ∈ VALID_HOOKS（实测 17 个，见 §6）

metadata:
  author: vermes-core
  source: builtin               # builtin | user | aegis | module
```

### 3.1 与 `f26352f82` 现有 7 字段的映射（升级路径）

| 现有字段 | 定稿去向 |
|---|---|
| `name` | 拆成 `id`（匹配键）+ `name`（展示名）。**兼容期**：缺 `id` 时回落用 `name` 作 `id` |
| `content` | 保留 |
| `order` | 更名 `priority`（避免与 YAML 序歧义），兼容期两名皆读 |
| `triggers.{type,...}` | 展开为 `conditions` + `model_affinity`；`type: always` → 空 conditions |
| `replaceable` | 移入 `governance.replaceable`（顶层保留读，兼容期） |
| `version` / `description` | 保留（`description` 归 `metadata`，兼容期两处皆读） |

**兼容策略**：v1 加载器对 v0 文件宽松解析（缺字段取默认），不强制一次性重写 32 个内置文件；但新写/AEGIS 生成的一律用 v1 全字段。

---

## 4. 拍板点逐条裁决

### ① kind 5 类 — 采纳，但收窄 Phase 1 实装范围

GLM5.2 的核实正确：`behavior_rule` 当前零对应物。追加一条：**`lifecycle_hook` 作为独立 kind 与 `lifecycle.hooks` 字段语义重叠**（任何 kind 都能挂钩子）。

定稿：
- Phase 1 实装 3 类：`prompt_fragment` / `injection` / `strategy_dial`
- `behavior_rule` / `lifecycle_hook` 写进枚举但标 **RESERVED**，加载器遇到时警告并跳过（不报错，为 Phase 2 留门）

### ② 目录结构 — 采纳混合模式，但 override 键必须与路径解耦

内置平铺 `vermes_cli/processors/*.yaml`（PyInstaller 友好）+ 用户子目录 `~/.vermes/processors/<id>/processor.yaml`（支持 companion 文件）。

**但必须同时定死两条，否则这是返工高发点：**

1. **override 匹配键 = manifest 的 `id` 字段，与文件名/目录名完全无关。** 路径只用于发现。
   现状 `name = data.get("name", path.stem)` 把「文件名」「展示名」「匹配键」三者混为一谈 → 混合目录下必然出现「用户以为覆盖了，其实新建了一个」。
2. **watcher 必须同步改扫描模式。** 当前 `user_dir.glob("*.yaml")` 只扫平铺；改子目录后 watcher **立刻失效**（这是 Phase 0 刚花力气建起来的能力，别在 Phase 1 悄悄弄丢）。应改为 `user_dir.glob("*/processor.yaml")` + 兼容 `user_dir.glob("*.yaml")`。

**附带修一个现有 watcher bug**：`_first_scan` 标志位在 `user_dir` 不存在时不会翻转，导致「用户首次创建 processors 目录并放入文件」那一次被当成首扫吞掉，**首个用户 processor 需要改第二次才生效**。修法：把 `_first_scan[0] = False` 移出 `if user_dir.exists()` 分支。

### ③ 内置可覆盖 + 三级保护 — 采纳

`replaceable:false`（identity 等）/ `L1` 自动 / `L2` 人工，三级同意。补两点：
- 保护的**执行点**在 loader（现在位置正确），但**前提是 §1.6 的路径 bug 先修好**，否则保护形同虚设
- `replaceable` 归入 `governance` 段，避免顶层字段散落

### ④ 落盘 — 已落盘（本文件）

与 GLM5.2 已写的 `docs/phase1-design-spec.md` 关系：那是**实现稿**（v0 简化版），本文件是**定稿 schema + 评审**（v1）。升级完成后建议把 `docs/phase1-design-spec.md` 更新为 v1 或标注 superseded，避免两份规格并存。

---

## 5. mustache 决策 — 采纳手写不引依赖，但必须补 3 条规范

「冻结包不加新依赖」是铁律，手写 `{{var}}` 替换（~50 行）正确。但以下三条**必须现在写进 schema**，否则 Phase 2 补齐 mustache 规范时会变成破坏性变更：

1. **`render.engine` 默认 `none`。**
   现有 32 个 content 里含 JSON 示例、代码片段、可能出现 `{` / `{{`。全局开渲染会静默破坏正文。**只有显式声明 `mustache` 才渲染**，且内置文件全部保持 `none`，逐个迁移时才切换。

2. **`on_missing` 必须显式定义，默认 `keep`。**
   变量缺失时是保留 `{{var}}` 原样、替空串、还是抛错？不定死，以后改默认值就是全局行为变更。选 `keep` 的理由：prompt 场景下留下可见占位比静默变空串更容易发现问题。

3. **不实现 `{{{raw}}}`，`{{var}}` 一律不做 HTML 转义。**
   prompt 不是 HTML，转义只会引入 `&amp;` 污染。明确写进 spec，防止将来「按 mustache 规范补齐」时反而改坏。

Phase 2 若需条件渲染（`{{#if}}`/`{{#each}}`），届时再评估引 `chevron`，并以 `render.engine: mustache-full` 作为新枚举值区分，不动 v1 语义。

---

## 6. 补充定死项（GLM5.2 与初版 schema 均未覆盖，属返工高发）

### 6.1 `hash` 计算规范

Phase 2 变体隔离、AEGIS 提案追踪、Critic 幅度比较全靠它。必须定死：

> `hash = sha256( canonical(manifest 去掉 governance.hash 后的全部字段) )`
>
> canonical 规则：key 递归字典序排序 / 值按 YAML safe_dump(default_flow_style=False, allow_unicode=True) / 统一 LF / 每行 rstrip / 文件尾单个 `\n`。

不定死 canonical 化，两台机器算出的 hash 就会不同，变体隔离直接失效。

### 6.2 拼接顺序的确定性（保 prefix-cache）

- 一级：`layer`（stable → context → volatile，固定序）
- 二级：`priority` 升序
- 三级：**`id` 字典序**（tie-break）

第三级必须有。否则同 priority 的多个 processor 顺序取决于 dict/glob 遍历顺序，跨进程可能变化 → 系统提示词前缀抖动 → **prefix cache 全量失效**，直接体现为 token 成本上升。

### 6.3 `api` 版本的前向兼容策略

- **未知次要字段**：忽略 + debug 日志（不报错）→ 老加载器能读新文件
- **未知 `api` 主版本**（如 `vermes.processor/v2`）：拒绝加载 + warning → 不猜语义
- 加载失败的单个文件不得影响其他 processor（当前 `_parse_yaml` 返回 `None` 的行为正确，保留）

### 6.4 VALID_HOOKS 实测口径

`vermes_cli/plugins.py:128-168` 实测 **17 个**（GLM5.2 报的 17 正确；此前记录的 15 已过时——后续新增了 `pre_approval_request` / `post_approval_response`）：

```
pre_tool_call, post_tool_call, transform_terminal_output, transform_tool_result,
transform_llm_output, pre_llm_call, post_llm_call, pre_api_request, post_api_request,
on_session_start, on_session_end, on_session_finalize, on_session_reset, subagent_stop,
pre_gateway_dispatch, pre_approval_request, post_approval_response
```

`lifecycle.hooks` 的取值加载时必须对这 17 个做校验，非法值警告并丢弃。

---

## 7. 升级执行清单（按依赖排序）

| # | 动作 | 阻断性 |
|---|---|---|
| 1 | 修 `_get_builtin_dir()` 的 `parent` → `parent.parent` | **P0** |
| 2 | 加不 mock 的真实加载回归测试（≥30 个 + 断言 memory_guidance 在内） | **P0** |
| 3 | 8 个无 fallback 调用点统一改 `_proc_or_default()`，`_PROCESSOR_FALLBACK` 补齐 13 项 | **P0** |
| 4 | 验证 `vermes-backend.spec` 把 `vermes_cli/processors/*.yaml` 打进冻结包 | **P0** |
| 5 | 扩展 dataclass 到 v1 全字段（id/kind/layer/model_affinity/conditions/render/governance/lifecycle/metadata），v0 宽松兼容 | P1 |
| 6 | override 键改为 `id`，与路径解耦 | P1 |
| 7 | watcher 扫描改 `*/processor.yaml` + 兼容平铺；修 `_first_scan` 首创目录吞事件 bug | P1 |
| 8 | 加 `is_processor_hot_path()` + `classify_component_swap` 第三类，档位读 `governance.risk_tier`，fail-closed L2 | P1 |
| 9 | 手写 mustache（默认 engine=none / on_missing=keep / 不转义 / 不支持 raw） | P2 |
| 10 | `hash` canonical 计算 + 三级确定性排序 | P2 |
| 11 | 32 个内置文件补 v1 字段（可分批，兼容期不阻断） | P3 |

1–4 必须在 Phase 1 打包进 DMG 之前完成，否则会把「删掉 8 段行为指令」的版本发到运行态。

---

## 8. 复用的既有结论（勿重造）

- 治理三件套已就位：三档记忆式批准（`tools/approval.py`）/ `.bak` + `rollback_change(initiator='user')` / Critic + 确定性闸门 + T4 幅度护栏
- Phase 0 watcher 范式（dependency-free 轮询 + debounce + 显式 reload 去重）直接复用，不引 watchdog
- 反模式警戒：**「真正生效的键没文档，文档化的键不生效」** —— 本次新增的每个配置键，落地后必须 grep 读取方确认，不能只看默认段

---

## 9. 落地状态（`449cb64fb`，2026-08-04 收口）

清单 1–9 由 `7cfdf1ef3` 完成并经源码级复核属实；**第 10 项（hash canonical + 三级排序）与一项新发现的治理漏洞**由 `449cb64fb` 补齐。

| # | 项 | 状态 | 落点 |
|---|---|---|---|
| 1–4 | P0 路径 / 真实回归测试 / fallback 13 项 / spec datas | ✅ `7cfdf1ef3` | 真跑验证 COUNT=32 |
| 5–8 | v1 全字段 / id 解耦 / watcher 子目录 / 第四类判据 | ✅ `7cfdf1ef3` | — |
| 9 | 手写 mustache | ✅ `7cfdf1ef3` | 三条规范写死 |
| 10a | 三级确定性排序 layer→priority→id | ✅ `449cb64fb` | `_LAYER_ORDER` / `layer_rank` |
| 10b | `hash` canonical 计算 | ✅ `449cb64fb` | `compute_manifest_hash()` |
| **C** | **processor 档位不可自证**（新增） | ✅ `449cb64fb` | `_resolve_processor_tier()` |
| 11 | 32 个内置补 v1 字段 | ⏳ 未做（兼容期不阻断） | 见下方警告 |

### 9.1 治理修正：档位不可自证（C）

原实现从 target 文件自身读 `governance.risk_tier` —— 而该文件正是被治理对象。**另有一处 `Path` 未导入，NameError 被 `except Exception: pass` 吞掉，导致档位读取从未真正运行、实际恒 L2。** 即：洞是潜伏的而非已触发，但任何人补上 `from pathlib import Path` 的那一刻就会打开。

定稿规则（`_resolve_processor_tier`）：

1. **取 (磁盘旧档, 待写新档) 中更严格者。** 放宽档位需付出一次「当前档位」的审批；收紧永远免费。
2. **文件不存在（新建）恒 L2。** 不存在「生而受信」。
3. **L0 不适用于 processor，一律钳到 L1。** content 即行为宪法，最无害的改动也须留下可撤回账本记录。L1 本就不弹窗 → 弹窗预算零增加。
4. **L2 逃生门用独立键** `approvals.processor_modify_always_confirm`（默认 `True`），不复用 `source_modify_always_confirm`（后者文档语义是 `.py` 源码改写）。

信任建立路径因此是唯一的：首次写入走 L2 人工确认 → 之后该 processor 的同档改写才是 L1 静默+可撤回。

### 9.2 遗留警告

- **32 个内置 processor 仍无 `governance:` 段** → 走 dataclass 默认 L2。当前无害（内置在冻结包内，判为 `source_level`），但 **Phase 2 若把内置搬进热路径，会一次性全变 L2 全弹窗**。迁移时必须同批补 governance 段。
- **Phase 1 尚未进 DMG**（2.3.7 打的是 Phase 0）。以上全部改动需一次 `build.sh` 重建才进运行态。
