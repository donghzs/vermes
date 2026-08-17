"""M1-1 ProToolAdapter 接口契约测试（真实执行，不 mock）。

验证点（对照 `PRO_TOOL_ADAPTER_DESIGN.md` §3）：
1. ABC 强制：缺任一抽象方法的子类实例化即 TypeError（防止有人「漏实现」被静默放行）。
2. 完整实现可实例化，且每个方法返回契约规定的真实类型。
3. 序列化往返：FeatureNode / EditOp / AdapterResult 的 to_dict ↔ from_dict 一致，
   这是 `GET .../feature-tree` 与 `POST /api/mfgcad/edit` 的 JSON 契约基础。

本文件不 mock 任何路径解析/资源发现逻辑（base.py 纯数据 + ABC，无此类函数），
所有断言跑在真实代码上。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vermes_cli.mfgcad.backends import (
    AdapterResult,
    EditOp,
    FeatureNode,
    ProToolAdapter,
)


class _ConcreteAdapter(ProToolAdapter):
    """实现全部 9 个抽象方法的参考子类，行为用真实对象返回（不 mock）。"""

    name = "concrete-test"

    def is_available(self) -> bool:
        return True

    def ensure_ready(self, auto_setup: bool = False) -> bool:
        return True

    def create_doc(self, session_id: str) -> Path:
        return Path(f"/tmp/sessions/{session_id}/native.FCStd")

    def open(self, doc_path: str) -> bool:
        return Path(doc_path).exists()

    def import_step(self, session_id: str, step_path: str) -> AdapterResult:
        tree = [
            FeatureNode(id="body0", kind="body", label="BaseBody"),
            FeatureNode(id="fil0", kind="fillet", label="Fillet", params={"radius": 2.0}),
        ]
        return AdapterResult.ok_result(
            feature_tree=tree,
            native_doc=Path(f"/tmp/sessions/{session_id}/native.FCStd"),
        )

    def get_feature_tree(self, session_id: str) -> list[FeatureNode]:
        return [FeatureNode(id="body0", kind="body", label="BaseBody")]

    def apply_edit_op(self, session_id: str, op: EditOp) -> AdapterResult:
        return AdapterResult.ok_result(
            feature_tree=[FeatureNode(id=op.target, kind=op.op, label=op.op)],
            exports={op.op: Path(f"/tmp/sessions/{session_id}/{op.op}.stl")},
        )

    def export(self, session_id: str, formats: list[str]) -> dict[str, Path]:
        return {fmt: Path(f"/tmp/sessions/{session_id}/out.{fmt}") for fmt in formats}

    def close(self, session_id: str) -> None:
        return None


# ── ABC 强制 ──────────────────────────────────────────────

class TestAbcEnforcement:
    def test_concrete_adapter_instantiable(self):
        # 完整实现可实例化
        assert isinstance(_ConcreteAdapter(), ProToolAdapter)

    def test_missing_one_method_raises(self):
        # 漏实现 export → 实例化 TypeError（真实 ABC 机制，非 mock）
        class Incomplete(ProToolAdapter):
            name = "incomplete"

            def is_available(self): return True
            def ensure_ready(self, auto_setup=False): return True
            def create_doc(self, session_id): return Path("/x")
            def open(self, doc_path): return True
            def import_step(self, session_id, step_path): return AdapterResult.ok_result()
            def get_feature_tree(self, session_id): return []
            def apply_edit_op(self, session_id, op): return AdapterResult.ok_result()
            def close(self, session_id): return None
            # export 故意缺失

        import pytest
        with pytest.raises(TypeError):
            Incomplete()


# ── 方法返回类型契约 ──────────────────────────────────────

class TestMethodContracts:
    def setup_method(self):
        self.adapter = _ConcreteAdapter()

    def test_name(self):
        assert self.adapter.name == "concrete-test"

    def test_is_available_bool(self):
        assert self.adapter.is_available() is True

    def test_ensure_ready_bool(self):
        assert self.adapter.ensure_ready(auto_setup=True) is True

    def test_create_doc_path(self):
        assert isinstance(self.adapter.create_doc("s1"), Path)

    def test_open_bool(self):
        # 不存在的路径 → False（不抛）
        assert self.adapter.open("/no/such/file.FCStd") is False

    def test_import_step_returns_adapter_result(self):
        r = self.adapter.import_step("s1", "/x.step")
        assert isinstance(r, AdapterResult)
        assert r.ok is True
        assert isinstance(r.feature_tree, list)
        assert all(isinstance(n, FeatureNode) for n in r.feature_tree)
        assert isinstance(r.native_doc, Path)

    def test_get_feature_tree_list(self):
        tree = self.adapter.get_feature_tree("s1")
        assert isinstance(tree, list)
        assert all(isinstance(n, FeatureNode) for n in tree)

    def test_apply_edit_op_returns_adapter_result(self):
        r = self.adapter.apply_edit_op("s1", EditOp(op="fillet", target="edges_all", params={"radius": 1.5}))
        assert isinstance(r, AdapterResult)
        assert r.ok is True
        assert isinstance(r.exports, dict)
        assert all(isinstance(v, Path) for v in r.exports.values())

    def test_export_dict_of_paths(self):
        out = self.adapter.export("s1", ["stl", "step"])
        assert isinstance(out, dict)
        assert set(out) == {"stl", "step"}
        assert all(isinstance(v, Path) for v in out.values())

    def test_close_none(self):
        assert self.adapter.close("s1") is None


# ── 序列化往返（JSON 契约基础） ───────────────────────────

class TestSerialization:
    def test_feature_node_roundtrip(self):
        node = FeatureNode(
            id="f1", kind="fillet", label="Fillet",
            params={"radius": 2.0},
            children=[FeatureNode(id="b0", kind="body", label="Base")],
        )
        restored = FeatureNode.from_dict(node.to_dict())
        assert restored == node

    def test_feature_node_from_dict_missing_field(self):
        import pytest
        with pytest.raises(ValueError):
            FeatureNode.from_dict({"id": "x"})  # 缺 kind/label

    def test_edit_op_roundtrip(self):
        op = EditOp(op="draft", target="face:3", params={"angle": 1.5})
        assert EditOp.from_dict(op.to_dict()) == op

    def test_edit_op_from_dict_missing_field(self):
        import pytest
        with pytest.raises(ValueError):
            EditOp.from_dict({"op": "draft"})  # 缺 target

    def test_adapter_result_roundtrip_ok(self):
        r = AdapterResult.ok_result(
            feature_tree=[FeatureNode(id="b0", kind="body", label="Base")],
            native_doc=Path("/s/native.FCStd"),
            exports={"stl": Path("/s/out.stl")},
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["native_doc"] == "/s/native.FCStd"
        assert d["exports"] == {"stl": "/s/out.stl"}
        assert d["feature_tree"][0]["id"] == "b0"

    def test_adapter_result_err(self):
        r = AdapterResult.err("几何非法")
        assert r.ok is False
        assert r.error == "几何非法"
        assert r.to_dict()["ok"] is False
        assert r.to_dict()["error"] == "几何非法"

    def test_adapter_result_err_cannot_hide_ok(self):
        # ok=False 必须带 error，调用方据此标红节点（§9 风险缓解）
        r = AdapterResult.err("引擎未就绪")
        assert r.ok is False
        assert r.feature_tree is None
