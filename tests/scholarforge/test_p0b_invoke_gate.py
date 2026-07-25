"""P0b 验证：POST /api/tools/invoke（registry.dispatch）+ api_save_section 质量闸门。

不依赖 LLM/网络：闸门本身为零联网 fail-open；invoke 端点走 registry.dispatch。
"""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tools.registry import registry
import hermes_cli.scholarforge.blueprint as bp
import hermes_cli.scholarforge.tools as _sf_tools

# 运行时由 module_loader 调 register_tools() 填充全局 registry；
# 隔离测试进程需手动触发一次（handler 不依赖 host_api，传 None 即可）。
_sf_tools.register_tools()


def _make_app():
    app = FastAPI()
    bp.register_to(app)
    return app


def test_dispatch_unknown_tool_returns_error_json():
    out = registry.dispatch("__no_such_tool__", {})
    assert isinstance(out, str)
    payload = json.loads(out)
    assert "error" in payload


def test_scholarforge_write_entry_registered_and_async():
    entry = registry.get_entry("scholarforge_write")
    assert entry is not None
    assert entry.is_async is True
    assert callable(entry.handler)


def test_invoke_route_registered():
    app = _make_app()
    paths = [getattr(r, "path", None) for r in app.routes]
    assert "/api/tools/invoke" in paths


def test_invoke_route_unknown_tool():
    client = TestClient(_make_app())
    resp = client.post(
        "/api/tools/invoke", json={"name": "__no_such_tool__", "args": {}}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "result" in body
    assert "error" in body["result"]


def test_invoke_route_missing_name_400():
    client = TestClient(_make_app())
    resp = client.post("/api/tools/invoke", json={})
    assert resp.status_code == 400


def test_api_save_section_runs_quality_gate(monkeypatch):
    calls = []
    saves = []

    def fake_gate(project_id, section_type, content, mode="flag", stage="write"):
        calls.append((project_id, section_type, content, mode, stage))
        return (content + " [PURIFIED]", "report-md", False)

    def fake_save(pid, section_key, content):
        saves.append((pid, section_key, content))

    def fake_update(pid, **kwargs):
        pass

    monkeypatch.setattr(
        "hermes_cli.scholarforge.quality_gate.run_quality_gate", fake_gate
    )
    monkeypatch.setattr(
        "hermes_cli.scholarforge.database.save_section_content", fake_save
    )
    monkeypatch.setattr(
        "hermes_cli.scholarforge.database.update_project", fake_update
    )

    client = TestClient(_make_app())
    resp = client.post(
        "/api/scholar/projects/1/section/introduction",
        json={"content": "raw draft text"},
    )
    assert resp.status_code == 200
    # 闸门被调用且 mode=flag（与 commit 0fa84d3f9 一致）
    assert calls, "run_quality_gate was not called"
    assert calls[0][3] == "flag"
    # 净化后的内容用于落库 —— 消除「前端编辑直存绕过闸门」缺口
    assert saves, "save_section_content was not called"
    assert saves[0][2] == "raw draft text [PURIFIED]"
