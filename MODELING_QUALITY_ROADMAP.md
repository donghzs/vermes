# Vermes 3D 模块 · 建模质量提升路线图 v2

> 状态：规划稿 v2（2026-08-17）｜ 触发：真机反馈「细节/纹理不专业」+ 战略讨论「直接桥接行业专业软件」
> 配套文档：[`PRO_TOOL_ADAPTER_DESIGN.md`](./PRO_TOOL_ADAPTER_DESIGN.md)（FreeCAD 嵌入技术设计，团队对齐用）
> 已修项：会话历史可删除（`web_server.py` DELETE 路由 + `ThreeDStudio.vue` 按钮）；prompt_toolkit 漏声明；STEP 上传/预览 f-string bug；两模块仓 README。

---

## 0. 一句话战略（v2 主轴变化）

**v1 思路**：纯 AI 一次出图 → 质量上限被「NL→CAD 黑盒」卡死，怎么调提示词都到不了专业级。
**v2 思路**：Vermes 不做「又一个 AI CAD」，而做**行业专业软件的 AI 编排层**——自然语言进来，Vermes（agent）驱动用户已经在用的专业软件（FreeCAD / Fusion / Blender …）干活。AI 降门槛，专业内核定天花板，人做最后 20% 裁决。

这把 Vermes 从「模型无关」自然扩展成「**模型无关 × 工具无关**」编排：专业软件 = agent 经脚本 API 调用的另一个后端。

---

## 1. 核心判断：为什么纯 AI 出图有天花板（保留，作为论证）

链路 `自然语言 → MAC 引擎(run_mac.py 黑盒) → build123d CSG B-rep → STEP/STL` 的硬约束：

| 能做（强项） | 做不了（天花板） |
|---|---|
| 功能机械件：笔筒/支架/法兰/齿轮 | 有机造型、生物/雕刻感 |
| 参数真可调、出工程图/BOM | 表面纹理（法线/凹凸贴图）——B-rep 无「贴图」概念 |
| 布尔/阵列/圆角（取决于 agent 翻得细） | 电影级细节、照片级渲染质感 |

**结论**：质量上限被 MAC 黑盒 + NL→CAD 拆解双重限定。纯改代码到不了专业级。**换「桥接专业工具」才破天花板**——因为底层是真实参数化内核，人和 AI 加的圆角/阵列/拔模/纹理都是真专业的。

---

## 2. 战略框架：ProToolAdapter（行业专业工具适配）

每个行业真正常用的专业软件就 2–3 个（机械：SolidWorks/Fusion/FreeCAD/CATIA；3D 美术：Blender/Maya/ZBrush；AEC：Revit/Rhino/SketchUp；PCB：KiCad/Altium）。所以**不铺 50 个集成，只做一个 `ProToolAdapter` 抽象 + 每行业一参考实现**：

- **统一接口**：`open(doc)` / `create_doc()` / `run_script(code, lang)` / `get_feature_tree()` / `apply_edit_op(op)` / `export(formats)` / `is_available()`
- **FreeCAD = 首个开源零授权参考后端**（Python 可脚本、原生 B-rep 参数化，契合现有 `subprocess` 模式）
- **SolidWorks/Fusion/Blender =「自带授权」后端**：用户有 license + 本机装了软件，Vermes 驱动其 API（Fusion Python / SolidWorks COM / Blender bpy）

**诚实坑（团队须知晓）**：各工具 API 不统一，适配器底层不可能 100% 一致；但**「编辑操作词汇表」可跨工具归一**——`fillet/draft/pattern/boolean/scale/split` 这套 op，每个工具都有对应原语，Vermes 翻成各 native 调用。用户与 AI 点同一套语义，底层谁执行无所谓。

> 详细接口契约、FreeCAD headless 桥协议、特征树存储、与现有代码衔接点见 `PRO_TOOL_ADAPTER_DESIGN.md`。

---

## 3. Track 划分（v2 重排）

### Track D（NEW · 主路径）：ProToolAdapter + FreeCAD M1 — 决定「能否达专业级」
- **D1 FreeCAD headless 桥**：常驻 `vermes_freecad_bridge.py`（stdio/socket 收 edit-op JSON → 驱动 FreeCAD API → 回特征树 JSON + 导出 mesh）。复用现有 `subprocess` 引擎模式。
- **D2 STEP 进 FreeCAD 特征树**：`mfg_text_to_cad` 产出的 STEP 作为 base body 导入 FreeCAD PartDesign Body，成为可编辑特征。
- **D3 编辑操作词汇表落地**：`fillet/draft/pattern/boolean/scale/split` 在 FreeCAD 上的翻译表 + 前端编辑面板触发。
- **D4 会话=特征树**：把现有「孤儿 JSON session」升级为「特征树 + 原生 .FCStd 源文件」——源文件即真相源，操作日志可回滚，堆积从缺点变资产（顺带根治历史 bug）。
- **D5 引擎分发**：FreeCAD 作重资产模块，经 P6/P7 按需下载（`ensure_freecad_ready(auto_setup)` 复用 `engine_setup.py` 模式）。
- **D6 高级模式**：「在 FreeCAD 中打开」按钮，detached 拉起真实 FreeCAD GUI 交给专业用户。

落点（非重写，是增量）：`vermes_cli/mfgcad/backends/freecad_adapter.py` + `vermes_freecad_bridge.py`；`engine_setup.py` 加 `ensure_freecad_ready`；`web_server.py` 加 `POST /api/mfgcad/edit` + `GET .../feature-tree`；`ThreeDStudio.vue` 编辑面板 + 打开按钮；`toolsets.py` 加 `mfg_open_in_freecad`/`mfg_edit_feature`/`mfg_export_fcstd`；`vermes-mod-freecad-engine` 发 P7 catalog。
周期估计：MVP（D1+D2+D3 单文件闭环）~1–2 周；D4–D6 ~再加 1–2 周。

### Track A（降级为过渡快赢）：NL→CAD 拆解增强 — 不换引擎，给 base body 提质感
- A1 自动圆角/倒角/拔模；A2 阵列/镜像/布尔精细化；A4 草图反推；A5 QA 评测闭环。
- **定位变化**：不再是「达专业级」的主路径，而是给 FreeCAD 当 base body 的「快速粗模」提质感。A1+A2 改提示词 1–2 天即可。
- 保留：作为「无 FreeCAD 时的轻量兜底」，让没装专业软件的小白也有更细的玩具级输出。

### Track B：有机造型 + 表面纹理（Blender/Trellis 层）— 大工程
- B1 启用 Trellis 后端（text-to-3D 扩散，当前零可跑）；B2 图生纹理贴已有 mesh；B3 mesh 后处理（水密/重拓扑/减面）。
- 与 D 互补不替代：FreeCAD 管制造级 B-rep，Blender/Trellis 管可视化级有机/纹理。Round-trip B-rep→mesh→B-rep 有损，制造链以 FreeCAD 为真。

### Track C：渲染质感与质检（体验层）— 立竿见影
- C1 PBR 材质预览（金属/塑料预设）；C2 可打印性报告增强；C3 网格质量门禁。纯前端/轻后端，C1 1 天可见效。

---

## 4. 里程碑（v2 重排）

| 阶段 | 内容 | 周期 | 产出 |
|---|---|---|---|
| M0（快赢·不阻塞） | A1+A2 提示词精细化 + C1 PBR 预览 | ~3 天 | 轻量兜底更细、预览更专业 |
| **M1（主路径 MVP）** | **D1+D2+D3：FreeCAD headless 桥 + STEP 进特征树 + 编辑面板** | **~1–2 周** | **AI/人共编真专业件，达工程级** |
| M2 | D4 会话=特征树 + D6 高级模式打开 GUI | ~1–2 周 | 可回滚工程历史 + 专业用户全 GUI |
| M3 | D5 引擎按需分发（P6/P7） | ~数天 | FreeCAD 不塞基础 DMG，按需下载 |
| M4 | B1 Trellis 真机 | 数周 | 有机/纹理成为可能 |
| M5 | 第二后端（Blender bpy / Fusion BYO） | 按需求 | 跨工具编排 |

**诚实建议**：M0 先发（零风险、肉眼可见），同时开 M1。M1 是「达专业级」的真正钥匙，应优先投入。B 轨不阻塞 M0/M1。

---

## 5. 明确不做（MVP 边界，防 scope 膨胀）
- ❌ **不把 FreeCAD 完整 Qt GUI 嵌进 Electron**（进程隔离/包体爆炸大坑）。MVP = headless 内核 + 受控编辑面板；完整 GUI 走「高级模式」detached 拉起。
- ❌ 不为每个行业一次性接 50 个工具——只做 `ProToolAdapter` 抽象 + FreeCAD 参考实现，其余按需。
- ❌ 不做 B-rep→mesh→B-rep 制造级往返（有损）。FreeCAD 制造真，Blender 仅可视化。
- ❌ 不捆绑 SolidWorks/Fusion 等授权软件——只做「自带授权」后端接口，用户自备。

---

## 6. 验证方式（每步真机闭环，沿用纪律）
- D 轨：装 FreeCAD → `ensure_freecad_ready(auto_setup)` 建引擎 → `mfg_text_to_cad` 出 STEP → 导入 FreeCAD 特征树 → 前端加 2mm 圆角（edit-op）→ 导出 STEP/STL → 比对特征树节点。
- 历史根治：连续开 5 个文件 → 特征树列表 5 条 → 删 2 条 → 剩 3 条且 .FCStd 同步删。
- 回归：项目 `.venv` 跑 `tests/mfgcad/ -p no:xdist -o addopts=""`（sandbox 托管 venv 缺 pytest-asyncio，勿用）。

## 7. 给团队的决策清单
1. FreeCAD 作首个参考后端是否同意？（开源零授权，风险最低）
2. MVP 是否接受「headless 内核 + 受控编辑面板」，暂不做完整 GUI 嵌入？
3. 第二后端优先级：Blender（纹理/有机）vs Fusion（机械主流）vs SolidWorks（机械主流）？
4. FreeCAD 引擎分发走 P6/P7 按需下载，还是先本地集成验证？

---

## 8. 商业闭环：制造业模具变现（首个变现垂类 · 决策已定）

- **首个变现点 = 制造业模具/注塑行业**（用户自有注塑厂 + 模具产业链）。3D 模块不只作玩具，而是**建模制图服务收费 + 产品自用 + 硬核垂直能力打开 Vermes 垂直市场**的三合一。
- **差异化壁垒**：此层（制造级 agent 建模/制图交付）几乎无人做 —— 是 Vermes 的垂直护城河。
- **反向锁定 FreeCAD 为变现后端**：模具需要制造级 B-rep（分型/拔模/收缩率/壁厚/公差/BOM），FreeCAD PartDesign 正好覆盖；Blender 只补有机/纹理展示层，不进制造链。
- **质量标尺 = mold-ready**（非「看起来专业」）：出图须含拔模角≥下限、无侧凹/或带侧抽、壁厚在料厚区间、按材料收缩率补偿（ABS~1.005 / POM~1.020 等）、可出 2D 工程图 + BOM。M1 的 QA 闭环须含这些模具专项检查。
- **对 Track 的影响**：Track D 的编辑操作词汇表 + QA 须加模具专属项（见 `PRO_TOOL_ADAPTER_DESIGN.md` §12）；变现 aperture = 「交付包」（STEP + 2D 图 + BOM + 模具规格）作为可计费交付物，契合 Vermes「垂直交付 agent」定位（交付物非对话）。

## 9. 范围取舍：深耕制造业闭环，不进影视 3D 建模赛道（战略锁定 · 2026-08-17）

- **不进入视频/影视 3D 建模赛道**：非自有行业，存在入行壁垒（客户群/审美/工业管线），容易白做工、陷入画面军备竞赛，且无法复用制造业资产。Vermes 的 3D 能力**只服务于制造业闭环**，不为影视内容生产铺独立赛道。
- **制造业闭环 = 飞轮（竞品不可复制的护城河）**：
  自建模 → 出样品 → 开模具 → 量产（自有注塑厂）→ 自研产品，后期甚至延伸至 **CNC 刀路编程**（制造链路下游）。
  真客户订单驱动模型产出 → 出厂实物反馈反哺系统精度 → 系统越用越准 → 拿更多订单。这条飞轮依赖「自有工厂」实体资产，纯软件 agent 玩家无法复制 = **结构性壁垒**，非技术壁垒可比。
- **对 Track 的硬性约束**：
  - Track B（Blender/Trellis 有机+纹理）保留为**制造件的可视化/有机补充**（产品外壳有机造型、展示渲染），但**不作为独立营收赛道**与影视竞争；
  - 所有 3D 投入的 **ROI 标尺统一为「能否缩短 建模→样品→模具→量产 周期、降低模具试错成本」**，而非「画面多炫」；
  - 潜在延伸点列入路线图候补：**CNC 编程**作制造链路下游新垂直能力，与模具闭环同源，优先级高于影视。
