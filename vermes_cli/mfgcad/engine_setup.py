"""mfgcad 引擎自安装能力（Agent 自理，而非用户/开发者手动配环境）。

设计目标（对齐产品哲学：Agent 产品，不是开发者工具）：
- 用户说「我要建一个笔筒」，Agent 调 mfg_text_to_cad；
- 若检测到 MAC 引擎 venv 未就绪，Agent 自己建 venv、装依赖、验证，装完无感继续出图；
- 用户只看到「首次配置引擎中，请稍候…」，不需要懂 venv/pip。

本模块纯 stdlib（asyncio/subprocess/venv/pathlib），import 零重依赖，可安全在
tools.py 的 handler 内惰性 import。

依赖集依据（已实核，非猜测）：
- run_mac.py 文档字符串明示：build123d / cadquery-ocp / trimesh / langgraph / aider / cadpy
- multi_agent_cad/nodes.py 实际 import：from openai import OpenAI、pydantic
  → 故 CORE_DEPS 含 openai/pydantic（缺则 build_graph 时 ModuleNotFoundError）
- packages/cadpy 是本地源码包（pyproject.toml，PyPI 无 cadpy）→ 走本地 pip install
- aider 仅在 --workflow-id aider 时惰性 import，默认 original 路径不需要 → OPTIONAL（best-effort）

包名已核验 PyPI：cadquery-ocp 7.9.3.1.1 存在；OCP 是另一套旧打包，不用。
"""

from __future__ import annotations

import asyncio
import subprocess
import venv
from pathlib import Path
from typing import Callable, Optional

# ── 依赖清单（实核于 2026-08-16） ─────────────────────────

CORE_DEPS: list[str] = [
    "build123d",       # 参数化 CAD 顶层 API（拉 cadquery/OCP）
    "cadquery-ocp",    # OpenCascade Python 绑定（macOS arm64 原生 wheel）
    "trimesh",         # 3MF 手搓所需网格库
    "langgraph",       # multi_agent_cad 流水线
    "openai",          # nodes.py: from openai import OpenAI（MAC 的 LLM client）
    "pydantic",        # nodes.py / schemas.py
    "prompt_toolkit",  # build123d→ipython→prompt_toolkit 间接依赖，显式声明防首跑缺失
]

OPTIONAL_DEPS: list[str] = [
    "aider",           # 仅 --workflow-id aider 自修复路径需要；best-effort
]

# 真·就绪验证要能 import 的模块（缺任一即视为未就绪）
DEFAULT_VERIFY_MODS: list[str] = [
    "build123d",
    "OCP",
    "trimesh",
    "langgraph",
    "openai",
    "pydantic",
    "multi_agent_cad",  # 引擎自有包；能 import 即证明依赖链闭合
]


def get_engine_dir() -> Path:
    """解析 MAC 引擎根目录（可被 MFG_CAD_ENGINE_DIR 覆盖）。"""
    import os

    return Path(
        os.environ.get("MFG_CAD_ENGINE_DIR", str(Path.home() / ".vermes" / "engines" / "mac"))
    ).resolve()


def _find_system_python() -> str:
    """查找可用的系统 Python 3.11+（不能是嵌入/frozen Python，否则 venv symlink 会崩）。

    优先级：
    1. MFG_CAD_VENV_PYTHON 环境变量（用户显式指定）
    2. /opt/homebrew/bin/python3.11（macOS Homebrew）
    3. /usr/local/bin/python3.11（macOS Intel Homebrew）
    4. python3.11 in PATH（但不保证非 frozen）
    5. 回退到 sys.executable（最后手段，可能崩）
    """
    import os, shutil
    # 1. 用户显式指定
    env_py = os.environ.get("MFG_CAD_VENV_PYTHON")
    if env_py and shutil.which(env_py):
        return env_py
    # 2-3. Homebrew 路径
    for candidate in (
        "/opt/homebrew/bin/python3.11",
        "/usr/local/bin/python3.11",
        "/opt/homebrew/bin/python3.12",
        "/usr/local/bin/python3.12",
    ):
        if Path(candidate).is_file():
            return candidate
    # 4. PATH 中的 python3.11
    found = shutil.which("python3.11")
    if found:
        return found
    # 5. 最后手段
    return sys.executable


def _create_venv_with_python(python_exe: str, venv_dir: str) -> None:
    """用指定 Python 解释器创建 venv（避免 frozen/embedded Python symlink 崩溃）。"""
    import subprocess as _sp
    result = _sp.run(
        [python_exe, "-m", "venv", venv_dir],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"venv 创建失败 (exit={result.returncode}): {result.stderr[-300:]}"
        )
    # 确保 venv 内有 pip
    venv_py = str(Path(venv_dir) / "bin" / "python")
    ensure = _sp.run(
        [venv_py, "-m", "ensurepip", "--upgrade"],
        capture_output=True, text=True, timeout=60,
    )
    # ensurepip 失败不致命，后面会 pip install --upgrade pip


def _venv_python(engine_dir: Path) -> Path:
    return engine_dir / ".venv" / "bin" / "python"


def engine_code_present(engine_dir: Path) -> bool:
    """引擎代码（run_mac.py + multi_agent_cad 包）是否就位。

    缺这个说明用户没把 Multi-Agent-CAD 放到引擎目录 —— 不是 venv 问题，
    自动安装无解，需引导用户放置引擎代码。
    """
    ed = Path(engine_dir)
    return (ed / "run_mac.py").is_file() and (ed / "multi_agent_cad").is_dir()


def is_provisioned(engine_dir: Path) -> bool:
    """引擎是否已就绪：代码在 + venv 解释器在。

    注：深层 import 校验放在 provision_engine 的 verify 阶段；此处只做廉价文件检查，
    避免每次工具调用都 fork 子进程。venv 存在但依赖残缺时，provision(force=True) 可修复。
    """
    ed = Path(engine_dir)
    if not engine_code_present(ed):
        return False
    return _venv_python(ed).is_file()


async def _pip(
    venv_python: Path,
    args: list[str],
    progress: Optional[Callable[[str], None]] = None,
    timeout: int = 1800,
) -> tuple[bool, str]:
    """在引擎 venv 内跑 pip，返回 (ok, error)。"""
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [str(venv_python), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, f"pip 超时（>{timeout}s），依赖较大可重试或手动安装"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-20:])
        return False, f"exit={proc.returncode}\n{tail}"
    return True, ""


async def _verify_imports(
    venv_python: Path,
    mods: list[str] = DEFAULT_VERIFY_MODS,
    timeout: int = 300,
) -> tuple[bool, str]:
    """在引擎 venv 内验证关键模块可 import。"""
    script = (
        "import importlib\n"
        "mods = " + repr(mods) + "\n"
        "bad = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{m}: {e}')\n"
        "print('OK' if not bad else 'BAD:\\n' + '\\n'.join(bad))\n"
    )
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [str(venv_python), "-c", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"verify 子进程失败: {type(e).__name__}: {e}"
    out = (proc.stdout or "").strip()
    if out == "OK":
        return True, ""
    return False, out or (proc.stderr or "未知导入错误")


async def provision_engine(
    engine_dir,
    *,
    force: bool = False,
    include_aider: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    _core_deps: Optional[list[str]] = None,
    _optional_deps: Optional[list[str]] = None,
    _verify_mods: Optional[list[str]] = None,
) -> dict:
    """一键安装 MAC 引擎 venv（建 venv → 升 pip → 装核心 → 装本地 cadpy → 可选 aider → 验证）。

    Args:
        engine_dir: 引擎根目录（含 run_mac.py + multi_agent_cad/）
        force: True=即使 venv 存在也重装全部依赖（不删 venv，避免误删用户资产）
        include_aider: True=额外装 aider（aider 工作流用；best-effort，失败不致命）
        progress: 可选回调，每步推送状态消息（用于「请稍候」前端提示）
        _core_deps/_optional_deps/_verify_mods: 测试可注入，生产用默认

    Returns:
        {"ok": bool, "steps": [str], "step": str|None, "error": str}
        step 标记失败发生在哪一步（venv/pip_core/pip_cadpy/pip_aider/verify）。
    """
    ed = Path(engine_dir).resolve()
    core = _core_deps if _core_deps is not None else CORE_DEPS
    optional = _optional_deps if _optional_deps is not None else OPTIONAL_DEPS
    verify_mods = _verify_mods if _verify_mods is not None else DEFAULT_VERIFY_MODS
    steps: list[str] = []

    def log(msg: str) -> None:
        steps.append(msg)
        if progress:
            progress(msg)

    if not engine_code_present(ed):
        return {
            "ok": False,
            "steps": steps,
            "step": "engine_code",
            "error": (
                f"引擎代码缺失：{ed}/run_mac.py 或 {ed}/multi_agent_cad 不存在。"
                "自动安装只解决 venv/依赖，不能替你下载 Multi-Agent-CAD 引擎本身。"
                "请把 MAC 引擎（含 run_mac.py 与 multi_agent_cad/ 包）放到该目录，"
                "或设 MFG_CAD_ENGINE_DIR 指向引擎根。"
            ),
        }

    vpy = _venv_python(ed)

    # 1) venv
    if not vpy.is_file() or force:
        if vpy.is_file() and force:
            log("🔁 force=true：重装依赖（保留现有 venv）")
        else:
            sys_py = _find_system_python()
            log(f"🔧 创建引擎 venv（首次，约数十秒）使用 {sys_py}…")
            try:
                await asyncio.to_thread(
                    _create_venv_with_python, sys_py, str(ed / ".venv")
                )
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "steps": steps, "step": "venv", "error": f"{type(e).__name__}: {e}"}
    else:
        log("✓ venv 已存在，跳过创建")

    # 2) pip 升级
    log("⬆️ 升级 pip…")
    ok, err = await _pip(vpy, ["-m", "pip", "install", "--upgrade", "pip"])
    if not ok:
        return {"ok": False, "steps": steps, "step": "pip_upgrade", "error": err}

    # 3) 核心依赖（首次数百 MB，慢）
    log(f"📦 安装核心依赖：{', '.join(core)}（首次较大，请耐心，几分钟到十几分钟）…")
    ok, err = await _pip(vpy, ["-m", "pip", "install", *core], timeout=1800)
    if not ok:
        return {"ok": False, "steps": steps, "step": "pip_core", "error": err}

    # 4) 本地 cadpy（packages/cadpy，PyPI 无此包）
    cadpy = ed / "packages" / "cadpy"
    if cadpy.is_dir():
        log("📦 安装本地 cadpy（packages/cadpy）…")
        ok, err = await _pip(vpy, ["-m", "pip", "install", str(cadpy)], timeout=600)
        if not ok:
            return {"ok": False, "steps": steps, "step": "pip_cadpy", "error": err}
    else:
        log("⏭️ 未发现 packages/cadpy，跳过（不影响核心建模）")

    # 5) aider（可选，best-effort）
    if include_aider and optional:
        log(f"📦 安装 aider（可选，{', '.join(optional)}）…")
        ok, err = await _pip(vpy, ["-m", "pip", "install", *optional], timeout=1200)
        if not ok:
            log("⚠️ aider 安装失败（best-effort，不影响 original 默认工作流）：" + err.splitlines()[0])
    elif optional and not include_aider:
        log("⏭️ 跳过 aider（默认 original 工作流不需要；需要 aider 自修复时设 include_aider=true）")

    # 6) 验证（含 1 次重试兜底，防首跑偶发缺包）
    log("🔍 验证关键模块可 import…")
    ok, err = await _verify_imports(vpy, verify_mods)
    if not ok:
        log("⏳ 首次验证未过，等待 3s 后重试一次…")
        await asyncio.sleep(3)
        ok, err = await _verify_imports(vpy, verify_mods)
    if not ok:
        return {"ok": False, "steps": steps, "step": "verify", "error": err}

    log("✅ 引擎安装完成，可出图")
    return {"ok": True, "steps": steps, "step": None, "error": ""}


def format_setup_failure(res: dict, engine_dir: Path) -> str:
    """把安装失败结果格式化成 Agent 可读的引导信息。"""
    ed = Path(engine_dir)
    step = res.get("step") or "?"
    err = (res.get("error") or "").strip()
    tail = "\n".join(err.splitlines()[-8:]) if err else ""
    return (
        "⚠️ 3D 引擎自动安装未完全成功（卡在步骤：%s）。\n"
        "错误摘要：\n%s\n\n"
        "可重试：\n"
        "  ① 再次调用 mfg_setup_engine(force=true) 自动重试；\n"
        "  ② 或手动在终端执行：\n"
        "     cd %s\n"
        "     python3 -m venv .venv\n"
        "     .venv/bin/python -m pip install --upgrade pip\n"
        "     .venv/bin/python -m pip install %s\n"
        "     .venv/bin/python -m pip install packages/cadpy\n"
        "  ③ 若卡在 verify 且 confirm 依赖已装，可 mfg_setup_engine(force=true) 重验。"
    ) % (step, tail, ed, " ".join(CORE_DEPS))


async def ensure_mac_ready(
    engine_dir,
    *,
    auto_setup: bool = True,
    include_aider: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    """mfg_text_to_cad 等工具调用前的「确保引擎就绪」。

    Returns:
        (ready, message)
        - ready=True：可直接调引擎
        - ready=False：message 是给 Agent/用户的引导（含如何调用 mfg_setup_engine）
    """
    ed = Path(engine_dir)
    if not engine_code_present(ed):
        return False, (
            "❌ 未找到 MAC 引擎代码于 %s（需 run_mac.py + multi_agent_cad/）。\n"
            "这是引擎本身缺失，不是 venv 问题：请把 Multi-Agent-CAD 引擎放到该目录，"
            "或设 MFG_CAD_ENGINE_DIR 指向引擎根，然后调用 mfg_setup_engine 安装依赖。"
        ) % ed
    if is_provisioned(ed):
        return True, ""
    if not auto_setup:
        return False, (
            "⚙️ 检测到 3D 引擎 venv 尚未安装（首次使用）。\n"
            "请先调用 mfg_setup_engine() 一键安装（约需几分钟，仅首次），装好后再重试本次建模。\n"
            "安装完成后 Agent 会自动继续出图，用户无需其他操作。"
        )
    # 自动安装
    if progress:
        progress("⚙️ 首次配置 3D 引擎中，请稍候…（创建 venv 并安装依赖，仅此一次）")
    res = await provision_engine(ed, include_aider=include_aider, progress=progress)
    if res["ok"]:
        return True, ""
    return False, format_setup_failure(res, ed)


# ─────────────────────────────────────────────────────────────
# FreeCAD 引擎就绪（M1-4 · ProToolAdapter 后端分发，复用 P6/P7）
# ─────────────────────────────────────────────────────────────

# FreeCAD 引擎目录（~/.vermes/engines/freecad），用户可手动放入 freecadcmd 或让 Vermes
# 自动发现系统已安装的 FreeCAD（discovery-first 设计，不做 2.5GB 重资产分发）。
FREECAD_ENGINE_DIR_NAME = "freecad"


def get_freecad_engine_dir() -> Path:
    """解析 FreeCAD 引擎目录（可被 VERMES_FREECAD_ENGINE_DIR 覆盖）。

    与 FreeCADAdapter._DEFAULT_ENGINE_CMD 的父目录保持一致：~/.vermes/engines/freecad。
    """
    import os

    return Path(
        os.environ.get(
            "VERMES_FREECAD_ENGINE_DIR",
            str(Path.home() / ".vermes" / "engines" / "freecad"),
        )
    ).resolve()


# macOS 常见安装位置（用户已装 FreeCAD.app 但未走 P7 引擎目录）。
# 抽成模块级常量便于测试 hermetic：monkeypatch 为 () 即可屏蔽系统 FreeCAD，
# 避免「测试机装了 FreeCAD」导致 _find_freecadcmd 返回系统路径、破坏 absent 断言。
FREECAD_SYSTEM_DIRS = (
    # macOS
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/opt/homebrew/opt/freecad/libexec/bin/freecadcmd",
    # Linux
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
    # Windows (standard install paths)
    r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe",
    r"C:\Program Files\FreeCAD\bin\freecadcmd.exe",
    r"C:\Program Files (x86)\FreeCAD 1.0\bin\freecadcmd.exe",
    r"C:\Program Files (x86)\FreeCAD\bin\freecadcmd.exe",
)


def _find_freecadcmd(engine_dir: Path) -> Optional[Path]:
    """定位 freecadcmd：引擎目录优先 → 系统常见路径 → PATH 查找。"""
    # 1. 引擎目录（~/.vermes/engines/freecad/，或用户已手动放入）
    fc = Path(engine_dir) / "freecadcmd"
    if fc.exists():
        return fc
    # 2. 系统常见安装路径（macOS/Linux/Windows）
    for c in FREECAD_SYSTEM_DIRS:
        if Path(c).exists():
            return Path(c)
    # 3. PATH 查找
    import shutil
    on_path = shutil.which("freecadcmd")
    if on_path:
        return Path(on_path)
    return None


def _install_guide() -> str:
    """返回 FreeCAD 安装指引文案（平台感知）。"""
    import platform

    system = platform.system()
    if system == "Darwin":
        return (
            "⚙️ 未检测到 FreeCAD（freecadcmd 不在搜索路径）。\n"
            "安装方式（任选其一）：\n"
            "  ① brew install --cask freecad\n"
            "  ② 从 https://www.freecad.org/downloads.php 下载 DMG 并安装\n"
            "安装后重试即可，Vermes 会自动发现。"
        )
    elif system == "Windows":
        return (
            "⚙️ 未检测到 FreeCAD（freecadcmd.exe 不在搜索路径）。\n"
            "安装方式（任选其一）：\n"
            "  ① 从 https://www.freecad.org/downloads.php 下载安装包\n"
            "  ② winget install FreeCAD.FreeCAD\n"
            "安装后重试即可，Vermes 会自动发现。"
        )
    else:  # Linux
        return (
            "⚙️ 未检测到 FreeCAD（freecadcmd 不在搜索路径）。\n"
            "安装方式（任选其一）：\n"
            "  ① sudo apt install freecad （Debian/Ubuntu）\n"
            "  ② sudo dnf install freecad （Fedora）\n"
            "  ③ 从 https://www.freecad.org/downloads.php 下载 AppImage\n"
            "安装后重试即可，Vermes 会自动发现。"
        )


def ensure_freecad_ready(
    engine_dir=None,
    *,
    auto_setup: bool = False,
    progress: Optional[Callable[[str], None]] = None,
    _installer=None,  # deprecated: kept for test compat, ignored
) -> tuple[bool, str]:
    """FreeCAD 引擎就绪检查（M1-4 · discovery-first 设计）。

    设计哲学（ProToolAdapter）：用户自装 FreeCAD，Vermes 自动发现 + 自动连接。
    不做 2.5GB 重资产分发——FreeCAD 是成熟开源软件，用户已有或容易获取。

    Returns (ready, message):
    - ready=True：freecadcmd 已就位，可直接拉起 bridge；
    - ready=False：message 是给 Agent/用户的安装指引。

    发现路径（按优先级）:
      1. ~/.vermes/engines/freecad/freecadcmd（用户手动放入）
      2. macOS /Applications/FreeCAD.app/... / Linux /usr/bin / Windows Program Files
      3. PATH 查找（which/where freecadcmd）
    """
    ed = Path(engine_dir) if engine_dir else get_freecad_engine_dir()
    if _find_freecadcmd(ed) is not None:
        return True, ""

    # 未找到 → 返回平台感知的安装指引
    return False, _install_guide()
