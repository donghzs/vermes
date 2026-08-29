"""
Vermes 侧 cadir 工具注册 —— CAD-IR 契约建模工具集。

设计要点（对齐 mfgcad/ScholarForge 集成模板）：
  * ``register_tools(host_api=None)`` 由 agent/module_loader 在 host_api 注入后调用，
    不在此处 import-time 注册。
  * handler 签名 ``handler(args: dict, **kw) -> str``，返回字符串，❌ 前缀表示失败。
  * 契约编译器（cad_ir_contract.py，cad.ir.v1）为纯 Python，主 venv 进程内直接加载。
  * 几何核验/构建执行（build123d/trimesh/numpy）跑在引擎 venv 子进程
    （``_engine_runner.py``，对齐 mfgcad 的引擎桥接模式：主 venv 不装重依赖）。

资产来源：text-to-cad-pipeline 技能（2026-08-26 吸收自 Partloom，MAC POC 实测定型）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from tools.registry import registry

_HERE = Path(__file__).resolve().parent

# 引擎 venv python 解析：CADIR_ENGINE_PY 覆盖 → 默认 MAC 引擎 venv（同 mfgcad）
_ENGINE_PY_DEFAULT = Path.home() / ".vermes" / "engines" / "mac" / ".venv" / "bin" / "python"


def _resolve_engine_py() -> Path:
    # 注意：不调用 .resolve()——venv 的 bin/python 是指向基础解释器的符号链接，
    # resolve 会得到裸解释器路径，venv 上下文（site-packages）随之丢失（2026-08-29 实测）。
    return Path(os.environ.get("CADIR_ENGINE_PY", str(_ENGINE_PY_DEFAULT)))


# ── 同目录脚本加载（兼容 builtin 与 ~/.vermes/modules 两种安装位）──────────────
_CONTRACT_MOD = None


def _load_contract_compiler():
    """进程内加载 cad_ir_contract.py（纯 Python，无重依赖）。"""
    global _CONTRACT_MOD
    if _CONTRACT_MOD is None:
        spec = importlib.util.spec_from_file_location("cadir_cad_ir_contract", _HERE / "cad_ir_contract.py")
        mod = importlib.util.module_from_spec(spec)
        # 必须先注册进 sys.modules：该脚本用 `from __future__ import annotations` +
        # @dataclass(frozen=True)，未注册时 dataclasses._is_type 查 sys.modules 得 None 直接崩
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _CONTRACT_MOD = mod
    return _CONTRACT_MOD


def _parse_contract_arg(args: dict) -> tuple[str, dict | None]:
    """返回 (错误信息, 契约 dict)。支持 contract_json 字符串或 contract_path 文件。"""
    raw = (args.get("contract_json") or "").strip()
    path = (args.get("contract_path") or "").strip()
    if raw:
        try:
            design = json.loads(raw)
        except json.JSONDecodeError as e:
            return f"❌ contract_json 不是合法 JSON：{e}", None
        return "", design
    if path:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"❌ contract_path 不存在：{p}", None
        try:
            design = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return f"❌ 契约文件解析失败（{p.name}）：{e}", None
        return "", design
    return "❌ 缺少契约：请提供 contract_json（JSON 字符串）或 contract_path（文件路径）。", None


def _run_engine(argv: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[str, dict | None]:
    """引擎 venv 子进程执行，返回 (合并输出, 末行 JSON 或 None)。"""
    engine_py = _resolve_engine_py()
    if not engine_py.is_file():
        return (
            "❌ 未找到引擎 Python（%s）。请先安装 3D 引擎（mfg_setup_engine 工具），"
            "或设 CADIR_ENGINE_PY 指向含 build123d/trimesh/numpy 的 venv python。" % engine_py,
            None,
        )
    try:
        # 清除宿主 Python 环境污染（PYTHONHOME/PYTHONPATH 会让引擎 venv 的 site-packages
        # 失效——PyInstaller 打包宿主下必踩，2026-08-29 实测）
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}
        proc = subprocess.run(
            [str(engine_py), *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"❌ 引擎执行超时（>{timeout}s）：{' '.join(argv[:3])} …", None
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    output = stdout + ("\n[stderr]\n" + stderr if stderr.strip() else "")
    payload = None
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    return output, payload


# ── 工具 1：契约编译（纯 Python，进程内）────────────────────────────────────
async def _handle_cadir_compile(args: dict, **kw: Any) -> str:
    err, design = _parse_contract_arg(args)
    if err:
        return err
    cc = _load_contract_compiler()
    result = cc.CADIRCompiler().compile_with_errors(design)
    lines = [result.summary()]
    if not result.success:
        return "❌ 契约编译失败（cad.ir.v1）：\n" + "\n".join(lines)

    if args.get("build_script"):
        script = cc.ir_to_build123d(result.ir)
        out = Path(args["build_script"]).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(script, encoding="utf-8")
        lines.append(f"\nbuild123d 脚本已生成：{out}")
    if args.get("return_ir"):
        lines.append("\n规范化 IR：\n" + json.dumps(result.ir, ensure_ascii=False, indent=2))
    lines.append("\n💡 下一步：cadir_build 用同一契约生成 STEP 模型。")
    return "✅ 契约编译通过（cad.ir.v1）：\n" + "\n".join(lines)


# ── 工具 2：契约构建（引擎 venv 执行生成的 build123d 脚本）────────────────────
async def _handle_cadir_build(args: dict, **kw: Any) -> str:
    err, design = _parse_contract_arg(args)
    if err:
        return err
    cc = _load_contract_compiler()
    result = cc.CADIRCompiler().compile_with_errors(design)
    if not result.success:
        return "❌ 契约编译失败，不执行构建：\n" + result.summary()

    # 工作目录
    session_id = (args.get("session_id") or "").strip() or f"auto_{int(time.time())}"
    out_dir = Path((args.get("output_dir") or "").strip() or (Path.home() / ".vermes" / "cadir" / "output" / session_id)).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    # 持久化契约原文（供 3D 工作室「编辑契约→重建」读取；P2-4）
    try:
        (out_dir / "contract.json").write_text(
            json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass

    # 生成脚本 + 同目录放置 spur_gear.py（gear 操作依赖）
    script = cc.ir_to_build123d(result.ir)
    script_path = out_dir / "generated_model.py"
    script_path.write_text(script, encoding="utf-8")
    shutil.copy2(_HERE / "spur_gear.py", out_dir / "spur_gear.py")

    # 引擎 venv 执行（cwd=out_dir → output.step 落在工作目录）
    stdout, _ = _run_engine([str(script_path)], cwd=out_dir, timeout=args.get("timeout") or 300)
    step_path = out_dir / "output.step"
    stl_path = out_dir / "output.stl"
    if not step_path.is_file():
        return (
            "❌ build123d 构建失败（未产出 output.step）：\n"
            f"脚本：{script_path}\n引擎输出（尾部）：\n{stdout.strip()[-2000:]}"
        )

    # 构建后自动几何核验（对抗 LLM/QA 误报的最终裁判）
    v_stdout, v = _run_engine(
        [str(_HERE / "_engine_runner.py"), "--verify-step", str(step_path)],
        timeout=120,
    )
    metrics = ""
    if v:
        metrics = (
            f"\n几何核验：solids={v.get('solids')}，size={v.get('size')}，"
            f"volume={v.get('volume_mm3')}mm³"
        )
    preview = (
        "✅ CAD-IR 构建完成：\n"
        f"  STEP：{step_path}\n"
        f"  STL：{stl_path}（预览网格，tolerance=0.005）\n"
        f"  脚本：{script_path}\n"
        f"  特征数：{len(result.ir.get('features', []))}{metrics}\n"
        "💡 可用 cadir_verify_step 带期望值做严格核验。"
    )
    # 结构化产物推送（与 present_files 同机制）：artifacts 经 tool_executor
    # _build_tool_artifacts 读 result["artifacts"] 直接灌入 tool_end 事件，
    # 不依赖 preview 文本正则提取 / 截断长度，截断免疫。
    return {
        "preview": preview,
        "artifacts": [
            {"path": str(step_path), "title": "output.step", "source": "cadir_build"},
            {"path": str(stl_path), "title": "output.stl", "source": "cadir_build"},
        ],
    }


# ── 工具 3：STEP 几何核验（引擎 venv）───────────────────────────────────────
async def _handle_cadir_verify_step(args: dict, **kw: Any) -> str:
    step_file = (args.get("step_file") or "").strip()
    if not step_file:
        return "❌ 缺少必填参数 step_file（.step 文件路径）。"
    p = Path(step_file).expanduser()
    if not p.is_file():
        return f"❌ STEP 文件不存在：{p}"

    argv = [str(_HERE / "_engine_runner.py"), "--verify-step", str(p)]
    if args.get("expect_vol") is not None:
        argv += ["--expect-vol", str(args["expect_vol"])]
    if args.get("expect_bbox"):
        bbox = args["expect_bbox"]
        if isinstance(bbox, (list, tuple)):
            argv += ["--expect-bbox", ",".join(str(x) for x in bbox)]
        else:
            argv += ["--expect-bbox", str(bbox)]
    if args.get("expect_solids") is not None:
        argv += ["--expect-solids", str(args["expect_solids"])]
    if args.get("tolerance") is not None:
        argv += ["--tolerance", str(args["tolerance"])]

    stdout, v = _run_engine(argv, timeout=120)
    if v is None:
        return "❌ 几何核验引擎执行失败：\n" + stdout.strip()[-1500:]

    lines = [
        f"file   : {p}",
        f"solids : {v.get('solids')}",
        f"size   : {v.get('size')}",
        f"volume : {v.get('volume_mm3')} mm³",
    ]
    for c in v.get("checks", []):
        mark = "OK" if c.get("ok") else "MISMATCH"
        lines.append(f"check {c.get('check')}: expect={c.get('expect')} got={c.get('got')} → {mark}")
    verdict = "✅ PASS" if v.get("ok") else "❌ FAIL"
    return f"{verdict} STEP 几何核验：\n" + "\n".join(lines)


# ── 工具 4：STL 网格核验（引擎 venv）───────────────────────────────────────
async def _handle_cadir_verify_stl(args: dict, **kw: Any) -> str:
    stl_file = (args.get("stl_file") or "").strip()
    if not stl_file:
        return "❌ 缺少必填参数 stl_file（.stl 文件路径）。"
    p = Path(stl_file).expanduser()
    if not p.is_file():
        return f"❌ STL 文件不存在：{p}"

    argv = [str(_HERE / "_engine_runner.py"), "--verify-stl", str(p)]
    if args.get("write_clean"):
        argv += ["--write-clean", str(Path(args["write_clean"]).expanduser())]

    stdout, v = _run_engine(argv, timeout=120)
    if v is None:
        return "❌ STL 核验引擎执行失败：\n" + stdout.strip()[-1500:]

    lines = [
        f"file      : {p}",
        f"triangles : {v.get('triangles')}",
        f"bad_faces : {v.get('bad_faces')}",
    ]
    if v.get("clean_file"):
        lines.append(f"clean     : {v.get('clean_file')}（{v.get('clean_triangles')} 面，丢弃 {v.get('dropped')} 坏面）")
    verdict = "✅ PASS" if v.get("ok") else "❌ FAIL（存在坏面：NaN/Inf/极端坐标）"
    return f"{verdict}\n" + "\n".join(lines)


# ── schema ──────────────────────────────────────────────────────────────────
_COMPILE_SCHEMA = {
    "name": "cadir_compile",
    "description": (
        "CAD-IR 契约编译器（cad.ir.v1）：把 LLM/用户的建模意图 JSON 契约归一化成稳定、"
        "mm 基准的规范化 IR——操作别名消歧（hole/center_hole/cut_circle→through_hole）、"
        "单位统一（支持 mm/cm/in 及中文单位）、字段级契约校验、依赖图拓扑排序+环检测。"
        "LLM 只生成 JSON 契约（比直接生成 Python 代码稳定得多），建模逻辑留在确定性地步。"
        "适合「已有/想让 LLM 产出建模契约 JSON，需要校验合法性并规范化」的场景。"
        "返回编译结果摘要（PASS/FAIL + 逐特征明细 + 错误路径）。"
        "可选 build_script 参数把规范化 IR 翻译成 build123d 脚本落盘；"
        "return_ir=true 额外返回完整规范化 IR。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contract_json": {
                "type": "string",
                "description": "契约 JSON 字符串（cad.ir.v1）。与 contract_path 二选一。结构：{version:'cad.ir.v1', unit_system:'mm', features:[{id, operation, parameters, dependencies?}]}",
            },
            "contract_path": {
                "type": "string",
                "description": "契约 JSON 文件路径。与 contract_json 二选一。",
            },
            "build_script": {
                "type": "string",
                "description": "可选：把规范化 IR 翻译成的 build123d 脚本写入该路径。",
            },
            "return_ir": {
                "type": "boolean",
                "description": "可选：返回完整规范化 IR JSON（默认 false 只返回摘要）。",
            },
        },
        "required": [],
    },
}

_BUILD_SCHEMA = {
    "name": "cadir_build",
    "description": (
        "CAD-IR 契约构建：校验契约 → 翻译成 build123d 脚本 → 在 3D 引擎 venv 中执行 → "
        "产出 STEP 三维模型，并自动做构建后几何核验（实体数/包围盒/体积）。"
        "与 mfg_text_to_cad（自然语言→4-Agent 引擎）互补：cadir_build 走确定性契约翻译链路，"
        "适合「契约已通过 cadir_compile 校验，要生成可制造的 STEP 文件」的场景。"
        "gear 操作自动携带参数化齿轮生成器。"
        "返回 STEP 文件路径 + 特征数 + 几何核验指标。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "contract_json": {
                "type": "string",
                "description": "契约 JSON 字符串（cad.ir.v1，需通过 cadir_compile 校验）。与 contract_path 二选一。",
            },
            "contract_path": {
                "type": "string",
                "description": "契约 JSON 文件路径。与 contract_json 二选一。",
            },
            "output_dir": {
                "type": "string",
                "description": "可选：输出目录（STEP+脚本落此）。默认 ~/.vermes/cadir/output/<session_id>。",
            },
            "session_id": {
                "type": "string",
                "description": "可选：会话 ID（用于输出目录命名，留空自动生成）。",
            },
            "timeout": {
                "type": "integer",
                "description": "可选：引擎执行超时秒数（默认 300）。",
            },
        },
        "required": [],
    },
}

_VERIFY_STEP_SCHEMA = {
    "name": "cadir_verify_step",
    "description": (
        "STEP 几何独立核验（build123d）：实体数、包围盒、体积，可带期望值严格对比。"
        "这是对抗 LLM 生成管线 QA 误报的最终裁判——从实际 STEP 几何重derive真相，"
        "不受 pipeline 缓存/残留污染。"
        "适合「拿到 STEP 文件，需要验证几何是否符合设计意图（单实体、尺寸、体积）」的场景。"
        "返回核验指标 + 逐项 PASS/MISMATCH 判定。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "step_file": {
                "type": "string",
                "description": "STEP 文件路径。",
            },
            "expect_solids": {
                "type": "integer",
                "description": "可选：期望实体数（1=单实体，多实体=打印散架风险）。",
            },
            "expect_bbox": {
                "type": "array",
                "items": {"type": "number"},
                "description": "可选：期望包围盒尺寸 [X, Y, Z]（mm）。",
            },
            "expect_vol": {
                "type": "number",
                "description": "可选：期望体积（mm³）。",
            },
            "tolerance": {
                "type": "number",
                "description": "可选：允许偏差（体积按百分比，bbox 按 mm；默认 0.5）。",
            },
        },
        "required": ["step_file"],
    },
}

_VERIFY_STL_SCHEMA = {
    "name": "cadir_verify_stl",
    "description": (
        "STL 网格质量核验：按 50B/三角形正确解析二进制 STL（避免流式错位产生幽灵垃圾面），"
        "统计三角形总数与坏面数（NaN/Inf/极端坐标>1e6）。"
        "可选 write_clean 过滤坏面后写回干净 STL。"
        "适合「3D 打印前检查网格质量」或「STL 出现诡异伪影需要判质」的场景。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "stl_file": {
                "type": "string",
                "description": "STL 文件路径。",
            },
            "write_clean": {
                "type": "string",
                "description": "可选：过滤坏面后写回干净 STL 的输出路径。",
            },
        },
        "required": ["stl_file"],
    },
}


def register_tools(host_api=None) -> None:
    """由 agent/module_loader 调用（host_api 注入后），注册 4 个 cadir 工具。"""
    registry.register(
        name="cadir_compile",
        toolset="cadir",
        schema=_COMPILE_SCHEMA,
        handler=_handle_cadir_compile,
        is_async=True,
        emoji="📐",
        description="CAD-IR 契约编译器（cad.ir.v1）：校验+归一化建模意图 JSON 契约",
    )
    registry.register(
        name="cadir_build",
        toolset="cadir",
        schema=_BUILD_SCHEMA,
        handler=_handle_cadir_build,
        is_async=True,
        emoji="🔧",
        description="CAD-IR 契约构建：契约→build123d→STEP（引擎 venv 执行+自动几何核验）",
    )
    registry.register(
        name="cadir_verify_step",
        toolset="cadir",
        schema=_VERIFY_STEP_SCHEMA,
        handler=_handle_cadir_verify_step,
        is_async=True,
        emoji="🔍",
        description="STEP 几何独立核验：实体数/包围盒/体积 vs 期望值（对抗 QA 误报的最终裁判）",
    )
    registry.register(
        name="cadir_verify_stl",
        toolset="cadir",
        schema=_VERIFY_STL_SCHEMA,
        handler=_handle_cadir_verify_stl,
        is_async=True,
        emoji="🧱",
        description="STL 网格质量核验：50B/三角形正确解析+坏面统计+可选清洗",
    )
