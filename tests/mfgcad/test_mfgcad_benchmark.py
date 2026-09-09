"""benchmark 模块测试。"""
import pytest
from vermes_cli.mfgcad.benchmark import (
    BenchmarkTask,
    TaskResult,
    TASKS,
    run_benchmark,
    score_task,
)


def test_tasks_count():
    assert len(TASKS) == 10


def test_tasks_categories():
    cats = [t.category for t in TASKS]
    assert cats.count("basic") == 5
    assert cats.count("intermediate") == 3
    assert cats.count("advanced") == 2


def test_tasks_have_prompts():
    for t in TASKS:
        assert t.prompt and len(t.prompt) > 5
        assert t.id and len(t.id) > 0


def test_tasks_unique_ids():
    ids = [t.id for t in TASKS]
    assert len(ids) == len(set(ids))


def test_score_pass():
    task = TASKS[0]  # calibration cube
    result = TaskResult(
        task_id=task.id,
        pass_=True,
        step_generated=True,
        volume_mm3=125000,
        bbox_mm=(50, 50, 50),
        parameter_count=3,
        wall_time_s=5.0,
    )
    scores = score_task(result, task)
    assert scores["pass"] is True
    assert scores["step_generated"] is True
    assert scores["volume_in_range"] is True
    assert scores["bbox_match"] is True
    assert scores["meets_min_params"] is True


def test_score_volume_out_of_range():
    task = TASKS[0]
    result = TaskResult(
        task_id=task.id,
        pass_=True,
        step_generated=True,
        volume_mm3=50000,  # 远小于预期 125000
        bbox_mm=(50, 50, 50),
        parameter_count=3,
    )
    scores = score_task(result, task)
    assert scores["volume_in_range"] is False


def test_score_bbox_mismatch():
    task = TASKS[0]
    result = TaskResult(
        task_id=task.id,
        pass_=True,
        step_generated=True,
        volume_mm3=125000,
        bbox_mm=(70, 50, 50),  # x 偏差过大
        parameter_count=3,
    )
    scores = score_task(result, task)
    assert scores["bbox_match"] is False


def test_score_fail():
    task = TASKS[0]
    result = TaskResult(
        task_id=task.id,
        pass_=False,
        step_generated=False,
        error="引擎未就绪",
    )
    scores = score_task(result, task)
    assert scores["pass"] is False
    assert scores["step_generated"] is False


def test_score_insufficient_params():
    task = TASKS[8]  # smart_case, min_parameters=8
    result = TaskResult(
        task_id=task.id,
        pass_=True,
        step_generated=True,
        volume_mm3=55000,
        bbox_mm=(80, 50, 30),
        parameter_count=3,  # 少于 min_parameters=8
    )
    scores = score_task(result, task)
    assert scores["meets_min_params"] is False


def test_run_benchmark_no_engine(monkeypatch):
    """引擎未就绪时 benchmark 应优雅降级。"""
    import asyncio

    async def mock_ensure(*args, **kwargs):
        return False, "引擎未安装"

    monkeypatch.setattr(
        "vermes_cli.mfgcad.engine_setup.ensure_mac_ready",
        mock_ensure,
    )

    result = run_benchmark(tasks=TASKS[:2], verbose=False)
    assert result["summary"]["total"] == 2
    assert result["summary"]["passed"] == 0
    assert result["summary"]["pass_rate"] == 0.0
    assert all(r["pass"] is False for r in result["results"])
    assert all("引擎未就绪" in r.get("error", "") for r in result["results"]) or \
           all(not r["step_generated"] for r in result["results"])
