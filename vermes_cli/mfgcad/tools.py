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
            "auto_setup": {
                "type": "boolean",
                "description": "可选：true=若检测到 MAC 引擎 venv 未安装，Agent 自动一键安装（建 venv+装依赖+验证）后无感继续出图（默认 true，首次使用才会触发，仅此一次）。false=不自动安装，改为返回引导让你/Agent 调用 mfg_setup_engine。",
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


# ── M1 FreeCAD 专业精修后端（ProToolAdapter 参考实现） ──
# 把「模型无关 × 工具无关」的 ProToolAdapter 接入 agent 工具层。引擎缺失时优雅降级，
# 不影响既有 build123d 粗模兜底（Track A）。详见 PRO_TOOL_ADAPTER_DESIGN.md。
_FREECADE_ADAPTER = None


def _get_freecad_adapter():
    """返回（缓存的）FreeCADAdapter 单例，复用常驻 bridge 子进程。"""
    global _FREECADE_ADAPTER
    if _FREECADE_ADAPTER is None:
        from vermes_cli.mfgcad.backends import FreeCADAdapter

        _FREECADE_ADAPTER = FreeCADAdapter(sessions_root=str(_mfg_home() / "sessions"))
    return _FREECADE_ADAPTER


def _ensure_freecad_doc(adapter, session_id: str):
    """打开已有 .FCStd（真相源），否则从会话内 STEP 导入；无模型返回 None。

    实现 §5：session = 特征树，重新编辑即重新 open(.FCStd) 追加 edit-op，天然可回滚。
    """
    fcstd = _mfg_home() / "sessions" / session_id / "native.FCStd"
    if fcstd.exists():
        adapter.open(str(fcstd))
        return fcstd
    step = None
    for base in (_mfg_home() / "sessions" / session_id, _mfg_home() / "output" / session_id):
        if not base.is_dir():
            continue
        for ext in ("*.step", "*.stp"):
            hits = sorted(base.glob(ext))
            if hits:
                step = hits[0]
                break
        if step is not None:
            break
    if step is None:
        return None
    res = adapter.import_step(session_id, str(step))
    return str(res.native_doc) if res.ok else None


def _write_session_record(session_id: str, record: dict) -> None:
    """把会话状态落到 sessions/<id>/session.json（自动合并 session_id/ts）。"""
    sess_dir = _mfg_home() / "sessions" / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    record = {**record, "session_id": session_id, "ts": record.get("ts", int(time.time()))}
    (sess_dir / "session.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )


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


def _resolve_mfgcad_service_creds() -> dict:
    """读取 mfgcad 专属服务配置（api_key + base_url + model）。

    优先级：统一凭证层 mfgcad 服务 > 主 Agent 活跃 provider。
    返回 dict: {api_key, base_url, model}，未配置的字段为空字符串。
    """
    creds = {"api_key": "", "base_url": "", "model": ""}
    # 1) mfgcad 专属配置（统一凭证层）
    try:
        from agent.service_credentials import get_service_credentials
        svc = get_service_credentials("mfgcad")
        creds["api_key"] = svc.get("api_key") or ""
        creds["base_url"] = svc.get("base_url") or ""
    except Exception:
        pass
    # model 从 extra_fields 读（中央配置 services.mfgcad.MFG_CAD_MODEL 或 env）
    if not creds["model"]:
        creds["model"] = os.environ.get("MFG_CAD_MODEL", "")
    # 2) 回退主 Agent 活跃 provider（只补空字段）
    if not creds["api_key"]:
        try:
            from vermes_cli.auth import (
                get_active_provider,
                resolve_api_key_provider_credentials,
            )
            pid = get_active_provider()
            if pid:
                c = resolve_api_key_provider_credentials(pid) or {}
                creds["api_key"] = c.get("api_key") or ""
                if not creds["base_url"]:
                    creds["base_url"] = c.get("base_url") or ""
        except Exception:
            pass
    return creds


def _resolve_api_key() -> str:
    """统一从前端用户配置读取 LLM key —— 禁止各插件散读 os.environ。

    优先级：
      1. mfgcad 专属 key（前端「设置 → 服务 → 制造 CAD」单字段，env MFG_CAD_API_KEY）
      2. 复用主 Agent 的活跃 provider key
    """
    return _resolve_mfgcad_service_creds()["api_key"]


def _resolve_api_key_provider_base_url() -> str:
    """返回 LLM base_url：mfgcad 专属 > 活跃 provider。"""
    return _resolve_mfgcad_service_creds()["base_url"]


def _resolve_mfgcad_model() -> str:
    """返回 mfgcad 专属模型名（env MFG_CAD_MODEL），未配置返回空。"""
    return _resolve_mfgcad_service_creds()["model"]


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
    auto_setup = args.get("auto_setup")
    if auto_setup is None:
        auto_setup = True  # 默认首次自动安装引擎
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

    # ── 引擎自安装引导（首次使用 Agent 自理）──
    # mac 后端依赖独立 venv；若未就绪，按 auto_setup 自动安装或返回引导。
    if backend.name == "mac":
        from vermes_cli.mfgcad import engine_setup

        ready, msg = await engine_setup.ensure_mac_ready(
            engine_setup.get_engine_dir(),
            auto_setup=auto_setup,
            include_aider=(workflow_id == "aider"),
        )
        if not ready:
            return msg

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

    # 参数化重建地基：若引擎落盘了 build123d 源码，则抽取参数并持久化
    build123d_source_path = None
    has_parameters = False
    if ok:
        try:
            from vermes_cli.mfgcad.parametric import (
                acquire_source,
                persist_source,
                extract_parameters,
                save_parameters,
            )
            src = acquire_source(session_id, output_dir)
            if src:
                persist_source(session_id, src)
                params = extract_parameters(src)
                if params:
                    save_parameters(session_id, params)
                    has_parameters = True
                build123d_source_path = str(
                    _mfg_home() / "sessions" / session_id / "build123d_source.py"
                )
        except Exception:
            pass

    # 落状态化 session 记录
    try:
        _write_session_record(
            session_id,
            {
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
                "build123d_source": build123d_source_path,
                "has_parameters": has_parameters,
            },
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


async def _handle_mfg_rebuild_parametric(args: dict, **kw: Any) -> str:
    """参数化重建：对已有 build123d 会话改参后重建（拖滑块改参的核心入口）。

    流程：load 源会话的 build123d 源码 → 校验参数名 → 源码级 apply_parameters
    改参 → 调 MAC 引擎 rebuild_from_script 重建 → 落新 session（含新源码/参数）。
    网格引擎（TRELLIS）无源码、不含可调常量，自然走「无可用源码」降级分支。
    """
    base_session_id = (args.get("base_session_id") or "").strip()
    if not base_session_id:
        return "❌ 缺少必填参数 base_session_id（要改参的源会话 ID，从 /api/mfgcad/sessions 列表获取）。"
    params = args.get("parameters")
    if not isinstance(params, dict) or not params:
        return "❌ 缺少必填参数 parameters（格式：{\"参数名\": 新数值}，如 {\"HEIGHT\": 120.0}）。"
    auto_setup = args.get("auto_setup")
    if auto_setup is None:
        auto_setup = True

    from vermes_cli.mfgcad.parametric import (
        load_source,
        acquire_source,
        apply_parameters,
        persist_source,
        extract_parameters,
        save_parameters,
    )
    source = load_source(base_session_id) or acquire_source(base_session_id, None)
    if not source:
        return (
            f"❌ 会话 {base_session_id} 无可用的 build123d 源码，无法参数化重建。"
            "可能原因：① 该会话由网格引擎（TRELLIS）生成，尺寸不可编辑；"
            "② MAC 引擎未落盘 build123d_source.py（需引擎支持 --script 重建）。"
        )

    # 校验参数名确实存在于源码，避免静默改错对象
    available = extract_parameters(source)
    unknown = [k for k in params if k not in available]
    if unknown:
        return (
            f"❌ 参数名不存在于源码：{', '.join(unknown)}。"
            f"可用参数：{', '.join(available.keys()) or '（无，该源码无可调常量）'}"
        )

    try:
        new_source = apply_parameters(source, params)
    except Exception as e:
        return f"❌ 参数重写失败: {type(e).__name__}: {e}"

    new_session_id = f"auto_{int(time.time())}"
    output_dir = str(_mfg_home() / "output" / new_session_id)

    from vermes_cli.mfgcad.engine_backends import resolve_backend
    from vermes_cli.mfgcad import engine_setup

    backend = resolve_backend(None)  # 参数化重建固定走默认后端（mac，参数化 B-Rep）
    ready, msg = await engine_setup.ensure_mac_ready(
        engine_setup.get_engine_dir(), auto_setup=auto_setup, include_aider=False
    )
    if not ready:
        return msg

    env = {}
    api_key = _resolve_api_key()
    if api_key:
        env["MFG_CAD_API_KEY"] = api_key

    try:
        result = await backend.rebuild_from_script(new_source, output_dir, workflow_id="original", env=env)
    except Exception as e:
        return f"❌ 引擎重建失败: {type(e).__name__}: {e}"

    # 持久化新会话（含新源码 + 本次应用的参数），供继续微调
    build123d_source_path = str(_mfg_home() / "sessions" / new_session_id / "build123d_source.py")
    has_parameters = False
    try:
        persist_source(new_session_id, new_source)
        new_params = extract_parameters(new_source)
        if new_params:
            save_parameters(new_session_id, new_params)
            has_parameters = True
    except Exception:
        pass

    step_path = result.files.get("step")
    stl_path = result.files.get("stl")
    stl_3mf_path = result.files.get("3mf")
    glb_path = result.files.get("glb")
    preview_path = result.files.get("png")
    volume = result.volume_mm3
    qa = result.qa or {}

    try:
        _write_session_record(
            new_session_id,
            {
                "request": f"[参数化重建] base={base_session_id}",
                "workflow_id": "original",
                "checkpoint": False,
                "backend": backend.name,
                "ok": result.ok,
                "step_path": step_path,
                "stl_path": stl_path,
                "stl_3mf_path": stl_3mf_path,
                "glb_path": glb_path,
                "preview_path": preview_path,
                "volume_mm3": volume,
                "qa": qa,
                "error_type": result.error_type,
                "build123d_source": build123d_source_path,
                "has_parameters": has_parameters,
                "base_session_id": base_session_id,
                "applied_parameters": params,
            },
        )
    except Exception:
        pass

    if not result.ok:
        return f"❌ 参数化重建失败（{result.error_type}）：{result.message or ''}"

    vol_str = f"{volume:.2f} mm³（{volume/1000:.3f} cm³）" if volume is not None else ""
    return (
        f"✅ 参数化重建完成（基于 {base_session_id}，改参：{params}）\n"
        + (f"STEP: {step_path}\n" if step_path else "")
        + (f"STL: {stl_path}\n" if stl_path else "")
        + (f"3MF: {stl_3mf_path}\n" if stl_3mf_path else "")
        + (f"体积 {vol_str}\n" if vol_str else "")
        + f"新会话 session_id={new_session_id}。可继续拖滑块微调或导出 STEP/STL。"
    )


MFG_REBUILD_SCHEMA = {
    "type": "object",
    "properties": {
        "base_session_id": {
            "type": "string",
            "description": "要改参的源会话 ID（需 has_parameters=true、含 build123d 源码的会话）。",
        },
        "parameters": {
            "type": "object",
            "description": (
                "要修改的参数（参数名 → 新数值）。如 {\"HEIGHT\": 120.0, \"HOLE_COUNT\": 12}。"
                "参数名须与 /api/mfgcad/sessions/<id>/parameters 返回的键一致。"
            ),
            "additionalProperties": {"type": "number"},
        },
        "auto_setup": {
            "type": "boolean",
            "description": "可选：true=若 MAC 引擎 venv 未安装则自动安装后重建（默认 true）。false=不自动安装，改为返回引导调用 mfg_setup_engine。",
        },
    },
    "required": ["base_session_id", "parameters"],
}

MFG_BOM_SCHEMA = {
    "type": "object",
    "properties": {
        "session_id": {
            "type": "string",
            "description": "要生成 BOM 的建模会话 ID。",
        },
        "preset": {
            "type": "string",
            "description": "行业 preset 名称（如 mechanical_part/print_part），用于补充材料/工艺信息。留空自动推断。",
        },
    },
    "required": ["session_id"],
}

MFG_PROJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "list", "get", "update", "delete", "link", "unlink"],
            "description": "项目管理动作。",
        },
        "project_id": {"type": "integer", "description": "项目 ID（get/update/delete/link/unlink 需要）。"},
        "title": {"type": "string", "description": "项目名称（create 需要）。"},
        "template": {"type": "string", "description": "模板名（create 可选）：injection_mold/3d_print/mechanical_part/ecommerce_display/film_prop。"},
        "notes": {"type": "string", "description": "项目备注（create/update 可选）。"},
        "session_id": {"type": "string", "description": "要关联/解关联的会话 ID（link/unlink 需要）。"},
    },
    "required": ["action"],
}

MFG_TEMPLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "get"],
            "description": "查看模板列表或单个模板详情。",
        },
        "template": {"type": "string", "description": "模板名（get 需要）。"},
    },
    "required": ["action"],
}


MFG_SETUP_SCHEMA = {
    "type": "object",
    "properties": {
        "engine": {
            "type": "string",
            "enum": ["mac", "trellis"],
            "description": "可选：要安装的引擎。默认 mac（Multi-Agent-CAD，精确 CAD）。trellis 需 GPU 权重，暂不支持自动安装。",
        },
        "force": {
            "type": "boolean",
            "description": "可选：true=即使 venv 已存在也重装全部依赖（不删 venv）。用于依赖损坏后修复。默认 false。",
        },
        "include_aider": {
            "type": "boolean",
            "description": "可选：true=额外安装 aider（仅 --workflow-id aider 自修复路径需要，较大且 best-effort）。默认 false。",
        },
    },
    "required": [],
}


async def _handle_mfg_setup_engine(args: dict, **kw: Any) -> str:
    """一键安装 3D 建模引擎（Agent 自理，终端用户零配置）。

    检测 MAC 引擎 venv 是否就绪：缺失则建 venv → 升 pip → 装核心依赖
    （build123d/cadquery-ocp/trimesh/langgraph/openai/pydantic）→ 装本地 cadpy
    → 可选 aider → 验证关键导入。幂等：已就绪直接返回。

    返回人类可读状态；失败返回含重试/手动命令的引导。
    """
    from vermes_cli.mfgcad import engine_setup

    engine = (args.get("engine") or "mac").strip()
    force = bool(args.get("force"))
    include_aider = bool(args.get("include_aider"))

    if engine != "mac":
        return (
            "⚠️ 目前仅支持自动安装 mac 引擎（Multi-Agent-CAD）。"
            "trellis 需本地 GPU 权重，请按文档手动部署到 ~/.vermes/engines/trellis/。"
        )

    ed = engine_setup.get_engine_dir()
    if not engine_setup.engine_code_present(ed):
        return (
            "❌ 未找到 MAC 引擎代码于 %s（需 run_mac.py + multi_agent_cad/）。\n"
            "自动安装只解决 venv/依赖，不能替你下载引擎本身。请把 Multi-Agent-CAD 引擎\n"
            "（含 run_mac.py 与 multi_agent_cad/ 包）放到该目录，或设 MFG_CAD_ENGINE_DIR 指向引擎根。"
        ) % ed

    if engine_setup.is_provisioned(ed) and not force:
        return f"✅ MAC 引擎已就绪（{ed}/.venv）。无需安装，可直接建模。"

    # 收集进度，拼成一个可读状态回执
    steps: list[str] = []
    res = await engine_setup.provision_engine(
        ed, force=force, include_aider=include_aider, progress=steps.append
    )

    if res["ok"]:
        summary = "\n".join(steps)
        return (
            f"✅ MAC 引擎安装完成（{ed}/.venv）。\n{summary}\n"
            "现在可直接调用 mfg_text_to_cad 出图，终端用户无需其他操作。"
        )
    return engine_setup.format_setup_failure(res, ed)


async def _handle_mfg_generate_bom(args: dict, **kw: Any) -> str:
    """生成 BOM + 组装指南。"""
    session_id = args.get("session_id", "")
    preset_name = args.get("preset", "")
    if not session_id:
        return "❌ 缺少 session_id 参数。"

    # 解析凭证
    api_key, base_url, model = _resolve_mfgcad_service_creds()
    if not api_key:
        return (
            "❌ 未配置 LLM API Key。请在「设置 → 服务 → 制造 CAD」填写 API Key，"
            "或在主 Agent 设置中配置一个活跃的 Provider。"
        )

    # 加载 preset（可选）
    preset = None
    if preset_name:
        try:
            from vermes_cli.mfgcad.clarify import _load_presets
            all_presets = _load_presets()
            preset = all_presets.get(preset_name)
        except Exception:
            pass

    try:
        from vermes_cli.mfgcad.bom import generate_bom
        markdown = await generate_bom(
            session_id=session_id,
            api_key=api_key,
            base_url=base_url,
            model=model,
            preset=preset,
        )
        return markdown
    except Exception as e:
        return f"❌ BOM 生成失败：{type(e).__name__}: {e}"


async def _handle_mfg_project(args: dict, **kw: Any) -> str:
    """3D 建模项目管理。"""
    from vermes_cli.mfgcad import projects as proj_mod

    action = args.get("action", "list")

    if action == "create":
        title = args.get("title", "")
        if not title:
            return "❌ 创建项目需要 title 参数。"
        template = args.get("template", "")
        notes = args.get("notes", "")
        p = proj_mod.create_project(title, template=template, notes=notes)
        lines = [f"✅ 项目创建成功 #{p['id']}「{p['title']}」"]
        if template:
            t = proj_mod.get_template(template)
            if t:
                lines.append(f"📋 模板：{t['name']} — {t['description']}")
                lines.append(f"💡 建议请求：{t.get('suggested_request', '')}")
        return "\n".join(lines)

    elif action == "list":
        projects = proj_mod.list_projects()
        if not projects:
            return "暂无 3D 建模项目。用 action=create 创建。"
        lines = ["## 📦 3D 建模项目\n"]
        for p in projects:
            tpl = p.get("template", "")
            tpl_tag = f" [{tpl}]" if tpl else ""
            n_sessions = len(p.get("session_ids", []))
            lines.append(f"- #{p['id']}「{p['title']}」{tpl_tag} — {n_sessions} 个会话")
        return "\n".join(lines)

    elif action == "get":
        pid = args.get("project_id")
        if not pid:
            return "❌ 需要 project_id 参数。"
        p = proj_mod.get_project(pid)
        if not p:
            return f"❌ 项目 #{pid} 不存在。"
        lines = [f"## 项目 #{p['id']}「{p['title']}」"]
        if p.get("template"):
            lines.append(f"模板: {p['template']}")
        if p.get("notes"):
            lines.append(f"备注: {p['notes']}")
        lines.append(f"会话数: {len(p.get('session_ids', []))}")
        for sid in p.get("session_ids", []):
            lines.append(f"  - {sid}")
        return "\n".join(lines)

    elif action == "update":
        pid = args.get("project_id")
        if not pid:
            return "❌ 需要 project_id 参数。"
        kwargs = {}
        for k in ("title", "template", "notes"):
            if k in args:
                kwargs[k] = args[k]
        p = proj_mod.update_project(pid, **kwargs)
        if not p:
            return f"❌ 项目 #{pid} 不存在。"
        return f"✅ 项目 #{pid} 已更新。"

    elif action == "delete":
        pid = args.get("project_id")
        if not pid:
            return "❌ 需要 project_id 参数。"
        if proj_mod.delete_project(pid):
            return f"✅ 项目 #{pid} 已删除。"
        return f"❌ 项目 #{pid} 不存在。"

    elif action == "link":
        pid = args.get("project_id")
        sid = args.get("session_id")
        if not pid or not sid:
            return "❌ 需要 project_id 和 session_id 参数。"
        if proj_mod.link_session(pid, sid):
            return f"✅ 会话 {sid} 已关联到项目 #{pid}。"
        return f"❌ 项目 #{pid} 不存在。"

    elif action == "unlink":
        pid = args.get("project_id")
        sid = args.get("session_id")
        if not pid or not sid:
            return "❌ 需要 project_id 和 session_id 参数。"
        if proj_mod.unlink_session(pid, sid):
            return f"✅ 会话 {sid} 已从项目 #{pid} 解除关联。"
        return f"❌ 项目 #{pid} 不存在。"

    return f"❌ 未知动作：{action}"


async def _handle_mfg_template(args: dict, **kw: Any) -> str:
    """查看 3D 建模模板。"""
    from vermes_cli.mfgcad import projects as proj_mod

    action = args.get("action", "list")

    if action == "list":
        templates = proj_mod.list_templates()
        lines = ["## 📋 3D 建模模板\n"]
        for key, t in templates.items():
            lines.append(f"### {key} — {t['name']}")
            lines.append(f"{t['description']}")
            params = t.get("default_params", {})
            if params:
                lines.append("**默认参数**:")
                for pk, pv in params.items():
                    lines.append(f"  - {pk}: {pv}")
            sr = t.get("suggested_request")
            if sr:
                lines.append(f"💡 **建议请求**：{sr}")
            lines.append("")
        return "\n".join(lines)

    elif action == "get":
        name = args.get("template", "")
        t = proj_mod.get_template(name)
        if not t:
            return f"❌ 模板「{name}」不存在。可用模板：{', '.join(proj_mod.list_templates().keys())}"
        lines = [f"## 📋 {name} — {t['name']}"]
        lines.append(f"{t['description']}")
        lines.append(f"\n**Preset**: {t.get('preset', '')}")
        params = t.get("default_params", {})
        if params:
            lines.append("\n**默认参数**:")
            for pk, pv in params.items():
                lines.append(f"  - {pk}: {pv}")
        sr = t.get("suggested_request")
        if sr:
            lines.append(f"\n💡 **建议请求**：{sr}")
        return "\n".join(lines)

    return f"❌ 未知动作：{action}"


def register_tools(host_api=None):
    """Register mfgcad tools in the global registry.

    Called by agent/module_loader after host_api injection. host_api is
    accepted for signature parity with ScholarForge but unused — mfgcad is
    self-contained (its own LLM client + engine venv).
    """
    # 接入统一凭证层：声明「制造 CAD」服务的完整三字段配置（key + base_url + model），
    # 使前端「设置 → 服务 → 制造 CAD」可独立配置 3D 建模专用的 LLM 厂商。
    # 留空则回退复用 Vermes 主 Agent 的活跃 provider（key/base_url/model 全派生）。
    try:
        from agent.service_credentials import register_service
        register_service(
            "mfgcad",
            api_key_env_var="MFG_CAD_API_KEY",
            base_url_env_var="MFG_CAD_BASE_URL",
            label="制造 CAD (3D 建模)",
            category="services",
            description="3D 建模专用 LLM 配置。留空则复用 Vermes 主 Agent 的活跃 Provider。填写后 3D 建模（含歧义检查、视觉理解）走此独立配置。",
            extra_fields=[
                {"key": "MFG_CAD_MODEL", "label": "制造 CAD 模型名", "secret": False},
            ],
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

    # 引擎自安装（Agent 自理，终端用户零配置；首次使用 mac 未就绪时由 mfg_text_to_cad 自动触发）
    try:
        registry.register(
            name="mfg_setup_engine",
            toolset="mfgcad",
            schema=MFG_SETUP_SCHEMA,
            handler=_handle_mfg_setup_engine,
            is_async=True,
            emoji="⚙️",
            description="一键安装 3D 建模引擎（MAC）：建 venv+装依赖+验证，终端用户零配置；引擎缺失时 Agent 自动调用",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfg_setup_engine registration failed: %s", e)

    registry.register(
        name="mfg_clarify",
        toolset="mfgcad",
        schema=MFG_CLARIFY_SCHEMA,
        handler=_handle_mfg_clarify,
        is_async=True,
        emoji="🔍",
        description="建模需求歧义检查：检查请求是否清晰，返回追问建议",
    )

    registry.register(
        name="mfg_rebuild_parametric",
        toolset="mfgcad",
        schema=MFG_REBUILD_SCHEMA,
        handler=_handle_mfg_rebuild_parametric,
        is_async=True,
        emoji="🎚️",
        description="参数化重建：拖滑块改参后重建（改 build123d 源码而非网格），小白傻瓜式优化出最终版",
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

    # 切片②：BOM + 组装指南
    try:
        registry.register(
            name="mfg_generate_bom",
            toolset="mfgcad",
            schema=MFG_BOM_SCHEMA,
            handler=_handle_mfg_generate_bom,
            is_async=True,
            emoji="📋",
            description="BOM+组装指南：从建模会话生成结构化物料清单、组装步骤、成本估算、3D 打印建议",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfgcad BOM tool registration failed: %s", e)

    # 切片③：项目管理 + 模板
    try:
        registry.register(
            name="mfg_project",
            toolset="mfgcad",
            schema=MFG_PROJECT_SCHEMA,
            handler=_handle_mfg_project,
            is_async=True,
            emoji="📁",
            description="3D 建模项目管理：创建/列表/查看/更新/删除项目，关联建模会话",
        )
        registry.register(
            name="mfg_template",
            toolset="mfgcad",
            schema=MFG_TEMPLATE_SCHEMA,
            handler=_handle_mfg_template,
            is_async=True,
            emoji="📋",
            description="查看 3D 建模模板：注塑件/3D打印件/机械零件/电商展示/影视道具",
        )
        # 标准件库
        from .standard_parts import list_parts, get_part, search_parts, list_categories
        STANDARD_PART_SCHEMA = {
            "name": "mfg_standard_part",
            "description": "查询标准件库（螺丝/螺母/轴承/垫圈），获取件号和 STEP 文件路径",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "get", "search", "categories"],
                        "description": "list=列出件号, get=获取单个, search=搜索, categories=分类列表",
                    },
                    "part_id": {
                        "type": "string",
                        "description": "件号（action=get 时必填，如 M5_screw）",
                    },
                    "category": {
                        "type": "string",
                        "description": "分类（action=list 时可选：screws/nuts/bearings/washers）",
                    },
                    "query": {
                        "type": "string",
                        "description": "搜索词（action=search 时必填）",
                    },
                },
                "required": ["action"],
            },
        }

        async def _handle_mfg_standard_part(args: dict, **kw: Any) -> str:
            action = args.get("action", "list")
            if action == "list":
                parts = list_parts(args.get("category"))
                if not parts:
                    return "标准件库为空"
                lines = [f"标准件库（{len(parts)} 件）:"]
                for p in parts:
                    status = "✅" if p["available"] else "⬇"
                    lines.append(f"  {status} {p['id']}: {p['name']} ({p['standard']})")
                return "\n".join(lines)
            elif action == "get":
                part_id = args.get("part_id", "")
                info = get_part(part_id)
                if not info:
                    return f"未找到标准件: {part_id}"
                lines = [f"{info['name']} ({info['id']})", f"  标准: {info['standard']}"]
                if info.get("parameters"):
                    lines.append("  参数:")
                    for k, v in info["parameters"].items():
                        lines.append(f"    {k}: {v}mm")
                if info.get("file_path"):
                    lines.append(f"  STEP: {info['file_path']}")
                else:
                    lines.append("  STEP: 未下载（标准件库尚未配置）")
                return "\n".join(lines)
            elif action == "search":
                q = args.get("query", "")
                results = search_parts(q)
                if not results:
                    return f"未找到匹配 '{q}' 的标准件"
                lines = [f"搜索 '{q}'（{len(results)} 结果）:"]
                for r in results:
                    status = "✅" if r["available"] else "⬇"
                    lines.append(f"  {status} {r['id']}: {r['name']} ({r['standard']})")
                return "\n".join(lines)
            elif action == "categories":
                cats = list_categories()
                return f"标准件分类: {', '.join(cats)}"
            return f"未知操作: {action}"

        registry.register(
            name="mfg_standard_part",
            toolset="mfgcad",
            schema=STANDARD_PART_SCHEMA,
            handler=_handle_mfg_standard_part,
            is_async=True,
            emoji="🔧",
            description="查询标准件库（螺丝/螺母/轴承/垫圈），获取件号和参数",
        )
        # 制造链路
        from .manufacturing import export_dxf, slice_gcode, send_print
        MFG_DXF_SCHEMA = {
            "name": "mfg_export_dxf",
            "description": "从 STEP 文件导出 DXF（2D 投影，激光切割/钣金用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "filename": {"type": "string", "description": "STEP 文件名"},
                    "views": {"type": "array", "items": {"type": "string"}, "description": "投影视图（top/front/side）"},
                },
                "required": ["session_id", "filename"],
            },
        }

        async def _handle_mfg_export_dxf(args: dict, **kw: Any) -> str:
            sid = args.get("session_id", "")
            fname = args.get("filename", "")
            views = args.get("views", ["top", "front", "side"])
            output_dir = Path.home() / ".vermes" / "mfgcad" / "output" / sid
            step_file = output_dir / fname
            result = export_dxf(step_file, views=views)
            if result["ok"]:
                return f"✅ DXF 已导出: {result['dxf_path']}（视图: {', '.join(result.get('views', []))})"
            return f"❌ DXF 导出失败: {result['error']}"

        registry.register(
            name="mfg_export_dxf",
            toolset="mfgcad",
            schema=MFG_DXF_SCHEMA,
            handler=_handle_mfg_export_dxf,
            is_async=True,
            emoji="📐",
            description="从 STEP 导出 DXF（2D 工程图，激光切割用）",
        )

        MFG_SLICE_SCHEMA = {
            "name": "mfg_slice_gcode",
            "description": "调用本地切片软件生成 G-code（3D 打印用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "filename": {"type": "string", "description": "STL 文件名"},
                    "layer_height": {"type": "number", "description": "层高 mm，默认 0.2"},
                    "infill": {"type": "number", "description": "填充密度 %，默认 20"},
                    "filament": {"type": "string", "description": "耗材类型，默认 PLA"},
                    "dry_run": {"type": "boolean", "description": "只预览不执行，默认 true"},
                },
                "required": ["session_id", "filename"],
            },
        }

        async def _handle_mfg_slice_gcode(args: dict, **kw: Any) -> str:
            sid = args.get("session_id", "")
            fname = args.get("filename", "")
            layer_height = args.get("layer_height", 0.2)
            infill = args.get("infill", 20)
            filament = args.get("filament", "PLA")
            dry_run = args.get("dry_run", True)
            output_dir = Path.home() / ".vermes" / "mfgcad" / "output" / sid
            stl_file = output_dir / fname
            result = slice_gcode(stl_file, layer_height=layer_height, infill=infill,
                                 filament=filament, dry_run=dry_run)
            if result["ok"]:
                dry = " [dry-run]" if result.get("dry_run") else ""
                return f"✅ G-code 生成{dry}: {result['gcode_path']}（切片器: {result['slicer']}）\n  命令: {result.get('command', '')}"
            return f"❌ 切片失败: {result['error']}"

        registry.register(
            name="mfg_slice_gcode",
            toolset="mfgcad",
            schema=MFG_SLICE_SCHEMA,
            handler=_handle_mfg_slice_gcode,
            is_async=True,
            emoji="🖨️",
            description="调用切片软件生成 G-code（3D 打印）",
        )

        MFG_PRINT_SCHEMA = {
            "name": "mfg_send_print",
            "description": "推送 G-code 到 Bambu/拓竹打印机（当前仅 dry-run 预览，真推送待实现）",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID"},
                    "filename": {"type": "string", "description": "G-code 文件名"},
                    "printer_ip": {"type": "string", "description": "打印机 IP 地址"},
                    "access_code": {"type": "string", "description": "打印机访问码"},
                    "dry_run": {"type": "boolean", "description": "只预览不推送，默认 true"},
                },
                "required": ["session_id", "filename"],
            },
        }

        async def _handle_mfg_send_print(args: dict, **kw: Any) -> str:
            sid = args.get("session_id", "")
            fname = args.get("filename", "")
            printer_ip = args.get("printer_ip", "")
            access_code = args.get("access_code", "")
            dry_run = args.get("dry_run", True)
            output_dir = Path.home() / ".vermes" / "mfgcad" / "output" / sid
            gcode_file = output_dir / fname
            result = send_print(gcode_file, printer_ip=printer_ip, access_code=access_code, dry_run=dry_run)
            if result["ok"]:
                dry = " [dry-run]" if result.get("dry_run") else ""
                return f"✅ 打印推送{dry}: {result.get('message', '')}"
            return f"❌ 推送失败: {result['error']}"

        registry.register(
            name="mfg_send_print",
            toolset="mfgcad",
            schema=MFG_PRINT_SCHEMA,
            handler=_handle_mfg_send_print,
            is_async=True,
            emoji="🚀",
            description="推送 G-code 到打印机（当前仅 dry-run 预览，真推送待实现）",
        )
        # 机器人模型导出
        from .robot_export import RobotLink, RobotJoint, export_urdf, export_srdf, export_sdf
        MFG_URDF_SCHEMA = {
            "name": "mfg_export_urdf",
            "description": "从多零件组装体导出 URDF/SRDF/SDF（机器人描述格式，ROS/Gazebo 用）",
            "parameters": {
                "type": "object",
                "properties": {
                    "robot_name": {"type": "string", "description": "机器人名称"},
                    "links": {
                        "type": "array",
                        "description": "link 列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "mass": {"type": "number"},
                                "geometry_file": {"type": "string"},
                            },
                        },
                    },
                    "joints": {
                        "type": "array",
                        "description": "joint 列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "joint_type": {"type": "string", "enum": ["revolute", "continuous", "fixed", "prismatic"]},
                                "parent": {"type": "string"},
                                "child": {"type": "string"},
                            },
                        },
                    },
                    "format": {"type": "string", "enum": ["urdf", "srdf", "sdf", "all"], "description": "导出格式，默认 all"},
                },
                "required": ["robot_name", "links"],
            },
        }

        async def _handle_mfg_export_urdf(args: dict, **kw: Any) -> str:
            robot_name = args.get("robot_name", "vermes_robot")
            fmt = args.get("format", "all")
            link_data = args.get("links", [])
            joint_data = args.get("joints", [])

            links = [RobotLink(**l) for l in link_data]
            joints = [RobotJoint(**j) for j in joint_data]

            results = []
            if fmt in ("urdf", "all"):
                r = export_urdf(links, joints, robot_name=robot_name)
                if r["ok"]:
                    results.append(f"✅ URDF: {r['urdf_path']}（{r['link_count']} links, {r['joint_count']} joints）")
                else:
                    results.append(f"❌ URDF: {r['error']}")
            if fmt in ("srdf", "all"):
                r = export_srdf(links, joints, robot_name=robot_name)
                if r["ok"]:
                    results.append(f"✅ SRDF: {r['srdf_path']}")
                else:
                    results.append(f"❌ SRDF: {r['error']}")
            if fmt in ("sdf", "all"):
                r = export_sdf(links, joints, robot_name=robot_name)
                if r["ok"]:
                    results.append(f"✅ SDF: {r['sdf_path']}")
                else:
                    results.append(f"❌ SDF: {r['error']}")

            return "\n".join(results)

        registry.register(
            name="mfg_export_urdf",
            toolset="mfgcad",
            schema=MFG_URDF_SCHEMA,
            handler=_handle_mfg_export_urdf,
            is_async=True,
            emoji="🤖",
            description="导出 URDF/SRDF/SDF（机器人描述格式，ROS/Gazebo）",
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfgcad project/template tools registration failed: %s", e)

    # ── M1 FreeCAD 专业精修工具（ProToolAdapter 参考实现） ──
    try:
        MFG_OPEN_FREECAD_SCHEMA = {
            "name": "mfg_open_in_freecad",
            "description": (
                "在 FreeCAD 专业 GUI 中打开当前会话的模型（.FCStd 真相源），交还文件给用户做专业精修（D6）。"
                "若会话仅有 STEP/STL，会先导入为可编辑 .FCStd 再返回路径。"
                "仅当 FreeCAD 引擎已就绪时可用；未安装则提示去 ModuStore 安装 vermes-mod-freecad-engine。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "设计会话 ID（由 mfg_text_to_cad / 上传产生）。"},
                },
                "required": ["session_id"],
            },
        }

        MFG_EDIT_FEATURE_SCHEMA = {
            "name": "mfg_edit_feature",
            "description": (
                "对当前会话模型施加一次专业级参数化编辑（圆角/拔模/阵列/布尔/缩放），"
                "经 FreeCAD 后端作用在特征树上，返回更新后的特征树 + 预览 STL 路径。"
                "适合「模型大体已生成，想加圆角/出模角/阵列」的精修场景（制造业模具变现关键路径）。"
                "op.op∈{fillet,draft,pattern,boolean,scale}；op.target∈{edges_all,edge:<id>,face:<id>,body:<id>,tool:<id>}；"
                "op.params 视 op 而定（如 fillet→{radius}，draft→{angle}，pattern→{mode,count,dist/angle}）。"
                "FreeCAD 未就绪时返回引导，不影响已有模型。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "设计会话 ID。"},
                    "op": {
                        "type": "object",
                        "description": "编辑操作：{op, target, params}。",
                        "properties": {
                            "op": {"type": "string", "description": "fillet|draft|pattern|boolean|scale"},
                            "target": {"type": "string", "description": "edges_all|edge:<id>|face:<id>|body:<id>|tool:<id>"},
                            "params": {"type": "object", "description": "操作参数，随 op 变化（radius/angle/count/dist 等）。"},
                        },
                        "required": ["op", "target"],
                    },
                },
                "required": ["session_id", "op"],
            },
        }

        MFG_EXPORT_FCSTD_SCHEMA = {
            "name": "mfg_export_fcstd",
            "description": (
                "导出当前 FreeCAD 会话的模型为指定格式（step/stl），返回文件路径。"
                "用于把精修后的模型交付下游（模具/量产）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "设计会话 ID。"},
                    "formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["step", "stl"]},
                        "description": "导出格式，默认 [step, stl]。",
                    },
                },
                "required": ["session_id"],
            },
        }

        async def _handle_mfg_open_in_freecad(args: dict, **kw: Any) -> str:
            session_id = (args.get("session_id") or "").strip()
            if not session_id:
                return "❌ 缺少必填参数 session_id。"
            adapter = _get_freecad_adapter()
            if not adapter.is_available():
                return "⚠️ FreeCAD 引擎未就绪：请先安装 freecad 引擎（ModuStore 装 vermes-mod-freecad-engine，或 mfg_setup_engine），再在 FreeCAD 中打开模型做专业精修。"
            doc = _ensure_freecad_doc(adapter, session_id)
            if doc is None:
                return "❌ 会话无模型：请先 mfg_text_to_cad 生成或上传 STEP，再在 FreeCAD 打开。"
            return (
                f"✅ 可在 FreeCAD 中打开：{doc}\n"
                "（Vermes 仅交还文件，专业精修在 FreeCAD GUI 完成，D6）"
            )

        async def _handle_mfg_edit_feature(args: dict, **kw: Any) -> str:
            session_id = (args.get("session_id") or "").strip()
            op_dict = args.get("op") or {}
            if not session_id:
                return "❌ 缺少必填参数 session_id。"
            if not op_dict.get("op") or not op_dict.get("target"):
                return "❌ 缺少 op.op / op.target（编辑操作）。"
            adapter = _get_freecad_adapter()
            if not adapter.is_available():
                return "⚠️ FreeCAD 引擎未就绪：请先安装 freecad 引擎再精修；或继续用 build123d 粗模兜底。"
            doc = _ensure_freecad_doc(adapter, session_id)
            if doc is None:
                return "❌ 会话无模型：请先 mfg_text_to_cad 生成或上传 STEP。"
            from vermes_cli.mfgcad.backends import EditOp

            try:
                result = adapter.apply_edit_op(session_id, EditOp.from_dict(op_dict))
            except Exception as e:  # §9：几何非法 → ok=False+error，前端标红，不破坏已有树
                return f"❌ 编辑调用失败：{e}"
            if not result.ok:
                return f"❌ 编辑失败：{result.error}（已有特征树未破坏，可重试其他参数）"
            exports = adapter.export(session_id, ["stl"])
            stl = exports.get("stl")
            lines = [f"✅ 已对会话 {session_id} 施加编辑：{op_dict.get('op')} @ {op_dict.get('target')}"]
            if result.feature_tree:
                lines.append("特征树：")
                for n in result.feature_tree:
                    params = ",".join(f"{k}={v}" for k, v in n.params.items())
                    lines.append(f"  • {n.kind}:{n.id} {('(' + params + ')') if params else ''}")
            if stl:
                lines.append(f"预览 STL：{stl}")
            return "\n".join(lines)

        async def _handle_mfg_export_fcstd(args: dict, **kw: Any) -> str:
            session_id = (args.get("session_id") or "").strip()
            if not session_id:
                return "❌ 缺少必填参数 session_id。"
            formats = args.get("formats") or ["step", "stl"]
            adapter = _get_freecad_adapter()
            if not adapter.is_available():
                return "⚠️ FreeCAD 引擎未就绪：请先安装 freecad 引擎再导出。"
            doc = _ensure_freecad_doc(adapter, session_id)
            if doc is None:
                return "❌ 会话无模型：请先 mfg_text_to_cad 生成或上传 STEP。"
            try:
                out = adapter.export(session_id, formats)
            except Exception as e:
                return f"❌ 导出失败：{e}"
            if not out:
                return "❌ 导出失败：无输出。"
            return "✅ 导出完成：\n" + "\n".join(f"  • {k}: {v}" for k, v in out.items())

        for _name, _schema, _handler, _emoji, _desc in (
            ("mfg_open_in_freecad", MFG_OPEN_FREECAD_SCHEMA, _handle_mfg_open_in_freecad, "🧰", "在 FreeCAD 专业 GUI 打开当前会话模型（.FCStd 真相源）"),
            ("mfg_edit_feature", MFG_EDIT_FEATURE_SCHEMA, _handle_mfg_edit_feature, "✏️", "FreeCAD 专业级参数化精修（圆角/拔模/阵列/布尔/缩放）"),
            ("mfg_export_fcstd", MFG_EXPORT_FCSTD_SCHEMA, _handle_mfg_export_fcstd, "📦", "导出 FreeCAD 会话为 STEP/STL"),
        ):
            registry.register(
                name=_name,
                toolset="mfgcad",
                schema=_schema,
                handler=_handler,
                is_async=True,
                emoji=_emoji,
                description=_desc,
            )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("mfgcad freecad tools registration failed: %s", e)
