# Vermes 3D 模块 · 建模质量提升路线图

> 状态：规划稿（2026-08-17）｜ 触发：真机测试反馈「细节/纹理不专业，未达生产级」
> 配套已修项：设计会话历史可删除（`DELETE /api/mfgcad/sessions/{sid}` + 清空全部 + 前端按钮，见 `web_server.py` / `ThreeDStudio.vue`）

---

## 0. 先校准预期：当前能力的真实天花板

当前链路 = `自然语言 → MAC 引擎(run_mac.py 外部黑盒) → build123d 参数化 CAD 脚本 → STEP/STL`。
这个范式的硬约束：

| 能做（强项） | 做不了（天花板） |
|---|---|
| 功能机械件：笔筒/支架/法兰/齿轮/外壳 | 有机造型、人物/生物、雕刻感 |
| 参数真可调、出工程图/BOM/打印建议 | 表面纹理（法线/凹凸贴图）——B-rep 实体没有「贴图」概念 |
| 布尔运算、阵列、圆角（取决于 agent 翻得细不细） | 电影级细节、照片级渲染质感 |

**结论**：「细节纹理还不太行」一半是 bug（agent 翻得粗糙），一半是范式（build123d 本来就不产纹理）。
纯改几行代码到不了「专业生产级细节纹理」——那条路要换引擎或加后处理。下面分三轨，按 ROI 排序。

---

## 1. Track A：NL→CAD 拆解增强（机械件精细化）— 最高 ROI，不换引擎

**目标**：让功能件自动更精细，逼近「工程可用」而非「玩具级」。

- **A1 自动细节基元**：agent 拆解时默认补 圆角(fillet)/倒角(chamfer)/拔模(draft)/壁厚过渡，而不仅是单 primitives。
- **A2 阵列/镜像/布尔精细化**：`PolarArray`/`LinearArray`/`mirror` + 多体布尔，减少「一个方块」式输出。
- **A3 约束驱动建模**：从自然语言抽尺寸约束（配合/公差/干涉检查），出图前自检是否可装配。
- **A4 草图反推（从参考图）**：上传图片/手绘 → 抽 2D 截面 → `make_face` → `extrude`，提升「照图建模」保真度。
- **A5 prompt/评测闭环**：每出一张图用 QA(水密性/壁厚/干涉) 反向喂 agent 重生成，迭代到通过。

落点：`vermes_cli/mfgcad/tools.py`（`_handle_mfg_text_to_cad` 的 NL→build123d 提示词与后处理）、`agent/prompt` 模板。
快赢：A1+A2 改提示词即可，1–2 天；A4 中等。

---

## 2. Track B：有机造型 + 表面纹理（换范式）— 大工程，决定「纹理」能否有

**目标**：让「细节纹理/有机感」成为可能。当前 Trellis 后端是工厂零可跑的 stub。

- **B1 启用 Trellis 后端**：接入 text-to-3D 扩散（Trellis/InstantMesh），产出带表面细节的 mesh；需权重+本地资产落地 + 真机验证（当前只代码层+优雅降级）。
- **B2 上传参考图→纹理**：用户给图，用图生纹理（diffusion）贴到已有 mesh（法线/凹凸/Albedo），经 Blender/trimesh 烘焙。
- **B3 mesh 后处理链路**：`trimesh` 修复 → 水密 → 重拓扑 → 减面，喂给打印/预览。

落点：`vermes_cli/mfgcad/backends/trellis.py`（现有骨架）、新 `texturing.py`。
诚实标注：B 是大工程，权重下载/显存/真机验证都未铺，预估数周级，且依赖外部模型可用性。

---

## 3. Track C：渲染质感与质检（体验层）— 中等，立竿见影于「看起来专业」

- **C1 PBR 材质预览**：前端 `ModelViewer` 给金属/塑料/木材 PBR 预设，模型「看起来」专业（不改几何）。
- **C2 可打印性报告增强**：自动支撑建议、悬垂角、最小壁厚红区标注（已在 BOM/print-advice 基础上加可视化）。
- **C3 网格质量门禁**：导出前水密/法向/自交检查，fail 时给修复建议。

落点：`frontend/src/components/ModelViewer.vue`、web_server 的 `print-advice`/`qa`。
快赢：C1 纯前端，1 天可见效。

---

## 4. 里程碑与排序（建议）

| 阶段 | 内容 | 周期 | 产出 |
|---|---|---|---|
| M1（快赢） | A1+A2 提示词精细化 + C1 PBR 预览 | ~3 天 | 功能件明显更细、预览更专业 |
| M2 | A4 草图反推 + A5 评测闭环 | ~1 周 | 「照图建模」保真度提升 |
| M3 | B1 Trellis 真机落地 | 数周 | 有机造型/纹理成为可能 |
| M4 | B2/B3 纹理后处理 | 数周 | 表面细节可达 |

**诚实建议**：先冲 M1（不碰引擎、零风险、肉眼可见），再 M2；B 轨是「纹理」真正解锁的前提，但工程大、依赖外部模型，单独排期、不阻塞 M1/M2。

## 5. 验证方式（每步都真机闭环）

- 出图：`mv ~/.vermes/engines/mac/.venv .venv.backup` → 触发 auto_setup → `mfg_text_to_cad` → 真出 STEP → QA pass。
- 细节评审：同一 prompt 改前/改后各出一张，人工比对圆角/阵列/壁厚细节是否更丰富。
- 纹理（B 轨）：Trellis 出 mesh → 渲染带纹理预览 → 肉眼确认表面细节。
- 回归：`.venv/bin/python -m pytest tests/mfgcad/ -p no:xdist -o addopts=""`（注意 sandbox 托管 venv 缺 pytest-asyncio，需在项目 .venv 跑）。
