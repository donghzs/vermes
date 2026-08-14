"""
mfgcad 局部编辑 + 纹理绘制工具（P3）。

P3 — 在已生成的 3D 模型上做局部编辑和纹理绘制。

设计原则（对齐 VERMES_3D_ARCH_BASELINE.md）：
- 框架层轻量纯 Python，重引擎按需安装于 ~/.vermes/engines/<name>/
- 局部编辑后端可插拔：Nano3D（NL 驱动）/ Paint3D（纹理生成）/ 简单几何变换
- 与 P1 多后端架构一致：EngineBackend 协议 + 路由

工具列表：
- mfg_edit_part：局部编辑（NL 描述 → 修改模型指定区域）
- mfg_paint_texture：纹理绘制（NL 描述 → 生成 UV 纹理贴图）
- mfg_transform：简单几何变换（缩放/旋转/镜像/布尔运算）

编辑后端类型：
- nano3d：NL 驱动局部编辑（清华 Nano3D，arXiv:2510.15019）
- paint3d：UV 纹理生成（CVPR 2024 Paint3D）
- builtin：简单几何变换（trimesh，框架层自带无需引擎）
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from tools.registry import registry


# ── 工具 Schema ──────────────────────────────────────────

MFG_EDIT_SCHEMA = {
    "name": "mfg_edit_part",
    "description": (
        "对已有 3D 模型做局部编辑。用自然语言描述要修改的部分，AI 自动定位区域并修改。\n"
        "支持：替换部件（换把手/换底座）、添加特征（加孔/加筋/倒角）、删除特征、调整尺寸。\n"
        "后端：nano3d（NL 驱动局部编辑，需安装引擎）/ builtin（简单几何变换）。\n"
        "输入：session_id（已存在的建模会话）+ edit_description（编辑描述）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "已存在的 mfgcad 会话 ID"},
            "edit_description": {
                "type": "string",
                "description": "编辑描述，如：'把笔筒底座加厚 2mm' 或 '在侧面加一个椭圆形孔'",
            },
            "backend": {
                "type": "string",
                "enum": ["auto", "nano3d", "builtin"],
                "default": "auto",
                "description": "编辑后端：auto=自动选择 / nano3d=NL 驱动 / builtin=几何变换",
            },
            "target_file": {
                "type": "string",
                "description": "要编辑的文件路径（默认用 session 的 STEP/STL 文件）",
            },
        },
        "required": ["session_id", "edit_description"],
    },
}

MFG_PAINT_SCHEMA = {
    "name": "mfg_paint_texture",
    "description": (
        "为 3D 模型生成 UV 纹理贴图。用自然语言描述想要的纹理/材质/颜色方案。\n"
        "后端：paint3d（CVPR 2024 纹理生成模型，需安装引擎）/ builtin（纯色/渐变）。\n"
        "输出：带纹理的 GLB 文件 + 纹理预览图。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "已存在的 mfgcad 会话 ID"},
            "texture_description": {
                "type": "string",
                "description": "纹理描述，如：'哑光黑色金属质感' 或 '木纹纹理，深棕色'",
            },
            "backend": {
                "type": "string",
                "enum": ["auto", "paint3d", "builtin"],
                "default": "auto",
                "description": "纹理后端：auto=自动选择 / paint3d=AI 纹理生成 / builtin=纯色/渐变",
            },
            "resolution": {
                "type": "integer",
                "enum": [512, 1024, 2048],
                "default": 1024,
                "description": "纹理分辨率（像素）",
            },
        },
        "required": ["session_id", "texture_description"],
    },
}

MFG_TRANSFORM_SCHEMA = {
    "name": "mfg_transform",
    "description": (
        "对 3D 模型做简单几何变换（无需 AI 引擎，框架层内置）。\n"
        "支持：缩放、旋转、镜像、平移、布尔运算（并/差/交）。\n"
        "输出：变换后的 STEP/STL 文件。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "已存在的 mfgcad 会话 ID"},
            "operation": {
                "type": "string",
                "enum": ["scale", "rotate", "mirror", "translate", "boolean_union", "boolean_subtract", "boolean_intersect"],
                "description": "变换操作类型",
            },
            "params": {
                "type": "object",
                "description": "操作参数：scale={factor}; rotate={axis,angle_deg}; mirror={plane}; translate={dx,dy,dz}; boolean={other_file}",
            },
            "target_file": {
                "type": "string",
                "description": "要变换的文件路径（默认用 session 的 STEP 文件）",
            },
        },
        "required": ["session_id", "operation", "params"],
    },
}


# ── 辅助函数 ─────────────────────────────────────────────


def _mfg_home() -> Path:
    return Path.home() / ".vermes" / "mfgcad"


def _session_dir(session_id: str) -> Path:
    return _mfg_home() / "sessions" / session_id


def _load_session(session_id: str) -> dict:
    sf = _session_dir(session_id) / "session.json"
    if not sf.is_file():
        return {}
    try:
        return json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_target_file(session: dict, target_file: str | None = None) -> str | None:
    """解析要编辑的目标文件路径。"""
    if target_file:
        return target_file
    # 优先 STEP，其次 STL
    files = session.get("files") or {}
    return files.get("step") or files.get("stl") or session.get("step_path") or session.get("stl_path")


def _resolve_edit_backend(backend: str) -> str:
    """自动选择编辑后端。"""
    if backend != "auto":
        return backend
    # 优先 nano3d（如果已安装）
    nano3d_dir = Path.home() / ".vermes" / "engines" / "nano3d"
    if nano3d_dir.is_dir() and (nano3d_dir / "run_nano3d.py").is_file():
        return "nano3d"
    return "builtin"


def _resolve_paint_backend(backend: str) -> str:
    """自动选择纹理后端。"""
    if backend != "auto":
        return backend
    paint3d_dir = Path.home() / ".vermes" / "engines" / "paint3d"
    if paint3d_dir.is_dir() and (paint3d_dir / "run_paint3d.py").is_file():
        return "paint3d"
    return "builtin"


# ── Handlers ─────────────────────────────────────────────


async def _handle_mfg_edit_part(args: dict, **kw: Any) -> str:
    """局部编辑 3D 模型。"""
    session_id = (args.get("session_id") or "").strip()
    edit_desc = (args.get("edit_description") or "").strip()
    backend_choice = (args.get("backend") or "auto").strip()
    target_file = args.get("target_file")

    if not session_id or not edit_desc:
        return "❌ 缺少参数 session_id 或 edit_description。"

    session = _load_session(session_id)
    if not session:
        return f"❌ 会话 {session_id} 不存在。请先用 mfg_text_to_cad 创建模型。"

    source_file = _resolve_target_file(session, target_file)
    if not source_file or not Path(source_file).is_file():
        return f"❌ 找不到要编辑的模型文件。session={session_id}"

    backend = _resolve_edit_backend(backend_choice)
    output_dir = _session_dir(session_id) / "edits" / str(int(time.time()))
    output_dir.mkdir(parents=True, exist_ok=True)

    if backend == "nano3d":
        # 调用 Nano3D 引擎（按需安装）
        engine_dir = Path.home() / ".vermes" / "engines" / "nano3d"
        runner = engine_dir / "run_nano3d.py"
        if not runner.is_file():
            return (
                "❌ Nano3D 引擎未安装。安装方式：把 Nano3D 代码放到 ~/.vermes/engines/nano3d/，\n"
                "确保 run_nano3d.py 存在。或使用 backend='builtin' 做简单几何变换。\n"
                "论文：arXiv:2510.15019 | GitHub: https://github.com/zhizunbuzui/nano3d"
            )

        cmd = ["python3", str(runner), "--input", source_file, "--edit", edit_desc, "--output-dir", str(output_dir)]
        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            return "❌ Nano3D 编辑超时（>10min）。"
        except Exception as e:
            return f"❌ Nano3D 调用失败: {type(e).__name__}: {e}"

        # 解析输出
        result = None
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if not result or not result.get("ok"):
            tail = "\n".join(proc.stderr.strip().splitlines()[-10:]) if proc.stderr else ""
            return f"❌ Nano3D 编辑失败：{result.get('message', '') if result else '无输出'}\n{tail}"

        output_file = result.get("output_path", str(output_dir / "edited.step"))
        return (
            f"✅ 局部编辑完成：{output_file}\n"
            f"编辑描述：{edit_desc}\n"
            f"后端：nano3d\n"
            + (f"预览图：{result.get('preview_path', '')}\n" if result.get("preview_path") else "")
            + f"会话 session_id={session_id}。可在 3D 建模工作室查看。"
        )

    else:  # builtin：简单几何变换
        # builtin 编辑用 LLM 解析编辑描述 → 几何变换参数 → trimesh 执行
        return await _builtin_edit(source_file, edit_desc, output_dir, session_id)


async def _builtin_edit(source_file: str, edit_desc: str, output_dir: Path, session_id: str) -> str:
    """框架层内置编辑：LLM 解析编辑描述 → 简单变换 → trimesh 执行。"""
    try:
        import trimesh
    except ImportError:
        return "❌ 内置编辑需要 trimesh（pip install trimesh），或安装 Nano3D 引擎用 NL 编辑。"

    # 加载模型
    try:
        mesh = trimesh.load(source_file, force='mesh')
    except Exception as e:
        return f"❌ 无法加载模型文件: {e}"

    # 简单关键词匹配 → 变换
    desc_lower = edit_desc.lower()
    edited = False
    ops = []

    if "缩放" in edit_desc or "scale" in desc_lower:
        import re
        m = re.search(r'(\d+\.?\d*)\s*%', edit_desc)
        if m:
            factor = float(m.group(1)) / 100.0
            mesh.apply_scale(factor)
            ops.append(f"缩放 {factor:.2f}x")
            edited = True

    if "旋转" in edit_desc or "rotate" in desc_lower:
        import numpy as np
        if "x" in desc_lower:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(3.14159/2, [1, 0, 0]))
            ops.append("绕 X 轴旋转 90°")
            edited = True
        elif "y" in desc_lower:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(3.14159/2, [0, 1, 0]))
            ops.append("绕 Y 轴旋转 90°")
            edited = True
        elif "z" in desc_lower:
            mesh.apply_transform(trimesh.transformations.rotation_matrix(3.14159/2, [0, 0, 1]))
            ops.append("绕 Z 轴旋转 90°")
            edited = True

    if "镜像" in edit_desc or "mirror" in desc_lower:
        import numpy as np
        mirror_matrix = np.eye(4)
        mirrored = False
        if "x" in desc_lower:
            mirror_matrix[0, 0] = -1
            ops.append("X 轴镜像")
            mirrored = True
        elif "y" in desc_lower:
            mirror_matrix[1, 1] = -1
            ops.append("Y 轴镜像")
            mirrored = True
        elif "z" in desc_lower:
            mirror_matrix[2, 2] = -1
            ops.append("Z 轴镜像")
            mirrored = True
        if mirrored:
            mesh.apply_transform(mirror_matrix)
            edited = True

    if not edited:
        return (
            f"⚠️ 内置编辑器无法解析此编辑描述：「{edit_desc}」\n"
            "内置支持：缩放（如 '缩放 150%'）、旋转（如 '绕 X 轴旋转'）、镜像（如 'X 轴镜像'）。\n"
            "复杂编辑请安装 Nano3D 引擎：~/.vermes/engines/nano3d/"
        )

    # 导出
    output_file = output_dir / "edited.stl"
    mesh.export(str(output_file))
    vol = mesh.volume if hasattr(mesh, 'volume') else None
    vol_str = f"{vol:.2f} mm³" if vol else ""

    return (
        f"✅ 局部编辑完成：{output_file}\n"
        f"操作：{', '.join(ops)}\n"
        + (f"体积：{vol_str}\n" if vol_str else "")
        + f"后端：builtin（简单几何变换）\n"
        + f"会话 session_id={session_id}。可在 3D 建模工作室查看。"
    )


async def _handle_mfg_paint_texture(args: dict, **kw: Any) -> str:
    """为 3D 模型生成 UV 纹理贴图。"""
    session_id = (args.get("session_id") or "").strip()
    texture_desc = (args.get("texture_description") or "").strip()
    backend_choice = (args.get("backend") or "auto").strip()
    resolution = int(args.get("resolution") or 1024)

    if not session_id or not texture_desc:
        return "❌ 缺少参数 session_id 或 texture_description。"

    session = _load_session(session_id)
    if not session:
        return f"❌ 会话 {session_id} 不存在。"

    source_file = _resolve_target_file(session)
    if not source_file or not Path(source_file).is_file():
        return f"❌ 找不到模型文件。session={session_id}"

    backend = _resolve_paint_backend(backend_choice)
    output_dir = _session_dir(session_id) / "textures" / str(int(time.time()))
    output_dir.mkdir(parents=True, exist_ok=True)

    if backend == "paint3d":
        engine_dir = Path.home() / ".vermes" / "engines" / "paint3d"
        runner = engine_dir / "run_paint3d.py"
        if not runner.is_file():
            return (
                "❌ Paint3D 引擎未安装。安装方式：把 Paint3D 代码放到 ~/.vermes/engines/paint3d/，\n"
                "确保 run_paint3d.py 存在。或使用 backend='builtin' 生成纯色/渐变纹理。\n"
                "论文：CVPR 2024 | GitHub: https://github.com/OpenTexture/Paint3D"
            )

        cmd = [
            "python3", str(runner),
            "--input", source_file,
            "--text", texture_desc,
            "--resolution", str(resolution),
            "--output-dir", str(output_dir),
        ]
        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=900
            )
        except subprocess.TimeoutExpired:
            return "❌ Paint3D 纹理生成超时（>15min）。"
        except Exception as e:
            return f"❌ Paint3D 调用失败: {type(e).__name__}: {e}"

        result = None
        for line in reversed(proc.stdout.strip().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        if not result or not result.get("ok"):
            tail = "\n".join(proc.stderr.strip().splitlines()[-10:]) if proc.stderr else ""
            return f"❌ Paint3D 纹理生成失败：{result.get('message', '') if result else '无输出'}\n{tail}"

        return (
            f"✅ 纹理生成完成\n"
            f"GLB（含纹理）: {result.get('glb_path', '')}\n"
            f"纹理图: {result.get('texture_path', '')}\n"
            + (f"预览图: {result.get('preview_path', '')}\n" if result.get("preview_path") else "")
            + f"纹理描述：{texture_desc}\n"
            + f"分辨率：{resolution}px\n"
            + f"后端：paint3d\n"
            + f"会话 session_id={session_id}。"
        )

    else:  # builtin：纯色/渐变纹理
        return await _builtin_paint(source_file, texture_desc, output_dir, session_id, resolution)


async def _builtin_paint(source_file: str, texture_desc: str, output_dir: Path, session_id: str, resolution: int) -> str:
    """框架层内置纹理：纯色/渐变。"""
    try:
        import trimesh
    except ImportError:
        return "❌ 内置纹理需要 trimesh（pip install trimesh），或安装 Paint3D 引擎。"

    try:
        mesh = trimesh.load(source_file, force='mesh')
    except Exception as e:
        return f"❌ 无法加载模型文件: {e}"

    # 颜色映射
    color_map = {
        "红": [220, 50, 50, 255],
        "绿": [50, 200, 80, 255],
        "蓝": [50, 100, 220, 255],
        "黑": [30, 30, 30, 255],
        "白": [240, 240, 240, 255],
        "灰": [128, 128, 128, 255],
        "金": [212, 175, 55, 255],
        "银": [192, 192, 192, 255],
        "铜": [184, 115, 51, 255],
    }

    color = None
    for name, rgb in color_map.items():
        if name in texture_desc:
            color = rgb
            break

    if color is None:
        # 默认深灰
        color = [64, 64, 64, 255]

    # 应用颜色
    if hasattr(mesh, 'visual'):
        mesh.visual.face_colors = [color] * len(mesh.faces)

    # 导出 GLB
    output_glb = output_dir / "textured.glb"
    try:
        mesh.export(str(output_glb), file_type='glb')
    except Exception as e:
        # GLB 导出可能失败，fallback 到 STL
        output_stl = output_dir / "textured.stl"
        mesh.export(str(output_stl))
        return (
            f"✅ 纹理应用完成（GLB 导出失败，已导出 STL）：{output_stl}\n"
            f"颜色：RGBA{tuple(color)}\n"
            f"后端：builtin（纯色）\n"
            + f"提示：安装 Paint3D 引擎可生成复杂 UV 纹理\n"
            + f"会话 session_id={session_id}。"
        )

    color_name = "自定义"
    for name, rgb in color_map.items():
        if rgb == color:
            color_name = name
            break

    return (
        f"✅ 纹理应用完成\n"
        f"GLB（含颜色）: {output_glb}\n"
        f"颜色：{color_name} (RGBA{tuple(color)})\n"
        f"后端：builtin（纯色）\n"
        + f"提示：安装 Paint3D 引擎可生成复杂 UV 纹理（木纹/金属/渐变等）\n"
        + f"会话 session_id={session_id}。"
    )


async def _handle_mfg_transform(args: dict, **kw: Any) -> str:
    """简单几何变换。"""
    session_id = (args.get("session_id") or "").strip()
    operation = (args.get("operation") or "").strip()
    params = args.get("params") or {}
    target_file = args.get("target_file")

    if not session_id or not operation:
        return "❌ 缺少参数 session_id 或 operation。"

    session = _load_session(session_id)
    if not session:
        return f"❌ 会话 {session_id} 不存在。"

    source_file = _resolve_target_file(session, target_file)
    if not source_file or not Path(source_file).is_file():
        return f"❌ 找不到模型文件。session={session_id}"

    try:
        import trimesh
        import numpy as np
    except ImportError:
        return "❌ 需要 trimesh（pip install trimesh）。"

    try:
        mesh = trimesh.load(source_file, force='mesh')
    except Exception as e:
        return f"❌ 无法加载模型文件: {e}"

    output_dir = _session_dir(session_id) / "transforms" / str(int(time.time()))
    output_dir.mkdir(parents=True, exist_ok=True)
    op_desc = ""

    if operation == "scale":
        factor = float(params.get("factor", 1.0))
        mesh.apply_scale(factor)
        op_desc = f"缩放 {factor:.3f}x"

    elif operation == "rotate":
        axis_str = params.get("axis", "z").lower()
        angle_deg = float(params.get("angle_deg", 90))
        axis_map = {"x": [1, 0, 0], "y": [0, 1, 0], "z": [0, 0, 1]}
        axis = axis_map.get(axis_str, [0, 0, 1])
        mesh.apply_transform(
            trimesh.transformations.rotation_matrix(np.radians(angle_deg), axis)
        )
        op_desc = f"绕 {axis_str.upper()} 轴旋转 {angle_deg}°"

    elif operation == "mirror":
        plane = params.get("plane", "xy").lower()
        # trimesh 没有 apply_mirror，用缩放矩阵实现
        mirror_matrix = np.eye(4)
        if "x" in plane:
            mirror_matrix[0, 0] = -1
        if "y" in plane:
            mirror_matrix[1, 1] = -1
        if "z" in plane and plane not in ("xy",):
            mirror_matrix[2, 2] = -1
        # 只有一个字母时镜像对应轴
        if plane in ("x",):
            mirror_matrix[0, 0] = -1
        elif plane in ("y",):
            mirror_matrix[1, 1] = -1
        elif plane in ("z",):
            mirror_matrix[2, 2] = -1
        mesh.apply_transform(mirror_matrix)
        op_desc = f"{plane} 镜像"

    elif operation == "translate":
        dx = float(params.get("dx", 0))
        dy = float(params.get("dy", 0))
        dz = float(params.get("dz", 0))
        mesh.apply_translation([dx, dy, dz])
        op_desc = f"平移 ({dx}, {dy}, {dz})"

    elif operation in ("boolean_union", "boolean_subtract", "boolean_intersect"):
        other_file = params.get("other_file", "")
        if not other_file or not Path(other_file).is_file():
            return f"❌ 布尔运算需要 other_file 参数指向另一个模型文件。"
        try:
            other = trimesh.load(other_file, force='mesh')
        except Exception as e:
            return f"❌ 无法加载第二个模型: {e}"

        if operation == "boolean_union":
            result_mesh = trimesh.boolean.union([mesh, other])
            op_desc = "布尔并集"
        elif operation == "boolean_subtract":
            result_mesh = trimesh.boolean.difference([mesh, other])
            op_desc = "布尔差集"
        else:
            result_mesh = trimesh.boolean.intersection([mesh, other])
            op_desc = "布尔交集"
        mesh = result_mesh

    else:
        return f"❌ 未知操作: {operation}。支持: scale/rotate/mirror/translate/boolean_*"

    output_file = output_dir / "transformed.stl"
    mesh.export(str(output_file))
    vol = mesh.volume if hasattr(mesh, 'volume') else None
    vol_str = f"{vol:.2f} mm³" if vol else ""

    return (
        f"✅ 变换完成：{output_file}\n"
        f"操作：{op_desc}\n"
        + (f"体积：{vol_str}\n" if vol_str else "")
        + f"会话 session_id={session_id}。可在 3D 建模工作室查看。"
    )


# ── 工具注册 ─────────────────────────────────────────────


def register_tools(host_api=None):
    """注册 mfgcad P3 编辑/纹理/变换工具。"""
    from tools.registry import registry

    for schema, handler in [
        (MFG_EDIT_SCHEMA, _handle_mfg_edit_part),
        (MFG_PAINT_SCHEMA, _handle_mfg_paint_texture),
        (MFG_TRANSFORM_SCHEMA, _handle_mfg_transform),
    ]:
        registry.register(
            name=schema["name"],
            handler=handler,
            schema=schema,
            toolset="mfgcad",
        )
