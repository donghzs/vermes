# cadir — CAD-IR 契约建模工具集

LLM 只生成 JSON 契约（比直接生成 Python 代码稳定得多），建模逻辑留在确定性地步。

## 工具链

| 工具 | 依赖 | 作用 |
|------|------|------|
| `cadir_compile` | 无（纯 Python） | 契约校验+归一化（cad.ir.v1）：操作别名消歧、单位统一、字段级契约、依赖图拓扑排序 |
| `cadir_build` | 3D 引擎 venv | 契约→build123d 脚本→STEP，构建后自动几何核验 |
| `cadir_verify_step` | 3D 引擎 venv | STEP 几何独立核验（实体数/包围盒/体积 vs 期望值）——对抗 QA 误报的最终裁判 |
| `cadir_verify_stl` | 3D 引擎 venv | STL 网格核验（50B/三角形正确解析+坏面统计+可选清洗） |

## 典型链路

```
LLM 生成契约 JSON ──cadir_compile──▶ 规范化 IR ──cadir_build──▶ output.step
                                                        │
                     cadir_verify_step ◀────────────────┘（自动核验 + 可选严格期望值对比）
```

## 引擎要求

`cadir_build` / `cadir_verify_*` 需要 3D 引擎 venv（build123d/trimesh/numpy）：
- 默认路径 `~/.vermes/engines/mac/.venv`（由 mfg_setup_engine 安装）
- 可用 `CADIR_ENGINE_PY` 环境变量覆盖

`cadir_compile` 无引擎依赖，随时可用。

## 契约示例

见 `examples/plate.json`。契约规范：`version: cad.ir.v1` + `unit_system` + `features[]`（每个 feature 含 id/operation/parameters/dependencies）。支持的操作别名（部分）：hole/center_hole/cut_circle→through_hole、tap_hole→threaded_hole、pad→boss、cut_extrude→pocket、base_plate、cylinder、gear、fillet 等。

## 资产来源

- 契约编译器：2026-08-26 吸收自 Partloom cad.ir.v1 设计，MAC POC 实测定型
- 几何核验脚本：text-to-CAD 管线验证/调试实战沉淀（2026-08-17 树叶实测定型）
