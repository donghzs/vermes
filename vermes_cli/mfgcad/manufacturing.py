"""
Vermes 制造链路 — DXF 导出 + G-code + 3D 打印推送。

对标 text-to-cad 的制造 Skill：
  - mfg_export_dxf: 从 STEP 导出 DXF（2D 工程图格式，激光切割/钣金用）
  - mfg_slice_gcode: 调本地 OrcaSlicer/PrusaSlicer 切片生成 G-code
  - mfg_send_print: 推送到 Bambu/拓竹打印机（局域网 HTTP API，默认 dry-run）
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DXF 导出
# ---------------------------------------------------------------------------

def export_dxf(
    step_path: Path,
    output_path: Optional[Path] = None,
    views: List[str] = None,
) -> Dict[str, Any]:
    """从 STEP 文件导出 DXF（2D 投影）。

    用 build123d 的 Projection 功能生成三视图 DXF。
    需要引擎 venv 中已安装 build123d + ezdxf。

    Args:
        step_path: STEP 文件路径
        output_path: DXF 输出路径（缺省同名 .dxf）
        views: 投影视图列表（默认 ["top", "front", "side"]）

    Returns:
        {"ok": bool, "dxf_path": str, "views": [...], "error": str}
    """
    step_path = Path(step_path)
    if not step_path.exists():
        return {"ok": False, "error": f"STEP 文件不存在: {step_path}"}

    if output_path is None:
        output_path = step_path.with_suffix(".dxf")
    else:
        output_path = Path(output_path)

    if views is None:
        views = ["top", "front", "side"]

    venv_python = Path.home() / ".vermes" / "engines" / "mac" / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return {"ok": False, "error": "引擎 venv 未安装，请先调用 mfg_setup_engine"}

    script = f'''
import sys
from pathlib import Path
from build123d import import_step, Plane, Projection

step_path = Path({str(step_path)!r})
dxf_path = Path({str(output_path)!r})
views = {views!r}

shape = import_step(step_path)

try:
    import ezdxf
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
except ImportError:
    # 无 ezdxf 时用 build123d 导出
    shape.export_dxf(dxf_path)
    print(f"OK: {{dxf_path}}")
    sys.exit(0)

# 有 ezdxf：多视图投影
for view in views:
    if view == "top":
        proj = shape.project(Plane.XY)
    elif view == "front":
        proj = shape.project(Plane.XZ)
    elif view == "side":
        proj = shape.project(Plane.YZ)
    else:
        continue
    if proj:
        # 写入 DXF
        try:
            proj.export_dxf(dxf_path.with_suffix(f".{{view}}.dxf"))
        except Exception:
            pass

# 主 DXF
try:
    shape.export_dxf(dxf_path)
    print(f"OK: {{dxf_path}}")
except Exception as e:
    print(f"FAIL: {{e}}")
    sys.exit(1)
'''

    try:
        result = subprocess.run(
            [str(venv_python), "-c", script],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"DXF 导出失败: {result.stderr[:300]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "DXF 导出超时（60s）"}
    except Exception as e:
        return {"ok": False, "error": f"DXF 导出异常: {e}"}

    return {
        "ok": True,
        "dxf_path": str(output_path),
        "views": views,
    }


# ---------------------------------------------------------------------------
# G-code 切片
# ---------------------------------------------------------------------------

def slice_gcode(
    stl_path: Path,
    output_path: Optional[Path] = None,
    slicer: str = "auto",  # auto / orca / prusa / cura
    profile: str = "default",
    layer_height: float = 0.2,
    infill: float = 20,
    filament: str = "PLA",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """调用本地切片软件生成 G-code。

    自动检测已安装的切片软件（OrcaSlicer > PrusaSlicer > Cura）。
    dry_run=True 时只返回命令不执行。

    Returns:
        {"ok": bool, "gcode_path": str, "slicer": str, "command": str, "error": str}
    """
    stl_path = Path(stl_path)
    if not stl_path.exists():
        return {"ok": False, "error": f"STL 文件不存在: {stl_path}"}

    if output_path is None:
        output_path = stl_path.with_suffix(".gcode")
    else:
        output_path = Path(output_path)

    # 检测切片软件
    slicer_cmd = None
    if slicer == "auto":
        for name, cmd in [
            ("orca", shutil.which("OrcaSlicer") or shutil.which("orca-slicer")),
            ("prusa", shutil.which("PrusaSlicer") or shutil.which("prusa-slicer")),
            ("cura", shutil.which("CuraEngine") or shutil.which("cura")),
        ]:
            if cmd:
                slicer_cmd = cmd
                slicer = name
                break
    else:
        slicer_cmd = shutil.which(slicer)

    if not slicer_cmd:
        return {
            "ok": False,
            "error": f"未找到切片软件（{slicer}），请安装 OrcaSlicer/PrusaSlicer/Cura",
            "slicer": slicer,
        }

    # 构建命令
    if slicer == "cura":
        cmd = [
            slicer_cmd,
            stl_path,
            "-o", output_path,
            "-l", str(layer_height),
            "-f", str(infill),
        ]
    else:
        # OrcaSlicer / PrusaSlicer CLI
        cmd = [
            slicer_cmd,
            "--slice",
            "--output", str(output_path),
            "--layer-height", str(layer_height),
            "--fill-density", str(infill),
            "--filament-type", filament,
        ]
        if profile != "default":
            cmd.extend(["--load-profile", profile])
        cmd.append(str(stl_path))

    if dry_run:
        return {
            "ok": True,
            "gcode_path": str(output_path),
            "slicer": slicer,
            "command": " ".join(cmd),
            "dry_run": True,
        }

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            return {"ok": False, "error": f"切片失败: {result.stderr[:300]}", "slicer": slicer}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "切片超时（300s）", "slicer": slicer}
    except Exception as e:
        return {"ok": False, "error": f"切片异常: {e}", "slicer": slicer}

    return {
        "ok": True,
        "gcode_path": str(output_path),
        "slicer": slicer,
        "command": " ".join(cmd),
    }


# ---------------------------------------------------------------------------
# 打印推送（Bambu/拓竹）
# ---------------------------------------------------------------------------

def send_print(
    gcode_path: Path,
    printer_ip: str = "",
    printer_port: int = 80,
    access_code: str = "",
    dry_run: bool = True,
) -> Dict[str, Any]:
    """推送 G-code 到 Bambu/拓竹打印机。

    ⚠️ 当前状态：仅 dry_run=True 预览可用。真实推送需要完整的 HTTP multipart
    实现（含 access_code 认证），尚未完成——非 dry_run 时返回「待后续完善」。
    见 module.yaml / 对外文档：打印推送为预览就绪、真推送待做，请勿宣称已完工。

    Bambu Lab 打印机局域网 HTTP API：
      POST /api/1/print/project/file
      (需要 access_code 认证)

    dry_run=True 时只返回命令不实际推送。

    Returns:
        {"ok": bool, "printer": str, "file": str, "error": str}
    """
    gcode_path = Path(gcode_path)
    if not gcode_path.exists():
        return {"ok": False, "error": f"G-code 文件不存在: {gcode_path}"}

    if not printer_ip:
        return {
            "ok": False,
            "error": "未配置打印机 IP，请在设置中配置或通过参数传入",
        }

    # Bambu LAN API
    url = f"http://{printer_ip}:{printer_port}/api/1/print/project/file"

    if dry_run:
        return {
            "ok": True,
            "printer": f"{printer_ip}:{printer_port}",
            "file": str(gcode_path),
            "url": url,
            "dry_run": True,
            "message": f"[dry-run] 将推送 {gcode_path.name} 到 {url}（需 access_code 认证）",
        }

    # 真实推送
    try:
        import urllib.request
        import urllib.error

        # Bambu HTTP API 需要特定格式（multipart/form-data）
        # 这里简化为提示用户手动上传或后续实现完整 HTTP 推送
        return {
            "ok": False,
            "error": "真实打印推送需要完整 HTTP multipart 实现（待后续完善），当前请用 dry_run=True 预览",
            "url": url,
        }
    except Exception as e:
        return {"ok": False, "error": f"推送异常: {e}"}


__all__ = [
    "export_dxf",
    "slice_gcode",
    "send_print",
]
