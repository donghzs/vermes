"""REST 层回归测试：验证 workflow 蓝图端到端（路由 + handler + SQLite 持久化）。

不依赖 LLM：覆盖 CRUD 全链路 + 校验分支；run 端点仅验证路由/404/校验，
真实 LLM 执行由 G6 触发器测试覆盖（需凭证，不在本环境跑）。
"""

import copy
import os
import tempfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 隔离测试 DB：vermes_cli/blueprints/workflows.py 用 VERMES_HOME/workflow_templates.db
_TMP = tempfile.mkdtemp(prefix="wf_rest_")
os.environ["VERMES_HOME"] = _TMP

from vermes_cli.blueprints.workflows import register_to  # noqa: E402

app = FastAPI()
register_to(app)
client = TestClient(app)


def _wf(name):
    """返回全新深拷贝，避免用例间共享 steps 导致依赖被意外篡改。"""
    base = {
        "name": name,
        "description": "端到端回归",
        "steps": [
            {"id": "a", "title": "收集需求", "description": "d1", "deliverable": "x", "done_when": "y", "dependencies": []},
            {"id": "b", "title": "写方案", "description": "d2", "deliverable": "z", "done_when": "w", "dependencies": ["a"]},
        ],
    }
    return copy.deepcopy(base)


def test_save_and_list():
    r = client.post("/api/workflows", json=_wf("rest_demo"))
    assert r.status_code == 200, r.text
    assert r.json()["version"] >= 1
    lst = client.get("/api/workflows").json()
    assert any(w["name"] == "rest_demo" and w["step_count"] == 2 for w in lst)


def test_get_and_dependencies_persisted():
    r = client.get("/api/workflows/rest_demo")
    assert r.status_code == 200
    body = r.json()
    assert len(body["steps"]) == 2
    by_id = {s["id"]: s for s in body["steps"]}
    assert by_id["b"]["dependencies"] == ["a"]
    assert by_id["a"]["dependencies"] == []


def test_xy_coordinates_persisted():
    """前端 DAG 编辑器持久化节点坐标（x/y），后端须原样透传存储与返回。"""
    wf = _wf("with_xy")
    wf["steps"][0]["x"] = 120.5
    wf["steps"][0]["y"] = 64.0
    wf["steps"][1]["x"] = 480.0
    wf["steps"][1]["y"] = 200.25
    r = client.post("/api/workflows", json=wf)
    assert r.status_code == 200, r.text
    body = client.get("/api/workflows/with_xy").json()
    by_id = {s["id"]: s for s in body["steps"]}
    assert by_id["a"]["x"] == 120.5
    assert by_id["a"]["y"] == 64.0
    assert by_id["b"]["x"] == 480.0
    assert by_id["b"]["y"] == 200.25


def test_bad_dependency_rejected():
    bad = _wf("bad_dep")
    bad["steps"][1]["dependencies"] = ["ghost"]
    r = client.post("/api/workflows", json=bad)
    assert r.status_code == 400


def test_run_unknown_404():
    r = client.post("/api/workflows/nope/run", json={})
    assert r.status_code == 404


def test_delete():
    # 先存一个
    client.post("/api/workflows", json=_wf("to_del"))
    assert client.get("/api/workflows/to_del").status_code == 200
    d = client.delete("/api/workflows/to_del")
    assert d.status_code == 200 and d.json().get("ok") is True
    assert client.get("/api/workflows/to_del").status_code == 404
