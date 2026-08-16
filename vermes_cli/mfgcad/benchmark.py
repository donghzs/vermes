"""
Vermes mfgcad benchmark — 10 任务端到端评测。

参考 text-to-cad 的 benchmark 范式，建立可度量的「Agent 建模能力」基准集。
每个任务包含：NL prompt + 预期几何特征 + 评分维度。

使用方式：
  # CLI 手动跑
  vermes module benchmark mfgcad

  # Python API
  from vermes_cli.mfgcad.benchmark import run_benchmark
  results = run_benchmark(engine="mac", verbose=True)

评分维度：
  - pass/fail：STEP 文件是否成功生成
  - volume_match：体积是否在预期范围（±20%）
  - bbox_match：包围盒尺寸是否匹配
  - parameter_count：可抽参数数量（可编辑性指标）
  - wall_time_s：生成耗时
"""

from __future__ import annotations

import time
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 10 任务定义
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTask:
    """单个 benchmark 任务。"""
    id: str
    prompt: str
    category: str  # basic / intermediate / advanced
    expected_volume_mm3: Optional[Tuple[float, float]] = None  # (min, max)
    expected_bbox_mm: Optional[Tuple[float, float, float]] = None  # (x, y, z)
    min_parameters: int = 3  # 最少可抽参数数
    timeout_s: int = 120


TASKS: List[BenchmarkTask] = [
    # ── 基础（5 个）──
    BenchmarkTask(
        id="01_calibration_cube",
        prompt="创建一个 50×50×50mm 的校准立方体",
        category="basic",
        expected_volume_mm3=(124000, 126000),  # 50^3 = 125000
        expected_bbox_mm=(50, 50, 50),
        min_parameters=3,
    ),
    BenchmarkTask(
        id="02_hollow_cylinder",
        prompt="创建一个空心圆柱，外径 60mm，内径 50mm，高 100mm",
        category="basic",
        expected_volume_mm3=(78000, 88000),  # π(30²-25²)×100 ≈ 86394
        expected_bbox_mm=(60, 60, 100),
        min_parameters=4,
    ),
    BenchmarkTask(
        id="03_hex_nut",
        prompt="创建一个 M10 六角螺母，对边距 17mm，厚度 8mm",
        category="basic",
        expected_volume_mm3=(1000, 3000),  # 近似
        expected_bbox_mm=(17, 17, 8),
        min_parameters=4,
    ),
    BenchmarkTask(
        id="04_l_bracket",
        prompt="创建一个 L 型支架，两臂各长 80mm、宽 40mm、厚 10mm",
        category="basic",
        expected_volume_mm3=(55000, 70000),
        expected_bbox_mm=(80, 80, 40),
        min_parameters=5,
    ),
    BenchmarkTask(
        id="05_sphere_shell",
        prompt="创建一个球壳，外径 80mm，壁厚 3mm",
        category="basic",
        expected_volume_mm3=(200000, 250000),  # 4/3π(40³-37³) ≈ 225000
        expected_bbox_mm=(80, 80, 80),
        min_parameters=3,
    ),
    # ── 中级（3 个）──
    BenchmarkTask(
        id="06_gear_20t",
        prompt="创建一个 20 齿直齿轮，模数 2，齿宽 15mm，内孔径 10mm",
        category="intermediate",
        expected_volume_mm3=(5000, 15000),
        expected_bbox_mm=(48, 48, 15),  # m*z = 2*20 = 40 直径 + 齿顶
        min_parameters=5,
    ),
    BenchmarkTask(
        id="07_threaded_rod",
        prompt="创建一根 M8 螺纹杆，长度 100mm",
        category="intermediate",
        expected_volume_mm3=(3000, 6000),
        expected_bbox_mm=(8, 8, 100),
        min_parameters=3,
    ),
    BenchmarkTask(
        id="08_assembly_two_parts",
        prompt="创建一个轴孔配合：轴 Φ20×50mm，孔件 60×60×30mm 中间挖 Φ20 通孔",
        category="intermediate",
        expected_volume_mm3=(30000, 45000),
        expected_bbox_mm=(60, 60, 50),  # 组装后
        min_parameters=6,
    ),
    # ── 高级（2 个）──
    BenchmarkTask(
        id="08_smart_case",
        prompt="创建一个智能硬件外壳，外形 80×50×30mm，壁厚 3mm，顶部有 20×20mm 凸台",
        category="advanced",
        expected_volume_mm3=(40000, 70000),
        expected_bbox_mm=(80, 50, 30),
        min_parameters=8,
    ),
    BenchmarkTask(
        id="10_planetary_gear_set",
        prompt="创建一个行星齿轮组：太阳轮 12 齿，行星轮 18 齿×3，齿圈 48 齿，模数 1.5，厚度 10mm",
        category="advanced",
        expected_volume_mm3=(20000, 50000),
        expected_bbox_mm=(75, 75, 10),  # 齿圈外径 ≈ m*(z+2) = 1.5*50
        min_parameters=8,
        timeout_s=300,
    ),
]


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------

@dataclass
class TaskResult:
    """单个任务的运行结果。"""
    task_id: str
    pass_: bool = False
    step_generated: bool = False
    volume_mm3: Optional[float] = None
    bbox_mm: Optional[Tuple[float, float, float]] = None
    parameter_count: int = 0
    wall_time_s: float = 0.0
    error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


def score_task(
    result: TaskResult,
    task: BenchmarkTask,
) -> Dict[str, Any]:
    """对单个任务结果评分。"""
    scores = {
        "pass": result.pass_,
        "step_generated": result.step_generated,
        "wall_time_s": round(result.wall_time_s, 2),
    }

    if result.volume_mm3 is not None and task.expected_volume_mm3:
        lo, hi = task.expected_volume_mm3
        scores["volume_in_range"] = lo <= result.volume_mm3 <= hi
        scores["volume_mm3"] = round(result.volume_mm3, 1)

    if result.bbox_mm is not None and task.expected_bbox_mm:
        ex, ey, ez = task.expected_bbox_mm
        rx, ry, rz = result.bbox_mm
        scores["bbox_match"] = (
            abs(rx - ex) < ex * 0.2 and
            abs(ry - ey) < ey * 0.2 and
            abs(rz - ez) < ez * 0.2
        )
        scores["bbox_mm"] = [round(v, 1) for v in result.bbox_mm]

    scores["parameter_count"] = result.parameter_count
    scores["meets_min_params"] = result.parameter_count >= task.min_parameters

    return scores


# ---------------------------------------------------------------------------
# 运行
# ---------------------------------------------------------------------------

def run_benchmark(
    engine: str = "mac",
    tasks: Optional[List[BenchmarkTask]] = None,
    verbose: bool = False,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """运行 benchmark。

    Args:
        engine: 引擎名（"mac" / "trellis"）
        tasks: 要跑的任务列表（None = 全部）
        verbose: 打印详细日志
        output_path: 结果写入 JSON 文件

    Returns:
        {"total": 10, "passed": 7, "results": [...], "summary": {...}}
    """
    if tasks is None:
        tasks = TASKS

    results: List[TaskResult] = []
    passed = 0

    for task in tasks:
        if verbose:
            print(f"\n{'='*60}")
            print(f"任务 {task.id} [{task.category}]")
            print(f"  prompt: {task.prompt}")

        tr = TaskResult(task_id=task.id)
        t0 = time.time()

        try:
            # 调引擎
            from vermes_cli.mfgcad.engine_setup import ensure_mac_ready
            from vermes_cli.mfgcad.tools import _handle_mfg_text_to_cad
            from agent.module_catalog import ensure_assets_ready

            # 确保引擎就绪
            engine_dir = Path.home() / ".vermes" / "engines" / "mac"
            ok, msg = __import__("asyncio").run(
                ensure_mac_ready(engine_dir, auto_setup=False)
            )
            if not ok:
                tr.error = f"引擎未就绪: {msg}"
                tr.wall_time_s = time.time() - t0
                results.append(tr)
                if verbose:
                    print(f"  ❌ {tr.error}")
                continue

            # 调 mfg_text_to_cad
            import uuid
            session_id = f"bench_{task.id}_{uuid.uuid4().hex[:6]}"

            handler_result = _handle_mfg_text_to_cad(
                description=task.prompt,
                engine=engine,
                session_id=session_id,
                output_format="step",
            )

            tr.wall_time_s = time.time() - t0

            if isinstance(handler_result, dict) and handler_result.get("ok"):
                tr.step_generated = True
                tr.pass_ = True
                passed += 1

                # 尝试读取体积和 bbox
                session_dir = Path.home() / ".vermes" / "mfgcad" / "sessions" / session_id
                session_json = session_dir / "session.json"
                if session_json.exists():
                    data = json.loads(session_json.read_text())
                    tr.volume_mm3 = data.get("volume_mm3")
                    tr.bbox_mm = data.get("bbox_mm")

                # 抽参数
                try:
                    from vermes_cli.mfgcad.parametric import extract_parameters, acquire_source
                    source = acquire_source(session_id)
                    if source:
                        params = extract_parameters(source)
                        tr.parameter_count = len(params)
                except Exception:
                    pass

                if verbose:
                    print(f"  ✅ 通过 ({tr.wall_time_s:.1f}s)")
                    if tr.volume_mm3:
                        print(f"  体积: {tr.volume_mm3:.1f} mm³")
            else:
                error_msg = handler_result.get("error", "未知错误") if isinstance(handler_result, dict) else str(handler_result)
                tr.error = error_msg
                if verbose:
                    print(f"  ❌ {tr.error}")

        except Exception as e:
            tr.wall_time_s = time.time() - t0
            tr.error = str(e)
            if verbose:
                print(f"  ❌ 异常: {e}")

        results.append(tr)

    # 汇总
    summary = {
        "total": len(tasks),
        "passed": passed,
        "pass_rate": round(passed / len(tasks) * 100, 1) if tasks else 0,
        "avg_time_s": round(sum(r.wall_time_s for r in results) / len(results), 2) if results else 0,
        "categories": {},
    }

    for cat in ("basic", "intermediate", "advanced"):
        cat_tasks = [r for r, t in zip(results, tasks) if t.category == cat]
        cat_total = len(cat_tasks)
        cat_passed = sum(1 for r in cat_tasks if r.pass_)
        summary["categories"][cat] = {
            "total": cat_total,
            "passed": cat_passed,
            "pass_rate": round(cat_passed / cat_total * 100, 1) if cat_total else 0,
        }

    scored = [score_task(r, t) for r, t in zip(results, tasks)]

    output = {
        "summary": summary,
        "results": scored,
        "tasks": [
            {"id": t.id, "prompt": t.prompt, "category": t.category}
            for t in tasks
        ],
    }

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))
        logger.info("benchmark 结果写入 %s", output_path)

    return output


__all__ = [
    "BenchmarkTask",
    "TaskResult",
    "TASKS",
    "run_benchmark",
    "score_task",
]
