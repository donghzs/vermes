# ProToolAdapter 技术设计 · FreeCAD 嵌入参考实现

> 配套：[`MODELING_QUALITY_ROADMAP.md`](./MODELING_QUALITY_ROADMAP.md) v2（战略/排序）
> 目标：把 Vermes 3D 从「纯 AI 一次出图」升级为「AI 编排层 × 行业专业软件」，FreeCAD 作首个开源参考后端。
> 读者：Vermes 团队 + `run_mac.py` 引擎作者（黑盒对齐用）。

---

## 1. 目标与范围（MVP）

**做**：在现有 `mfg_text_to_cad` 产出（STEP/STL）之上，加一层 **FreeCAD headless 内核**，使 AI 与用户能对模型做**专业级参数化精修**（圆角/拔模/阵列/布尔），并把「查看历史」升级为**可回滚的特征树**（原生 .FCStd 为真相源）。

**不做（MVP 边界）**：
- 不嵌 FreeCAD 完整 Qt GUI 进 Electron（大坑）。
- 不接 SolidWorks/Fusion 等授权软件（仅留 BYO 接口）。
- 不做 B-rep↔mesh 制造级往返。

---

## 2. 架构总览

```
自然语言
   │
   ▼
Vermes Agent (LLM, 模型无关)
   │  tool_call: mfg_text_to_cad / mfg_edit_feature / mfg_open_in_freecad
   ▼
web_server.py  (Electron 主进程内 FastAPI)
   │  POST /api/mfgcad/edit  {session_id, op}
   │  GET  /api/mfgcad/sessions/{id}/feature-tree
   ▼
ProToolAdapter (abc)  ── FreeCADAdapter (参考实现)
   │                      │  JSON over stdio/socket
   ▼                      ▼
            vermes_freecad_bridge.py  (常驻 headless FreeCAD 子进程)
                              │  import FreeCAD, Part, PartDesign, Mesh
                              ▼
                    原生 .FCStd 文档 (真相源)  + 导出的 STEP/STL
                              │
                              ▼
            ThreeDStudio.vue 查看器 + 编辑面板 (fillet 滑块/阵列/布尔)
```

**关键点**：FreeCAD 跑在**独立 headless 子进程**（bridge），不在 Electron 主进程、不在 GUI 线程。前端只发 edit-op JSON、收特征树 JSON + mesh 路径。这正是现有 `engine_setup.py` 里 `subprocess.run(build123d...)` 模式的延伸。

---

## 3. ProToolAdapter 接口契约（Python abc）

```python
# vermes_cli/mfgcad/backends/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

@dataclass
class FeatureNode:
    id: str                 # 稳定节点 id（session 内唯一）
    kind: str               # "body"|"fillet"|"draft"|"pattern"|"boolean"|"sketch"|...
    label: str
    params: dict[str, Any] = field(default_factory=dict)
    children: list["FeatureNode"] = field(default_factory=list)

@dataclass
class EditOp:
    op: str                 # fillet|draft|pattern|boolean|scale|split|...
    target: str             # "edges_all"|"edge:<id>"|"face:<id>"|"body:<id>"|"tool:<id>"
    params: dict[str, Any] = field(default_factory=dict)

@dataclass
class AdapterResult:
    ok: bool
    feature_tree: Optional[list[FeatureNode]] = None
    native_doc: Optional[Path] = None      # .FCStd
    exports: dict[str, Path] = field(default_factory=dict)  # {"stl":..., "step":...}
    error: Optional[str] = None

class ProToolAdapter(ABC):
    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool: ...
    @abstractmethod
    def ensure_ready(self, auto_setup: bool = False) -> bool: ...
    @abstractmethod
    def create_doc(self, session_id: str) -> Path: ...
    @abstractmethod
    def open(self, doc_path: str) -> bool: ...
    @abstractmethod
    def import_step(self, session_id: str, step_path: str) -> AdapterResult: ...
    @abstractmethod
    def get_feature_tree(self, session_id: str) -> list[FeatureNode]: ...
    @abstractmethod
    def apply_edit_op(self, session_id: str, op: EditOp) -> AdapterResult: ...
    @abstractmethod
    def export(self, session_id: str, formats: list[str]) -> dict[str, Path]: ...
    @abstractmethod
    def close(self, session_id: str) -> None: ...
```

---

## 4. FreeCADAdapter + headless bridge 设计

### 4.1 桥进程 `vermes_freecad_bridge.py`
- 启动方式：`freecadcmd -c vermes_freecad_bridge.py`（FreeCAD 自带无 GUI 解释器 `freecadcmd`，比 `python -c import FreeCAD` 更稳，避免导入顺序坑）。
- 协议：从 stdin 读一行 JSON 请求 `{cmd, session_id, payload}`；写一行 JSON 响应。长驻、按 session_id 维护 `App.Document` 句柄表。**可选**升级为本地 Unix socket（多并发），MVP 用 stdin/stdout 单工足够。
- 重入安全：`bridge` 由 `web_server` 懒启动（首 edit-op 时），`ensure_freecad_ready` 失败则返回「引擎未就绪」给前端提示去装。

### 4.2 STEP 进特征树（D2）
```python
import Import, Part, PartDesign
doc = App.newDocument(session_id)
Import.insert(step_path, doc.Name)          # STEP → Part.Shape
body = doc.addObject("PartDesign::Body", "BaseBody")
body.BaseFeature = doc.Objects[-1]           # 包成 PartDesign Body，成为可编辑特征
doc.recompute()
```
导出：`import Mesh; Mesh.export([body.Shape], stl_path)`；STEP 重导出：`Import.export([body], step_path)`。

### 4.3 编辑操作词汇表 → FreeCAD 原语（D3 翻译表）
| EditOp | FreeCAD 实现 | 备注 |
|---|---|---|
| `fillet` target=edges_all, radius | `body.addObject("PartDesign::Fillet", "Fillet")` + `Fillet.Radius=radius` + `Fillet.Base=body` + `Fillet.Edges=[(edge_i, radius)...]` | 全边圆角需枚举 `body.Shape.Edges` |
| `draft` face, angle | `PartDesign::Draft` | 需中性面/拔模方向 |
| `pattern` linear, count, dist | `PartDesign::LinearPattern` | 基于某特征 |
| `pattern` circular, count | `PartDesign::PolarPattern` | |
| `boolean` cut/fuse, tool | `PartDesign::Boolean` (Cut/Fuse/Common) | tool=另一 body |
| `scale` factor | `Part::Scale` 或 `body.Shape.scale` | |
| `split` | `PartDesign::Slice`/`Boolean` | |

> 每个 op 翻译成一段 FreeCAD Python，经 bridge 执行。`apply_edit_op` 返回更新后的特征树，前端刷新面板。

### 4.4 特征树提取（D4）
遍历 `doc.Objects` → 对每个 `PartDesign::*`/特征对象取 `Name`/`Label`/`Type` + 关键 `params`（Fillet.Radius 等）→ 序列化为 `FeatureNode` 列表。前端据此渲染可点击/可改的树。

---

## 5. 会话 = 特征树（根治历史 bug，D4）

**现状**：`POST /api/mfgcad/upload` 每次无脑新建 `sessions/<sid>/session.json` 孤儿文件，无删除（已修 DELETE 但仍是静态记录）。
**目标**：
- `sessions/<sid>/` 下存 `native.f cstd`（真相源，由 bridge 写回）+ `feature_tree.json`（Vermes 维护的可回滚操作日志）+ `meta.json`。
- 删除 session ⇒ 同步删 `.FCStd` + `output/<sid>`（复用已写的 `_remove_mfgcad_session`）。
- 「重新编辑」= 重新 `open(.FCStd)` → 追加 edit-op，特征树天然可回滚（FreeCAD 特征树本身可抑制某节点）。
- 堆积从缺点变资产：这是专业人员的工程历史，本就该留；提供「清空全部」+ 单条删除（已做）。

---

## 6. 前端（ThreeDStudio.vue）

- 编辑面板（新增右/左侧栏）：fillet 半径滑块、阵列数量/间距、布尔类型下拉、scale 因子——每个控件 `@change` → `POST /api/mfgcad/edit` → 刷新特征树 + 查看器。
- 「在 FreeCAD 中打开」按钮（高级模式，D6）：detached 拉起 `freecad <doc.f cstd>`，用户得完整专业 GUI；Vermes 仅交还文件。
- 复用现有 🗑 删除 + 清空（已落地）；删除时 confirm 提示「将同时删除 .FCStd 源文件」。

---

## 7. 引擎接入（discovery-first · 取代原 §7 分发方案）

> **范式变更（commit `47825b405`）**：原「~2.5GB 重资产分发」方案已废弃。改为 **discovery-first**——用户自装 FreeCAD（成熟开源软件，易获取），Vermes 自动发现并连接，**不做自动下载分发**。此范式同时确立未来 ProToolAdapter 后端（SolidWorks/Fusion/Blender/Catia）的统一接入方式：用户自装，Vermes 发现。

FreeCAD 发现路径（`engine_setup._find_freecadcmd`，按优先级）：
1. 引擎目录 `~/.vermes/engines/freecad/freecadcmd`（用户手动放入，可选）
2. 系统常见路径：macOS `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`、Linux `/usr/bin/freecadcmd`、Windows `C:\Program Files\FreeCAD*\bin\freecadcmd.exe`
3. `PATH` 查找（`shutil.which("freecadcmd")`）

缺失时 `ensure_freecad_ready` 返回**平台感知安装指引**（macOS `brew install --cask freecad` / DMG；Windows winget / 安装包；Linux `apt`/`dnf`/AppImage），不尝试下载。`auto_setup` 参数保留但已 deprecated（ignored）。

---

## 8. 与现有代码的衔接（增量，非重写）

| 文件 | 改动 |
|---|---|
| `vermes_cli/mfgcad/backends/base.py` | **新增** `ProToolAdapter` abc + dataclasses |
| `vermes_cli/mfgcad/backends/freecad_adapter.py` | **新增** FreeCAD 参考实现 |
| `vermes_cli/mfgcad/vermes_freecad_bridge.py` | **新增** headless 桥进程 |
| `vermes_cli/mfgcad/engine_setup.py` | **加** `ensure_freecad_ready()`（复用 provision 模式）✅ M1-4 已落地 `c5a244b6b` |
| `vermes_cli/web_server.py` | **加** `POST /api/mfgcad/edit`、`GET /api/mfgcad/sessions/{id}/feature-tree`；复用 `_remove_mfgcad_session` |
| `frontend/src/components/ThreeDStudio.vue` | **加** 编辑面板 + 「在 FreeCAD 打开」按钮 |
| `vermes_cli/mfgcad/tools.py` | **加** `mfg_open_in_freecad`/`mfg_edit_feature`/`mfg_export_fcstd`（与既有 18 工具同文件注册，无独立 toolsets.py） |
| `vermes_cli/mfgcad/tools.py` | **加** 上述 handler（agent 也能调 edit-op） |
| `vermes-mod-freecad-engine/`（**已废弃 · `47825b405` 清理**） | M1-5 曾建重资产模块仓 + P7 catalog 条目做 ~2.5GB 自动分发；discovery-first 范式下该分发路径废弃，模块仓与 catalog 条目已删除（避免空 url 条目致模块商店「安装」报「资产没有 url」）。FreeCAD 改为用户自装 + Vermes 自动发现（见 §7）。 |

> 现有 `mfg_text_to_cad` / build123d / MAC 后端**全部保留**作「快速粗模」兜底（Track A）；FreeCAD 是「专业精修」叠加层。

---

## 9. 风险与缓解

| 风险 | 缓解 |
|---|---|
| FreeCAD headless PartDesign 某些操作需 recompute / 有版本差异（0.21 vs 1.1） | 用户自装 FreeCAD（发现路径见 §7），每个 edit-op 后 `doc.recompute()`；版本差异由 `_infer_kind`/几何取形兜底（见 §13.4 H7） |
| `freecadcmd` 启动形式坑 | FreeCAD 1.1 把脚本路径当**模块导入**而非执行，故 bridge 用 `freecadcmd -c "exec(open(bridge, encoding='utf-8').read())"` 强制脚本执行；旧版/CLI-Anything `run_macro` 用 `freecadcmd <脚本>` argv 透传。统一**不用**裸 `python -c import FreeCAD`。另设 `PYTHONUTF8=1` 防中文注释 ascii 报错（M1-6 真机，见 §13.4 H5） |
| FreeCAD 体量大、首装慢 | **discovery-first**：用户自装（brew/DMG/winget/apt/dnf），Vermes 自动发现；不做分发下载（见 §7 范式变更 `47825b405`） |
| 编辑 op 在 FreeCAD 失败（几何非法） | `apply_edit_op` 返回 `ok=False` + error；前端标红该节点，不破坏已有树 |
| 用户机器无 FreeCAD（BYO 场景） | `is_available()` 返回 False → 前端提示装引擎或走 build123d 兜底 |
| 特征树 JSON 与 .FCStd 不一致 | 以 .FCStd 为真，会话加载时 bridge 重新提取特征树覆盖 JSON |

---

## 10. M1 原型验证计划（1–2 周，给团队排期）

1. **环境**：本机装 FreeCAD（锁 1.0），验证 `freecadcmd <脚本>` 可 `import Part, PartDesign, Mesh`（脚本文件 argv 透传，非 `-c`）。
2. **bridge PoC**：`vermes_freecad_bridge.py` 读 stdin JSON → `import_step` → 回特征树 JSON；手工喂一个现有 STEP（如 leaf_texture_v3.step）跑通。
3. **edit-op PoC**：发 `{op:"fillet", target:"edges_all", radius:2.0}` → 回更新特征树 + 导出 STL；肉眼/QA 比对圆角生效。
4. **接线 web_server**：`POST /api/mfgcad/edit` + `GET .../feature-tree`；用 curl 跑通端到端（不依赖前端）。
5. **前端面板**：ThreeDStudio 加 fillet 滑块 → 真机点一下出圆角。
6. **历史根治验证**：连开 5 文件→删 2→剩 3 且 .FCStd 同步删。
7. ~~**引擎分发 PoC**~~（已废弃）：原「`ensure_freecad_ready` 从模块下载」方案被 discovery-first 取代（`47825b405`），不再做分发。改为验证用户自装 FreeCAD 后 `is_available()` 自动发现。

> 每个 PoC 都**真机跑**，不靠单测断言（参考本轮 STEP f-string bug 教训：动态脚本 bug 单测覆盖不到）。

## 11. 给引擎作者（run_mac.py）的对齐问题
- 黑盒现在吐 build123d 脚本；是否愿意增加「吐 FreeCAD API 调用」分支（或 Vermes 直接走 `freecad_adapter` 不经黑盒）？
- base body 的 STEP 由谁产：继续黑盒 build123d 出 STEP，还是黑盒直接出 FreeCAD 脚本？建议前者（解耦、复用已验证出图链路）。
- 特征树节点 id 稳定性：agent 多轮编辑需稳定 id，由 bridge 分配并回写。

---

## 12. 制造业模具变现：mold-ready 专属操作（FreeCAD 后端 · 变现关键路径）

首个变现垂类 = 模具/注塑（用户自有产业链），故 FreeCAD 后端不是「第一个开源后端」而是**变现后端**。在 §4.3 通用词汇表上增补模具专属 EditOp：

| EditOp | FreeCAD 实现 | 模具意义 |
|---|---|---|
| `draft` face, angle, direction | `PartDesign::Draft`（中性面+方向） | 出模角，防卡模 |
| `scale` factor=收缩率 | `Part::Scale` 或 `body.Shape.scale` | 按材料收缩率补偿（ABS~1.005 / POM~1.020） |
| `split`/parting | `PartDesign::Boolean` 或 `Part::Slice` 沿分型面切 | 分型/分模，求前后模 |
| `wall_thick_check` | 遍历面算法 / 第三方 | 壁厚在区间内（防缩水/欠注） |
| `undercut_check` | 拔模方向投影 | 有侧凹 → 需侧抽/行位 |
| `boss`/`rib` | `PartDesign::Pad` / `Additive` | 加强筋/司筒柱 |

**QA 闭环（M1 起须含模具专项）**：拔模角≥下限、无未处理侧凹、壁厚达标、收缩率已补偿、可出 2D 工程图 + BOM。交付物 = 「STEP + 2D 图 + BOM + 模具规格」包，作为可计费服务交付物（契合 Vermes 垂直交付 agent 定位）。

---

## 13. 外部参考：CLI-Anything (HKUDS) 对照与 M1-6 hardening

> 2026-08-19 用户分享，已 web 源码级核实（非二手描述）。定位：CLI-Anything 是「任意 GUI 软件 → Agent 原生 CLI」的**横向工具生成器**，与我们的垂直制造业壁垒互补，非竞争。

### 13.1 核实事实
- 真仓库 `HKUDS/CLI-Anything`（用户初给 `cli-anything/cli-anything` 为 **404**）。HKUDS = 香港大学数据智能实验室。
- 许可证 **Apache 2.0**（仓库 badge + 页脚核实；网上有二手文误写 MIT，已排除）。Star 数网上乱写（34k/31.5k/2k 皆有），仓库页未显示确切值，**不当真**。
- **已原生覆盖 FreeCAD**：`cli-anything-freecad` = 258 命令 / 17 组，覆盖全工作台（Part/Sketcher/PartDesign/Assembly/Mesh/TechDraw/Draft/FEM/CAM-CNC/Surface/Spreadsheet/Import/Export/Measure/Materials），无头导出 STEP/IGES/STL/OBJ/DXF/PDF/glTF/3MF；前置 `freecadcmd` 在 PATH（与 `engine_setup.get_freecad_engine_dir` / `_locate_freecadcmd` 查找点一致）。

### 13.2 架构对照（我们的 M1 ↔ CLI-Anything）
| 我们 M1 组件 | CLI-Anything 等价物 |
|---|---|
| `ProToolAdapter`(base.py 抽象契约) | 生成的 Click CLI 契约（每软件一套） |
| `vermes_freecad_bridge.py`(持久无头子进程 + stdin JSON 行) | `cli-anything-freecad` + `<software>_backend.py`(每命令一次性 macro：temp .py → `freecadcmd <script>`) |
| `FreeCADAdapter`(传输/解析，不 import FreeCAD) | CLI 调用层(`subprocess.run`) |
| `EditOp` 词表(fillet/draft/pattern/boolean/scale/split) | Part/PartDesign 命令组（258 条，广得多） |
| `.FCStd` = 会话真相源 + 可回滚（§5） | `--json -p proj.json` 项目文件（JSON 状态持久化，无状态每命令） |
| **mold-ready 领域校验（拔模/分型/收缩率/壁厚/公差/BOM）** | **没有**（纯横向工具） |
| P7 catalog + `ensure_freecad_ready`（重引擎分发） | `cli-hub install freecad`（它的包管理器，独立想到一块） |

### 13.3 结论
- **非威胁，是互补**：它横向生成工具，我们壁垒在垂直（mold-ready + 自有注塑厂飞轮）。连「重引擎分发」都和 P7 独立想到了一块。
- 保留已锁定的 M1-1~M1-5 手搓桥（领域逻辑坐其上），把 CLI-Anything 当 M1-6 无头传输层 hardening 参考，**不替换为它的 harness**（避免返工 + 它在我们的垂直层之上无增量）。
- **设计验证**：CLI-Anything 用「每命令一次性 macro + `proj.json` 文件态」；我们是**持久桥进程 + .FCStd 会话真相源**。持久桥更契合 §5「会话=特征树 + 可回滚」，故 M1-6 保持持久桥设计。

### 13.4 M1-6 hardening 清单（源自 `cli-anything-freecad/utils/freecad_backend.py` 实战模式 + 本机 FreeCAD 1.1 真机验证）
- **H1 macOS 引擎发现（✅ 已在 M1-6 真机实施）**：`freecad_adapter._locate_freecadcmd()` 与 `engine_setup._find_freecadcmd()` 已增补回退路径 `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd` 与 `/opt/homebrew/opt/freecad/libexec/bin/freecadcmd`（FreeCAD 1.1 .app 实际位置，**注意不是** `Contents/MacOS/FreeCADCmd`——CLI-Anything `find_freecad` 写的 `Contents/MacOS` 路径与实测不符，以本机实测为准）。支持 `FREECAD_PATH` 环境变量覆盖。
- **H2 导出后文件存在性校验**：`vermes_freecad_bridge._export` 导出后 `assert os.path.isfile(out)`，缺失则 `ok=False`（CLI-Anything `export_headless` 同律：**「不要因为退出码 0 就信导出成功」**——对齐用户「测试全绿≠功能可用」纪律）。
- **H3 子进程超时**：bridge 调 freecadcmd 加 `timeout`（CLI-Anything 默认 120s），防几何卡死挂起。
- **H4 错误归一化**：freecadcmd 缺失/超时返回 `{ok:False, ...}` 而非抛（CLI-Anything `_run` 同律），已被 `adapter.is_available()` 优雅降级吸收。
- **H5 launch 形式（✅ 已实施，重要版本坑）**：FreeCAD 1.1 的 `freecadcmd` 把脚本路径当**模块导入**而非执行，故 `_start_bridge` 改用 `[cmd, "-c", "exec(open(bridge, encoding='utf-8').read())"]`（强制以脚本执行）。⚠️ **纠正前文**：§9 / §10 / 本§13.2 写的「freecadcmd argv 透传非 `-c`」在 1.1 不成立——CLI-Anything `run_macro([freecad, script])` 的 argv 形式在 1.1 可能不执行，须按版本适配。另设 `PYTHONUTF8=1` 解决 FreeCAD 内置 Python 默认 ascii 读不了中文注释的报错；`VERMES_MFG_SESSIONS_DIR` 经 env 传入替 `--sessions-dir` argv（1.1 不吃该 argv）。
- **H6 `_bridge_script()` 路径 bug（✅ 已修复）**：旧 `Path(__file__).with_name("vermes_freecad_bridge.py")` 在 `backends/` 下找 bridge（文件实际在 `mfgcad/`），M1-6 会直接找不到桥；已改为 `Path(__file__).resolve().parent.parent / "vermes_freecad_bridge.py"`。
- **H7 fillet 几何取形（✅ 已修复）**：FreeCAD 1.1 的 `PartDesign::Body.Shape` 返回 `PartDesign.Feature` 而非 `TopoDS Shape`，`fillet` 直接 `body.Shape.Edges` 拿空几何；已改为优先 `BaseFeature.Shape` → 回退 `TipShape` → 再 `body.Shape`（`vermes_freecad_bridge._apply_edit_op`）。
- 以上 H1–H7 中带 ✅ 的已在 M1-6 真机落地（本回合一并提交）；H2/H3 为后续待补的强度项。
- **H8 fillet 节点 kind 标签（✅ 已修复·P3, commit `fa34c01f5`）**：QClaw 的 1.1.3 修复把 fillet 产出从 `PartDesign::Fillet` 改为 `Part::Feature`（绕 DAG cycle + Tip shape empty），`_KIND_MAP` 只认 `PartDesign::Fillet → "fillet"` 会漏标。P3 修复：新增 `_infer_kind()` 按 `Label` 语义推断 `Part::Feature` 的 kind（fillet/chamfer/draft/pattern/boolean/scale/split）；全局 `_FEATURE_PARAMS` 存几何操作参数（`FreeCAD` 对象不支持自定义 `setattr`）；`_extract_params` 从全局 dict 取参展平。**已双端真机验证**：① 本沙箱 FreeCAD 1.1 独立复跑核心链路，fillet 节点实测 `('Fillet','fillet',{'radius':2.0})`——kind=fillet 且 params.radius=2.0 正确提取；② 用户 web 端 `POST /api/mfgcad/edit (fillet r=1.5)` → 13 节点，`Fillet001 kind=fillet params={radius:1.5}`。274 passed 零回归。
- **H9 `_FEATURE_PARAMS` 进程内状态限制（⚠️ 已知·P3 遗留）**：`_FEATURE_PARAMS` 是 **bridge 进程内** 全局 dict，bridge 子进程**重启后**历史 fillet 的 params 丢失（表现为旧 fillet 节点 params 为空，新 fillet 正常）。不影响功能（仍凭 Label 锚定），但 `get_feature_tree` 在重启后读旧会话会丢参数。⚠️ **推荐根治**：把 params 持久化进 `.FCStd` 自定义属性（`obj.addProperty("App::PropertyString","VermesMeta")` 存 JSON），使其随会话真相源落地、跨重启/重开可读；届时可删掉桥内全局 dict。归为 P3/P4 增强，非阻塞。
- 以上 H1–H9 中带 ✅ 的已在 M1-6 真机落地；H2/H3 为后续待补强度项；H9 为已知限制 + 推荐根治。

### 13.5 下一个后端策略（最高 ROI 点）
- 我们 v2 战略要接 Blender/Fusion/SolidWorks。CLI-Anything 已有 `cli-anything-blender`(208 命令) 等现成 harness —— 届时**直接当 `ProToolAdapter` 后端**，省掉手搓桥成本。即「工具无关」扩张走「现成 harness 适配」而非「从零写桥」。

### 13.6 「比 HuggingFace Agents 更适合跨软件流程」纠正
- HF Agents 是 **agent 运行时（编排器）**，CLI-Anything 是**工具生成器**，两层东西。准确说法：接任意 GUI 软件时比手搓 HF tool wrapper 省力，互补非替代。
