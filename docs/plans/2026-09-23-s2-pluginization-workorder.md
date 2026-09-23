# S2 自进化插件化 · 实施工单（纸面）

> 定位：先纸面过关再迁码（roadmap §8.7 锁定顺序第 2 项）。本文所有结论均带代码锚点，
> 未经核实的一律标注「待核实」。
> 工单快照：`main` @ `b8e1950510`（写稿时 HEAD 已前进，见 §9）。

---

## 0. 结论先行（三条）

1. **roadmap 里「S2 = 引入上游三个钩子」的前提不成立。** Vermes 已有其中两个
   （`pre_llm_call` / `post_tool_call` 均已投用），第三个的**等价物早就存在且能力更强**
   （YAML processor 体系）。真缺口只有一条注册 API。
2. **S2 的正确形态是「加一条形态兼容的注册 API + 迁剩余静态块」，不是「移植上游实现」。**
   照搬上游实现会绕过 Vermes 现成的 prompt-cache 防护（§2），属于典型的
   「接口不变、行为变了」—— 正是 §8.2 指标 5 要抓的那类退化。
3. **验收「注入文本逐字等价」不能只对裸文本比对**，必须绑定场景矩阵（§4）。
   否则 `should_inject()` 的条件组合会制造大量假阳性。

---

## 1. 家底实测（全部带行号）

| 能力 | 上游真源 | Vermes 现状 | 缺口 |
|---|---|---|---|
| `pre_llm_call` | `agent/turn_context.py:725` `_collect_pre_llm_call_context` | `agent/conversation_loop.py:1108-1142` 已 invoke，带完整注释 | **无** |
| `post_tool_call` | `agent/agent_runtime_helpers.py:2393` `emit_terminal_post_tool_call` | `vermes_cli/plugins.py:130` `VALID_HOOKS` 已含；`agent/tool_processor_loader.py:33` 已在 `invoke_hook("post_tool_call", …)` | **无** |
| `register_system_prompt_section` | `hermes_cli/plugins.py:940`（签名见下） | `PluginContext` 无此方法（`plugins.py:332-700` 逐个方法核对） | **唯一真缺口** |
| 静态块机制 | 上游无同名概念 | `agent/prompt_processor_loader.py`（v0/v1 schema）+ `vermes_cli/processors/` **36 个 YAML** | **反向领先** |

**静态块迁移度实测**：`agent/system_prompt.py:59-73` `_PROCESSOR_FALLBACK` 共 15 个键，
其中 **14 个已有同名 YAML**（identity / help_guidance / task_completion / memory_guidance /
session_search / skills_guidance / image_generate / academic_search / scholarforge_workflow /
kanban / computer_use / tool_use_enforcement / google_model / openai_model），
**仅 `editing_guardrails` 仍只有硬编码常量**。

→ 换句话说：**S2 的"迁静态块"已经完成约 93%（14/15）**，剩下的是一个键 + 一条注册 API。
这与「1–2 周」的排期不符，建议按 §5 重新估期。

### 上游注册 API 真实签名（`hermes_cli/plugins.py:940-964`）

```python
def register_system_prompt_section(
    self, id: str, content: Union[str, Callable[[Mapping[str, Any]], str]], *,
    position: str = "after_memory", max_chars: int = DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS,
) -> PluginRegistration:
    """Register bounded context frozen into each new session prompt. Callables receive a
    read-only session-info mapping; the rendered prompt is persisted by core verbatim."""
```

要点（**不同于"想当然"**）：
- 它是 **session 级快照**：会话开始时渲染一次，之后「persisted by core verbatim」——
  上游用「渲染一次」保 cache；Vermes 用「三层 + 会话内绝不重渲染」保 cache
  （`agent/system_prompt.py:200-204` 注释原文）。
  **同一个目标，两套机制**，都成立，但混用要小心。
- `position` 是枚举（如 `after_memory`），Vermes 用 `layer`(stable/context/volatile) + `priority` + `id`
  三级确定性排序，后者额外保证了**跨进程/跨机器字节一致**（`prompt_processor_loader.py:534-541`）。
- 它带 **`max_chars` 上限**；Vermes 的 `PromptProcessor` **没有单片段长度上限**。
- 它校验 id 格式 + 拒绝重复注册；Vermes 的 user processor 覆盖 builtin 是**允许**的（同名可覆盖）。

---

## 2. 为什么不能照搬上游实现（核心冲突）

两处现成的代码注释已经把风险写死了：

- `agent/conversation_loop.py:1113-1117`：
  > Context is ALWAYS injected into the user message, never the system prompt…
  > The system prompt is Vermes's territory.
- `agent/prompt_processor_loader.py:39-49`（`_LAYER_ORDER`）：
  > If a volatile fragment could sort ahead of a stable one… every turn would emit a
  > different prefix and the provider prompt cache would miss on the WHOLE prompt —
  > a direct token cost, not a cosmetic issue.

**推演**：上游 API 的 `content` 可以是 Python `Callable`。一旦某个插件回调返回随轮次变化的文本
（例如"当前未完成任务数"、"记忆召回 top-k"），而它被按默认塞进 **stable 层**，则
每一轮的 system prompt 前缀都不同 → provider prompt cache **全量 miss** → token 成本与
首 token 延迟上升。而它没有报错、没有日志、接口签名也不变 ——
这是「制度全绿但用户体感变差」的标准形态，也正是 §8.2 指标 5 存在的理由。

现有的 YAML processor 体系靠 `layer` 字段 + 三级排序已经把这个风险封住了；
**照搬上游实现等于把这层防护拆掉。**

---

## 3. 建议形态：API 同形，实现不同（adapter）

给插件作者看的接口与上游一致（便于未来搬上游插件代码），内部落到既有 processor 注册表：

```python
def register_system_prompt_section(
    self, id: str, content: Union[str, Callable[[Mapping[str, Any]], str]], *,
    layer: str = "stable",          # 显式声明，不藏在默认值里
    position: str | None = None,    # 兼容上游取值；映射到 layer+priority
    max_chars: int = DEFAULT_MAX,   # ← 上游有价值，建议照拿（见 §8 L-014 候选）
    conditions: dict | None = None, # 复用现有 v1 conditions（require_tools/platform/config_flag/model_affinity）
) -> PluginRegistration
```

约束（写进工单，不是建议）：
1. **render 型（Callable）默认必须落到 `volatile` 层**；要放 stable 需在登记时注明理由
   （可接受理由：内容只依赖 session 级常量）。
2. 走现有的 canonical hash（`prompt_processor_loader.py:286` `compute_manifest_hash`）
   → 每个注入片段可哈希、可比对、可 A/B。
3. id 校验与重复注册策略：**建议照搬上游**（见 §8 L-014 候选）。

收益/成本：

| 项 | 照搬上游实现 | adapter 形态（建议） |
|---|---|---|
| 未来搬上游插件代码 | 完全一致 | 基本一致（同签名） |
| prompt-cache 防护 | **拆掉**（高风险） | 保留并加强 |
| 用户热路 + AEGIS 可改 | **丢失**（改 YAML 热更新的能力没了） | 保留 |
| 片段可哈希/可 A/B | 无 | 保留 |
| 实施成本 | ~1 周 + 高风险回归 | ~1–2 天 + 试点 |

---

## 4. 逐字等价验收（本工单的核心增量）

### 4.1 为什么不能只比对裸文本

`PromptProcessor.should_inject(agent)`（`prompt_processor_loader.py:123-199`）依赖：
`require_tools` / `platform` / `config_flag` / `model_affinity`。
**同一句用户输入，在不同 toolset/model/platform 组合下注入结果本来就不同。**
不做固定就比对，会得到一堆「迁移前后不一致」的假阳性，最后被迫放宽标准。

### 4.2 场景矩阵（绑定 A/B 语料的固定维度）

| 维度 | 取值 | 数量 |
|---|---|---|
| model | qwen-max / gpt-4o / claude-sonnet / local-qwen | 4 |
| platform | cli / gateway / feishu / telegram | 4 |
| toolset | minimal / standard / full | 3 |

全组合 48 → 采用 **pairwise（两两覆盖）** 折到 **约 12–16 个场景**即可覆盖绝大多数注入分支。
`reports/ab-corpus/v1/corpus.yaml` 的 `fixed_context` 用**同一套维度**，两个验收体系对齐。

### 4.3 快照 harness 规格（**待实现**，非现有能力）

- 入口：`AIAgent._build_system_prompt()` → `build_system_prompt_parts(agent)`
  （`agent/system_prompt.py:188` 装配 / `:615` 拼接），返回 `stable/context/volatile` 三段字典。
- 现成 fixture：`tests/run_agent/test_run_agent.py:948` 的 `agent` / `agent_with_memory_tool`
  + conftest —— **从这里起手，不要另写 agent stub**。
- 产物：`reports/s2/<ver>/<scenario-id>.{stable,context,volatile}.txt`
  + `manifest.json`（每段 sha256 + 场景中记录）。
- 判定：
  - **stable 段必须逐字节相同**（这是硬门槛）；
  - context 段按场景等；
  - volatile 段允许时间戳等差异 → 用**显式白名单正则**，不许含糊地"忽略差异"。
- gold baseline：**必须在动第一行代码之前**用 `v2.5.2` 生成并入库 `reports/s2/gold/`；
  回归命令 `git diff --exit-code reports/s2/gold/`。

### 4.4 cache 前缀哨兵（回归必含）

- 同一会话连续两轮，stable 段 sha256 必须恒定 → 语料 `A08`/`B07` 已定义为哨兵用例。
- 这条比"逐字等价"更硬：**它直接把 §2 的 cache 污染风险变成可判定项**。

---

## 5. walking skeleton 步骤（每步一道门）

| 步 | 内容 | 通过门 |
|---|---|---|
| **S2.0** | **生成 gold 快照**（v2.5.2，任何改动之前） | `reports/s2/gold/` 入库，manifest 完整 |
| S2.1 | 新增 `register_system_prompt_section` API（**不改现有注入点**） | 所有场景三段文本与 gold **逐字相同**（行为零变化） |
| S2.2 | 迁 **1 个**静态块试点（推荐 `identity` 或 `help_guidance`：always 注入、无条件依赖） | 同上 + stable 层 `content_hash` 有canonical 值且可解释 |
| S2.3 | 迁剩余 14 键；`editing_guardrails` 补 YAML | 每个键单独一次比对 |
| S2.4 | 最后才删 `_PROCESSOR_FALLBACK` 硬编码回退 | 全场景绿 + cache 哨兵绿 |

任一步不过 → 停，登记 DIVERSION，**不放宽标准**（roadmap §4 否决条款）。

---

## 6. 风险与回退

| 风险 | 触发 | 判据 | 回退 |
|---|---|---|---|
| cache 前缀污染 | Callable 内容随轮变化落在 stable | 同会话两轮 stable sha256 不一致 | 该片段降级到 volatile 层 |
| 同名冲突 | 插件注册 id 撞上 builtin/user processor | 重复注册报错 vs 允许覆盖（**待拍板**） | 明确优先级：建议 plugin < builtin < user hot path |
| 片段膨胀 | 无 `max_chars` 上限（现状缺失） | 单片段 > 阈值 | 采纳上游上限（L-014 候选） |
| 迁移中途要回滚 | 任一步不过 | — | **现状无可回滚开关**；若需要，须额外实现 `VERMES_PROMPT_PROCESSORS_LEGACY=1` 走 `_PROCESSOR_FALLBACK`（**目前不存在，勿当作已有能力**） |

---

## 7. A/B 语料（已冻结）

- `reports/ab-corpus/v1/corpus.yaml`：**32 条**（中文长会话 8 / 跨会话记忆 8 / 工具调用链 8 / 渠道收发 8），
  每条带 `fixed_context` + ≥2 条可执行断言。
- `reports/ab-corpus/v1/corpus.sha256`：**`bb0a4369989dbebe…`**（全文 sha256 前 16 位）。
- 冻结校验：`tests/tools/test_ab_corpus.py` **5 passed**（校验和不一致即红，提示升版本目录）。
- 自我纠错记录：首次生成时有 4 条用例只有 1 条断言（A06/B04/B06/D04），
  **是被这条校验测当场抓出来的**，已补齐并重算校验和。

---

## 8. 顺手可取的三个便宜项（建议登记为 L-014 候选）

不迁机制，只取上游已经证明有用的护栏：

1. **`max_chars` 上限**：`PromptProcessor` 目前无单片段长度上限，插件可撑爆 prompt。
2. **id 格式校验**（`is_valid_system_prompt_section_id`）：1–128 位小写 + `.`/`_`/`-`。
3. **重复注册拒绝**（上游 `ValueError` 明确报出「已被某插件注册」），比静默覆盖更易排障。

---

## 9. 待拍板

| # | 事项 | 建议 |
|---|---|---|
| P1 | 是否接受 **adapter 形态**（而非照搬上游实现） | 建议接受，理由见 §2 |
| P2 | plugin / builtin / user hot path 的同名优先级 | 建议 plugin < builtin < user |
| P3 | 是否实现回退开关 `VERMES_PROMPT_PROCESSORS_LEGACY` | **合成定案（2026-09-23）**：不做新旧双轨；改 `VERMES_DISABLE_PROMPT_SECTIONS=id1,id2`（`register_plugin_processor` 前置过滤）。时机= S2.2 首次动注入点；退役=`v3.0.0-distribution` 前。**Hermes 补点（必须一并做）**：① 可发现性——`list_prompt_sections()` 列出 id/layer/source/path（已落 `prompt_processor_loader.py`）；② 可见性——被禁用的段启动/诊断时**显式打印**「已按 env 禁用 N 段：id1,id2」，禁止静默失效 |
| P4 | S2 排期是否按 93% 已完成的事实重估 | 建议重估为真缺口部分：**1 条 API + 1 个残留键 + gold 基线** |

## 9b. S2.2 执行顺序（2026-09-23 定）

1. **`identity`** walking skeleton（always / 无条件）  
2. **`editing_guardrails`**（`_PROCESSOR_FALLBACK` 唯一残留硬编码；迁完可整条退役该常量）  
3. 其余键逐个迁，每键一次 gold 比对  

### 9b.1 `computer_use` 哨兵坑（Hermes 点名，退役 `_PROCESSOR_FALLBACK` 前必读）

`system_prompt.py:71` `"computer_use": None,  # imported lazily below` **不是普通常量**：

```python
# :89-95 — fallback 为 None 时走惰性导入分支，而不是「无兜底」
fallback = _PROCESSOR_FALLBACK.get(name)
if fallback is not None:
    return fallback
if name == "computer_use":
    from agent.prompt_builder import COMPUTER_USE_GUIDANCE
    return COMPUTER_USE_GUIDANCE
```

**简单删键会丢兜底语义**（YAML 缺失时 guidance 静默消失）。退役该常量时必须二选一：
① 把 `COMPUTER_USE_GUIDANCE` 改成模块级可延迟绑定的可调用/属性；或
② 在 `_proc_or_default` 保留显式 `computer_use` 惰性分支（并加契约测：删 YAML 后仍有兜底）。

## 9c. gold 覆盖口径（防误读）

- S01–S16 = pairwise 主矩阵（对齐 A/B 语料 fixed_context 四模型）；**S17** = model 维探针（`gemini-2.5-pro` 命中 `google_model`，stable 必须区别于 qwen/claude/local-qwen）。  
- `qwen-max`/`claude-sonnet`/`local-qwen` 不命中任何 `model_affinity` 时 stable 全同 —— 唯一 stable 指纹以 manifest 为准，**不得**写成「N 条独立护栏」。  
- **S11** 带 `system_message`，是唯一的 **context 非空** 探针；迁块进 context 层必须以它为护栏，缺则先升 gold。

> 口径纪律（roadmap §8.1）：引用本文数字请带命令与 HEAD hash；HEAD 在写稿期间仍在前进。
