"""
Vermes 侧 mfgcad 工具注册 + 子进程桥接编排。

设计要点（对齐 ScholarForge 集成模板）：
  * ``register_tools(host_api=None)`` 由 agent/module_loader 在 host_api 注入后调用，
    不在此处 import-time 注册（与 ScholarForge 一致）。
  * 单一真相源 handler 签名：``handler(args: dict, **kw) -> str``（async 由
    registry.dispatch 自动桥接），返回字符串，❌ 前缀表示失败。
  * 引擎（MAC）跑在独立 venv，Vermes 主 venv 不装 build123d 等重依赖。
    mfg_text_to_cad 经子进程桥接调用 ``engine/run_mac.py``，解析其 JSON。
  * 状态化 design session：每次调用落 ``~/.vermes/mfgcad/sessions/<id>/session.json``。
  * CHECKPOINT：checkpoint=true 时生成后暂停人工核对，不自动定稿、不自动打印。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from tools.registry import registry

_HERE = Path(__file__).resolve().parent
# 引擎层（重依赖）按需安装、不在基础仓库内 —— 默认位于用户目录，可被
# MFG_CAD_ENGINE_DIR 覆盖。见 VERMES_3D_ARCH_BASELINE.md §3。
_ENGINE_DIR_DEFAULT = Path.home() / ".vermes" / "engines" / "mac"

MFGCAD_SCHEMA = {
    "name": "mfg_text_to_cad",
    "description": (
        "3D 建模：把自然语言建模需求直接生成 STEP 三维模型文件。"
        "底层由 Multi-Agent-CAD 引擎驱动（4-Agent 流水线：需求解析→几何规划→"
        "build123d 确定性代码翻译→双引擎几何/网格校验），无需人工写代码。"
        "适合「用户用中文描述一个零件/打印件（尺寸/形状/壁厚/孔位等），希望得到可制造的 STEP+STL+3MF」的场景。"
        "返回 STEP/STL/3MF 文件路径 + 体积 + 双引擎校验结果。"
        "可选参数：session_id 用于状态化续作；checkpoint=true 生成后暂停人工核对不自动定稿；"
        "workflow_id 选引擎工作流（original=确定性翻译优先，aider=Aider 优先）；"
        "preset 选场景模板（mechanical_part/print_part/ecommerce_display/film_prop）；"
        "auto_clarify=true 自动检查歧义并追问（默认 true）。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "自然语言建模需求，含关键尺寸。例：『做一个笔筒：圆柱体，外径 60 毫米，高 100 毫米，壁厚 3 毫米，底部封闭，顶部开口。』",
            },
            "session_id": {
                "type": "string",
                "description": "可选：设计会话 ID，用于状态化续作/核对。留空自动生成。",
            },
            "output_dir": {
                "type": "string",
                "description": "可选：STEP/STL 输出目录。留空默认 ~/.vermes/mfgcad/output/<session_id>。",
            },
            "workflow_id": {
                "type": "string",
                "enum": ["original", "aider"],
                "description": "可选：引擎工作流。original=确定性 build123d 翻译优先（推荐，便宜快）；aider=Aider 优先（更灵活更慢）。默认 original。",
            },
            "checkpoint": {
                "type": "boolean",
                "description": "可选：true=生成候选 STEP 后暂停，交人工核对尺寸/拓扑，不自动定稿；false=直接定稿返回。默认 false。",
            },
            "preset": {
                "type": "string",
                "enum": ["mechanical_part", "print_part", "ecommerce_display", "film_prop"],
                "description": "可选：场景模板。mechanical_part=机械零件，print_part=3D打印件，ecommerce_display=电商展示，film_prop=影视道具。留空自动猜测。",
            },
            "auto_clarify": {
                "type": "boolean",
                "description": "可选：true=建模前自动检查歧义（缺尺寸/矛盾），有问题则返回追问不调引擎。false=跳过检查直接建模。默认 true。",
            },
        },
        "required": ["request"],
    },
}

MFG_CLARIFY_SCHEMA = {
    "name": "mfg_clarify",
    "description": (
        "建模需求歧义检查：检查用户的自然语言建模请求是否包含足够信息来生成精确 3D 模型。"
        "返回缺失项 + 矛盾项 + 追问建议。纯 LLM 轻调用，不调建模引擎。"
        "适合在 mfg_text_to_cad 前先检查，或在 Agent 对话中主动判断是否需要追问用户。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "用户的自然语言建模请求。",
            },
            "preset": {
                "type": "string",
                "enum": ["mechanical_part", "print_part", "ecommerce_display", "film_prop"],
                "description": "可选：场景模板。留空自动猜测。",
            },
        },
        "required": ["request"],
    },
}


def _mfg_home() -> Path:
    return Path.home() / ".vermes" / "mfgcad"


def _resolve_engine() -> tuple[str, Path]:
    """Return (python_exe, engine_dir). Raise RuntimeError if not provisioned."""
    engine_dir = Path(os.environ.get("MFG_CAD_ENGINE_DIR", str(_ENGINE_DIR_DEFAULT))).resolve()
    if not (engine_dir / "run_mac.py").is_file():
        raise RuntimeError(
            f"未找到 MAC 引擎于 {engine_dir}/run_mac.py。请先安装引擎：把 Multi-Agent-CAD 放到该目录"
            f"（或设 MFG_CAD_ENGINE_DIR 指向引擎根）。引擎含 build123d/cadquery-ocp/trimesh/"
            f"langgraph/aider/cadpy 等重依赖，刻意不随 Vermes 基础安装打包。安装后在其下建 venv"
            f"（含上述依赖），并用 MFG_CAD_ENGINE_PY 指向其 python；或 MFG_CAD_ENGINE_PY 指向"
            f"已有的 MAC venv。"
        )
    python_exe = os.environ.get("MFG_CAD_ENGINE_PY")
    if not python_exe:
        candidate = engine_dir / ".venv" / "bin" / "python"
        if not candidate.is_file():
            raise RuntimeError(
                f"引擎 venv 未就绪：{candidate} 不存在。请按 MAC 文档用 conda/pip 在 "
                f"{engine_dir} 下创建含 build123d/cadquery-ocp/trimesh/langgraph/aider/cadpy 的 venv，"
                "并设置 MFG_CAD_ENGINE_PY 指向其 python；或设 MFG_CAD_ENGINE_PY 指向已有的 MAC venv。"
            )
        python_exe = str(candidate)
    return python_exe, engine_dir


def _resolve_api_key() -> str:
    """统一从前端用户配置读取 LLM key —— 禁止各插件散读 os.environ。

    Vermes 设计铁律（agent/service_credentials.py）：所有外部 API 调用都应从
    用户中央 API 配置读取，而非 ``os.environ.get("XXX_API_KEY")``。

    解析优先级：
      1. service_credentials.get_api_key("mfgcad") —— 用户在统一凭证层为
         「制造 CAD」单独设的 key（前端单字段渲染，env 名 MFG_CAD_API_KEY）。
         留空表示复用主 Agent 的 key。
      2. 复用用户在前端为 Vermes 主 Agent 配的同一把 LLM key
         （auth.resolve_api_key_provider_credentials(active_provider)）。
         这样「一个 API 设置，处处可用」，符合用户预期。
    两者皆空返回 ""，由调用方提示去前端配置。
    """
    # 1) mfgcad 专属覆盖（统一凭证层）
    try:
        from agent.service_credentials import get_api_key as _sc_get
        k = _sc_get("mfgcad")
        if k:
            return k
    except Exception:
        pass
    # 2) 复用主 Agent 的活跃 provider key（统一前端设置）
    try:
        from vermes_cli.auth import (
            get_active_provider,
            resolve_api_key_provider_credentials,
        )
        pid = get_active_provider()
        if pid:
            creds = resolve_api_key_provider_credentials(pid) or {}
            ak = creds.get("api_key")
            if ak:
                return ak
    except Exception:
        pass
    return ""


def _resolve_api_key_provider_base_url() -> str:
    """返回活跃 provider 的 base_url，供多模态工具复用。"""
    try:
        from vermes_cli.auth import (
            get_active_provider,
            resolve_api_key_provider_credentials,
        )
        pid = get_active_provider()
        if pid:
            creds = resolve_api_key_provider_credentials(pid) or {}
            return creds.get("base_url", "")
    except Exception:
        pass
    return ""


def _parse_engine_json(stdout: str) -> Optional[dict]:
    """Extract the single JSON result line emitted by run_mac.py."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


async def _handle_mfg_text_to_cad(args: dict, **kw: Any) -> str:
    request = (args.get("request") or "").strip()
    if not request:
        return "❌ 缺少必填参数 request（自然语言建模需求，如『做一个外径 60mm 壁厚 3mm 高 100mm 的笔筒』）。"

    session_id = (args.get("session_id") or "").strip() or f"auto_{int(time.time())}"
    output_dir = args.get("output_dir") or str(_mfg_home() / "output" / session_id)
    workflow_id = args.get("workflow_id") or "original"
    checkpoint = bool(args.get("checkpoint"))
    preset = (args.get("preset") or "").strip() or None
    auto_clarify = args.get("auto_clarify")
    if auto_clarify is None:
        auto_clarify = True  # 默认开启歧义检查

    # ── P0a 歧义澄清前置 ──
    # auto_clarify=true 时先检查请求是否清晰，不清晰则返回追问不调引擎
    if auto_clarify:
        try:
            from vermes_cli.mfgcad.clarify import check_clarity
            clarity = await check_clarity(request, preset)
            if not clarity.get("is_clear"):
                q = clarity.get("clarification_question", "")
                missing = clarity.get("missing", [])
                conflicts = clarity.get("conflicts", [])
                lines = ["⏸ 需要补充信息才能精确建模，请回答以下问题后重试：", ""]
                if q:
                    lines.append(q)
                if missing:
                    lines.append("")
                    lines.append("缺失项：")
                    for m in missing:
                        lines.append(f"  • {m.get('label', m.get('name', '?'))}: {m.get('reason', '')}")
                if conflicts:
                    lines.append("")
                    lines.append("矛盾项：")
                    for c in conflicts:
                        lines.append(f"  • {c.get('reason', '')}: {', '.join(c.get('items', []))}")
                lines.append("")
                lines.append(f"补充后可直接调用 mfg_text_to_cad（设 auto_clarify=false 跳过检查），")
                lines.append(f"或先调 mfg_clarify 检查是否清晰。")
                return "\n".join(lines)
            # 清晰 → 用增强后的 request（补全默认值）
            enhanced = clarity.get("enhanced_request", request)
            if enhanced and enhanced != request:
                request = enhanced
        except Exception:
            pass  # clarify 失败 fail-open，不阻断建模

    # ── 多后端路由（P1） ──
    # 据 preset.engine 选后端，无 preset 默认 mac
    from vermes_cli.mfgcad.engine_backends import resolve_backend
    from vermes_cli.mfgcad.clarify import get_preset as _get_preset

    preset_def = _get_preset(preset) if preset else None

    try:
        backend = resolve_backend(preset_def)
    except RuntimeError as e:
        return f"❌ 引擎未就绪: {e}"

    # 透传 API key
    env = dict(os.environ)
    key = _resolve_api_key()
    if key:
        env["DASHSCOPE_API_KEY"] = key
        env["DS_API_KEY"] = key
        env["OPENAI_API_KEY"] = key
        try:
            from vermes_cli.auth import (
                get_active_provider,
                resolve_api_key_provider_credentials,
            )
            pid = get_active_provider()
            if pid:
                creds = resolve_api_key_provider_credentials(pid) or {}
                bu = creds.get("base_url") or ""
                if bu:
                    env["OPENAI_API_BASE"] = bu
        except Exception:
            pass
    else:
        if backend.name == "trellis":
            if not env.get("TRELLIS_CLOUD_API_KEY"):
                return ("❌ 未配置 LLM API key。TRELLIS 云模式需设 TRELLIS_CLOUD_API_KEY；"
                        "本地模式需先安装引擎。或在「设置 → API」配置 LLM key。")
        else:
            return ("❌ 未配置 LLM API key。请在 Vermes 前端「设置 → API」中为「制造 CAD」"
                    "填一个 DeepSeek/OpenAI 兼容 key，或直接使用已为主 Agent 配置的同一把 key"
                    "（mfgcad 会自动复用活跃 provider 的 key）；引擎需要 key 才能调用大模型。")

    # 调后端
    try:
        result = await backend.generate(
            request=request,
            output_dir=output_dir,
            preset=preset_def,
            env=env,
            workflow_id=workflow_id,
            checkpoint=checkpoint,
        )
    except Exception as e:
        return f"❌ 引擎调用失败: {type(e).__name__}: {e}"

    ok = result.ok
    files = result.files or {}
    volume = result.volume_mm3
    qa = result.qa or {}
    passed = qa.get("passed", 0)
    issues = qa.get("issues", [])
    step_path = files.get("step")
    stl_path = files.get("stl")
    stl_3mf_path = files.get("3mf")
    glb_path = files.get("glb")
    preview_path = files.get("png")

    # 落状态化 session 记录
    try:
        sess_dir = _mfg_home() / "sessions" / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        (sess_dir / "session.json").write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "request": request,
                    "workflow_id": workflow_id,
                    "checkpoint": checkpoint,
                    "backend": backend.name,
                    "ok": ok,
                    "step_path": step_path,
                    "stl_path": stl_path,
                    "stl_3mf_path": stl_3mf_path,
                    "glb_path": glb_path,
                    "preview_path": preview_path,
                    "volume_mm3": volume,
                    "qa": qa,
                    "error_type": result.error_type,
                    "ts": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    vol_str = f"{volume:.2f} mm³（{volume/1000:.3f} cm³）" if volume is not None else ""

    if not ok:
        return f"❌ 建模失败（{result.error_type}）：{result.message or ''}"

    # ── 按后端类型格式化输出 ──
    if backend.name == "mac":
        if checkpoint:
            return (
                f"⏸ CHECKPOINT 人工核对：候选 STEP 已生成 {step_path}\n"
                f"体积 {vol_str}；双引擎校验通过 {passed} 项"
                + (f"（注意：{'; '.join(issues)}）" if issues else "")
                + (f"\nSTL（切片/打印）: {stl_path}" if stl_path else "")
                + (f"\n3MF（Bambu/切片）: {stl_3mf_path}" if stl_3mf_path else "")
                + f"\n请人工核对尺寸/拓扑。确认后再次调用 mfg_text_to_cad"
                + f"（同一 session_id={session_id}，checkpoint=false）定稿。"
            )
        return (
            f"✅ STEP 已生成：{step_path}\n"
            f"体积 {vol_str}；双引擎校验通过 {passed} 项"
            + (f"（提示：{'; '.join(issues)}）" if issues else "")
            + (f"\nSTL（切片/打印）: {stl_path}" if stl_path else "")
            + (f"\n3MF（Bambu/切片）: {stl_3mf_path}" if stl_3mf_path else "")
            + f"\n会话 session_id={session_id}。可用 CAD 软件打开 STEP/STL 查看。"
        )
    elif backend.name == "trellis":
        return (
            f"✅ 3D 模型已生成\n"
            + (f"GLB（网页/AR 展示）: {glb_path}\n" if glb_path else "")
            + (f"预览图: {preview_path}\n" if preview_path else "")
            + (f"体积 {vol_str}\n" if vol_str else "")
            + (f"（提示：{'; '.join(issues)}）" if issues else "")
            + f"\n会话 session_id={session_id}。可用 Three.js/ModelViewer 加载 GLB 展示。"
        )
    else:
        parts = ["✅ 模型已生成"]
        for fmt, p in files.items():
            if p:
                parts.append(f"{fmt.upper()}: {p}")
        if vol_str:
            parts.append(f"体积 {vol_str}")
        parts.append(f"会话 session_id={session_id}")
        return "\n".join(parts)


async def _handle_mfg_clarify(args: dict, **kw: Any) -> str:
    """建模需求歧义检查（独立工具，不调引擎）。"""
    request = (args.get("request") or "").strip()
    if not request:
        return "❌ 缺少参数 request。"

    preset = (args.get("preset") or "").strip() or None

    try:
        from vermes_cli.mfgcad.clarify import check_clarity
        result = await check_clarity(request, preset)
    except Exception as e:
        return f"❌ 歧义检查失败: {type(e).__name__}: {e}"

    is_clear = result.get("is_clear", True)
    preset_name = result.get("preset", "unknown")
    extracted = result.get("extracted", {})
    missing = result.get("missing", [])
    conflicts = result.get("conflicts", [])
    question = result.get("clarification_question", "")
    enhanced = result.get("enhanced_request", request)

    lines = [f"📋 歧义检查（场景: {preset_name}）", ""]

    if is_clear:
        lines.append("✅ 请求信息完整，可直接建模。")
        if extracted:
            lines.append("")
            lines.append("已识别信息：")
            for k, v in extracted.items():
                lines.append(f"  • {k}: {v}")
        if enhanced != request:
            lines.append("")
            lines.append(f"增强请求：{enhanced}")
        return "\n".join(lines)

    lines.append("⏸ 需要补充信息：")
    if question:
        lines.append("")
        lines.append(question)
    if missing:
        lines.append("")
        lines.append("缺失项：")
        for m in missing:
            lines.append(f"  • {m.get('label', m.get('name', '?'))}: {m.get('reason', '')}")
    if conflicts:
        lines.append("")
        lines.append("矛盾项：")
        for c in conflicts:
            lines.append(f"  • {c.get('reason', '')}: {', '.join(c.get('items', []))}")
    lines.append("")
    lines.append("补充信息后调用 mfg_text_to_cad 建模。")
    return "\n".join(lines)


def register_tools(host_api=None):
    """Register mfgcad tools in the global registry.

    Called by agent/module_loader after host_api injection. host_api is
    accepted for signature parity with ScholarForge but unused — mfgcad is
    self-contained (its own LLM client + engine venv).
    """
    # 接入统一凭证层：声明「制造 CAD」服务，使前端「设置 → API」能单字段渲染
    # 其 key（env 名 MFG_CAD_API_KEY）。留空则 _resolve_api_key 复用主 Agent
    # 的活跃 provider key —— 不散读 os.environ。
    try:
        from agent.service_credentials import register_service
        register_service(
            "mfgcad",
            api_key_env_var="MFG_CAD_API_KEY",
            label="制造 CAD (Multi-Agent-CAD)",
            category="services",
            description="自然语言生成 STEP 三维模型所需的 LLM key（DeepSeek/OpenAI 兼容）。留空则复用 Vermes 主 Agent 的 LLM key。",
        )
    except Exception:
        pass  # 凭证层缺失不阻断工具注册

    registry.register(
        name="mfg_text_to_cad",
        toolset="mfgcad",
        schema=MFGCAD_SCHEMA,
        handler=_handle_mfg_text_to_cad,
        is_async=True,
        emoji="🏭",
        description="3D 建模：自然语言需求直接生成 STEP 三维模型（双引擎校验）",
    )

    registry.register(
        name="mfg_clarify",
        toolset="mfgcad",
        schema=MFG_CLARIFY_SCHEMA,
        handler=_handle_mfg_clarify,
        is_async=True,
        emoji="🔍",
        description="建模需求歧义检查：检查请求是否清晰，返回追问建议",
    )

    # P3：局部编辑 + 纹理绘制 + 几何变换
    try:
        from vermes_cli.mfgcad.edit_tools import register_tools as _register_edit_tools
        _register_edit_tools(host_api)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfgcad P3 edit tools registration failed: %s", e)

    # P4：多模态控制（参考图/草图/bbox）
    try:
        from vermes_cli.mfgcad.multimodal_tools import register_tools as _register_mm_tools
        _register_mm_tools(host_api)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfgcad P4 multimodal tools registration failed: %s", e)
