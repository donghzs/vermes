"""Tests for the central tool registry."""

import json
import threading
from pathlib import Path
from unittest.mock import patch

from tools.registry import ToolRegistry, _module_registers_tools, discover_builtin_tools


def _dummy_handler(args, **kwargs):
    return json.dumps({"ok": True})


def _make_schema(name="test_tool"):
    return {
        "name": name,
        "description": f"A {name}",
        "parameters": {"type": "object", "properties": {}},
    }


class TestRegisterAndDispatch:
    def test_register_and_dispatch(self):
        reg = ToolRegistry()
        reg.register(
            name="alpha",
            toolset="core",
            schema=_make_schema("alpha"),
            handler=_dummy_handler,
        )
        result = json.loads(reg.dispatch("alpha", {}))
        assert result == {"ok": True}

    def test_dispatch_passes_args(self):
        reg = ToolRegistry()

        def echo_handler(args, **kw):
            return json.dumps(args)

        reg.register(
            name="echo",
            toolset="core",
            schema=_make_schema("echo"),
            handler=echo_handler,
        )
        result = json.loads(reg.dispatch("echo", {"msg": "hi"}))
        assert result == {"msg": "hi"}


class TestGetDefinitions:
    def test_returns_openai_format(self):
        reg = ToolRegistry()
        reg.register(
            name="t1", toolset="s1", schema=_make_schema("t1"), handler=_dummy_handler
        )
        reg.register(
            name="t2", toolset="s1", schema=_make_schema("t2"), handler=_dummy_handler
        )

        defs = reg.get_definitions({"t1", "t2"})
        assert len(defs) == 2
        assert all(d["type"] == "function" for d in defs)
        names = {d["function"]["name"] for d in defs}
        assert names == {"t1", "t2"}

    def test_skips_unavailable_tools(self):
        reg = ToolRegistry()
        reg.register(
            name="available",
            toolset="s",
            schema=_make_schema("available"),
            handler=_dummy_handler,
            check_fn=lambda: True,
        )
        reg.register(
            name="unavailable",
            toolset="s",
            schema=_make_schema("unavailable"),
            handler=_dummy_handler,
            check_fn=lambda: False,
        )
        defs = reg.get_definitions({"available", "unavailable"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "available"

    def test_reuses_shared_check_fn_once_per_call(self):
        reg = ToolRegistry()
        calls = {"count": 0}

        def shared_check():
            calls["count"] += 1
            return True

        reg.register(
            name="first",
            toolset="shared",
            schema=_make_schema("first"),
            handler=_dummy_handler,
            check_fn=shared_check,
        )
        reg.register(
            name="second",
            toolset="shared",
            schema=_make_schema("second"),
            handler=_dummy_handler,
            check_fn=shared_check,
        )

        defs = reg.get_definitions({"first", "second"})
        assert len(defs) == 2
        assert calls["count"] == 1


class TestUnknownToolDispatch:
    def test_returns_error_json(self):
        reg = ToolRegistry()
        result = json.loads(reg.dispatch("nonexistent", {}))
        assert "error" in result
        assert "Unknown tool" in result["error"]


class TestToolsetAvailability:
    def test_no_check_fn_is_available(self):
        reg = ToolRegistry()
        reg.register(
            name="t", toolset="free", schema=_make_schema(), handler=_dummy_handler
        )
        assert reg.is_toolset_available("free") is True

    def test_check_fn_controls_availability(self):
        reg = ToolRegistry()
        reg.register(
            name="t",
            toolset="locked",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: False,
        )
        assert reg.is_toolset_available("locked") is False

    def test_check_toolset_requirements(self):
        reg = ToolRegistry()
        reg.register(
            name="a",
            toolset="ok",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: True,
        )
        reg.register(
            name="b",
            toolset="nope",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: False,
        )

        reqs = reg.check_toolset_requirements()
        assert reqs["ok"] is True
        assert reqs["nope"] is False

    def test_get_all_tool_names(self):
        reg = ToolRegistry()
        reg.register(
            name="z_tool", toolset="s", schema=_make_schema(), handler=_dummy_handler
        )
        reg.register(
            name="a_tool", toolset="s", schema=_make_schema(), handler=_dummy_handler
        )
        assert reg.get_all_tool_names() == ["a_tool", "z_tool"]

    def test_get_registered_toolset_names(self):
        reg = ToolRegistry()
        reg.register(
            name="first", toolset="zeta", schema=_make_schema(), handler=_dummy_handler
        )
        reg.register(
            name="second", toolset="alpha", schema=_make_schema(), handler=_dummy_handler
        )
        reg.register(
            name="third", toolset="alpha", schema=_make_schema(), handler=_dummy_handler
        )
        assert reg.get_registered_toolset_names() == ["alpha", "zeta"]

    def test_get_tool_names_for_toolset(self):
        reg = ToolRegistry()
        reg.register(
            name="z_tool", toolset="grouped", schema=_make_schema(), handler=_dummy_handler
        )
        reg.register(
            name="a_tool", toolset="grouped", schema=_make_schema(), handler=_dummy_handler
        )
        reg.register(
            name="other_tool", toolset="other", schema=_make_schema(), handler=_dummy_handler
        )
        assert reg.get_tool_names_for_toolset("grouped") == ["a_tool", "z_tool"]

    def test_handler_exception_returns_error(self):
        reg = ToolRegistry()

        def bad_handler(args, **kw):
            raise RuntimeError("boom")

        reg.register(
            name="bad", toolset="s", schema=_make_schema(), handler=bad_handler
        )
        result = json.loads(reg.dispatch("bad", {}))
        assert "error" in result
        assert "RuntimeError" in result["error"]


class TestCheckFnExceptionHandling:
    """Verify that a raising check_fn is caught rather than crashing."""

    def test_is_toolset_available_catches_exception(self):
        reg = ToolRegistry()
        reg.register(
            name="t",
            toolset="broken",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: 1 / 0,  # ZeroDivisionError
        )
        # Should return False, not raise
        assert reg.is_toolset_available("broken") is False

    def test_check_toolset_requirements_survives_raising_check(self):
        reg = ToolRegistry()
        reg.register(
            name="a",
            toolset="good",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: True,
        )
        reg.register(
            name="b",
            toolset="bad",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: (_ for _ in ()).throw(ImportError("no module")),
        )

        reqs = reg.check_toolset_requirements()
        assert reqs["good"] is True
        assert reqs["bad"] is False

    def test_get_definitions_skips_raising_check(self):
        reg = ToolRegistry()
        reg.register(
            name="ok_tool",
            toolset="s",
            schema=_make_schema("ok_tool"),
            handler=_dummy_handler,
            check_fn=lambda: True,
        )
        reg.register(
            name="bad_tool",
            toolset="s2",
            schema=_make_schema("bad_tool"),
            handler=_dummy_handler,
            check_fn=lambda: (_ for _ in ()).throw(OSError("network down")),
        )
        defs = reg.get_definitions({"ok_tool", "bad_tool"})
        assert len(defs) == 1
        assert defs[0]["function"]["name"] == "ok_tool"

    def test_check_tool_availability_survives_raising_check(self):
        reg = ToolRegistry()
        reg.register(
            name="a",
            toolset="works",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: True,
        )
        reg.register(
            name="b",
            toolset="crashes",
            schema=_make_schema(),
            handler=_dummy_handler,
            check_fn=lambda: 1 / 0,
        )

        available, unavailable = reg.check_tool_availability()
        assert "works" in available
        assert any(u["name"] == "crashes" for u in unavailable)


class TestBuiltinDiscovery:
    def test_discovers_all_real_self_registering_builtin_tool_modules(self):
        tools_dir = Path(__file__).resolve().parents[2] / "tools"
        expected = [
            f"tools.{path.stem}"
            for path in sorted(tools_dir.glob("*.py"))
            if path.name not in {"__init__.py", "registry.py", "mcp_tool.py"}
            and _module_registers_tools(path)
        ]

        with patch("tools.registry.importlib.import_module"):
            imported = discover_builtin_tools(tools_dir)

        assert imported == expected

    def test_imports_only_self_registering_modules(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "__init__.py").write_text("", encoding="utf-8")
        (tools_dir / "registry.py").write_text("", encoding="utf-8")
        (tools_dir / "alpha.py").write_text(
            "from tools.registry import registry\nregistry.register(name='alpha', toolset='x', schema={}, handler=lambda *_a, **_k: '{}')\n",
            encoding="utf-8",
        )
        (tools_dir / "beta.py").write_text("VALUE = 1\n", encoding="utf-8")

        with patch("tools.registry.importlib.import_module") as mock_import:
            imported = discover_builtin_tools(tools_dir)

        assert imported == ["tools.alpha"]
        mock_import.assert_called_once_with("tools.alpha")

    def test_skips_mcp_tool_even_if_it_registers(self, tmp_path):
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "__init__.py").write_text("", encoding="utf-8")
        (tools_dir / "mcp_tool.py").write_text(
            "from tools.registry import registry\nregistry.register(name='mcp_alpha', toolset='mcp-test', schema={}, handler=lambda *_a, **_k: '{}')\n",
            encoding="utf-8",
        )
        (tools_dir / "alpha.py").write_text(
            "from tools.registry import registry\nregistry.register(name='alpha', toolset='x', schema={}, handler=lambda *_a, **_k: '{}')\n",
            encoding="utf-8",
        )

        with patch("tools.registry.importlib.import_module") as mock_import:
            imported = discover_builtin_tools(tools_dir)

        assert imported == ["tools.alpha"]
        mock_import.assert_called_once_with("tools.alpha")


class TestEmojiMetadata:
    """Verify per-tool emoji registration and lookup."""

    def test_emoji_stored_on_entry(self):
        reg = ToolRegistry()
        reg.register(
            name="t", toolset="s", schema=_make_schema(),
            handler=_dummy_handler, emoji="🔥",
        )
        assert reg._tools["t"].emoji == "🔥"

    def test_get_emoji_returns_registered(self):
        reg = ToolRegistry()
        reg.register(
            name="t", toolset="s", schema=_make_schema(),
            handler=_dummy_handler, emoji="🎯",
        )
        assert reg.get_emoji("t") == "🎯"

    def test_get_emoji_returns_default_when_unset(self):
        reg = ToolRegistry()
        reg.register(
            name="t", toolset="s", schema=_make_schema(),
            handler=_dummy_handler,
        )
        assert reg.get_emoji("t") == "⚡"
        assert reg.get_emoji("t", default="🔧") == "🔧"

    def test_get_emoji_returns_default_for_unknown_tool(self):
        reg = ToolRegistry()
        assert reg.get_emoji("nonexistent") == "⚡"
        assert reg.get_emoji("nonexistent", default="❓") == "❓"

    def test_emoji_empty_string_treated_as_unset(self):
        reg = ToolRegistry()
        reg.register(
            name="t", toolset="s", schema=_make_schema(),
            handler=_dummy_handler, emoji="",
        )
        assert reg.get_emoji("t") == "⚡"


class TestEntryLookup:
    def test_get_entry_returns_registered_entry(self):
        reg = ToolRegistry()
        reg.register(
            name="alpha", toolset="core", schema=_make_schema("alpha"), handler=_dummy_handler
        )
        entry = reg.get_entry("alpha")
        assert entry is not None
        assert entry.name == "alpha"
        assert entry.toolset == "core"

    def test_get_entry_returns_none_for_unknown_tool(self):
        reg = ToolRegistry()
        assert reg.get_entry("missing") is None


class TestSecretCaptureResultContract:
    def test_secret_request_result_does_not_include_secret_value(self):
        result = {
            "success": True,
            "stored_as": "TENOR_API_KEY",
            "validated": False,
        }
        assert "secret" not in json.dumps(result).lower()


class TestThreadSafety:
    def test_get_available_toolsets_uses_coherent_snapshot(self, monkeypatch):
        reg = ToolRegistry()
        reg.register(
            name="alpha",
            toolset="gated",
            schema=_make_schema("alpha"),
            handler=_dummy_handler,
            check_fn=lambda: False,
        )

        entries, toolset_checks = reg._snapshot_state()

        def snapshot_then_mutate():
            reg.deregister("alpha")
            return entries, toolset_checks

        monkeypatch.setattr(reg, "_snapshot_state", snapshot_then_mutate)

        toolsets = reg.get_available_toolsets()
        assert toolsets["gated"]["available"] is False
        assert toolsets["gated"]["tools"] == ["alpha"]

    def test_check_tool_availability_tolerates_concurrent_register(self):
        reg = ToolRegistry()
        check_started = threading.Event()
        writer_done = threading.Event()
        errors = []
        result_holder = {}
        writer_completed_during_check = {}

        def blocking_check():
            check_started.set()
            writer_completed_during_check["value"] = writer_done.wait(timeout=1)
            return True

        reg.register(
            name="alpha",
            toolset="gated",
            schema=_make_schema("alpha"),
            handler=_dummy_handler,
            check_fn=blocking_check,
        )
        reg.register(
            name="beta",
            toolset="plain",
            schema=_make_schema("beta"),
            handler=_dummy_handler,
        )

        def reader():
            try:
                result_holder["value"] = reg.check_tool_availability()
            except Exception as exc:  # pragma: no cover - exercised on failure only
                errors.append(exc)

        def writer():
            assert check_started.wait(timeout=1)
            reg.register(
                name="gamma",
                toolset="new",
                schema=_make_schema("gamma"),
                handler=_dummy_handler,
            )
            writer_done.set()

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        reader_thread.start()
        writer_thread.start()
        reader_thread.join(timeout=2)
        writer_thread.join(timeout=2)

        assert not reader_thread.is_alive()
        assert not writer_thread.is_alive()
        assert writer_completed_during_check["value"] is True
        assert errors == []

        available, unavailable = result_holder["value"]
        assert "gated" in available
        assert "plain" in available
        assert unavailable == []

    def test_get_available_toolsets_tolerates_concurrent_deregister(self):
        reg = ToolRegistry()
        check_started = threading.Event()
        writer_done = threading.Event()
        errors = []
        result_holder = {}
        writer_completed_during_check = {}

        def blocking_check():
            check_started.set()
            writer_completed_during_check["value"] = writer_done.wait(timeout=1)
            return True

        reg.register(
            name="alpha",
            toolset="gated",
            schema=_make_schema("alpha"),
            handler=_dummy_handler,
            check_fn=blocking_check,
        )
        reg.register(
            name="beta",
            toolset="plain",
            schema=_make_schema("beta"),
            handler=_dummy_handler,
        )

        def reader():
            try:
                result_holder["value"] = reg.get_available_toolsets()
            except Exception as exc:  # pragma: no cover - exercised on failure only
                errors.append(exc)

        def writer():
            assert check_started.wait(timeout=1)
            reg.deregister("beta")
            writer_done.set()

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        reader_thread.start()
        writer_thread.start()
        reader_thread.join(timeout=2)
        writer_thread.join(timeout=2)

        assert not reader_thread.is_alive()
        assert not writer_thread.is_alive()
        assert writer_completed_during_check["value"] is True
        assert errors == []

        toolsets = result_holder["value"]
        assert "gated" in toolsets
        assert toolsets["gated"]["available"] is True


class TestPluginToolsetAutoResolution:
    """P3-0 回归：插件 toolset 经钩子自动点亮，不依赖静态 TOOLSETS 字典。

    锁定不变量：任意 brick 的 ``register_tools`` 只要 ``registry.register(
    toolset=<brick>)``，前端 toolset 即自动可见——无需在 ``toolsets.py`` 的
    静态 ``TOOLSETS`` 里手加条目。这正是删除 ``toolsets.py`` 中冗余静态
    ``cadir`` 条目（ed617cfa45 绕 bug 补丁）安全性的命门。
    """

    def test_plugin_toolset_resolves_without_static_entry(self):
        from tools.registry import registry
        from toolsets import TOOLSETS, get_toolset_names, resolve_toolset

        # 合成 brick 名：刻意不在静态 TOOLSETS 里，模拟「新装上的 GitHub brick」
        ts = "p3_plugin_toolset_regression"
        names = [f"{ts}_compile", f"{ts}_build", f"{ts}_verify", f"{ts}_export"]
        assert ts not in TOOLSETS, "合成 toolset 不应出现在静态 TOOLSETS（测试自污染）"

        for n in names:
            registry.register(
                name=n, toolset=ts, schema=_make_schema(n), handler=_dummy_handler
            )
        try:
            # 经插件钩子解析（非静态字典）→ 证明单一真相源是 tools.registry
            assert set(resolve_toolset(ts)) == set(names)
            assert ts in get_toolset_names()
        finally:
            for n in names:
                registry.deregister(n)

    def test_static_and_plugin_toolset_merge_is_idempotent(self):
        from tools.registry import registry
        from toolsets import get_toolset

        ts = "p3_plugin_toolset_regression"
        names = [f"{ts}_compile", f"{ts}_build"]
        for n in names:
            registry.register(
                name=n, toolset=ts, schema=_make_schema(n), handler=_dummy_handler
            )
        try:
            resolved = get_toolset(ts)
            assert resolved is not None
            assert set(resolved["tools"]) == set(names)
            # 删静态项后走插件钩子；这与静态条目并存时不重复、不遗漏
            assert resolved["includes"] == []
        finally:
            for n in names:
                registry.deregister(n)


class TestPluginToolsetBrickDescription:
    """P3-4 验收项 #27：插件 toolset 的 description 须读 brick 元数据，
    消除 P3-0 删静态 TOOLSETS 后的「Plugin toolset: {name}」占位退化。"""

    def test_plugin_toolset_uses_brick_description(self):
        from tools.registry import registry
        from toolsets import TOOLSETS, get_toolset
        from vermes_cli.capabilities.registry import BrickEntry, get_brick_registry

        ts = "p3_brick_desc_synth"
        names = [f"{ts}_build", f"{ts}_compile"]
        assert ts not in TOOLSETS, "合成 toolset 不应出现在静态 TOOLSETS（测试自污染）"
        for n in names:
            registry.register(
                name=n, toolset=ts, schema=_make_schema(n), handler=_dummy_handler
            )

        # brick 元数据描述（module.yaml description 的真相源，类比 cadir 中文文案）
        fake_brick = BrickEntry(
            id=f"module:{ts}",
            type="module",
            name=ts,
            description="P3 合成 brick 中文描述（cad.ir.v1 类比，验证占位消除）",
        )

        class _FakeBrickRegistry:
            def discover(self, refresh=False):
                return [fake_brick]

        try:
            # patch 后 get_toolset 走插件回退分支，应读 brick 描述而非占位
            with patch(
                "vermes_cli.capabilities.registry.get_brick_registry",
                return_value=_FakeBrickRegistry(),
            ):
                info = get_toolset(ts)
            assert info is not None
            assert info["description"] == fake_brick.description
            assert not info["description"].startswith("Plugin toolset:")
            assert set(info["tools"]) == set(names)
        finally:
            for n in names:
                registry.deregister(n)
