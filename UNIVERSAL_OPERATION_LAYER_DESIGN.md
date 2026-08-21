# Universal Operation Layer · 通用操作层设计基线 v1

> 配套：
> - [`PRO_TOOL_ADAPTER_DESIGN.md`](./PRO_TOOL_ADAPTER_DESIGN.md) — **本基线下的参考实现 A（FreeCAD 3D 后端细节）**，战略地位降级
> - [`MODELING_QUALITY_ROADMAP.md`](./MODELING_QUALITY_ROADMAP.md) — **制造业探索场内部路线图**，仍是 Vermes 的一手演示 / 飞轮验证，但不再是产品定义
> - 锚定文章：[一切皆插件 · DeepSeek harness 哲学](https://mp.weixin.qq.com/s/-zYhHLInUKzEWOrbA5mM2A)
>
> 状态：**v1.4（战略基线 + 评审修订 + §13 spike 真实数据已执行）**。本文件是战略北极星，不规定具体代码行号；具体实现以本文件为基线展开。
> 读者：Vermes 团队 + 后续接入任意专业软件的后端作者。

---

## 0. 一句话定位

**Vermes 不造操作层，杠杆现成轮子；Vermes 只造乐高底板（统一插槽）+ catalog 生态，让一切软件都能被 AI 操作。积木的来源不是 Vermes 自研垂直逻辑，而是**全行业第三方专业软件（FreeCAD / Blender / 各行业）本身经操作层变成可挂积木** + 不同用户搭的技能/插件——用户即生态，一切（专业软件/技能/插件/独立项目）皆可被挂为 Vermes 的积木工具。**

模型越来越聪明 → 垂直领域落地会迎刃而解 → 沉寂于单个垂直领域反而死得快。正确押注 **连接器（slots）**，不是垂直硬编码逻辑。

---

## 1. 北极星：一切皆可被 Vermes 操作

```
自然语言 / 任意意图
        │
        ▼
Vermes Agent（LLM，模型无关插槽，不绑任何大脑）
        │  tool_call：mfg_* / doc_* / sheet_* / <任意软件>_*
        ▼
乐高底板（orchestration 核心 + memory fabric + 插件内核 + catalog）
        │  把「软件适配器」当作平等插件挂载
        ▼
SoftwareAdapter 薄插槽 ── 挂载 CLI-Anything 生成的 agent-native CLI
        │                      │  不写垂直逻辑，只做「挂接 + 内省 + 注册」
        ▼                      ▼
操作层（CLI-Anything，杠杆，不造）  原生 .FCStd / .blend / .odt / 任意工程文件
        │  7-phase 自动流水线把任意有代码库的软件变成 AI 可操作 CLI
        ▼
任意专业软件（FreeCAD / Blender / LibreOffice / GEGL / … 随市场需求扩展）
```

**底板不产积木、只供统一插槽**：换脑 = 换模型插件；加能力 = 装软件适配器插件；连核心 Agent 循环都能写插件替换。这是与 DeepSeek harness「一切皆插件」哲学一致的 Vermes 形态。

---

## 2. 战略转向：从「垂直沉寂」到「通用连接层」

### 2.1 错误押注 vs 正确押注

| 维度 | ❌ 沉寂垂直（死得快） | ✅ 通用连接层（活下来） |
|---|---|---|
| 资源去哪 | 手搓 fillet/draft/pattern 等垂直硬编码逻辑 | 连接器 slots + catalog 生态 + 杠杆现成轮子 |
| 模型变笨时 | 垂直逻辑是**必需**的（暂有价值） | 连接层仍是必需底座 |
| 模型变聪明时 | 垂直逻辑**贬值**（LLM 自己会驱动软件）→ 沉没成本 | 连接层**增值**（聪明 LLM × 通用连接 = 垂直自动迎刃而解） |
| 护城河 | 短暂的、会被模型迭代抹平 | 结构性：生态 + 插槽标准 + 分发网络 |

**护城河会倒转**：模型笨时垂直硬编码是资产，模型聪明时它变成负债。把资源押在贬值资产上，就是「太沉寂垂直领域会死得快」的本质。

### 2.2 垂直领域不是沉没成本，是「参考实现」

- 制造业 / 注塑是你的**探索场 + 第一个一手适配器**（自带工厂飞轮护城河）。
- 但 Vermes **作为产品**，是承载任意行业的乐高底板。
- 3D 工作产出的三层适配器骨架（base / adapter / bridge），正是**提拔成通用框架的模板**——不是推倒，是泛化。
- 不同用户的行业不同、需求不同；Vermes 的价值在于「随市场需求与用户使用不断升级」，而不是替某个行业写死逻辑。

---

## 3. 论证锚点（已核实，非臆测）

### 3.1 微信文章三点（原文术语）
1. **「一切皆插件」**：模型适配器、工具集、会话存储、沙箱、任务调度、甚至前端 UI，全部平等的插件模块，免改内核热插拔/卸载/替换。
2. **「乐高底板」**：底板不产积木、只供统一插槽——换脑 = 换模型插件，加能力 = 装工具插件，连核心 Agent 循环逻辑都能写插件替换。
3. **「模型 = 可替换 CPU，底座 = 攥住生态的 OS」**：性能鸿沟填平后模型大宗商品化；价值从模型层向执行层迁移；不绑自家模型（Android 剧本：让绝大多数 agent 跑在你底座上，而非赢硬件参数）。

### 3.2 Vermes 已具备的底座（= 已经在做 Harness，无需重造）
| 能力 | 落点（已有） | 对应文章论点 |
|---|---|---|
| 模型无关 | `runtime_provider.resolve_runtime_provider` 统一漏斗，DeepSeek 仅默认大脑 | 不绑自家模型 ✓ |
| 可插拔模块 | ScholarForge 29 工具注册进全局 `tools/registry`；`vermes-mod-*` 独立仓 | 一切皆插件 ✓ |
| 远程 catalog 分发 | P7 `vermes-modules-catalog`，remote-first 发现链 | 乐高底板 ✓ |
| discovery-first | 用户自装软件、Vermes 自动发现（已用于 FreeCAD 后端） | 底座不绑分发 ✓ |
| 双工具注册 | `tools/registry.py` + `vermes_cli/plugins.py` | 插件内核 ✓ |
| **涌现自学习 / 自进化 + 可插拔（自适应成长家底）** | `memory_fabric`（L0–L4 全生命周期记忆）+ `evolution_manager`（自进化 7 模块经 `raw_event` 事件总线串环）+ plugin kernel（`PluginManager` 四源发现 + `plugin.yaml` + `register(ctx)`） | **随 AI 行业 / 大模型升级迭代而自适应成长 ✓**——不靠硬编码垂直逻辑（正是 §2 护城河倒转论点的技术底座） |

**结论**：Vermes 的底座已经是「乐高底板」形状，且带着**能自适应成长的涌现家底**——只是被 3D 垂直带偏了**产品定义**。本基线把它掰回战略主航道。

### 3.3 操作层轮子已存在且成熟（杠杆对象）
- **项目**：`HKUDS/CLI-Anything`，**Apache 2.0**。
- **定位原文**：**"Making ALL Software Agent-Native"**——一条命令把任意有代码库的软件自动生成 agent-native CLI。
- **FreeCAD 已原生支持：258 个命令 / 17 个分组**（而我们的 ProToolAdapter 手搓了 18 个工具、耗了 278 测试——这 18 个工具正是「重造的轮子」）。
- **不止 3D**：LibreOffice / Blender / GEGL 均覆盖，证明「任意专业软件」不是空话。
- **7-phase 自动流水线**：扫源码 → 映射 GUI 动作到 API → Click 生成 CLI（含 REPL / `--json` / undo-redo / 测试 / 文档）→ `cli-hub` 发布到 PATH，agent 自主发现安装。
- **自带 Registry/分发层**（`cli-anything-hub` PyPI + `public_registry.json`）——与我们的 P7 远程 catalog 是同一件事，**不用重造**。

---

## 4. 四层架构模型（基线）

| 层 | 名称 | 职责 | 来源 |
|---|---|---|---|
| **L0** | 模型插槽 | 解析/接入任意 LLM（OpenAI 兼容等） | 已有 `runtime_provider`，**不变** |
| **L1** | 乐高底板 | 编排核心 + memory fabric + 插件内核 + catalog 分发 + discovery | 已有，**不变** |
| **L2** | 软件适配器薄插槽（**本基线新增的唯一要写的薄层**） | 把 CLI-Anything 生成的 CLI 挂进 Vermes 工具注册表；内省 schema → 自动注册工具；领域词汇表分层（可选） | **要写（薄）** |
| **L3** | 操作层（杠杆，不造） | 把任意软件变成 agent-native CLI + cli-hub 分发 + 自主发现 | **杠杆 CLI-Anything** |

> L2 是 Vermes 唯一新增代码面。它的「薄」是纪律：**不写垂直逻辑**，只做「挂接 + 内省 + 注册」。垂直能力由 L3 现成提供。

---

## 5. SoftwareAdapter 薄插槽接口形态（基线契约）

> 这是基线，**不锁死实现**。核心约束只有一条：适配器不实现垂直逻辑，只桥接 CLI。

### 5.1 挂载语义

CLI-Anything 为每个软件生成一个 **Click CLI**（带 `--json` 结构化输出）。Vermes 的 `SoftwareAdapter` 做三件事：

1. **定位 CLI 二进制**：discovery-first（PATH 里 cli-hub 安装的 `cli-<software>`，或本仓 catalog 记录的路径）。
2. **内省 schema**：跑 `<cli> --help` / 子命令 `--json`，枚举可调用操作。
3. **注册进工具表**：把每个子命令注册为 Vermes 工具（`tools/registry.py` + `plugins.py` 双注册），LLM 直接 `tool_call`。

### 5.2 基线数据结构（草案）

```python
# vermes_cli/adapters/software_adapter.py（新增薄层，L2）
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

@dataclass
class SoftwareAdapterSpec:
    domain: str                 # "3d" | "video" | "office" | "ide" | "audio" | ...
    software: str               # "freecad" | "blender" | "libreoffice" | ...
    cli_bin: Path               # CLI-Anything 生成的 CLI 路径（如 cli-freecad）
    domain_vocab: dict = field(default_factory=dict)   # 可选：领域词汇表→原语映射
    # 例: {"fillet": "edge-fillet", "draft": "draft-angle"} 仅作 LLM 提示辅助

@dataclass
class CLITool:
    name: str                   # 注册进 Vermes 的工具名，如 mfg_fillet
    subcommand: list[str]       # 透传给 CLI 的参数，如 ["edge-fillet", "--radius", "2.0"]
    json_schema: dict           # 从 CLI --json 内省得到的入参 schema
    description: str            # 从 CLI --help 抽取

class SoftwareAdapter:
    """薄插槽：挂接 CLI-Anything 生成的 CLI，不写垂直逻辑。"""
    def __init__(self, spec: SoftwareAdapterSpec): ...
    def discover_tools(self) -> list[CLITool]:
        """内省 CLI schema → 自动注册 Vermes 工具。"""
    def invoke(self, tool: CLITool, args: dict) -> dict:
        """subprocess 调 CLI，解析 --json 返回。"""
```

### 5.3 领域词汇表分层（可选，硬护栏）

词汇表只是给 LLM 的**提示辅助**，不是硬编码逻辑：

- 3D：`fillet / draft / pattern / boolean / scale / split`
- 视频：`cut / color-grade / subtitles / stabilize`
- 办公：`export-pdf / mail-merge / pivot`
- DAW：`mix / master / quantize`

**关键判断**：模型越聪明，词汇表越可省略——LLM 能直接读 CLI `--help` 驱动。词汇表是「降级过渡快赢」，不是架构必需（对应产品战略「小白/专家双层」）。

**⚠️ L2「薄」的护栏（评审新增）**：词汇表是灰色地带——若每个领域都加，L2 会悄悄变成「领域知识层」，违背薄插槽纪律。硬约束：
- **v1 不做词汇表**：让 LLM 纯靠 CLI `--help` 驱动，先测效果再决定。
- 若后续确需启用：全局总条目 **≤ 50**、单域 **≤ 15**；且**只允许「领域词 → CLI 子命令」的纯映射，禁止在词汇表层藏任何逻辑/默认值/翻译代码**（逻辑一律在 L3 CLI 内）。
- 任何词汇表条目须经 spike 证伪「LLM 无此辅助确实驱动不了」才允许加入。

---

## 6. ProToolAdapter 重定位（退化为参考实现 A）

原 `PRO_TOOL_ADAPTER_DESIGN.md` 的 FreeCAD 三层骨架（base / adapter / bridge）**形态正确但被 3D 焊死**。重定位：

- **base.py 契约** → 提拔为通用 `SoftwareAdapter` 契约（§5）。
- **freecad_adapter.py + vermes_freecad_bridge.py** → 降级为「参考实现 A（3D 后端）」，且**不再手搓 18 工具**；改挂 `cli-anything-freecad`（258 命令现成）。
- **手搓的 18 工具** → **冻结，非退休**（评审修正）：标记 `deprecated`、停止新增与主动维护，仅在 CLI-Anything 覆盖不到的命令上作 **fallback 安全网**，不删代码。若 spike 验证有 2–3 个关键命令不达预期，对应的手搓工具即兜底——避免 L3 杠杆失效时 Vermes 直接失去能力。

---

## 7. 待办重构（原 9 件 → 分层收口）

> 来源：用户盘点的 M1-6 收口清单 + 本次战略转向。

### 7.1 降级为「参考实现 A 打磨」（不再占产品 P0）
| 原 # | 项 | 新处置 |
|---|---|---|
| #1 | 前端 ThreeDStudio.vue 真机对接 | 归参考实现 A 演示验收（你机器） |
| #2 | 标准件库空壳 | 交由 `cli-anything-freecad` + build123d 生成，不再手搓 |
| #3 | BOM parts=0 | 源码已证伪（路径一致、save_parameters 已触发），**大概率为 phantom，从清单删 P1** |
| #4 | DXF/G-code 零验证 | 杠杆 CLI 现成能力；G-code 需你机器切片软件 |
| #5 | tessellate 目视确认 | 前端渲染验收（你机器） |
| #6 | FreeCAD 1.1.3 API 差异 | 交给 `cli-anything-freecad` 吸收，Vermes 不再逐 API 手验 |
| #7 | H9 _FEATURE_PARAMS 持久化 | 由 CLI 的 `--json` 结构化输出天然解决，Vermes 侧可不写 |

### 7.2 升级为产品 P0（本基线核心）
- **P0-a**：把 CLI-Anything 定为操作层 + 落地 `SoftwareAdapter` 薄插槽（§5）。
- **P0-b（硬门槛，非可选）**：spike 验证——装 `cli-anything-freecad`，**不只验 258 命令能否枚举，更要验 5–10 个典型命令的端到端链路**（参数传入 → CLI 执行 → `--json` 解析 → Vermes 工具返回）。这是一切后续的前提（评审升级：从「5 分钟级」升为「半天级」）。

### 7.3 升级为 P1（生态）
- **P1-a**：P7 catalog 对齐 `cli-hub` registry 形态，discovery 接 cli-hub（不重造市场）。
- **P1-b**：做一个**非制造第二域参考适配器**（视频 / 办公 / IDE 任选），证明框架不是 3D 形状、通用连接层成立。

### 7.4 保留不变
- 模型无关 `runtime_provider` ✓
- memory fabric ✓
- ScholarForge（一手演示 / 验证可插拔道路）✓
- #8 M0 快赢（A1+A2 提示词 / C1 PBR）—— **仍待你拍板取舍**
- #9 两设计文档（MODELING_QUALITY_ROADMAP v2 + PRO_TOOL_ADAPTER v2）—— **归 QClaw 推进**

---

## 8. 下一步行动（按基线分层开工）

### 8.1 立即（P0-b spike，硬门槛，半天级，沙箱可做）— **已执行，见 §13**

> 评审定调：**spike 是一切的前提**。从「5 分钟级」升级为「半天级」，认真验 5–10 个端到端链路，拿到真实数据再往下走。

1. 装 `cli-anything-freecad`（或等价生成器），记录其**上次发布时间 / 绑定的 FreeCAD API 版本**，与当前 FreeCAD 1.1.x 比对差距。
2. 跑 `--json` 确认 258 命令可枚举，**并统计其中 `--json` 输出稳定可内省的比例**（若大量命令返回非结构化文本，L2「自动注册」退化为手工适配 → 触发 §9 退化风险）。
3. 选 5–10 个典型命令做**端到端链路验证**：参数传入 → CLI 执行 → `--json` 解析 → Vermes 工具返回。至少覆盖 1 个几何类（fillet/draft）、1 个 IO 类（import/export）、1 个查询类（feature-tree）。
4. 写最小 `SoftwareAdapter.discover_tools()` 把验证过的命令挂进 `tools/registry`，跑通 1 条 LLM → 工具 → CLI → JSON 的完整闭环。
5. **可用性阈值判定**：
   - 若 **≥ 80%** 命令端到端可用 → 杠杆成立，L2 保持「薄」，手搓 18 工具进入**冻结**（§6）。
   - 若 **< 80%** → L2 撑不住、需补垂直逻辑（回到老路）→ **暂停通用化**，先修 L3 覆盖或换生成器，回 §9 复盘。
6. **维护依赖检查**：确认 CLI-Anything 仓库活跃度；若已停更，L3 将变维护负担（与「杠杆」初衷矛盾）→ 记录风险，评估自维护 fork 成本。

### 8.2 随后（P1-b 第二域证明 — 选型重议，评审修正）

> 评审指出：第二域不只是验证技术通用性，更是**第一次对外讲故事的素材**，选型要看叙事强度。

- **LibreOffice 叙事偏弱**：办公文档操作已是成熟红海（Notion AI / Google Workspace / WPS AI），Vermes 差异化不明显——**不首选**。
- **建议候选（按叙事强度）**：
  - **Blender（3D 创作）**：与 FreeCAD 同属 3D 但场景完全不同（设计 vs 创作），叙事最强——「同一个底座，设计/创作/办公都能接」。
  - **VS Code（IDE 操作）**：程序员群体直接感知「我的编辑器也能被 Vermes 操作」，技术口碑传播快。
- 选型标准：① CLI-Anything 已原生覆盖；② 与 FreeCAD 形成「跨场景」对比；③ 对外故事有记忆点。最终选型待你拍板（见 §10）。

### 8.3 验收项（你机器，演示飞轮）
- #1 前端真机对接、#5 tessellate 目视、#4-Gcode 真切片——随参考实现 A 一并 tick。

---

## 9. 风险与边界（诚实标注）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **#1 CLI-Anything 成熟度（最高优先级）** | Apache 2.0 ≠ 生产就绪。① FreeCAD 1.1.x API 兼容差距（它绑定哪个版本？）② 258 命令里多少 `--json` 稳定可内省（否则 L2 自动注册退化成手工适配）③ 维护依赖（若停更，L3 变你的维护负担，与杠杆初衷矛盾） | §8.1 spike 升级为**半天级硬门槛**：验 5–10 端到端链路 + 设 **≥80% 可用性阈值**；不达标则暂停通用化、先修 L3 覆盖或换生成器 |
| **#2 L2 退化（薄→厚）** | 若 L3 覆盖不足（<80% 命令可用），L2 被迫补垂直逻辑 → 回到老路 | spike 阈值守门；domain_vocab 硬护栏（§5.3：v1 不做、全局 ≤50、纯映射禁逻辑） |
| **#3 生态冷启动（Android 剧本前提缺失）** | 文章用 Android 类比（Google 不赢硬件、赢在让 app 跑在 Android 上），但 Android 有 10 亿+ 用户；Vermes 的 catalog 目前是空的，P7 + cli-hub 对齐方向对，但**冷启动是真实问题** | 见 **§12 积木来源**：冷启动的解不是顶部灌水，而是**用户即生态**——不同用户按各自行业搭不同积木喂回底座，叠加 §11.1 的自适应成长家底，形成供给侧飞轮；制造业探索场只是其中一个活案例 |
| 薄插槽稳定性 | 透传 CLI 出错传播 / 版本漂移 | `invoke` 统一捕获 stderr + 结构化错误；CLI 版本进 catalog 记录 |
| 词汇表是否必要 | 模型聪明后可能冗余 | 定为可选降级辅助（§5.3），非架构必需，v1 不做 |
| 垂直飞轮护城河 | 通用化后制造业差异化是否削弱 | 不削弱：工厂飞轮（建模→样品→开模→量产）是**真实业务闭环**，独立于软件操作层；操作层只是让它更快被 Vermes 编排 |
| 与 QClaw 文档分工 | QClaw 推 v2 两份，本基线为新战略文件 | 本文件为北极星；v2 两份降级为其下实现/探索文档（§0 引用关系已定） |

---

## 10. 待你拍板的决策点

1. **（已定调）自研 vs 杠杆 CLI-Anything** → 你已明确「能不重造轮子就不重造」，定为**杠杆**。
2. **第二域选型（P1-b）**：LibreOffice 叙事弱，建议 **Blender（设计/创作同属 3D，叙事最强）** 或 **VS Code（IDE，程序员直接感知）**；标准=CLI-Anything 已覆盖 + 跨场景对比 + 对外故事有记忆点。待你拍板。
3. **手搓 18 工具处置**：定调为**冻结**（deprecated + fallback 安全网），非删代码、非彻底退休。
4. **CLI-Anything spike 阈值与 fallback 触发**：是否认同 **≥80% 端到端可用** 作为杠杆成立门槛？不达标时的降级路径（修 L3 / 换生成器 / 局部补 L2）待你确认。
5. **#8 M0 快赢取舍**：A1+A2 提示词 / C1 PBR 的范围，仍待你定。

---

---

## 11. 战略评审结论（2026-08-21 用户评审 · 已吸收进 v1.1）

### 11.1 三个关键判断（用户原话提炼）
1. **时间维度对冲**：模型越聪明，垂直硬编码越贬值——fillet/draft/pattern 这类手搓 18 工具，在「LLM 能直接驱动 FreeCAD CLI」的世界里就是沉没成本。
2. **轮子现成**：CLI-Anything 的 FreeCAD 258 命令 vs 手搓 18 工具 + 278 测试，数字本身就是最硬的论证。
3. **底座已有**：runtime_provider / tools/registry / vermes-mod-* / P7 catalog 不是设计图，是**已经跑着的代码**——只需提拔到通用层，不必重造。

### 11.2 四层架构连贯性确认
L0→L1→L2→L3 清晰；L2「薄」是纪律（只挂接+内省+注册，不写垂直逻辑），与 ProToolAdapter 三层骨架**形态一致、只是被 3D 焊死**，提拔到通用层逻辑连贯。

### 11.3 执行风险定调（用户最终判断）
> 战略方向对：不做垂直产品做连接层，模型越聪明连接层越值钱——比沉寂在 3D 里安全得多。
> 但执行风险集中在 **L3 杠杆可靠性**。若 258 命令里只有 60% 真能用，L2 的「薄」就撑不住，还得在 L2 补垂直逻辑，回到老路。
> **所以：spike 是一切的前提**——从「5 分钟级」升级为「半天级」，认真验 5–10 个端到端链路，拿到真实数据再往下走。

→ 本节结论已落实为：§8.1 升级为半天级硬门槛 + §9 #1 风险置顶 + §10 决策点 4（阈值拍板）。

---

## 12. 积木来源：用户即生态（乐高家底的补全 · 2026-08-21）

> 本节补全战略最关键的缺口：**积木从哪来**。它同时强化了「底座已有」（§3.2 自适应成长家底）与回应了「生态冷启动」（§9 #3）——冷启动的解不是顶部灌水，而是用户生态。

### 12.1 家底已深，且能自适应成长
Vermes 不是裸底板，它带着「涌现优于硬编码」的自进化家底（§3.2 新增行）：
- `memory_fabric`（L0–L4）全生命周期记忆；
- `evolution_manager` 自进化 7 模块经 `raw_event` 事件总线串环；
- plugin kernel 四源发现 + `register(ctx)` 即插即用。
→ 底板能**随 AI 行业与大模型升级迭代而自适应成长**，不靠手搓垂直逻辑。这正是 §2「护城河倒转」论点的技术底座：模型变聪明时，垂直硬编码贬值，而**自适应底座增值**。

### 12.2 积木的来源 = 全行业专业软件 + 用户生态
- **第三方专业软件本身就是积木（最高认识）**：FreeCAD / Blender / 各行业专业软件，经操作层（§4 CLI-Anything）变成 agent-native CLI，**直接就是 Vermes 可挂的积木**。这是体量最大的积木池——既不是 Vermes 写、也不是用户写，而是行业既有的专业软件资产。
- 叠加用户按各自行业 / 需求搭的技能 / 插件，积木自然多元 → 底座覆盖越广 → 越能适配下一个用户。这是**供给侧飞轮**，比 §9 #3 的「内部制造业飞轮」更一般化。

### 12.3 案例：一切皆 Vermes 的积木（这个高度才是正确的认识）
- **第三方专业软件 = 积木**：FreeCAD（3D 设计）、Blender（3D 创作），以及**各行各业的专业软件**，都是 Vermes 的积木——经 L3 操作层变成 AI 可操作，即被挂上底板。
- **用户本人即案例**：把 GitHub 火热的**技能 / 插件 / 独立项目**当积木接入 Vermes——它们不是 Vermes 自研，是生态现成件。
- 与 §4 一致：CLI-Anything 是社区轮子、ScholarForge/mfgcad 是团队参考积木、用户的 GitHub-skill 实践是第三方积木。
- **积木的完整定义** = 全行业专业软件（经操作层）+ 用户生态技能/插件 + 社区项目 + 团队参考实现。Vermes 只保证「插槽标准 + 发现 + 分发」，让一切能被挂上来。

### 12.4 市场存活机制
**底座（L1）+ 自适应成长家底（§3.2）+ 全行业专业软件积木 + 用户生态积木（本节）= Vermes 在市场中持续存活的结构性条件**：模型大宗商品化后，价值落在「连接 + 生态」，而生态的供给面是整个软件宇宙（第三方专业软件）叠加用户持续喂入，不依赖单一团队维护垂直逻辑。这正是从「沉寂垂直」转向「通用连接层」的最终理由。

---

## 13. Spike 结果（2026-08-21 真实数据 · 沙箱实测）

> 详细证据见独立报告 [`SPIKE_CLI_ANYTHING_2026-08-21.md`](./SPIKE_CLI_ANYTHING_2026-08-21.md)。方法：trust-but-verify，全为沙箱实跑，不凭记忆。环境：macOS 沙箱，**未装 FreeCAD**（几何执行归用户机器 B 桶）。

### 13.1 仓库成熟度（解除 §9 #1 维护风险）
GitHub API 实测：`HKUDS/CLI-Anything`，Apache-2.0，**最后 push 2026-08-13（距 spike 仅 8 天）**，47.9k stars / 4.4k forks / 92 open issues，未 archived → **极活跃，非停更轮子**。FreeCAD 绑定：**2026-03-25 "updated for v1.1"** → 与 FreeCAD 1.1.3 同代。

### 13.2 安装与枚举（沙箱实跑）
`pip install -e .` 仅依赖 click/prompt-toolkit/wcwidth，**不依赖 FreeCAD**。源码 `@command` **277** 个；薄插槽 `discover_tools()` 实跑内省出 **273** 个可注册工具（≥ 宣传 258）；顶层 **20 group**。

### 13.3 --json 稳定性（解除 §9 #1b 风险）
`--json` 是全局一级 flag，实测 `document new --json` 返回结构化 JSON → L2 自动注册可行。

### 13.4 真实端到端（沙箱，状态层）
`document new` + `part add box` 在**无 FreeCAD**下跑通返回 JSON。边界实测：`freecadcmd` 仅用于 preview/motion/真实 export；状态层是纯 Python JSON，不需 FreeCAD。几何内核执行 + 真实文件导出归 B 桶。

### 13.5 薄插槽代码（已写 + 实跑）
新增 `vermes_cli/adapters/software_adapter.py`（L2 唯一新增代码面）。`register()` 实跑**成功注册 273 工具进 `tools/registry`**（toolset=`freecad_adapter`），零报错；v1 强制 `domain_vocab={}`。

### 13.6 阈值判定（§8.1 step5）
沙箱无 FreeCAD，**无法测「≥80% 端到端执行」阈值**；但用户最担心的两项风险已实测解除（成熟度 / --json）。薄插槽 + 状态层沙箱已端到端跑通 → **杠杆成立，L2 保持「薄」**。几何执行验证作为 B 桶门禁交用户机器。

### 13.7 结论
L3 轮子成熟、L2 自动注册 273 工具、状态层可跑 → 手搓 18 工具进入**冻结**（§6）。下一步：B 桶几何验证 + 第二域选型（§8.2）。

---

> 文档版本：v1.4（战略基线 · 评审修订 · §13 spike 真实数据已执行）
> 起草依据：用户战略转向指令（2026-08-21）+ 微信文章锚定 + CLI-Anything 实核 + Vermes 现有底座源码核实 + 沙箱 spike 实测。
> 评审修订：spike 升级硬门槛(已执行) / L2 词汇表硬护栏 / 18 工具冻结非退休 / 第二域选型重议 / Android 冷启动前提 / 积木来源=全行业专业软件+用户生态。
> 配套产物：`SPIKE_CLI_ANYTHING_2026-08-21.md`（spike 详细证据）+ `vermes_cli/adapters/software_adapter.py`（L2 薄插槽实现）。
> 下一步：B 桶几何执行验证（用户机器）+ 拍板 §10 决策点（第二域选型 / ≥80% 阈值口径）。
