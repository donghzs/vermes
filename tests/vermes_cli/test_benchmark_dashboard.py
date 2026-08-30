# -*- coding: utf-8 -*-
"""P4-4 T2 可视化大盘端点测试。

验证 benchmark 端点注册 + 数据读取 + dry-run 触发。
"""
import sys
import json
import os
from pathlib import Path

sys.path.insert(0, "/Users/dongzusheng/Projects/vermes-electron")

import pytest
from starlette.testclient import TestClient


def test_benchmark_endpoints_registered():
    """端点注册到 app 且在 _PUBLIC_API_PATHS（免鉴权）。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)

    # GET /runs 应返回 200（public 端点，无 token）
    r = client.get("/api/v1/benchmark/runs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "runs" in body
    assert "total" in body
    assert isinstance(body["runs"], list)

    # GET /tasks 应返回 200
    r2 = client.get("/api/v1/benchmark/tasks")
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert "tasks" in body2
    assert "total" in body2
    assert body2["total"] >= 1  # 至少有 scholarforge 任务


def test_benchmark_runs_structure():
    """runs 返回结构正确（每条含 mode/llm_tier/summary/results）。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)
    r = client.get("/api/v1/benchmark/runs?limit=5")
    assert r.status_code == 200
    body = r.json()
    for run in body["runs"]:
        assert "mode" in run
        assert "llm_tier" in run
        assert "summary" in run
        assert "results" in run
        for result in run["results"]:
            assert "task_id" in result
            assert "pass" in result
            assert "wired" in result


def test_benchmark_tasks_cover_scholarforge():
    """tasks 覆盖 scholarforge 27 工具的关联任务。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)
    r = client.get("/api/v1/benchmark/tasks")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    # 每个任务有 id/title/kind/tools/llm_required
    for t in tasks:
        assert "id" in t
        assert "title" in t
        assert "kind" in t
        assert "tools" in t
        assert "llm_required" in t
    # 至少有 single 和 pipeline 两类
    kinds = {t["kind"] for t in tasks}
    assert "single" in kinds or "pipeline" in kinds


def test_benchmark_run_dry_triggers():
    """POST /run mode=dry 触发一次 dry-run benchmark 并返回报告。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)
    r = client.post("/api/v1/benchmark/run?mode=dry&llm_tier=strong")
    assert r.status_code == 200, r.text
    report = r.json()
    assert "mode" in report
    assert report["mode"] == "dry"
    assert "llm_tier" in report
    assert "summary" in report
    assert "results" in report


def test_benchmark_run_live_rejected():
    """POST /run mode=live 返回 501（需 CLI 触发）。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)
    r = client.post("/api/v1/benchmark/run?mode=live&llm_tier=strong")
    assert r.status_code == 501


def test_benchmark_run_invalid_mode():
    """POST /run mode=invalid 返回 400。"""
    import vermes_cli.web_server as ws

    client = TestClient(ws.app)
    r = client.post("/api/v1/benchmark/run?mode=invalid")
    assert r.status_code == 400
