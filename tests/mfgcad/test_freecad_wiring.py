"""M1-3 web_server + agent 工具接线测试（真实路由，FreeCAD 用假适配器）。

验证 FreeCADAdapter 已接入两条真实链路：
1. web_server `POST /api/mfgcad/edit` 与 `GET .../feature-tree`（端到端，经 TestClient）。
2. 三个 agent 工具（mfg_open_in_freecad/mfg_edit_feature/mfg_export_fcstd）已注册进全局 registry。
FreeCAD 翻译逻辑由 M1-6 真机 PoC 覆盖；此处用假 _transport 验证「请求→响应→JSON」真实接线。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.backends import FreeCADAdapter
from vermes_cli.mfgcad import tools as mfgcad_tools
from tools.registry import registry


def _fake_adapter():
    # sys.executable 必存在 → is_available()=True，且不拉起真 FreeCAD
    a = FreeCADAdapter(freecadcmd=sys.executable)

    def transport(cmd, sid, payload):
        if cmd == "open":
            return {"ok": True}
        if cmd == "edit_op":
            return {
                "ok": True,
                "feature_tree": [
                    {"id": "Body", "kind": "body", "label": "BaseBody"},
                    {"id": "Fillet", "kind": "fillet", "label": "Fillet", "params": {"radius": 2.0}},
                ],
                "native_doc": "/s/s1/native.FCStd",
            }
        if cmd == "export":
            return {"ok": True, "exports": {"stl": "/s/s1/out.stl"}}
        if cmd == "feature_tree":
            return {"ok": True, "feature_tree": [{"id": "Body", "kind": "body", "label": "BaseBody"}]}
        return {"ok": True}

    a._transport = transport
    return a


class TestRegistration:
    def test_three_new_tools_registered(self):
        mfgcad_tools.register_tools()  # 触发注册（模块_loader 在 app 启动时会调）
        names = registry.get_tool_names_for_toolset("mfgcad")
        for n in ("mfg_open_in_freecad", "mfg_edit_feature", "mfg_export_fcstd"):
            assert n in names, f"{n} 未注册进 toolset=mfgcad"


class TestEditRoute:
    def test_edit_success(self, monkeypatch):
        a = _fake_adapter()
        monkeypatch.setattr(mfgcad_tools, "_get_freecad_adapter", lambda: a)
        monkeypatch.setattr(mfgcad_tools, "_ensure_freecad_doc", lambda adapter, sid: "/fake/native.FCStd")
        from fastapi.testclient import TestClient
        from vermes_cli.web_server import app

        c = TestClient(app)
        r = c.post(
            "/api/mfgcad/edit",
            json={"session_id": "s1", "op": {"op": "fillet", "target": "edges_all", "params": {"radius": 2.0}}},
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["feature_tree"][-1]["kind"] == "fillet"
        assert d["stl_url"].endswith("out.stl")

    def test_edit_engine_unavailable_returns_409(self, monkeypatch):
        a = FreeCADAdapter(freecadcmd="/no/such/freecadcmd")  # is_available()=False
        monkeypatch.setattr(mfgcad_tools, "_get_freecad_adapter", lambda: a)
        from fastapi.testclient import TestClient
        from vermes_cli.web_server import app

        c = TestClient(app)
        r = c.post(
            "/api/mfgcad/edit",
            json={"session_id": "s1", "op": {"op": "fillet", "target": "edges_all", "params": {}}},
        )
        assert r.status_code == 409
        assert r.json()["ok"] is False

    def test_edit_missing_op_returns_400(self, monkeypatch):
        a = _fake_adapter()
        monkeypatch.setattr(mfgcad_tools, "_get_freecad_adapter", lambda: a)
        from fastapi.testclient import TestClient
        from vermes_cli.web_server import app

        c = TestClient(app)
        r = c.post("/api/mfgcad/edit", json={"session_id": "s1", "op": {}})
        assert r.status_code == 400


class TestFeatureTreeRoute:
    def test_feature_tree_returns_nodes(self, monkeypatch):
        a = _fake_adapter()
        monkeypatch.setattr(mfgcad_tools, "_get_freecad_adapter", lambda: a)
        monkeypatch.setattr(mfgcad_tools, "_ensure_freecad_doc", lambda adapter, sid: "/fake/native.FCStd")
        from fastapi.testclient import TestClient
        from vermes_cli.web_server import app

        c = TestClient(app)
        r = c.get("/api/mfgcad/sessions/s1/feature-tree")
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["feature_tree"][0]["kind"] == "body"
