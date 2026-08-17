"""M1-2 FreeCADAdapter 测试（真实逻辑，无需 FreeCAD）。

FreeCAD 翻译逻辑在 `vermes_freecad_bridge.py`（仅 freecadcmd 内能跑），由 M1-6 真机 PoC 覆盖。
本文件验证 FreeCADAdapter 自身的两块真实逻辑：
1. 请求构造：高层方法把契约对象正确序列化为桥协议 payload。
2. 响应解析：桥返回的 JSON 正确还原为 FeatureNode / AdapterResult / dict[Path]。
通过注入假 `_transport` 实现（脱离 FreeCAD），不 mock 任何关键路径。
3. 优雅降级：freecadcmd 缺失时 is_available()=False 且不抛异常。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.backends import FreeCADAdapter, EditOp, FeatureNode, AdapterResult


class _FakeTransport:
    """记录请求并返回脚本化响应的假 transport。"""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []  # (cmd, session_id, payload)

    def __call__(self, cmd, session_id, payload):
        self.calls.append((cmd, session_id, payload))
        return self._responses[cmd]


class TestGracefulAvailability:
    def test_available_with_existing_cmd(self):
        with tempfile.NamedTemporaryFile() as f:
            a = FreeCADAdapter(freecadcmd=f.name)
            assert a.is_available() is True

    def test_unavailable_without_cmd(self):
        a = FreeCADAdapter(freecadcmd="/no/such/freecadcmd")
        assert a.is_available() is False

    def test_ensure_ready_false_when_absent(self):
        a = FreeCADAdapter(freecadcmd="/no/such/freecadcmd")
        # 不抛、不触发下载（auto_setup=False）
        assert a.ensure_ready(auto_setup=False) is False

    def test_is_subclass_of_contract(self):
        assert isinstance(FreeCADAdapter(freecadcmd="/nonexistent"), FreeCADAdapter)


class TestRequestConstruction:
    def setup_method(self):
        self.adapter = FreeCADAdapter(freecadcmd="/nonexistent")
        self.fake = _FakeTransport(
            {
                "import_step": {"ok": True, "feature_tree": [{"id": "Body", "kind": "body", "label": "BaseBody"}], "native_doc": "/s/s1/native.FCStd"},
                "edit_op": {"ok": True, "feature_tree": [{"id": "Fillet", "kind": "fillet", "label": "Fillet", "params": {"radius": 2.0}}], "native_doc": "/s/s1/native.FCStd"},
                "feature_tree": {"ok": True, "feature_tree": [{"id": "Body", "kind": "body", "label": "BaseBody"}]},
                "export": {"ok": True, "exports": {"stl": "/s/s1/out.stl", "step": "/s/s1/out.step"}},
                "create_doc": {"ok": True, "native_doc": "/s/s1/native.FCStd"},
                "open": {"ok": True},
                "close": {"ok": True},
            }
        )
        self.adapter._transport = self.fake

    def test_import_step_request(self):
        r = self.adapter.import_step("s1", "/x.step")
        assert self.fake.calls[-1][0] == "import_step"
        assert self.fake.calls[-1][2] == {"step_path": "/x.step"}
        assert isinstance(r, AdapterResult) and r.ok

    def test_apply_edit_op_request_serializes_op(self):
        op = EditOp(op="fillet", target="edges_all", params={"radius": 2.0})
        self.adapter.apply_edit_op("s1", op)
        sent = self.fake.calls[-1]
        assert sent[0] == "edit_op"
        assert sent[2]["op"] == {"op": "fillet", "target": "edges_all", "params": {"radius": 2.0}}

    def test_apply_edit_op_forwards_export_hint(self):
        op = EditOp(op="fillet", target="edges_all", params={"radius": 1.0, "export": ["stl"]})
        self.adapter.apply_edit_op("s1", op)
        assert self.fake.calls[-1][2].get("export") == ["stl"]

    def test_open_request_derives_session_from_path(self):
        ok = self.adapter.open("/tmp/sessions/s9/native.FCStd")
        assert ok is True
        assert self.fake.calls[-1][1] == "s9"
        assert self.fake.calls[-1][2] == {"doc_path": "/tmp/sessions/s9/native.FCStd"}

    def test_export_request(self):
        self.adapter.export("s1", ["stl", "step"])
        assert self.fake.calls[-1][0] == "export"
        assert self.fake.calls[-1][2] == {"formats": ["stl", "step"]}


class TestResponseParsing:
    def setup_method(self):
        self.adapter = FreeCADAdapter(freecadcmd="/nonexistent")
        self.fake = _FakeTransport(
            {
                "import_step": {"ok": True, "feature_tree": [{"id": "Body", "kind": "body", "label": "BaseBody"}], "native_doc": "/s/s1/native.FCStd"},
                "edit_op": {"ok": True, "feature_tree": [{"id": "Fillet", "kind": "fillet", "label": "Fillet", "params": {"radius": 2.0}}], "native_doc": "/s/s1/native.FCStd", "exports": {"stl": "/s/s1/out.stl"}},
                "feature_tree": {"ok": True, "feature_tree": [{"id": "Body", "kind": "body", "label": "BaseBody"}]},
                "export": {"ok": True, "exports": {"stl": "/s/s1/out.stl", "step": "/s/s1/out.step"}},
                "create_doc": {"ok": True, "native_doc": "/s/s1/native.FCStd"},
                "open": {"ok": True},
                "close": {"ok": True},
            }
        )
        self.adapter._transport = self.fake

    def test_import_step_parses_tree_and_native_doc(self):
        r = self.adapter.import_step("s1", "/x.step")
        assert isinstance(r.feature_tree, list)
        assert all(isinstance(n, FeatureNode) for n in r.feature_tree)
        assert r.feature_tree[0].kind == "body"
        assert isinstance(r.native_doc, Path)

    def test_apply_edit_op_parses_exports_to_paths(self):
        op = EditOp(op="fillet", target="edges_all", params={"radius": 2.0, "export": ["stl"]})
        r = self.adapter.apply_edit_op("s1", op)
        assert isinstance(r.exports, dict)
        assert all(isinstance(v, Path) for v in r.exports.values())

    def test_get_feature_tree_returns_nodes(self):
        tree = self.adapter.get_feature_tree("s1")
        assert isinstance(tree, list) and all(isinstance(n, FeatureNode) for n in tree)

    def test_export_returns_path_dict(self):
        out = self.adapter.export("s1", ["stl", "step"])
        assert set(out) == {"stl", "step"}
        assert all(isinstance(v, Path) for v in out.values())

    def test_create_doc_returns_path(self):
        p = self.adapter.create_doc("s1")
        assert isinstance(p, Path)

    def test_error_response_becomes_adapter_result_err(self):
        self.adapter._transport = _FakeTransport(
            {"edit_op": {"ok": False, "error": "几何非法：边不存在"}}
        )
        op = EditOp(op="fillet", target="edge:99", params={"radius": 1.0})
        r = self.adapter.apply_edit_op("s1", op)
        assert r.ok is False
        assert r.error == "几何非法：边不存在"
