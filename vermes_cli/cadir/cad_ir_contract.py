#!/usr/bin/env python3
"""
cad_ir_contract.py — 轻量版 CAD-IR 契约编译器（吸收自 Partloom cad.ir.v1 设计，2026-08-26）

作用：把 LLM/用户的建模意图归一化成稳定、mm 基准的规范化 IR（Intermediate Representation），
     校验通过后再由确定性代码翻译成 build123d 调用。LLM 只生成 JSON 契约（比直接生成
     Python 代码稳定得多），建模逻辑留在确定性地步。

设计要点（取 Partloom 之长）：
  1. 版本化契约：cad.ir.v1
  2. 操作别名归一化：hole/center_hole/cut_circle → through_hole（LLM 模糊词消歧）
  3. 单位统一：所有长度字段 *_mm 规范名 + UNIT_FACTORS_MM 换算表（含中文单位）
  4. 字段级契约：FEATURE_FIELDS 定义每个操作的规范字段/别名/必填/正数约束
  5. 依赖图：features[].dependencies[] 显式声明，编译时拓扑排序 + 环检测
  6. 语义引用：target.face_role（如 outer_horizontal_face）替代裸坐标

用法：
  python cad_ir_contract.py examples/plate.json            # 校验并打印规范化 IR
  python cad_ir_contract.py examples/plate.json --build    # 校验后翻译成 build123d 脚本

退出码：0=编译通过，1=有错误（打印 errors 明细），2=用法错误。
"""
from __future__ import annotations

import copy
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CAD_IR_VERSION = "cad.ir.v1"
DEFAULT_UNIT = "mm"

# ── 操作别名归一化（LLM 输出模糊词 → 规范 operation）──────────────────────────
# 吸收自 Partloom FEATURE_ALIASES，只保留 build123d 能确定性实现的操作子集
OPERATION_ALIASES: dict[str, str] = {
    # 打孔
    "hole": "through_hole",
    "simple_hole": "through_hole",
    "center_hole": "through_hole",
    "centre_hole": "through_hole",
    "cut_circle": "through_hole",
    "through_hole": "through_hole",
    # 螺纹
    "threaded_hole": "threaded_hole",
    "tap_hole": "threaded_hole",
    "external_thread": "external_thread",
    "screw_thread": "external_thread",
    "shaft_thread": "external_thread",
    # 凸台/切除
    "boss": "boss",
    "boss_extrude": "boss",
    "extruded_boss": "boss",
    "pad": "boss",
    "pocket": "pocket",
    "cut_extrude": "pocket",
    "extruded_cut": "pocket",
    # 槽
    "slot": "slot",
    "rectangular_slot": "slot",
    "keyway_slot": "slot",
    # 倒角/圆角/穹顶
    "fillet": "fillet",
    "round": "fillet",
    "edge_round": "fillet",
    "chamfer": "chamfer",
    "bevel": "chamfer",
    "dome": "dome",
    "face_dome": "dome",
    # 图案阵列
    "linear_pattern": "linear_pattern",
    "rectangular_pattern": "linear_pattern",
    "circular_pattern": "circular_pattern",
    "bolt_circle_pattern": "bolt_circle_pattern",
    "mirror": "mirror",
    # 基础件
    "base_plate": "base_plate",
    "plate": "base_plate",
    "box": "base_plate",
    "cylinder": "cylinder",
    "gear": "gear",
    "spur_gear": "gear",
    "gear_pair": "gear_pair",
    # 回转/扫描/放样
    "revolve": "revolve",
    "profile_extrude": "profile_extrude",
    "extrude_profile": "profile_extrude",
    "sweep": "sweep",
    "loft": "loft",
    # 壳/筋/拔模
    "shell": "shell",
    "hollow": "shell",
    "rib": "rib",
    "draft": "draft",
    # 钣金/焊件/自由曲面（路线图）
    "sheet_metal": "sheet_metal",
    "weldment": "weldment",
    "freeform_surface": "freeform_surface",
    "assembly_mate": "assembly_mate",
}

# 歧义特征类型：单独出现时无法确定是新建还是修改，需看 dependencies 上下文
AMBIGUOUS_TYPES = {"extrude", "cut", "profile", "sketch"}

# 修改类操作（必须有依赖的目标体）vs 新建类操作（新文档/新实体）
MODIFIER_OPERATIONS = {
    "boss", "pocket", "through_hole", "threaded_hole", "external_thread",
    "slot", "fillet", "chamfer", "dome", "linear_pattern", "circular_pattern",
    "bolt_circle_pattern", "mirror", "shell", "draft", "rib",
}
NEW_DOCUMENT_OPERATIONS = {
    "base_plate", "cylinder", "gear", "gear_pair", "revolve", "profile_extrude",
    "sweep", "loft", "sheet_metal", "weldment", "freeform_surface", "assembly_mate",
}
STANDARD_OPERATIONS = tuple(sorted(set(OPERATION_ALIASES.values())))

# ── 单位换算（mm 基准，含中英别名）───────────────────────────────────────────
UNIT_FACTORS_MM: dict[str, float] = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
    "millimetre": 1.0, "millimetres": 1.0, "毫米": 1.0,
    "cm": 10.0, "centimeter": 10.0, "centimeters": 10.0, "厘米": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0, "米": 1000.0,
    "in": 25.4, "inch": 25.4, "inches": 25.4, '"': 25.4, "英寸": 25.4,
}


@dataclass(frozen=True)
class QuantityField:
    """字段级契约：规范名 + 兼容名 + 别名 + 必填 + 正数约束。"""
    canonical: str
    compatibility: str
    aliases: tuple[str, ...]
    required: bool = True
    positive: bool = True


# ── 操作字段契约（对齐 Partloom 命名，只含我们的操作子集）────────────────────
FEATURE_FIELDS: dict[str, tuple[QuantityField, ...]] = {
    "base_plate": (
        QuantityField("length_mm", "length", ("length_mm", "length", "overall_length_mm", "overall_length")),
        QuantityField("width_mm", "width", ("width_mm", "width", "overall_width_mm", "overall_width")),
        QuantityField("thickness_mm", "thickness", ("thickness_mm", "thickness", "height_mm", "height")),
    ),
    "cylinder": (
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter", "dia_mm", "dia")),
        QuantityField("height_mm", "height", ("height_mm", "height", "length_mm", "length")),
    ),
    "boss": (
        QuantityField("length_mm", "length", ("length_mm", "length")),
        QuantityField("width_mm", "width", ("width_mm", "width")),
        QuantityField("height_mm", "height", ("height_mm", "height", "depth_mm", "depth")),
    ),
    "pocket": (
        QuantityField("length_mm", "length", ("length_mm", "length")),
        QuantityField("width_mm", "width", ("width_mm", "width")),
        QuantityField("depth_mm", "depth", ("depth_mm", "depth")),
    ),
    "through_hole": (
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter", "hole_diameter_mm", "hole_diameter")),
    ),
    "threaded_hole": (
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter", "hole_diameter_mm", "hole_diameter")),
        QuantityField("thread_depth_mm", "thread_depth", ("thread_depth_mm", "thread_depth", "depth_mm", "depth"), required=False),
    ),
    "slot": (
        QuantityField("length_mm", "length", ("length_mm", "length", "slot_length_mm", "slot_length")),
        QuantityField("width_mm", "width", ("width_mm", "width", "slot_width_mm", "slot_width")),
        QuantityField("depth_mm", "depth", ("depth_mm", "depth"), required=False),
    ),
    "fillet": (
        QuantityField("radius_mm", "radius", ("radius_mm", "radius", "fillet_radius_mm", "fillet_radius")),
    ),
    "chamfer": (
        QuantityField("size_mm", "size", ("size_mm", "size", "distance_mm", "distance", "chamfer_size_mm", "chamfer_size")),
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter"), required=False),
    ),
    "dome": (
        QuantityField("height_mm", "height", ("height_mm", "height", "dome_height_mm", "dome_height")),
    ),
    "shell": (
        QuantityField("thickness_mm", "thickness", ("thickness_mm", "thickness")),
    ),
    "rib": (
        QuantityField("width_mm", "width", ("width_mm", "width"), required=False),
        QuantityField("height_mm", "height", ("height_mm", "height"), required=False),
        QuantityField("thickness_mm", "thickness", ("thickness_mm", "thickness"), required=False),
    ),
    "linear_pattern": (
        QuantityField("spacing_mm", "spacing", ("spacing_mm", "spacing", "spacing_x_mm", "spacing_x")),
    ),
    "circular_pattern": (
        QuantityField("radius_mm", "radius", ("radius_mm", "radius", "pcd_mm", "pcd")),
    ),
    "bolt_circle_pattern": (
        QuantityField("pcd_mm", "pcd", ("pcd_mm", "pcd", "pitch_circle_diameter_mm", "pitch_circle_diameter")),
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter", "hole_diameter_mm", "hole_diameter"), required=False),
    ),
    "gear": (
        QuantityField("module_mm", "module", ("module_mm", "module", "m")),
        QuantityField("face_width_mm", "face_width", ("face_width_mm", "face_width", "width_mm", "width")),
    ),
    "revolve": (
        QuantityField("angle_deg", "angle", ("angle_deg", "angle", "sweep_angle_deg", "sweep_angle"), required=False),
    ),
    "profile_extrude": (
        QuantityField("height_mm", "height", ("height_mm", "height", "depth_mm", "depth")),
    ),
    "sweep": (
        QuantityField("diameter_mm", "diameter", ("diameter_mm", "diameter"), required=False),
    ),
    "sheet_metal": (
        QuantityField("thickness_mm", "thickness", ("thickness_mm", "thickness")),
        QuantityField("length_mm", "length", ("length_mm", "length"), required=False),
        QuantityField("width_mm", "width", ("width_mm", "width"), required=False),
        QuantityField("bend_radius_mm", "bend_radius", ("bend_radius_mm", "bend_radius"), required=False),
    ),
}

# 允许出现在 options 里的键（未知 key 报 warning）
KNOWN_OPTION_KEYS = {
    "position", "mode", "gear_type", "pressure_angle_deg", "backlash_mm",
    "keyway", "count", "axis", "angle_deg", "pattern_count", "spacing_deg",
    "flip", "through_all", "both_sides", "tolerance_mm",
}

# target 语义引用契约
TARGET_RESOLUTIONS = {"new_body", "semantic_reference", "reference"}
FACE_ROLES = {
    "outer_horizontal_face", "inner_horizontal_face", "top_face", "bottom_face",
    "outer_vertical_face", "side_face", "end_face", "hole_cylindrical_face",
}


@dataclass
class CADIRCompileResult:
    success: bool
    design: dict[str, Any] = field(default_factory=dict)
    ir: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"compile: {'✅ PASS' if self.success else '❌ FAIL'} (cad.ir.v1)"]
        if self.success:
            lines.append(f"  features: {len(self.ir.get('features', []))} ops")
            for f in self.ir["features"]:
                lines.append(
                    f"    - [{f['id']}] {f['operation']} params={f['parameters']} "
                    f"deps={[d.get('feature_id') for d in f.get('dependencies', [])]}"
                )
        for w in self.warnings:
            lines.append(f"  ⚠ {w.get('message')}")
        for e in self.errors:
            lines.append(f"  ❌ [{e.get('path', '?')}] {e.get('message')}")
        return "\n".join(lines)


# 整数/枚举参数（teeth 等）不经过 mm 换算；它们属于"通用参数"白名单
INT_KEYS = ("teeth", "count", "pattern_count")
FLOAT_KEYS = ("bore_diameter_mm", "keyway_width_mm", "keyway_depth_mm")
GENERIC_KEYS = set(INT_KEYS) | set(FLOAT_KEYS)


class CADIRCompiler:
    """把 LLM/用户意图 JSON 编译成规范化 mm 基准 CAD-IR。"""

    def __init__(self) -> None:
        self._pending_errors: list[dict[str, Any]] = []

    # ── 别名归一化 ─────────────────────────────────────────────────────────
    @classmethod
    def canonical_operation(cls, value: Any) -> tuple[str | None, bool]:
        """返回 (规范操作名, 是否识别)。未知操作返回 (None, False)。"""
        if not isinstance(value, str):
            return None, False
        op = value.strip().lower().replace("-", "_")
        return OPERATION_ALIASES.get(op, op), op in OPERATION_ALIASES

    # ── 单位 ───────────────────────────────────────────────────────────────
    @classmethod
    def normalize_unit(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        u = value.strip().lower()
        return u if u in UNIT_FACTORS_MM else None

    @classmethod
    def quantity_mm(cls, value: Any, default_unit: str = DEFAULT_UNIT) -> float | None:
        """数值 + 可选单位后缀（'20mm'/'2cm'/'1in'）→ mm float。"""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        if isinstance(value, (int, float)):
            factor = UNIT_FACTORS_MM.get(default_unit, 1.0)
            return float(value) * factor
        text = value.strip().lower().replace(" ", "")
        for unit, factor in sorted(UNIT_FACTORS_MM.items(), key=lambda kv: -len(kv[0])):
            if text.endswith(unit) and len(text) > len(unit):
                num = text[: len(text) - len(unit)]
                try:
                    return float(num) * factor
                except ValueError:
                    continue
        try:
            return float(text) * UNIT_FACTORS_MM.get(default_unit, 1.0)
        except ValueError:
            return None

    # ── 顶层编译入口 ───────────────────────────────────────────────────────
    def compile_with_errors(self, design: dict[str, Any]) -> CADIRCompileResult:
        self._pending_errors = []
        result = self.compile(design)
        for e in self._pending_errors:
            if e not in result.errors:
                result.errors.append(e)
        result.success = result.success and not result.errors
        return result

    def compile(self, design: dict[str, Any]) -> CADIRCompileResult:
        result = CADIRCompileResult(success=False, design=copy.deepcopy(design))
        if not isinstance(design, dict):
            result.errors.append({"path": "$", "message": "顶层必须是 JSON object"})
            return result

        version = design.get("version")
        if version != CAD_IR_VERSION:
            result.errors.append({"path": "$.version", "message": f"版本不匹配：期望 {CAD_IR_VERSION}，实际 {version!r}"})

        unit_system = design.get("unit_system", DEFAULT_UNIT)
        norm_unit = self.normalize_unit(unit_system)
        if norm_unit is None:
            result.errors.append({"path": "$.unit_system", "message": f"不支持的单位系统：{unit_system!r}（支持 {sorted(UNIT_FACTORS_MM)}）"})
        else:
            unit_system = norm_unit

        features_raw = design.get("features")
        if not isinstance(features_raw, list) or not features_raw:
            result.errors.append({"path": "$.features", "message": "features 必须是非空数组"})
            return result

        ir_features: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for i, raw in enumerate(features_raw):
            path = f"$.features[{i}]"
            feat = self._compile_feature(raw, path, unit_system)
            if feat is None:
                continue
            if feat["id"] in seen_ids:
                result.errors.append({"path": path, "message": f"重复的 feature id：{feat['id']!r}"})
                continue
            seen_ids.add(feat["id"])
            ir_features.append(feat)

        if result.errors:
            return result

        # 依赖图：拓扑排序 + 环检测
        dep_ok = self._resolve_dependencies(ir_features, result)
        if not dep_ok:
            return result

        # 修改类操作必须声明依赖（warning 级别）
        for feat in ir_features:
            if feat["operation"] in MODIFIER_OPERATIONS and not feat.get("dependencies"):
                result.warnings.append({
                    "path": f"$.features[{feat['id']}]",
                    "message": f"修改类操作 {feat['operation']} 没有声明 dependencies——将作用于主实体（primary_solid）",
                })

        result.ir = {
            "version": CAD_IR_VERSION,
            "unit_system": "mm",
            "task_type": design.get("task_type", "model_3d"),
            "part_type": design.get("part_type", "part"),
            "outputs": design.get("outputs", ["STEP"]),
            "features": ir_features,
        }
        result.success = True
        return result

    # ── 单 feature 编译 ────────────────────────────────────────────────────
    def _compile_feature(self, raw: Any, path: str, unit_system: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            self._err(path, "feature 必须是 object")
            return None
        errors: list[str] = []

        op_raw = raw.get("operation")
        op, recognized = self.canonical_operation(op_raw)
        if not recognized:
            self._err(path, f"未知操作 {op_raw!r}（支持：{', '.join(sorted(set(OPERATION_ALIASES.values())))}）")
            return None

        fid = raw.get("id")
        if not isinstance(fid, str) or not fid:
            fid = f"f_{op}_{abs(hash(str(raw))) % 10000}"
        params_raw = raw.get("parameters") or {}
        if not isinstance(params_raw, dict):
            self._err(path, "parameters 必须是 object")
            return None

        # 字段契约：别名归一 + 单位换算 + 必填 + 正数
        params: dict[str, Any] = {}
        contract = FEATURE_FIELDS.get(op, ())
        contract_by_alias: dict[str, QuantityField] = {}
        for qf in contract:
            contract_by_alias[qf.canonical] = qf
            contract_by_alias[qf.compatibility] = qf
            for a in qf.aliases:
                contract_by_alias[a] = qf

        provided: set[str] = set()
        for key, value in params_raw.items():
            k = key.strip().lower().replace("-", "_")
            if k in GENERIC_KEYS:
                # 通用参数先处理，不参与字段契约检查
                if k in INT_KEYS:
                    v = params_raw[k]
                    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                        errors.append(f"{k} 必须为正数，收到 {v!r}")
                    else:
                        params[k] = int(v)
                else:
                    mm = self.quantity_mm(value, unit_system)
                    if mm is None or mm < 0:
                        errors.append(f"{k} 无法解析或为负：{value!r}")
                    else:
                        params[k] = round(mm, 6)
                continue
            qf = contract_by_alias.get(k)
            if qf is None:
                errors.append(f"字段 {key!r} 不在 {op} 契约中（合法：{', '.join(f.canonical for f in contract) or '无'}）")
                continue
            mm = self.quantity_mm(value, unit_system)
            if mm is None:
                errors.append(f"{key}={value!r} 无法解析为长度值")
                continue
            if qf.positive and mm <= 0:
                errors.append(f"{qf.canonical} 必须为正数，收到 {mm}")
            params[qf.canonical] = round(mm, 6)
            provided.add(qf.canonical)

        # 必填检查
        for qf in contract:
            if qf.required and qf.canonical not in provided:
                errors.append(f"缺少必填字段 {qf.canonical}（别名：{', '.join(qf.aliases)}）")

        # options 白名单
        options_raw = raw.get("options") or {}
        options: dict[str, Any] = {}
        if isinstance(options_raw, dict):
            for ok, ov in options_raw.items():
                if ok in KNOWN_OPTION_KEYS or ok.endswith("_mm") or ok.endswith("_deg"):
                    options[ok] = ov

        # target
        target = self._compile_target(raw.get("target"), path)

        # dependencies
        deps = []
        for d in raw.get("dependencies") or []:
            if isinstance(d, dict) and d.get("feature_id"):
                deps.append({"kind": d.get("kind", "feature"), "feature_id": d["feature_id"]})
            elif isinstance(d, str):
                deps.append({"kind": "feature", "feature_id": d})

        if errors:
            self._err(path, "; ".join(errors))
            return None

        return {
            "id": fid,
            "name": raw.get("name") or fid,
            "operation": op,
            "required": bool(raw.get("required", True)),
            "parameters": params,
            "options": options,
            "target": target,
            "dependencies": deps,
        }

    def _compile_target(self, target_raw: Any, path: str) -> dict[str, Any]:
        if not isinstance(target_raw, dict):
            return {}
        resolution = target_raw.get("resolution")
        if resolution is not None and resolution not in TARGET_RESOLUTIONS:
            self._err(path, f"target.resolution 非法：{resolution!r}（支持 {sorted(TARGET_RESOLUTIONS)}）")
            return {}
        face_role = target_raw.get("face_role")
        if face_role is not None and face_role not in FACE_ROLES:
            self._err(path, f"target.face_role 非法：{face_role!r}（支持 {sorted(FACE_ROLES)}）")
            return {}
        out = {k: target_raw[k] for k in ("resolution", "body_ref", "feature_ref", "face_role") if k in target_raw}
        return out

    # ── 依赖图：拓扑排序 + 环检测 ─────────────────────────────────────────
    def _resolve_dependencies(self, features: list[dict[str, Any]], result: CADIRCompileResult) -> bool:
        by_id = {f["id"]: f for f in features}
        order: list[str] = []
        visited: dict[str, int] = {}  # 0=visiting, 1=done

        def visit(node: str, stack: list[str]) -> bool:
            if visited.get(node) == 1:
                return True
            if visited.get(node) == 0:
                cycle = " → ".join(stack[stack.index(node):] + [node])
                result.errors.append({"path": f"$.features[{node}]", "message": f"依赖环检测：{cycle}"})
                return False
            visited[node] = 0
            stack.append(node)
            feat = by_id.get(node)
            if feat is None:
                result.errors.append({"path": "$", "message": f"依赖指向不存在的 feature：{node!r}"})
                visited[node] = 1
                stack.pop()
                return False
            for d in feat.get("dependencies", []):
                if not visit(d["feature_id"], stack):
                    return False
            stack.pop()
            visited[node] = 1
            order.append(node)
            return True

        for f in features:
            if not visit(f["id"], []):
                return False

        # 按拓扑序重排 features（新建类在前，修改类按依赖顺序在后）
        features.sort(key=lambda f: order.index(f["id"]))
        return True

    # ── 错误辅助 ──────────────────────────────────────────────────────────
    def _err(self, path: str, message: str) -> None:
        self._pending_errors.append({"path": path, "message": message})


def load_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        sys.exit(f"文件不存在：{p}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ── IR → build123d 脚本翻译（确定性步骤）────────────────────────────────────
BUILD123D_HEADER = """\
# Auto-generated by cad_ir_contract.py (cad.ir.v1 → build123d)
# 校验通过后由确定性代码翻译，LLM 不直接生成 Python。
from build123d import *
"""


def ir_to_build123d(ir: dict[str, Any]) -> str:
    """把规范化 IR 翻译成 build123d 脚本（确定性翻译，不含任何 LLM 决策）。

    语义：
    - 新建类操作（base_plate/cylinder/gear...）构造实体
    - 修改类操作（through_hole/boss/pocket...）按 dependencies 对 current 做布尔操作
    - features 已按拓扑序排列 → 线性链式建模，最终 result 继承全部修改
    """
    lines = [BUILD123D_HEADER]
    current: str | None = None
    for feat in ir["features"]:
        op = feat["operation"]
        p = feat["parameters"]
        fid = feat["id"]
        lines.append(f"# --- [{fid}] {op} ---")
        if op == "base_plate":
            expr = f"Box({p['length_mm']}, {p['width_mm']}, {p['thickness_mm']})"
        elif op == "cylinder":
            expr = f"Cylinder({p['diameter_mm']}/2, {p['height_mm']})"
        elif op == "gear":
            # spur_gear(module, teeth, face_width, bore, pressure_angle_deg) → (solid, metrics)
            expr = (
                f"spur_gear(module={p['module_mm']}, teeth={p.get('teeth', 24)}, "
                f"face_width={p['face_width_mm']}, bore={p.get('bore_diameter_mm', 0)}, "
                f"pressure_angle_deg={feat.get('options', {}).get('pressure_angle_deg', 20.0)})[0]"
            )
            lines.insert(1, "from spur_gear import spur_gear  # 需与 scripts/spur_gear.py 同目录")
        elif op == "through_hole":
            # 贯通孔：build123d Cylinder 默认居中（-1000..+1000 覆盖 ±5 的板），
            # 不要再 moved——居中圆柱已覆盖目标体全厚（2026-08-26 实测教训：
            # 加了 moved(-1000) 只切掉下半，体积差 = πr²·半厚）。
            lines.append(
                f"hole_tool_{fid} = Cylinder({p['diameter_mm']}/2, 2000)"
            )
            expr = f"{current} - hole_tool_{fid}" if current else None
        elif op == "boss":
            expr = f"{current} + Box({p['length_mm']}, {p['width_mm']}, {p['height_mm']})" if current else None
        elif op == "fillet":
            expr = f"fillet({current}, edges={current}.edges(), radius={p['radius_mm']})" if current else None
        else:
            lines.append(f"# TODO(build123d 翻译): {op} {p}")
            expr = None
        if expr:
            lines.append(f"solid_{fid} = {expr}")
            current = f"solid_{fid}"
        lines.append("")
    if current:
        lines.append(f"result = {current}")
        lines.append("if result: export_step(result, 'output.step')")
    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("用法：python cad_ir_contract.py <design.json> [--build]")
    design = load_json(sys.argv[1])
    compiler = CADIRCompiler()
    result = compiler.compile_with_errors(design)
    print(result.summary())
    if not result.success:
        sys.exit(1)
    if "--build" in sys.argv:
        script = ir_to_build123d(result.ir)
        out = Path("generated_model.py")
        out.write_text(script, encoding="utf-8")
        print(f"\nbuild123d 脚本已生成：{out}")


if __name__ == "__main__":
    main()
