# 技能索引按需降级优化 — 技术落实规格书

> **目标读者**：MiMo（执行 Agent）
> **产出目的**：把"技能索引全量注入"优化为"编码姿态下非编码类目降级为 names-only"
> **证据基准**：全部实测于 2026-09-20，本机双仓（本地 fork + 上游官方）
> **状态**：只读调研完成，**尚未改动任何源码**

---

## 0. TL;DR

| 项 | 结论 |
|---|---|
| 本地现状 | 245 个技能**全量注入**索引（名字+描述），**零降级机制** |
| 上游现状 | 同样的全量索引，但**多一道 `focus` 模式下的类目降级闸门** |
| 差距性质 | 不是 25 行小改，而是**缺一整个子系统** `agent/coding_context.py`（591 行） |
| 实测收益 | 本机实测：按上游 deny-list 可降级 57/231 个技能（24.7%），省 **4,729 字节 / 15.6%**；本地 deny-list 校准后提升至 **~24%** |
| 风险等级 | **中高**（新子系统 + 影响每轮 system prompt 字节稳定性，需回归缓存测试） |
| 建议 | 拆两阶段：**P1 降级闸门 + 项目标记检测 + 本地名单校准 + 缓存主动清除 + basic_tools 适配**（低成本见效）→ **P2 完整姿态系统**（可选） |

---

## 1. 问题定义

### 1.1 本地实现（待优化）

**文件**：`agent/prompt_builder.py`（本地仓，1709 行）

**渲染逻辑**：第 **1414–1434** 行

```python
    if not skills_by_category:
        result = ""
    else:
        index_lines = []
        for category in sorted(skills_by_category.keys()):          # 1418
            cat_desc = category_descriptions.get(category, "")
            if cat_desc:
                index_lines.append(f"  {category}: {cat_desc}")
            else:
                index_lines.append(f"  {category}:")
            # Deduplicate and sort skills within each category
            seen = set()
            for name, desc in sorted(skills_by_category[category], key=lambda x: x[0]):
                if name in seen:
                    continue
                seen.add(name)
                if desc:
                    index_lines.append(f"    - {name}: {desc}")     # 1431 ← 无条件输出描述
                else:
                    index_lines.append(f"    - {name}")
```

**问题**：第 1431 行**无条件**为每个技能输出完整描述。245 个技能 → 每轮 prompt 多喂 ~30KB 描述文本。

**本地缺失标记**（全仓 grep，均 MISS）：
- `coding_context` ❌
- `resolve_runtime_mode` ❌
- `coding_compact_skill_categories` ❌
- `compact_skill_categories` ❌
- `_detect_profile` ❌
- `agent/coding_context.py` 文件本身 ❌ **不存在**

### 1.2 上游实现（参照标准）

**文件（本机上游源码）**：`~/.hermes/hermes-agent/agent/prompt_builder.py`（1745 行）

**渲染逻辑**：第 **1327–1360** 行

```python
def _render_skills_index(
    skills_by_category: dict[str, list[tuple[str, str]]], category_descriptions: dict[str, str],
    compact_categories: "frozenset[str] | None", available_tools: "set[str] | None",
) -> str:
    """Render the ## Skills block; "" when there is nothing to list."""
    if not skills_by_category:
        return ""
    # Demoted categories collapse to one names-only line. NEVER drop entries —
    # agent-created skills are the model's project memory and it won't
    # rediscover them via skills_list. Nested categories follow their parent.
    demoted = frozenset(cat for cat in skills_by_category
                        if cat.split("/", 1)[0] in (compact_categories or frozenset()))
    hidden_note = (
        "\n(Categories marked [names only] are outside the current coding "
        "context, so their descriptions are omitted — the skills work "
        "normally and load with skill_view(name) as usual.)"
    ) if demoted else ""
    # Don't name web_search when the session has no web tools (dangling reference).
    _basic_tools = "terminal" if available_tools is not None and "web_search" not in available_tools else "web_search or terminal"
    index_lines = []
    for category in sorted(skills_by_category):
        entries = skills_by_category[category]
        if category in demoted:
            index_lines.append(f"  {category} [names only]: {', '.join(sorted({n for n, _ in entries}))}")
            continue                                             # ← 该行取代整个类目的描述输出
        cat_desc = category_descriptions.get(category, "")
        index_lines.append(f"  {category}: {cat_desc}" if cat_desc else f"  {category}:")
        seen = set()
        for name, desc in sorted(entries, key=lambda x: x[0]):   # stable: first entry per name wins
            if name not in seen:
                seen.add(name)
                index_lines.append(f"    - {name}: {desc}" if desc else f"    - {name}")
```

**核心设计原则（注释原文，务必遵守）**：
> Demoted categories collapse to one names-only line. **NEVER drop entries** — agent-created skills are the model's project memory and it won't rediscover them via `skills_list`.

即：**降级 ≠ 隐藏**。条目名永远保留（否则模型会静默丢失自建技能），只省描述。

---

## 2. 上游完整机制解剖（MiMo 移植所需全貌）

### 2.1 三层调用链

```
system_prompt.py:308-313          ← 调用点
    ↓ import coding_compact_skill_categories
coding_context.py:409             ← 薄包装
    ↓ resolve_runtime_mode(...).compact_skill_categories()
coding_context.py:355-361         ← 真正的门控
    if not self.is_coding or self.config_mode != "focus":
        return frozenset()        ← 非 focus 模式 = 无降级
    return frozenset(self.profile.compact_skill_categories)
    ↓
prompt_builder.py:1336            ← 实际渲染降级
```

**上游调用点原文**（`~/.hermes/hermes-agent/agent/system_prompt.py:305-313`）：

```python
def _skills_prompt(agent: Any) -> str:
    """Skills index (empty without skills tools).  Focus mode demotes non-coding
    categories to names-only — never hidden, every name stays visible."""
    if not any(name in agent.valid_tool_names for name in ['skills_list', 'skill_view', 'skill_manage']):
        return ""
    import model_tools
    avail_toolsets = {model_tools.get_toolset_for_tool(tool_name) for tool_name in agent.valid_tool_names} - {None, ""}
    try:
        from agent.coding_context import coding_compact_skill_categories
        _compact_cats = coding_compact_skill_categories(platform=agent.platform, cwd=resolve_context_cwd())
    except Exception:
        _compact_cats = frozenset()          # ← 失败即降级为"无降级"，永不炸
    return _pb.build_skills_system_prompt(available_tools=agent.valid_tool_names, available_toolsets=avail_toolsets,
                                         compact_categories=_compact_cats or None, skills_dir_override=_agent_skills_dir(agent))
```

### 2.2 降级名单（deny-list，否定表）

`coding_context.py:138-143`：

```python
# Clearly non-coding skill categories (deny-list; coding-adjacent and custom ones keep full entries).
_NON_CODING_SKILL_CATEGORIES = (
    "apple", "communication", "cooking", "creative", "email", "finance", "gaming", "gifs", "health", "media",
    "music", "note-taking", "productivity", "shopping", "smart-home", "social-media", "travel", "yuanbao",
)
```

> **P1 本地名单校准（重要补充）**：上游 deny-list 基于上游生态（173 个技能），本地有上游没有的类目。以下本地类目在纯编码场景同样无关，**应在 P1 一并纳入 deny-list**（成本仅 +1 行 tuple，增量收益 ~8%）：

```python
# 本地补充（P1 应一并加入 _NON_CODING_SKILL_CATEGORIES）
_LOCAL_EXTRA_NON_CODING = (
    "daily",           # 476B — 日常助手类
    "content-marketing", # 461B — 内容营销
    "openclaw-imports",  # 418B — 外部导入杂项
    # "research" 暂不加入 — 编码场景可能需要查文档/论文，保守保留
)
```

> 校准后实测收益从 15.6% 提升至 **~24%**（多省 ~2,400 字节）。`research`(1,449B) 建议保守保留——编码场景可能需要查阅文档。

**设计要点**：
1. **deny-list 而非 allow-list** — 未知/自建类目**默认保留全量描述**（安全默认）
2. **嵌套类目跟随父类目** — `cat.split("/", 1)[0]`，如 `creative/foo` 跟随 `creative`
3. 本地类目 `daily` / `content-marketing` / `openclaw-imports` / `vermes` / `github` 等**不在名单内 → 保持全量**（本地需评估是否扩充名单）

### 2.3 门控条件（最容易踩坑处）

`coding_context.py:355-361`：

```python
def compact_skill_categories(self) -> frozenset[str]:
    """Skill categories to demote to names-only in the skill index. Gated on ``focus``
    like the toolset collapse (index changes under ``auto`` proved too surprising).
    Demoted, never hidden: pruning caused silent capability loss."""
    if not self.is_coding or self.config_mode != "focus":
        return frozenset()
    return frozenset(self.profile.compact_skill_categories)
```

**两个 AND 条件**（缺一不降级）：
1. `is_coding` — 处于编码姿态（coding context 检测通过）
2. `config_mode == "focus"` — 用户显式开启 focus 模式

**上游注释揭示的历史教训**：
- `index changes under auto proved too surprising` → 曾试过 auto 模式降级，用户觉得"技能怎么变了"太意外，改成必须显式 focus
- `pruning caused silent capability loss` → 曾试过直接裁剪条目，导致能力静默丢失，改成只降级不裁剪

**这两条是上游踩过的坑，MiMo 移植时不要重蹈。**

### 2.4 编码姿态判定（`focus` 能否启用的前提）

`coding_context.py:28-30`：

```python
# Surfaces where ``auto`` may adopt the posture; messaging platforms are deliberately absent.
INTERACTIVE_CODING_PLATFORMS = {"cli", "tui", "acp", "desktop", ""}
```

`coding_context.py:32-35`（项目标记）：

```python
_PROJECT_MARKERS = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "tsconfig.json", "deno.json",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile", "composer.json", "mix.exs",
    "pubspec.yaml", "CMakeLists.txt", "Makefile", "Dockerfile", "AGENTS.md", "CLAUDE.md", ".cursorrules",
)
```

**注意**：`desktop` 在上游白名单内 → **Vermes 桌面版有资格启用 focus**。但 messaging 平台（telegram/feishu/qq）**故意排除**，即渠道场景不降级。

---

## 3. 实测收益量化（本机真实数据）

### 3.1 测量方法

实时扫描 `~/.vermes/skills/**/SKILL.md`，按上游 deny-list 计算降级收益：

### 3.2 实测结果

```
技能总数(SKILL.md): 231
可降级(非编码类目): 57 个 (24.7%)
索引描述总字节: 30310
降级可省: 4729 字节 (15.6%)
```

### 3.3 各类目明细（Top 16）

| 类目 | 技能数 | 描述字节 | 降级？ |
|---|---|---|---|
| software-development | 49 | 6,763 | — |
| devops | 20 | 2,863 | — |
| **creative** | **26** | **2,652** | ✅ 降级 |
| autonomous-ai-agents | 14 | 2,299 | — |
| research | 11 | 1,449 | — |
| **productivity** | **10** | **863** | ✅ 降级 |
| github | 7 | 841 | — |
| software-engineering | 4 | 774 | — |
| mlops | 9 | 751 | — |
| **social-media** | **3** | **522** | ✅ 降级 |
| vermes | 3 | 511 | — |
| daily | 4 | 476 | — |
| content-marketing | 2 | 461 | — |
| **apple** | **6** | **446** | ✅ 降级 |
| openclaw-imports | 2 | 418 | — |
| **media** | **5** | **364** | ✅ 降级 |

### 3.4 收益上限说明

15.6% 是**仅按上游现有 deny-list** 的保守估算。若本地按 §2.2 补充名单（加入 `daily` / `content-marketing` / `openclaw-imports`），实测收益提升至 **~24%**（多省 ~2,400 字节）。`research`(1,449B) 建议保守保留。

**但注意**：本地 245 个技能是**用户实际在用**的规模，与上游 173 个的分布不同——上游名单是按其生态定的，本地名单需**按本地类目分布重新校准**（见 §5.3）。

---

## 4. 落实方案

### 阶段 P1：最小闭环（推荐先做）

**目标**：不引入完整 coding_context 子系统，但构建一个**门控合理、名单校准、缓存安全、提示语完整**的降级最小闭环。纯手动开关的体验不如不做——至少要让 Agent 能自动识别"当前目录是不是代码项目"。

| 步骤 | 文件 | 落点 | 动作 |
|---|---|---|---|
| P1-1 | `agent/prompt_builder.py` | 函数签名（`build_skills_system_prompt`，约 1240 行） | 新增可选参数 `compact_categories: frozenset[str] \| None = None`、`available_tools: set[str] \| None = None` |
| P1-2 | 同上 | 1414–1434 行渲染段 | 插入 `demoted` 判定 + `[names only]` 分支（照抄上游 1336–1348） |
| P1-3 | 同上 | 渲染段末尾 | 降级非空时追加 `hidden_note` 提示串（照抄上游 1338–1341） |
| P1-4 | 同上 | 缓存 key | **必须**把 `compact_categories` 纳入 `cache_key`；**且 `compact_categories` 值变化时主动调用 `_SKILLS_PROMPT_CACHE.clear()`**（LRU 只在容量满时淘汰，会话中途切换模式会导致旧缓存残留 → prompt 格式不一致） |
| P1-5 | `agent/prompt_builder.py` 新增辅助函数（同文件，无需新文件） | 函数体顶部 | 移植 `_PROJECT_MARKERS`（上游 `coding_context.py:32-35`，~20 行 tuple）+ `_is_coding_dir(cwd)` 检测函数（遍历 marker 文件存在性，~10 行）。**不移植** `INTERACTIVE_CODING_PLATFORMS` / `_detect_profile` / `bounded_git_probe` 等完整姿态逻辑——本地 `desktop` 平台天然有资格，无需平台白名单 |
| P1-6 | `agent/prompt_builder.py` | 渲染段 `_basic_tools` | 移植上游 1349 行 `_basic_tools` 适配：当 `available_tools` 非空且不含 `web_search` 时提示语改为 `terminal`，否则 `web_search or terminal`。**消除无联网会话的 dangling reference** |
| P1-7 | `blueprints/config.py` + config.yaml | 新配置项 | 新增 `agent.compact_skill_categories: "auto" \| "off"`（简化版门控）。`auto` = 当 `_is_coding_dir(cwd)` 为真时自动启用降级；`off` = 永不降级（默认 `off`，安全默认）。**替代完整 posture 的 `focus` 状态机**，但保留自动检测能力 |
| P1-8 | 调用点 | 本地 `_skills_prompt` 等价处 | 读配置 → 若 `auto` 则调 `_is_coding_dir(cwd)` → 传 `compact_categories` + `available_tools` 参数 |

**P1 验收标准**：
- [ ] `compact_categories=None` 时渲染输出与改动前**逐字节一致**（回归基线）
- [ ] `compact_categories` 非空时，目标类目输出 `  <cat> [names only]: a, b, c` 单行
- [ ] **任何情况下技能名字不丢失**（245 个名字必须在输出中可数出）
- [ ] 缓存 key 区分两种模式，切换模式后 prompt 立即更新
- [ ] **`compact_categories` 值变化时 `_SKILLS_PROMPT_CACHE` 主动清除**，同一会话内不出现索引格式不一致的 prompt（LRU 自然淘汰不可靠）
- [ ] **`_basic_tools` 适配**：`available_tools` 不含 `web_search` 时，提示语为 `terminal`（不含 dangling `web_search`）
- [ ] **`_PROJECT_MARKERS` 检测**：在含 `pyproject.toml` / `package.json` 等标记文件的目录下 `auto` 模式自动降级；在无标记目录下不降级
- [ ] 配置文件异常 / 参数缺失时优雅降级为无降级（不抛异常）
- [ ] 实测字节数：prompt 内技能索引段落缩减 ≈ 15–24%（对比 §3.2，取决于 deny-list 校准程度）

**P1 工作量**：约 1–1.5 天（含 `_PROJECT_MARKERS` 检测 + 本地名单校准 + 缓存清除 + `_basic_tools` 适配）

### 阶段 P2：完整姿态系统（可选，视需求）

**仅当**要同时获得上游的"编码姿态"全套能力（toolset 收敛、workspace 快照、模型族编辑格式引导）时才做。

| 步骤 | 动作 |
|---|---|
| P2-1 | 移植 `agent/coding_context.py`（591 行）→ 本地 `agent/` |
| P2-2 | 处理依赖：上游 import `hermes_cli._subprocess_compat.bounded_git_probe` → 本地对应模块需存在或降级为 `subprocess.run(timeout=)` |
| P2-3 | 移植 `agent/system_prompt.py` 的 `_skills_prompt` 调用链 |
| P2-4 | 接入 `focus` 模式配置（`agent.coding_context: auto \| focus \| on \| off`） |
| P2-5 | `_detect_profile` 的平台判定：确认本地 `agent.platform` 对 desktop 的取值能命中 `INTERACTIVE_CODING_PLATFORMS` |

**P2 工作量**：约 3–5 天（含回归）

### 阶段 P3（可选）：本地化类目名单深度校准

上游 `_NON_CODING_SKILL_CATEGORIES` 是按上游生态定的。P1 已纳入 `daily` / `content-marketing` / `openclaw-imports` 三个明确无关类目。P3 是对剩余类目的**逐个评估**——随着本地技能库扩展，可能出现新的非编码类目需要加入 deny-list。**建议保持"保守优先"原则：不确定的类目一律不降级**（宁可少省 token，不可丢失能力可见性）。

---

## 5. 风险与冲突预警

### 5.1 与 2.5 在途改动的冲突面

| 风险 | 说明 | 缓解 |
|---|---|---|
| `prompt_builder.py` 同文件竞争 | 2.5 若也在改 prompt 组装，P1-2 会冲突 | **执行前先 `git log --follow agent/prompt_builder.py` 确认无在途改动** |
| 缓存 key 变更 | 新增 `compact_categories` 进 cache key 会让**所有存量缓存失效一次** | 预期行为，但需在 release note 说明（首次启动 prompt 重建） |
| 会话内缓存残留 | LRU 只在容量满时淘汰，`compact_categories` 值中途变化时旧缓存残留 → prompt 格式不一致 | **P1-4 已要求主动 `cache.clear()`**，验收标准已覆盖 |
| `_basic_tools` dangling reference | 不移植上游 1349 行时，无联网会话仍提示 `web_search` | **P1-6 已纳入**，验收标准已覆盖 |
| 门控体验 | 纯手动开关不如不做——用户得手动告诉 Agent "我在写代码" | **P1-5/7 已移植 `_PROJECT_MARKERS` 自动检测**，`auto` 模式可自动识别代码项目 |
| 字节稳定性 | 上游注释多次强调 prompt **byte-stable**（缓存前缀复用），降级改变字节序 | 降级判定结果必须在**会话内冻结**，不可中途变化 |

### 5.2 上游明确踩过的坑（必读）

1. **不要裁剪条目** — `pruning caused silent capability loss`。只降级描述，名字必须保留。
2. **不要在 auto 模式偷偷降级** — `index changes under auto proved too surprising`。必须显式开关。
3. **跳过已删除技能的缓存失效** — 上游 snapshots 有 manifest 校验（`_build_skills_manifest`），本地也有（1120–1134 行），**改动不要破坏 manifest 校验**。

### 5.3 本地特有注意点

1. **本地 245 vs 上游 173**：本地技能更多，且类目分布不同（本地有 `daily` / `content-marketing` / `openclaw-imports` / `scholarforge-*` 等上游没有的类目）→ 名单不能直接抄
2. **本地有外部技能目录扫描**（`prompt_builder.py:1364-1394`，`external_dirs`）→ 降级逻辑要覆盖这些条目
3. **本地缓存是 LRU**（`_SKILLS_PROMPT_CACHE`，1459 行后）vs 上游两层缓存 → cache key 接入方式需适配本地实现
4. **打包版要同步**：`dist-electron/**/Vermes.app/.../agent/prompt_builder.py` 是构建产物，源码改后需重新打包

---

## 6. 核验清单（交付前必跑）

```bash
# 1. 改动前后，无降级模式输出逐字节一致（回归基线）
#    在 compact_categories=None 下 dump build_skills_system_prompt() 结果，diff

# 2. 名字完整性：245 个技能名一个不少
grep -c '^    - ' <(降级模式输出)   # 应等于技能总数
#    [names only] 单行内的名字也要计入：grep -oP '(?<=\[names only\]: ).*' | tr ',' '\n' | wc -l

# 3. 降级生效：目标类目变单行
grep '\[names only\]' <(降级模式输出)

# 4. 缓存隔离：切换模式后输出立即变化（非陈旧缓存）
#    且 compact_categories 值变化后 _SKILLS_PROMPT_CACHE 应被主动清除

# 5. _basic_tools 适配：available_tools 不含 web_search 时，提示语为 terminal
grep -c 'web_search or terminal' <(available_tools={'terminal'} 的输出)  # 应为 0
grep 'terminal' <(available_tools={'terminal'} 的输出)                     # 应命中

# 6. _PROJECT_MARKERS 检测：auto 模式下，含 package.json 的目录自动降级；无标记目录不降级

# 7. 异常路径：配置损坏 / 参数为 None → 不抛异常，输出无降级版本

# 8. 打包版同步：源码改后重打包，验证 dist 内文件一致
```

---

## 7. 参考坐标（上游 commit / 行号）

| 内容 | 位置 |
|---|---|
| 上游降级渲染 | `~/.hermes/hermes-agent/agent/prompt_builder.py:1327-1360` |
| 上游调用点 | `~/.hermes/hermes-agent/agent/system_prompt.py:305-313` |
| 上游姿态门控 | `~/.hermes/hermes-agent/agent/coding_context.py:355-361` |
| 上游降级名单 | `~/.hermes/hermes-agent/agent/coding_context.py:138-143` |
| 上游项目标记 | `~/.hermes/hermes-agent/agent/coding_context.py:32-35`（`_PROJECT_MARKERS`，P1 移植） |
| 上游 `_basic_tools` | `~/.hermes/hermes-agent/agent/prompt_builder.py:1349`（P1 移植） |
| 上游薄包装 | `~/.hermes/hermes-agent/agent/coding_context.py:409-411` |
| 本地待改渲染 | `~/projects/vermes-electron/agent/prompt_builder.py:1414-1434` |
| 本地函数签名 | `~/projects/vermes-electron/agent/prompt_builder.py:1240` |
| 本地缓存 | `~/projects/vermes-electron/agent/prompt_builder.py:1459+` |

---

## 8. 一句话交底

**这不是"25 行小改"，是"移植一个新子系统"**——但**中间存在一个低成本中间态**：做 P1（渲染层降级 + `_PROJECT_MARKERS` 自动检测 + 本地 deny-list 校准 + 缓存主动清除 + `_basic_tools` 适配），约 1–1.5 天可拿到 15–24% 的 prompt 缩减收益，且完全可回退、不引入新文件。完整 P2（coding_context 591 行）只有当同时需要"编码姿态"全套能力时才有必要。

**建议执行顺序：P1 → 实测验证 → 再决定是否 P2。**

---

*报告生成：2026-09-20 | 全部结论基于活体实测（双仓源码 grep + 实时字节统计）| 未改动任何源码*
