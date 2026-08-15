"""mfgcad 多后端引擎抽象层。

P1：把引擎接入从 MAC 硬编码泛化为可插拔多后端架构。

设计原则（对齐 VERMES_3D_ARCH_BASELINE.md）：
- 框架层轻量纯 Python 出厂自带，重引擎按需安装于 ~/.vermes/engines/<name>/
- 每个后端实现 EngineBackend 协议（generate → 结构化结果）
- preset.yaml 的 engine 字段决定路由（无 engine 字段 → 默认 mac）
- 新增后端只需：① 实现 EngineBackend ② 在 _BACKEND_REGISTRY 注册
- 不改现有 MAC 接入路径（零回归）

后端类型：
- mac：Multi-Agent-CAD（精确 CAD，输出 STEP/STL/3MF，子进程桥接）
- trellis：TRELLIS 2（生成式 3D，输出 GLB/网格，本地 CUDA 或云 API）
- cloud_api：云服务 API（Tripo/Meshy/Rodin 等，HTTP 调用）

引擎目录约定：
  ~/.vermes/engines/mac/        — MAC 引擎（已存在）
  ~/.vermes/engines/trellis/    — TRELLIS 2 引擎（按需安装）
  ~/.vermes/engines/<custom>/   — 用户自定义后端
"""

from __future__ import annotations

import abc
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Optional


# ── EngineBackend 协议 ───────────────────────────────────


class EngineBackend(abc.ABC):
    """引擎后端抽象基类。

    每个后端接收统一的 generate() 调用，返回统一的 EngineResult。
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """后端标识（如 'mac', 'trellis', 'cloud_api'）。"""

    @property
    @abc.abstractmethod
    def output_formats(self) -> list[str]:
        """该后端支持的输出格式（如 ['step', 'stl', '3mf']）。"""

    @abc.abstractmethod
    async def generate(
        self,
        request: str,
        output_dir: str,
        preset: dict | None = None,
        env: dict | None = None,
        **kwargs: Any,
    ) -> "EngineResult":
        """执行建模，返回结构化结果。

        Args:
            request: 自然语言建模需求（已过 clarify 增强）
            output_dir: 输出目录
            preset: 场景 preset 定义（含 slots/output_formats/engine）
            env: 环境变量（含 API key 等）
            **kwargs: 后端特有参数（如 workflow_id, checkpoint）
        """
        ...

    def is_available(self) -> bool:
        """检查后端是否已安装就绪。"""
        return True


class EngineResult:
    """统一引擎输出结构。"""

    def __init__(
        self,
        ok: bool,
        files: dict[str, str | None] | None = None,
        volume_mm3: float | None = None,
        qa: dict | None = None,
        error_type: str | None = None,
        message: str = "",
        raw: dict | None = None,
    ):
        self.ok = ok
        self.files = files or {}  # {"step": "/path/to/x.step", "stl": "...", "glb": "..."}
        self.volume_mm3 = volume_mm3
        self.qa = qa or {}
        self.error_type = error_type
        self.message = message
        self.raw = raw or {}

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "files": self.files,
            "volume_mm3": self.volume_mm3,
            "qa": self.qa,
            "error_type": self.error_type,
            "message": self.message,
            "raw": self.raw,
        }


# ── MAC 后端（现有子进程桥接收编） ────────────────────────


class MACBackend(EngineBackend):
    """Multi-Agent-CAD 精确 CAD 后端。

    子进程桥接 ~/.vermes/engines/mac/run_mac.py，输出 STEP/STL/3MF。
    """

    @property
    def name(self) -> str:
        return "mac"

    @property
    def output_formats(self) -> list[str]:
        return ["step", "stl", "3mf"]

    def is_available(self) -> bool:
        engine_dir = self._engine_dir()
        return (engine_dir / "run_mac.py").is_file()

    def _engine_dir(self) -> Path:
        return Path(
            os.environ.get("MFG_CAD_ENGINE_DIR", str(Path.home() / ".vermes" / "engines" / "mac"))
        ).resolve()

    def _python_exe(self) -> str:
        exe = os.environ.get("MFG_CAD_ENGINE_PY")
        if exe:
            return exe
        candidate = self._engine_dir() / ".venv" / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError(
            f"MAC 引擎 venv 未就绪：{candidate} 不存在。"
            f"请在 {self._engine_dir()} 下创建含 build123d/cadquery-ocp/trimesh 的 venv。"
        )

    async def generate(
        self,
        request: str,
        output_dir: str,
        preset: dict | None = None,
        env: dict | None = None,
        **kwargs: Any,
    ) -> EngineResult:
        import asyncio

        python_exe = self._python_exe()
        engine_dir = self._engine_dir()

        workflow_id = kwargs.get("workflow_id", "original")
        cmd = [
            python_exe,
            str(engine_dir / "run_mac.py"),
            "--request", request,
            "--output-dir", output_dir,
            "--workflow-id", workflow_id,
            "--max-retries", "5",
        ]

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, env=full_env, capture_output=True, text=True, timeout=1800
            )
        except subprocess.TimeoutExpired:
            return EngineResult(ok=False, error_type="timeout", message="建模超时（>30min）")
        except Exception as e:
            return EngineResult(ok=False, error_type="subprocess_error", message=f"{type(e).__name__}: {e}")

        # 解析 JSON 输出
        result = _parse_json_line(proc.stdout)
        if result is None:
            tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
            return EngineResult(
                ok=False,
                error_type="parse_error",
                message=f"引擎未返回可解析结果。退出码={proc.returncode}。\n{tail}",
            )

        return EngineResult(
            ok=bool(result.get("ok")),
            files={
                "step": result.get("step_path"),
                "stl": result.get("stl_path"),
                "3mf": result.get("stl_3mf_path"),
            },
            volume_mm3=result.get("volume_mm3"),
            qa=result.get("qa") or {},
            error_type=result.get("error_type"),
            message=result.get("message", ""),
            raw=result,
        )


# ── TRELLIS 后端（生成式 3D，设计框架） ───────────────────


class TrellisBackend(EngineBackend):
    """TRELLIS 2 生成式 3D 后端。

    支持三种部署模式（按优先级自动选择）：
    1. local_cuda：本地 NVIDIA GPU + CUDA（~/.vermes/engines/trellis/ 含模型权重）
    2. local_apple：Apple Silicon Metal（MacBook M1+， MPS 后端）
    3. cloud_api：云服务 API（无本地 GPU 时的 fallback）

    输出 GLB（含 PBR 纹理）+ 截图。
    """

    @property
    def name(self) -> str:
        return "trellis"

    @property
    def output_formats(self) -> list[str]:
        return ["glb", "png"]

    def _engine_dir(self) -> Path:
        return Path(
            os.environ.get("TRELLIS_ENGINE_DIR", str(Path.home() / ".vermes" / "engines" / "trellis"))
        ).resolve()

    def _python_exe(self) -> str:
        """解析 TRELLIS 本地推理用的 Python 解释器。

        解析顺序：① 环境变量 TRELLIS_ENGINE_PY 显式指定
                   ② 引擎 venv（<engine_dir>/.venv/bin/python）
        找不到时抛 RuntimeError，由调用方转为清晰的错误结果（不再裸 `python3`）。
        """
        exe = os.environ.get("TRELLIS_ENGINE_PY")
        if exe:
            return exe
        candidate = self._engine_dir() / ".venv" / "bin" / "python"
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError(
            f"TRELLIS 引擎解释器未就绪：{candidate} 不存在。"
            f"请在 {self._engine_dir()} 下创建含 torch/trellis 依赖的 venv，"
            f"或用 TRELLIS_ENGINE_PY 显式指定解释器。"
        )

    def _detect_mode(self) -> str:
        """检测可用部署模式。"""
        # 1. 本地 CUDA
        engine_dir = self._engine_dir()
        if (engine_dir / "run_trellis.py").is_file():
            try:
                import torch
                if torch.cuda.is_available():
                    return "local_cuda"
                if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return "local_apple"
            except ImportError:
                pass
        # 2. 云 API
        cloud_key = os.environ.get("TRELLIS_CLOUD_API_KEY", "")
        if cloud_key:
            return "cloud_api"
        # 3. 未就绪
        return "unavailable"

    def is_available(self) -> bool:
        return self._detect_mode() != "unavailable"

    async def generate(
        self,
        request: str,
        output_dir: str,
        preset: dict | None = None,
        env: dict | None = None,
        **kwargs: Any,
    ) -> EngineResult:
        import asyncio

        mode = self._detect_mode()
        if mode == "unavailable":
            return EngineResult(
                ok=False,
                error_type="engine_not_ready",
                message=(
                    "TRELLIS 引擎未就绪。安装方式：\n"
                    "  ① 本地 CUDA：把 TRELLIS 2 代码+权重放到 ~/.vermes/engines/trellis/，"
                    "安装 torch（CUDA 版）+ trellis 依赖\n"
                    "  ② Apple Silicon：同上，torch 用 MPS 后端\n"
                    "  ③ 云 API：设 TRELLIS_CLOUD_API_KEY 环境变量\n"
                    "详见 https://github.com/microsoft/TRELLIS"
                ),
            )

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        if mode in ("local_cuda", "local_apple"):
            return await self._run_local(request, output_dir, mode, full_env, **kwargs)
        else:
            return await self._run_cloud(request, output_dir, full_env, **kwargs)

    async def _run_local(
        self, request: str, output_dir: str, mode: str, env: dict, **kwargs: Any
    ) -> EngineResult:
        """本地 TRELLIS 推理（CUDA 或 Apple Silicon）。"""
        import asyncio

        engine_dir = self._engine_dir()
        runner = engine_dir / "run_trellis.py"

        if not runner.is_file():
            return EngineResult(
                ok=False,
                error_type="engine_not_installed",
                message=f"TRELLIS 运行脚本不存在：{runner}。请先安装 TRELLIS 引擎。",
            )

        try:
            python_exe = self._python_exe()
        except RuntimeError as e:
            return EngineResult(ok=False, error_type="engine_not_ready", message=str(e))

        cmd = [
            python_exe, str(runner),
            "--request", request,
            "--output-dir", output_dir,
            "--mode", mode,
        ]

        # 可选参数
        seed = kwargs.get("seed")
        if seed is not None:
            cmd.extend(["--seed", str(seed)])
        steps = kwargs.get("steps")
        if steps is not None:
            cmd.extend(["--steps", str(steps)])

        try:
            proc = await asyncio.to_thread(
                subprocess.run, cmd, env=env, capture_output=True, text=True, timeout=600
            )
        except subprocess.TimeoutExpired:
            return EngineResult(ok=False, error_type="timeout", message="TRELLIS 推理超时（>10min）")
        except Exception as e:
            return EngineResult(ok=False, error_type="subprocess_error", message=f"{type(e).__name__}: {e}")

        result = _parse_json_line(proc.stdout)
        if result is None:
            tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
            return EngineResult(
                ok=False,
                error_type="parse_error",
                message=f"TRELLIS 未返回可解析结果。退出码={proc.returncode}。\n{tail}",
            )

        return EngineResult(
            ok=bool(result.get("ok")),
            files={
                "glb": result.get("glb_path"),
                "png": result.get("preview_path"),
            },
            qa=result.get("qa") or {},
            error_type=result.get("error_type"),
            message=result.get("message", ""),
            raw=result,
        )

    async def _run_cloud(
        self, request: str, output_dir: str, env: dict, **kwargs: Any
    ) -> EngineResult:
        """云 API 调用（TRELLIS 或兼容服务）。

        框架层只定义 HTTP 契约，具体 endpoint 由 TRELLIS_CLOUD_API_BASE 配置。
        """
        import httpx

        api_key = env.get("TRELLIS_CLOUD_API_KEY", "")
        api_base = env.get("TRELLIS_CLOUD_API_BASE")
        if not api_base:
            return EngineResult(
                ok=False,
                error_type="config_missing",
                message=(
                    "TRELLIS 云 API 端点未配置。请设置环境变量 TRELLIS_CLOUD_API_BASE"
                    "（例如 https://your-trellis-cloud.example/v1）。"
                ),
            )

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{api_base}/generate",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "prompt": request,
                        "output_format": "glb",
                        "seed": kwargs.get("seed"),
                    },
                )
                resp.raise_for_status()
                data = resp.json()

            # 下载 GLB
            glb_url = data.get("glb_url")
            glb_path = str(Path(output_dir) / "output.glb")
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            if glb_url:
                async with httpx.AsyncClient(timeout=120) as client:
                    glb_resp = await client.get(glb_url)
                    glb_resp.raise_for_status()
                    Path(glb_path).write_bytes(glb_resp.content)

            return EngineResult(
                ok=True,
                files={"glb": glb_path if glb_url else None},
                qa={"mode": "cloud_api"},
                raw=data,
            )

        except Exception as e:
            return EngineResult(
                ok=False,
                error_type="cloud_api_error",
                message=f"云 API 调用失败: {type(e).__name__}: {e}",
            )


# ── 后端注册 + 路由 ────────────────────────────────────────


_BACKENDS: dict[str, EngineBackend] = {
    "mac": MACBackend(),
    "trellis": TrellisBackend(),
}


def register_backend(name: str, backend: EngineBackend) -> None:
    """注册自定义后端。"""
    _BACKENDS[name] = backend


def get_backend(name: str) -> EngineBackend | None:
    """取后端实例。"""
    return _BACKENDS.get(name)


def list_backends() -> list[str]:
    """列出所有已注册后端名。"""
    return sorted(_BACKENDS.keys())


def resolve_backend(preset: dict | None = None) -> EngineBackend:
    """据 preset.engine 字段路由到后端，无字段默认 mac。

    Raises:
        RuntimeError: 后端未注册或未安装。
    """
    engine_name = "mac"
    if preset and preset.get("engine"):
        engine_name = preset["engine"]

    backend = _BACKENDS.get(engine_name)
    if backend is None:
        raise RuntimeError(
            f"未知引擎后端 '{engine_name}'。已注册: {list_backends()}。"
            f"可通过 register_backend() 注册自定义后端。"
        )

    if not backend.is_available():
        raise RuntimeError(
            f"引擎 '{engine_name}' 未就绪。"
            f"请按文档安装：~/.vermes/engines/{engine_name}/"
        )

    return backend


# ── 工具函数 ─────────────────────────────────────────────


def _parse_json_line(stdout: str) -> dict | None:
    """从 stdout 提取最后一行 JSON。"""
    if not stdout:
        return None
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None
