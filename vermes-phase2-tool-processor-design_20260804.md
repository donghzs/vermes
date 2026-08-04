# Vermes 乐高式自进化 · Phase 2 设计：工具 Processor 外置

> 范围：乐高式自进化 Phase 2（把 **工具/Tool** 也外置为声明式 Processor 积木，与 Phase 1 的 prompt/行为层共享同一声明-治理-生命周期底座）
> 依赖：Phase 1 已落地（`449cb64fb` + `767eb2d60`，运行态 DMG 2.3.7 @ 08-04）
> 关联文档：`vermes-phase1-schema-design_20260803.md`（v1 schema 定稿）

---

## 0. 为什么做 Phase 2（与 Phase 1 的同构性）

Phase 1 把 **prompt/行为** 外置成 YAML Processor，解决了"行为宪法散落在 13 个 `_get_processor()` 调用点、硬编码、不可被用户/AEGIS 覆盖"的问题。

当前**工具**存在完全同构的痛点：

| 维度 | prompt（Phase 1 已解） | 工具（Phase 2 待解） |
|---|---|---|
| 声明位置 | 曾硬编码在 `system_prompt.py` | 硬编码在 `tools/*.py` 的 `registry.register(...)` |
| 治理 | 无 → 现 `governance.risk_tier` | 无（工具定义在代码里，改工具=改代码=走 source 审批，粗粒度） |
| 用户可定制 | `~/.vermes/processors/<id>/processor.yaml` override | **只能 fork 整个 Python 工具文件** |
| 生命周期 | `lifecycle.hooks`（pre/post 等） | 工具自身的 pre/post/transform 钩子散落在各工具实现里 |
| AEGIS 提案 | 可生成 processor.yaml | 无对应物 |

**核心目标**：让"工具"也成为一等公民积木——声明、治理、可用性、生命周期都用与 prompt 相同的 YAML 载体，让工具像 prompt 一样可被用户/AEGIS 声明式地增、改、治、钩，而不必改 Python。

---

## 1. 现状事实（设计依据，非假设）

### 1.1 工具注册模型（`tools/registry.py`）
- 每个工具文件在**模块级**调用 `registry.register(name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False, description="", emoji="", max_result_size_chars=None, dynamic_schema_overrides=None, override=False)`。
- `schema` = **OpenAI 格式函数调用 schema**（`name`/`description`/`parameters`），即暴露给 LLM 的体。`memory_tool.py:701-764` 是真实样本。
- `check_fn` = 可用性闭包（如 `check_memory_requirements`）；`get_definitions()` 只在 `check_fn()` 返回 True 时把工具放进 LLM 工具清单，结果约 30s TTL 缓存。
- `register()` 有防遮蔽逻辑：跨 toolset 同名会被拒，除非 `override=True`（插件显式替换）。

### 1.2 启动注入点（精确集成位）
- `model_tools.py:180` → `discover_builtin_tools()`（导入各工具模块触发 self-register）。
- `agent/agent_init.py:920-922` → `agent.valid_tool_names = {tool["function"]["name"] for tool in agent.tools}`，`agent.tools` 来自 registry `get_definitions`。
- **结论**：只要 tool processor 在 `discover_builtin_tools()` 之后注册进同一个 `ToolRegistry`，它就自动流入 `valid_tool_names`，下游 `conversation_loop` / `prompt_builder` / `tool_executor` 零改动。这是 Phase 2 的最小侵入接入点。

### 1.3 可用性与条件（v1 已定义）
Processor 的 `conditions` 块（`require_tools` / `require_capabilities` / `platform` / `config_flag`）已存在；工具还需 `requires_env`（env 变量齐备才可用）。这些可直接编译成 `check_fn` 等价谓词。

### 1.4 kind 分发（`agent/prompt_processor_loader.py:390-393`）
```python
kind = data.get("kind", "prompt_fragment")
if kind in ("behavior_rule", "lifecycle_hook"):   # RESERVED，警告跳过
    logger.info(...)
    return None
```
加 `tool` = 在此放行，并走**独立后处理路径**（注册进 ToolRegistry，而非贡献 prompt 文本）。

---

## 2. 设计决策（主推方案）

### 决策 A — 范围定为「薄桥接」（thin bridge），不重写执行语义
Phase 2 把工具的**声明 + 治理 + 可用性 + 生命周期**外置为 YAML，但**执行体仍是 Python**。

- `tool` processor 用 `handler.ref` 指向一个已存在的 Python 可调用，loader 解析该 dotted path 得到 `handler` 闭包，传给 `registry.register()`。
- **纯声明式执行**（`handler.inline`：用 YAML 描述 HTTP 调用 / shell 命令 / 脚本）**推迟到 Phase 2.5**，避免重造执行语义与安全边界。
- 收益：现有 Python 工具**一行不用改**；新增工具可纯 YAML 声明（配 `handler.ref`）；现有工具可被用户的 tool processor 以 `id` 匹配 **override 其声明/治理/可用性**而保留 Python 执行体。

> ⚠️ **`handler.ref` 可行性硬约束（设计审计补正 08-04）**：`handler.ref` **只能指向签名为 `(args, **kw)` 的具名函数**。
> - 实测：75 个工具中 28 个用 lambda 包装（如 `memory_tool` 的 `handler=lambda args, **kw: memory_tool(action=...)`），47 个用具名函数（如 `file_tools._handle_read_file(args, **kw)`）。
> - `tools.memory_tool.run` 不可行——`run` 不存在；`memory_tool` 函数存在但签名为具名参数（`target=...`），不匹配 registry 的 `(args, **kw)` 契约。
> - loader 解析 `handler.ref` 时**强制校验签名**：用 `inspect.signature` 确认可调用接受 `args` 位置参 + `**kw` 可变关键字；不匹配或不可达 → **error 跳过该 processor（绝不静默）**。
> - 决策 C 的 override 语义（"仅覆盖声明/治理/可用性/hooks，保留内置 Python handler，除非 handler.ref 显式不同"）天然回避此坑——大多数 override 不指定 ref。

### 决策 B — kind 枚举扩展
v1 五类 → 六类：
```
prompt_fragment | injection | strategy_dial   # Phase 1 实装
tool                                       # Phase 2 新增
behavior_rule | lifecycle_hook            # 仍 RESERVED（留给行为相相位，不并入 Phase 2）
```
loader 放行集合加入 `tool`，走独立后处理。

### 决策 C — tool processor schema（v1 公共字段 + 工具专属字段）
```yaml
api: vermes.processor/v1
kind: tool                          # ← Phase 2 新类
id: read_file                       # = 工具注册名（override 键，与路径解耦，沿用 §3 铁律）
name: 记忆读写工具
version: 1.0.0
enabled: true
priority: 100                       # 同 toolset 内排序（layer 对工具无意义，见决策 E）
layer: stable                       # 接受但忽略（工具非 prompt 文本），保留字段以符 v1

toolset: file                       # 映射 registry toolset
schema:                            # OpenAI 格式函数 schema（= 现 READ_FILE_SCHEMA）
  name: read_file
  description: "..."
  parameters: {type: object, properties: {...}}
handler:
  ref: tools.file_tools._handle_read_file   # dotted path → loader 解析为 callable（薄桥接）；必须 (args, **kw) 签名
  # inline: {...}                   # 推迟到 2.5
availability:                       # 编译成 check_fn 等价谓词（check_fn_ref 可选；缺省由 requires_env 派生）
  check_fn_ref: tools.file_tools._check_file_reqs   # 可选：Python 可用性闭包（如文件权限探测）；缺省由 requires_env 派生
  require_tools: []
  require_capabilities: []
  platform: ["*"]
  config_flag: {key: "", default: true}
  requires_env: ["OPENAI_API_KEY"]  # 新增：env 齐备才可用
is_async: false
max_result_size_chars: null
emoji: "📖"
dynamic_schema_overrides: null      # 可选，映射 register 的同名参数

model_affinity: {operator: any_of, match: []}
conditions: {require_tools: [], require_capabilities: [], platform: ["*"], config_flag: {key: "", default: true}}
render: {engine: none, on_missing: keep, inputs: {}}   # 工具无 content，render 空置
governance:
  risk_tier: L2                     # ← 工具默认 L2（见决策 D）
  replaceable: true
  mutable_by_aegis: true
  rollback: enabled
  critic_guarded: false
  hash: auto
lifecycle:
  hooks: ["post_tool_call:audit_memory_write"]   # 见决策 F
metadata: {author: vermes-core, source: builtin}
```

**校验规则（loader 对 `tool` kind）**：
- `schema` 必填（OpenAI 格式，含 `name`/`description`/`parameters`）；`name` 应等于 manifest `id`（否则告警）。
- `handler.ref` 必填（Phase 2）；解析失败 → 该 processor 跳过并记 error（绝不静默）。
- `content` 字段对 `tool` kind **忽略**（schema 即其"内容"）。
- `risk_tier` 缺失 → 默认 **L2**（fail-closed，工具具真实副作用）。

### 决策 D — 治理：工具默认 L2，且"改定义"与"调工具"是两道闸门
- 工具有真实世界副作用（写文件、发消息、调 API），其**processor 定义**被恶意改写 = 提权面。故 tool processor 默认 `risk_tier: L2`。
- **改 tool processor 定义**（写 `~/.vermes/processors/<id>/processor.yaml`）→ 走 Phase 1 已建好的 `processor_hot_path` 审批（`_resolve_processor_tier` → `approvals.processor_modify_always_confirm`）。L1 自动应用+账本，L2 人工确认。Phase 1 的"处理器不可自证 / L0 钳 L1 / L2 独立键"在此直接复用，无需新建治理层。
- **调 tool processor 对应的工具**（运行时执行）↔ 改其定义是**两件事**：执行侧审批（managed_tool_gateway / approval 既有路径）独立存在，Phase 2 不混入。只在需要时把 `risk_tier` 透传给执行闸门做提示，不重造执行审批。

### 决策 E — layer 对工具无意义
工具不进入 system prompt 文本，故 `layer`（保 prefix-cache）对工具无效。loader 仍接受该字段（符 v1），但注册时忽略；`priority` 仍用于在 toolset 内对工具排序/去重。

### 决策 F — 生命周期钩子挂在工具执行路径上
v1 已支持任意 kind 的 `lifecycle.hooks`，取值 ∈ `VALID_HOOKS`（实测 17 个，含 `pre_tool_call` / `post_tool_call` / `transform_tool_result` / `transform_terminal_output`）。
- tool processor 声明的 hooks **附加到该工具的执行路径**（按 `id` 匹配工具）。
- 例：`id: memory` 的 tool processor 带 `post_tool_call: audit_memory_write` → 每次记忆写入后跑审计钩子。这是"行为杠杆"的着力点，无需改 `memory_tool.py`。

### 决策 G — 接入点（最小侵入）
新增 `agent/tool_processor_loader.py`（与 `prompt_processor_loader.py` 平级）：
- `load_tool_processors() -> List[ToolEntry-ready dict]`：加载内置 `vermes_cli/processors/*.yaml`（`kind==tool`）+ 用户 `~/.vermes/processors/**/processor.yaml`（`kind==tool`）。
- 在 `model_tools.py:180` 之后调用，对每个 enabled tool processor 调 `registry.register(name=id, toolset, schema, handler=resolve_ref(handler.ref), check_fn=predicate_from_conditions+requires_env, is_async, max_result_size_chars, emoji, dynamic_schema_overrides)`。
- **override 语义**：manifest `id` = 工具注册名。用户 tool processor `id: memory` 匹配内置 `memory` → 覆盖其**声明/治理/可用性**（及 hooks），**保留内置 Python handler**，除非该 processor 的 `handler.ref` 指向别处。防遮蔽沿用 `register()` 既有逻辑（同 toolset 替换需 `override` 或显式 ref 不同）。
- **Watcher**：Phase 0/1 的用户目录 glob 已是 `*/processor.yaml` + `*.yaml`，tool processor 落同一目录即被自动 watch，无需改 watcher（乐高统一性的红利）。

### 决策 H — 复用 Phase 1 的 hash（B 项）
tool processor 的 `governance.hash`（canonical sha256）直接为 **Phase 3 变体隔离** 与 **AEGIS 工具提案追踪** 提供身份键——与 prompt processor 完全一致。Phase 2 不重复造 hash 机制，直接调用 `compute_manifest_hash()`。

---

## 3. 迁移路径（非破坏性）

1. **83 个 Python 工具**：保持不变，继续 self-register。tool processor 是**叠加层**。
2. **新增工具**：可纯 YAML 声明（`kind: tool` + `handler.ref` 指向某个 Python 可调用，或 2.5 的 `handler.inline`）。
3. **增强现有工具**：用户放一个 `id: <现有工具名>` 的 tool processor，仅 override 其治理/可用性/钩子，不动 Python。
4. **内置 tool processor**：随 Phase 2 把少量高频内置（如 `read_file`/`write_file`）补成 `kind: tool` 的 YAML 落地（带 `handler.ref` 指向既有 `(args, **kw)` 具名函数），作为示范与治理锚点；其余 80+ 维持 Python self-register，逐步迁移。

---

## 4. 拍板点（请逐条裁决）

- **P2-① 范围**：采纳「薄桥接」（`handler.ref` 指向 Python），`handler.inline` 纯声明式执行**推迟到 2.5**？还是本阶段就要 inline？
  - 推荐：推迟。重写执行语义风险高、收益低，Phase 2 先吃"声明+治理+生命周期外置"的红利。

- **P2-② 用户 tool processor 落点**：复用 `~/.vermes/processors/<id>/processor.yaml`（与 prompt 同目录，Watcher 已覆盖），还是独立建 `~/.vermes/tools/<id>/`？
  - 推荐：复用。乐高统一性，Watcher/override 逻辑一套共用，避免再建一套扫描。

- **P2-③ override 粒度**：用户 `id: memory` 的 tool processor 是**仅覆盖声明/治理/可用性/hooks（保留内置 Python handler）**，还是**整工具重注册**？
  - 推荐：仅覆盖声明层；handler 只在 `handler.ref` 显式不同时才换。最大兼容、最小惊吓。

- **P2-④ 工具默认 risk_tier**：L2（fail-closed，具副作用）？还是 L1？
  - 推荐：L2。工具改定义=提权面，必须人工确认兜底；与 prompt 默认 L1 拉开（prompt 无副作用）。

- **P2-⑤ `layer` 对工具**：接受但忽略（priority 排序）？还是直接禁止该字段？
  - 推荐：接受但忽略，保留 v1 字段完整性，注册时不传 layer 给 registry。

- **P2-⑥ 内置示范范围**：Phase 2 先把哪几个内置补成 `kind: tool` YAML？（影响测试面与回归风险）
  - 推荐：`read_file` + `write_file` 两个高频、副作用明确、治理价值高、且已有 `(args, **kw)` 签名具名 handler（`tools.file_tools._handle_read_file`/`_handle_write_file`）的作为示范；其余 80+ 维持 Python self-register，逐步迁移（`memory_tool` 等 lambda 包装工具待补适配函数后再迁）。

---

## 5. 与 Phase 1 的反模式对照（必查项）

| Phase 1 踩过的坑 | Phase 2 对应防范 |
|---|---|
| A：`layer` 字段写了零消费方 | tool 的 `layer` 明确"接受忽略"，注册时不传给 registry，避免再出现死字段 |
| B：`hash` 占位符永不替换 | 直接复用 `compute_manifest_hash()`，tool processor 解析期重算，不另造 |
| C：被治理对象自证档位 | tool processor 的 `risk_tier` 同样走 `processor_hot_path` 路径判定，不读自身声明定档 |
| 测试 mock 掉唯一会出错的那行 | tool processor 的 `handler.ref` 解析、**真实**注册进 `ToolRegistry` 必须有不 mock 回归（断言注册成功 + `get_definitions` 能返回该工具） |
| 字段写进 schema ≠ 已接线 | `handler.ref`/`availability`/`lifecycle.hooks` 每个都要 grep 消费方确认，不看定义处 |
| `except:pass` 吞 NameError | `handler.ref` 解析失败必须 **error 跳过 + 记日志**，绝不宽 except 吞掉 |

---

## 6. 验证清单（Phase 2 收口标准）

- [ ] `kind: tool` 被 loader 放行并走独立注册路径；`behavior_rule`/`lifecycle_hook` 仍 RESERVED 跳过。
- [ ] 内置 `read_file` tool processor（YAML + `handler.ref`）真实注册进 `ToolRegistry`，`model_tools.get_definitions(["read_file"])` 返回其 schema。
- [ ] 用户 `~/.vermes/processors/read_file/processor.yaml`（`id: read_file`，`risk_tier: L2`）override 内置声明；保留内置 handler；Watcher 改动即时生效。
- [ ] `handler.ref` 指向不存在的可调用 → 该 processor **error 跳过**（非静默），其余工具正常。
- [ ] `availability.requires_env` 缺失时工具不进 `valid_tool_names`（check_fn 谓词生效）。
- [ ] `lifecycle.hooks` 挂在工具执行路径（post_tool_call 钩子实际触发）。
- [ ] 改 tool processor（热路径）触发 Phase 1 审批（`processor_modify_always_confirm`），L2 人工确认、L1 自动+账本。
- [ ] 真实注册回归（不 mock）：`load_tool_processors()` + `registry.register` 端到端，断言工具数量与 schema。
- [ ] `test_prompt_processors.py` 既有 62 用例 + 新增 tool processor 用例全绿。

---

## 7. 后续路线

- **Phase 2.5**：`handler.inline` 纯声明式执行（HTTP/shell 规格），让"无 Python"的新工具也能纯 YAML 落地。
- **Phase 3**：变体隔离（依赖 Phase 1/2 共用的 `governance.hash` 作为身份键）。
- **Phase 4**：闭环串联 + 模型-Harness 联合进化（GRPO）。
