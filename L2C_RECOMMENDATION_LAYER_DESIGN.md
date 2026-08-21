# L2c 推荐层设计（基线 v1.2）

> 状态：**基线 v1.2，P0 已实现（recommend.py + test_recommend.py 10 passed + 真实 cli-hub 端到端探针），待评审**。
> 依据：用户战略定调（2026-08-21）——Vermes 核心生存能力 = 动态认知记忆（知道什么最合适用户场景的技能/工具/插件/第三方垂直软件），非静态持有积木。L2c 是 L2（被动）与 L2a（路由）的上层建筑：从「有什么用什么」进化到「需要什么推荐什么」。
> 纪律：与 L2a/L2b 一致——**薄**。不做推荐算法，做 intent→catalog 最短路径映射；让用户搜、让用户选，我们只负责把「搜到→安装→可用」做到零摩擦。

---

## 0. 定位（一句话）

**L2c 只回答「还缺什么积木」，不回答「哪个积木最好」。** 前者是薄插槽的职责，后者是厚认知层（memory_fabric + 自进化 + 使用记录）的职责。

## 1. 分工铁律（薄插槽 × 厚认知）

| 层 | 职责 | 落点 |
|---|---|---|
| 薄插槽 L2c | 多源 catalog 抽象 + intent→catalog 最短路径映射 + 差集 + 安装链路 | `vermes_cli/adapters/recommend.py`（待建） |
| 厚认知（已有） | 记录「什么场景→什么工具最合适」，喂排序信号 | memory_fabric + 自进化 + 使用记录 + verified_rate |

**铁律**：L2c 不内置推荐算法、不内置「最合适」的判断。它只做映射 + 差集，排序信号通过 `rank_hook` 由认知层注入。这样「知道什么最合适」永远是 Vermes 自己进化的认知，而非写死在某个 catalog 里。

## 2. 多源 catalog 抽象

单一数据源不可靠（CLI-Anything 会变、可被替代；GitHub 持续推新技能/插件/工具/模块）。catalog 必须是**多源抽象**，绝不硬编码单一生态。

```python
class CatalogSource(Protocol):
    name: str                                  # "cli-anything-hub" / "vermes-module" / "skill-market"
    def list_entries(self) -> list[CatalogEntry]: ...
    def search(self, query: str) -> list[CatalogEntry]: ...  # 可选；无则 list 后本地过滤

class CatalogIndex:
    def add_source(self, source: CatalogSource) -> None: ...
    def all_entries(self) -> list[CatalogEntry]: ...          # 多源聚合 + 去重（按 software+harness）
    def search(self, intent_tokens: list[str]) -> list[CatalogEntry]: ...  # 倒排 + 双语桥接
```

- **默认源（P0）**：`CliAnythingHubSource` —— 调 `cli-hub list --json` / `cli-hub search <term> --json` 解析 JSON（**已核实**：101 entries，13 字段，含 name/display_name/version/description/requires/homepage/install_cmd/entry_point/skill_md/category/contributors/_source）。
- **可插源（P3）**：`ModuleCatalogSource`（Vermes `module_catalog.py`）、`SkillMarketSource`（技能市场）、`GitHubSearchSource`（GitHub 生态搜索）——复用已有，非新造。

## 3. 数据模型

```python
@dataclass
class CatalogEntry:
    name: str            # cli-hub 短名（install 用），如 "freecad" / "cc-switch"
    software: str        # 差集 key：从 entry_point 去 "cli-anything-" 前缀（与 bootstrap 对齐），非 name 直取
    harness: str         # entry_point 原始命令名，如 "cli-anything-freecad"
    domain: str          # "3d"（从 cli-hub JSON category 直取，35 分类）
    description: str     # 从 cli-hub JSON description 直取
    requires: str        # "FreeCAD >= 1.1"（从 cli-hub JSON requires 直取，本体依赖）
    keywords: list[str]  # 倒排匹配用（name + software + domain + description 分词派生）
    source: str          # "cli-anything-hub"
    install_cmd: str     # "cli-hub install <name>"（用短名 name，cli-hub 封装了 pip install）
    version: str         # 从 cli-hub JSON version 直取
    homepage: str        # 从 cli-hub JSON homepage 直取
```

**差集 key 边界 case（已核实）**：`name`（短名）≠ `entry_point` 去前缀的 harness 有 **16 个**（如 `cc-switch` → `cli-anything-ccswitch`、`sketch` → `sketch-cli`、`slay_the_spire_ii` → `cli-anything-sts2`、`1password-cli` → `op`）。其中 `sketch-cli`/`lark-cli`/`op`/`sentry-cli` 等 entry_point **不带 `cli-anything-` 前缀**，bootstrap 的 `_iter_cli_anything_bins`（只扫 `cli-anything-*`）扫不到它们——这是 bootstrap 扫描范围的已知边界（后续扩展扫描范围）。因此 `software`（差集 key）必须从 `entry_point` 去前缀派生，而非 `name` 直取，否则差集对不上。

**domain 分类**：cli-hub 有 **35 个 category**（3d/ai/audio/automation/communication/data-science/database/debugging/design/devops/devtools/diagrams/finance/game/gamedev/generation/graphics/image/knowledge/knowledge-management/mobile/music/network/office/osint/productivity/project-management/science/scientific/search/storage/streaming/testing/video/web）。P0 直接透传 cli-hub 原生 category（从 JSON 读 `category` 字段），不自己做映射——让上游分类说话。双语桥接在 route_toolset 层做（已有），L2c 层透传。

## 4. 接口契约

```python
@dataclass
class Recommendation:
    software: str
    domain: str
    reason: str             # "命中关键词：建模/倒角"
    matched_keywords: list[str]
    source: str
    score: float            # 排序分（默认=关键词命中数，认知层可覆盖）
    adapter_install: str
    backend_hint: str
    already_installed: bool # True = 已装，进「已装但可升级」提示

def recommend(
    intent: str,
    installed: set[str] | None = None,
    rank_hook: Callable[[list[CatalogEntry], dict], list[CatalogEntry]] | None = None,
) -> list[Recommendation]: ...

def install(rec: Recommendation) -> InstallResult:
    # 两步：① adapter（cli-hub install <software>，秒级，cli-hub 封装了 pip install）
    #       ② backend（ensure_*_ready 平台感知指引，本体缺失时提示安装）
    #       ③ 装后触发 bootstrap.discover_l2_adapters() 重新扫描注册
    ...
```

**差集**：`recommend` 的 `installed` 来自 `bootstrap.discover_l2_adapters()` 返回的 key 集合——已装的不再推荐（或标记 `already_installed`）。

## 5. 两步安装（诚实边界）

`cli-hub install` 装的是 **CLI 适配层，不装软件本体**（CLI-Anything 文档明确 "Zero Compromise Dependencies"：后端缺失时测试失败而非跳过）。价值链必须拆两步，否则误导小白：

```
"我要做注塑件"
  → L2a 无 freecad 适配器
  → recommend("我要做注塑件") → [freecad: "3D 建模通常需要 FreeCAD"]
  → install:
      ① cli-hub install freecad（秒级，cli-hub 封装 pip install）
      ② ensure_freecad_ready 平台感知指引（brew install --cask freecad / DMG / winget）
  → bootstrap.discover_l2_adapters() 扫到并注册 273 工具
  → 开箱即用
```

② 的链路 Vermes 已有（discovery-first 范式 `ensure_freecad_ready`），L2c 接进来即可，不新造。

## 6. "薄"的实现（复用 L2a 倒排）

L2c 不写推荐算法，复用 `route_toolset` 的 `_tokenize` + 双语桥接模式：

```
intent → _tokenize → 匹配 catalog entry 的 domain/keywords（子串 + 双语桥接）
      → 得分（关键词命中数 + domain 结构分）
      → 差集（减去 installed）
      → 排序（rank_hook 或默认关键词得分）
      → 输出推荐列表（受 MIN_SCORE 阈值约束，消化 argmax 无门槛反模式）
```

## 7. 认知层接入点（厚认知）

L2c 的 `rank_hook` 是唯一的认知信号入口。第一版默认 rank = 关键词命中数（朴素）；认知层（memory_fabric / 自进化 / usage / verified_rate）通过 `rank_hook` 注入「什么最合适」的信号，例如：

- 使用频率（某 software 适配器工具被调用的次数）
- 用户反馈（某 software 的 verified_rate / 显式点赞）
- 场景共现（「做注塑件」历史上下文里通常装 FreeCAD）

**边界**：这些信号的计算在认知层，不在 L2c；L2c 只消费 `rank_hook` 返回的排序结果。认知层越进化，推荐越准，而 L2c 本身保持薄、永不过时。

## 8. 与现有家底的关系

| 已有 | L2c 复用方式 |
|---|---|
| `route_toolset` 倒排 + 双语桥接 | recommend 的映射逻辑 |
| `bootstrap.discover_l2_adapters` | 装后触发 + 提供 installed 差集 |
| `ensure_freecad_ready` | 本体安装指引（第二步） |
| `module_catalog.py` / 技能市场 | 作为额外 CatalogSource（P3） |
| memory_fabric + 自进化 + usage | rank_hook 认知信号（P2） |

## 9. 开工顺序

- **P0（最小切片）** ✅：`CatalogIndex` 抽象 + `CliAnythingHubSource`（包 cli-hub list）+ `recommend`（倒排 + 差集）+ 单测。落点 `vermes_cli/adapters/recommend.py` + `tests/adapters/test_recommend.py`（10 passed + 真实 cli-hub 端到端探针）。
- **P1**：`install` 两步链路（adapter + backend 指引）+ 装后触发 bootstrap + 单测。
- **P2**：`rank_hook` 认知信号接入（usage/feedback）+ 单测。
- **P3**：多源（module_catalog / 技能市场）+ 前端推荐卡片 UI。

**已核实**（2026-08-21）：
- `cli-hub list --json` → 101 entries，13 字段（name/display_name/version/description/requires/homepage/source_url/install_cmd/entry_point/skill_md/category/contributors/_source）
- `cli-hub search <term> --json` → 过滤子集，同格式
- `cli-hub install <name>` → name 是短名（如 `freecad`），实际安装 `cli-anything-freecad`（与 bootstrap `_PREFIX` 对齐）
- `category` 35 个分类，远超 `DOMAIN_BILINGUAL_HINTS` 的 4 个
- `install_cmd` 是 `pip install git+URL` 格式（但 `cli-hub install` 封装了这步，L2c 用 `cli-hub install` 更简洁）
- `requires` 是本体依赖声明（如 "FreeCAD >= 1.1"），对应两步安装的第二步
