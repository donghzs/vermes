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
        "制造业 text-to-CAD：把自然语言建模需求直接生成 STEP 三维模型文件。"
        "底层由 Multi-Agent-CAD 引擎驱动（4-Agent 流水线：需求解析→几何规划→"
        "build123d 确定性代码翻译→双引擎几何/网格校验），无需人工写代码。"
        "适合「用户用中文描述一个零件（尺寸/形状/壁厚/孔位等），希望得到可制造的 STEP」的场景。"
        "返回 STEP 文件路径 + 体积 + 双引擎校验结果。可在 CAD 软件中打开 STEP 查看。"
        "可选参数：session_id 用于状态化续作；checkpoint=true 生成后暂停人工核对不自动定稿；"
        "workflow_id 选引擎工作流（original=确定性翻译优先，aider=Aider 优先）。"
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

    try:
        python_exe, engine_dir = _resolve_engine()
    except RuntimeError as e:
        return f"❌ 引擎未就绪: {e}"

    # 透传 API key：engine 的 _llm_client 读 DASHSCOPE_API_KEY（legacy 命名）。
    env = dict(os.environ)
    key = _resolve_api_key()
    if key:
        env["DASHSCOPE_API_KEY"] = key
        env["DS_API_KEY"] = key
        env["OPENAI_API_KEY"] = key
        # 透传用户配置的 base_url（非硬编码 DeepSeek）
        # 优先从活跃 provider 的 base_url 取，fallback 留 DeepSeek（MAC POC 默认）
        base_url = "https://api.deepseek.com/v1"
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
                    base_url = bu
        except Exception:
            pass
        env["OPENAI_API_BASE"] = base_url
    else:
        return ("❌ 未配置 LLM API key。请在 Vermes 前端「设置 → API」中为「制造 CAD」"
                "填一个 DeepSeek/OpenAI 兼容 key，或直接使用已为主 Agent 配置的同一把 key"
                "（mfgcad 会自动复用活跃 provider 的 key）；引擎需要 key 才能调用大模型。")

    cmd = [
        python_exe, str(engine_dir / "run_mac.py"),
        "--request", request,
        "--output-dir", output_dir,
        "--workflow-id", workflow_id,
        "--max-retries", "5",
    ]

    try:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, env=env, capture_output=True, text=True, timeout=1800
        )
    except subprocess.TimeoutExpired:
        return "❌ 建模超时（>30min）。请简化需求或检查引擎日志。"
    except Exception as e:  # pragma: no cover - defensive
        return f"❌ 引擎子进程调用失败: {type(e).__name__}: {e}"

    result = _parse_engine_json(proc.stdout)
    if result is None:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        return (f"❌ 引擎未返回可解析结果。退出码={proc.returncode}。\n"
                f"--- 引擎日志尾部 ---\n{tail}")

    ok = bool(result.get("ok"))
    step_path = result.get("step_path")
    volume = result.get("volume_mm3")
    qa = result.get("qa") or {}
    passed = qa.get("passed", 0)
    issues = qa.get("issues", [])
    stl_path = result.get("stl_path")
    stl_3mf_path = result.get("stl_3mf_path")

    # 落状态化 session 记录（无论成败都记，便于续作/排查）。
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
                    "ok": ok,
                    "step_path": step_path,
                    "stl_path": stl_path,
                    "stl_3mf_path": stl_3mf_path,
                    "volume_mm3": volume,
                    "qa": qa,
                    "error_type": result.get("error_type"),
                    "ts": int(time.time()),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass  # 状态落盘失败不阻断主流程

    vol_str = f"{volume:.2f} mm³（{volume/1000:.3f} cm³）" if volume is not None else "未知"

    if not ok or not step_path:
        tail = "\n".join(proc.stderr.strip().splitlines()[-10:])
        diag = f"\n引擎日志尾部:\n{tail}" if tail else ""
        return (f"❌ 建模失败（{result.get('error_type')}）：{result.get('message','')}{diag}")

    if checkpoint:
        return (
            f"⏸ CHECKPOINT 人工核对：候选 STEP 已生成 {step_path}\n"
            f"体积 {vol_str}；双引擎校验通过 {passed} 项"
            + (f"（注意：{'; '.join(issues)}）" if issues else "")
            + (f"\nSTL（切片/打印）: {stl_path}" if stl_path else "")
            + (f"\n3MF（Bambu/切片）: {stl_3mf_path}" if stl_3mf_path else "")
            + f"\n请人工核对尺寸/拓扑。确认后再次调用 mfg_text_to_cad"
            f"（同一 session_id={session_id}，checkpoint=false）定稿，"
            f"或调用 mfg_dfm_prescreen 做可制造性初筛。"
        )

    return (
        f"✅ STEP 已生成：{step_path}\n"
        f"体积 {vol_str}；双引擎校验通过 {passed} 项"
        + (f"（提示：{'; '.join(issues)}）" if issues else "")
        + (f"\nSTL（切片/打印）: {stl_path}" if stl_path else "")
        + (f"\n3MF（Bambu/切片）: {stl_3mf_path}" if stl_3mf_path else "")
        + f"\n会话 session_id={session_id}。可用 CAD 软件打开 STEP/STL 查看，"
        f"或继续调用 mfg_dfm_prescreen / mfg_printer 进入下游。"
    )


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
        description="制造业 text-to-CAD：自然语言需求直接生成 STEP 三维模型（双引擎校验）",
    )
