# Phase 1 设计规约：Prompt Processor 外置

> 2026-08-03，基于 Phase 0 热加载闭环 + HarnessX 差距分析

## 现状全貌

### 系统提示词拼装链路（源码核实）

```
AIAgent._build_system_prompt()
  → agent/system_prompt.py: build_system_prompt_parts()
      → 三层拼装：stable + context + volatile
      → build_system_prompt() 拼接为单字符串，缓存于 agent._cached_system_prompt
```

### Stable 层（14 个 append 点，硬编码常量）

| # | 内容块 | 来源 | 触发条件 |
|---|---|---|---|
| 1 | SOUL.md 身份 | `load_soul_md()` | 文件存在 |
| 2 | DEFAULT_AGENT_IDENTITY | prompt_builder.py 硬编码 | SOUL.md 缺失 |
| 3 | VERMES_AGENT_HELP_GUIDANCE | 硬编码 | 无条件 |
| 4 | TASK_COMPLETION_GUIDANCE | 硬编码 | config agent.task_completion_guidance != false |
| 5 | MEMORY_GUIDANCE | 硬编码 | "memory" in tools |
| 6 | SESSION_SEARCH_GUIDANCE | 硬编码 | "session_search" in tools |
| 7 | SKILLS_GUIDANCE | 硬编码 | "skill_manage" in tools |
| 8 | IMAGE_GENERATE_GUIDANCE | 硬编码 | "image_generate" in tools |
| 9 | ACADEMIC_SEARCH_GUIDANCE | 硬编码 | "web_search" in tools |
| 10 | KANBAN_GUIDANCE | 硬编码 | "kanban_show" in tools / env var |
| 11 | COMPUTER_USE_GUIDANCE | 硬编码 | "computer_use" in tools |
| 12 | nous_subscription_prompt | `build_nous_subscription_prompt()` | 条件 |
| 13 | TOOL_USE_ENFORCEMENT_GUIDANCE | 硬编码 | config agent.tool_use_enforcement |
| 14 | GOOGLE/OPENAI_MODEL_OPERATIONAL_GUIDANCE | 硬编码 | model name 匹配 |
| 15 | skills_prompt | `build_skills_system_prompt()` | skills tools 存在 |
| 16 | alibaba model identity | 内联 f-string | provider=="alibaba" |
| 17 | env_hints | `build_environment_hints()` | 无条件 |
| 18 | env_probe | `get_environment_probe_line()` | config agent.environment_probe |
| 19 | active_profile_hint | `_resolve_active_profile_name()` | 条件 |
| 20 | effective_hint (platform) | PLATFORM_HINTS / WSL_HINT 等 | 条件 |

### Context 层（2 个 append 点）
- caller-supplied system_message
- context_files_prompt（AGENTS.md / .cursorrules / .vermes.md）

### Volatile 层（12+ 个 append 点，动态）
- memory snapshot / USER.md / external memory
- handoff / evolution / recall / continuity / decisions（经 memory_budget 统一裁剪）
- capability_report / active_skills / pending_skills

### 硬编码常量统计
- prompt_builder.py: 25 个大写常量（14 个是 guidance 文本块）
- system_prompt.py: 0 个（全部从 prompt_builder import）
- 所有 guidance 块是 Python 多行字符串字面量，改一个字就要重建 DMG

## 问题诊断

### 核心问题：提示词是代码不是数据

1. **改一个字要重建 DMG**：guidance 文本焊在 .py 里，PyInstaller 打包后是冻结二进制
2. **AEGIS 只能改阈值不能改提示词**：self_modify 改 .py 后要 reload（Phase 0 解决了热路径），但 guidance 在冻结包内不在 ~/.vermes/modules/
3. **无版本化**：改了 guidance 没有 diff/rollback 机制（self_modify 有 .bak 但 guidance 常量不走那个路径）
4. **无组合检测**：多个 guidance 块之间可能有矛盾/冗余，但没人审查

### 但不能全搬——分层处理

| 层 | 可外置？ | 理由 |
|---|---|---|
| Guidance 文本块（14 个） | ✅ | 纯文本，零逻辑，用户/AEGIS 改了改了就改了 |
| 触发条件（if "memory" in tools） | ❌ | 逻辑代码，外置=变成 DSL 解释器 |
| 动态内容（build_skills_system_prompt 等） | ❌ | 函数调用，不是文本 |
| Volatile 层 | ❌ | 全动态，不是积木 |

## 设计方案

### 积木描述格式：`processor.yaml`

不叫 "prompt.yaml"——因为 Phase 2 会把工具行为也外置，统一用 Processor 概念。

```yaml
# ~/.vermes/processors/identity.yaml
processor_type: prompt
name: identity
version: "1.0.0"
description: Agent 身份与核心行为定义

# 触发条件（声明式，不做图灵完备 DSL）
triggers:
  type: always  # always | tool_present | config_flag | model_match | provider_match
  # tool_present: { tools: ["memory", "session_search"] }
  # config_flag: { key: "agent.task_completion_guidance", default: true }
  # model_match: { patterns: ["gemini", "gemma"] }
  # provider_match: { value: "alibaba" }

# 内容块
content: |
  You are Vermes, a desktop AI agent for Chinese users...

# 优先级（数字越小越前）
order: 10

# 可替换性
replaceable: true
```

### 目录结构

```
~/.vermes/processors/
├── identity.yaml          # DEFAULT_AGENT_IDENTITY
├── help_guidance.yaml     # VERMES_AGENT_HELP_GUIDANCE
├── memory_guidance.yaml   # MEMORY_GUIDANCE
├── task_completion.yaml   # TASK_COMPLETION_GUIDANCE
├── tool_use_enforcement.yaml
├── google_model.yaml      # GOOGLE_MODEL_OPERATIONAL_GUIDANCE
├── openai_model.yaml      # OPENAI_MODEL_EXECUTION_GUIDANCE
├── skills_guidance.yaml
├── session_search.yaml
├── image_generate.yaml
├── academic_search.yaml
├── kanban.yaml
├── computer_use.yaml
└── platform_hints.yaml    # PLATFORM_HINTS / WSL_HINT 合并
```

### 加载器：`PromptProcessorLoader`

```python
# agent/prompt_processor_loader.py

@dataclass
class PromptProcessor:
    name: str
    content: str
    order: int
    triggers: dict
    replaceable: bool
    version: str
    source_path: Path  # 用于 reload watcher

class PromptProcessorLoader:
    """加载 ~/.vermes/processors/ 下的 YAML 积木。
    
    - 内置默认：打包时把当前硬编码常量导出为 YAML，放在 _internal/processors/
    - 用户覆盖：~/.vermes/processors/ 同名文件覆盖内置版
    - 热加载：复用 Phase 0 的 watcher 机制，文件变更自动 reload
    - AEGIS 可改：self_modify 改 ~/.vermes/processors/*.yaml → watcher 触发 → 下次 build_system_prompt 生效
    """
    
    BUILTIN_DIR = "_internal/processors"  # 冻结包内
    USER_DIR = "~/.vermes/processors"     # 用户热路径
    
    def load_all(self) -> List[PromptProcessor]:
        """加载所有 prompt processor，用户覆盖内置。"""
        ...
    
    def evaluate(self, processor: PromptProcessor, agent: Any) -> bool:
        """评估触发条件，返回是否应注入。"""
        ...
```

### system_prompt.py 改造

```python
# Before:
stable_parts.append(MEMORY_GUIDANCE)

# After:
for proc in self._prompt_processors:
    if proc.triggers_type == "tool_present":
        if set(proc.triggers["tools"]) & agent.valid_tool_names:
            stable_parts.append(proc.content)
    elif proc.triggers_type == "always":
        stable_parts.append(proc.content)
    # ...
```

### 触发条件类型（声明式，不做图灵完备 DSL）

| type | 参数 | 语义 |
|---|---|---|
| `always` | 无 | 无条件注入 |
| `tool_present` | `tools: [list]` | 任一工具存在则注入 |
| `config_flag` | `key, default` | config.yaml 布尔值为 true |
| `model_match` | `patterns: [list]` | model name 含任一 pattern |
| `provider_match` | `value: str` | provider 匹配 |
| `env_var` | `var, value` | 环境变量匹配 |

### 与 Phase 0 的衔接

1. **热加载**：watcher 已监控 `~/.vermes/modules/`，Phase 1 新增监控 `~/.vermes/processors/`
2. **classify_component_swap**：`~/.vermes/processors/*.yaml` → `module_hot_path`（L1，YOLO 豁免）
3. **AEGIS**：B1 候选可生成 "调整 guidance 文本" 提案，auto_apply 写 YAML → watcher reload → 下次 prompt 生效

### 安全边界

- **不可外置的**：触发条件逻辑（if/else 分支）、函数调用（build_skills_system_prompt 等）
- **可外置的**：guidance 文本内容、order 顺序、replaceable 标记
- **AEGIS 改 guidance 的限制**：只能改 `replaceable: true` 的 processor；`replaceable: false` 的（如 identity）强制 L2

### 不做的事

1. **不做 DSL 解释器**：触发条件是声明式枚举，不支持任意 Python 表达式
2. **不做模板引擎**：content 不支持 Jinja2/变量插值（alibaba model identity 除外，保持内联）
3. **不做可视化编辑器**：YAML 文件就是编辑器，用户用任何编辑器改

## 实施计划

### Step 1: 导出内置默认（~30 min）
- 写脚本把 14 个 guidance 常量导出为 YAML 文件，放入 `_internal/processors/`
- 写 `PromptProcessorLoader` + `PromptProcessor` dataclass
- 改 `build_system_prompt_parts` 读 processor 列表替代硬编码常量

### Step 2: 用户覆盖 + 热加载（~20 min）
- `~/.vermes/processors/` 目录扫描，同名覆盖内置
- watcher 新增监控 `~/.vermes/processors/`，变更时清缓存
- `build_system_prompt_parts` 缓存 key 加入 processor 目录 mtime

### Step 3: 测试 + 提交（~20 min）
- 单测：加载/覆盖/触发条件/热加载
- 回归：全量 system prompt 拼装结果与改前一致（文本 diff）
- 提交 + push

### Step 4: 构建验证（~15 min）
- build DMG → 安装 → 确认 `~/.vermes/processors/` 被读取
- 改一个 YAML → 不重启 → 下次对话生效

## 风险评估

| 风险 | 等级 | 缓解 |
|---|---|---|
| 改 YAML 后 prompt 变化导致 agent 行为异常 | 中 | replaceable 标记 + AEGIS 双闸门 |
| 触发条件不够用，需要加新 type | 低 | 枚举可扩展，加新 type 不破坏旧 |
| 内置默认与用户覆盖冲突 | 低 | 同名覆盖语义清晰，有 order 控制顺序 |
| prompt cache 失效 | 中 | processor 目录 mtime 加入 cache key |
| AEGIS 改 guidance 注入恶意指令 | 高 | hardcoded_guard 审查 content + replaceable 标记 |

## 与约束①的对齐

> "外置的是标量不是组件，config.yaml 不是积木正确载体"

✅ 本方案不往 config.yaml 堆 key。用独立 YAML 文件（每个 processor 一个），有完整的积木描述格式（name/version/triggers/content/order/replaceable），加载器是独立的 PromptProcessorLoader，不污染 config.py。

> "先定积木描述格式 + 加载器，再谈搬策略"

✅ 本方案第一步定义 `processor.yaml` schema + `PromptProcessorLoader`，第二步才搬 14 个 guidance 块。

> "Phase 1 真正的难点不是搬阈值而是定义格式"

✅ 确认。14 个 guidance 块搬出来是机械工作（30 min），格式定义才是设计核心。
