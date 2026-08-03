"""Phase 0 hot reload tests.

Tests the module hot-reload infrastructure:
1. _module_tool_names recording during register_modules
2. reload_module_tools: deregister old → load new → register new
3. is_module_hot_path / extract_module_name path helpers
4. classify_component_swap three-class classifier
5. rollback triggers symmetric reload
"""
import sys
import os
import importlib
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest

# Ensure vermes-electron is on sys.path
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestPathHelpers:
    def test_is_module_hot_path_true(self):
        from agent.module_loader import is_module_hot_path, get_modules_dir
        mod_dir = get_modules_dir()
        assert is_module_hot_path(str(mod_dir / "test_mod" / "backend" / "tools.py"))

    def test_is_module_hot_path_false_for_frozen(self):
        from agent.module_loader import is_module_hot_path
        assert not is_module_hot_path("/Applications/Vermes.app/Contents/Resources/app/vermes_cli/something.py")

    def test_is_module_hot_path_false_for_empty(self):
        from agent.module_loader import is_module_hot_path
        assert not is_module_hot_path("")

    def test_extract_module_name(self):
        from agent.module_loader import extract_module_name, get_modules_dir
        mod_dir = get_modules_dir()
        path = str(mod_dir / "scholarforge" / "backend" / "tools.py")
        assert extract_module_name(path) == "scholarforge"

    def test_extract_module_name_empty(self):
        from agent.module_loader import extract_module_name
        assert extract_module_name("/tmp/random.py") == ""


# ---------------------------------------------------------------------------
# classify_component_swap
# ---------------------------------------------------------------------------

class TestClassifyComponentSwap:
    def test_config_level(self):
        from tools.approval import classify_component_swap
        assert classify_component_swap("/path/to/config.yaml") == "config_level"

    def test_module_hot_path(self):
        from tools.approval import classify_component_swap
        from agent.module_loader import get_modules_dir
        mod_dir = get_modules_dir()
        result = classify_component_swap(str(mod_dir / "test_mod" / "backend" / "tools.py"))
        assert result == "module_hot_path"

    def test_source_level_for_frozen_py(self):
        from tools.approval import classify_component_swap
        assert classify_component_swap("/Applications/Vermes.app/Contents/Resources/app/vermes_cli/foo.py") == "source_level"

    def test_source_level_for_empty(self):
        from tools.approval import classify_component_swap
        assert classify_component_swap("") == "source_level"

    def test_module_yaml_is_module_hot_path_not_config(self):
        """A module.yaml inside ~/.vermes/modules/ should be module_hot_path, not config_level."""
        from tools.approval import classify_component_swap
        from agent.module_loader import get_modules_dir
        mod_dir = get_modules_dir()
        result = classify_component_swap(str(mod_dir / "test_mod" / "module.yaml"))
        assert result == "module_hot_path"


# ---------------------------------------------------------------------------
# _module_tool_names recording
# ---------------------------------------------------------------------------

class TestModuleToolNamesRecording:
    def test_register_modules_records_tool_names(self):
        """register_modules should populate _module_tool_names with the diff of tool names."""
        import tempfile
        from agent import module_loader as ml
        from tools.registry import registry

        # Save original state
        original_names = dict(ml._module_tool_names)
        original_tools = dict(registry._tools)
        original_mod_dir = ml._MODULES_DIR_CACHE

        tmpdir = Path(tempfile.mkdtemp())
        mod_dir = tmpdir / "test_record"
        (mod_dir / "backend").mkdir(parents=True)
        (mod_dir / "module.yaml").write_text("""
name: test_record
display_name: Test Record
version: 1.0.0
backend:
  entry: backend/blueprint.py
  tools_entry: backend/tools.py
""")
        (mod_dir / "backend" / "blueprint.py").write_text("""
def register_to(app, host_api=None):
    pass
""")
        (mod_dir / "backend" / "tools.py").write_text("""
from tools.registry import registry
def register_tools(host_api):
    registry.register(
        name="test_record_echo",
        toolset="test_record",
        schema={"type": "object", "properties": {}},
        handler=lambda args: {"ok": True},
        emoji="🧪",
    )
""")

        # Patch the cached modules dir
        ml._MODULES_DIR_CACHE = tmpdir
        try:
            from agent.module_loader import register_modules, HostAPI
            host_api = HostAPI()
            app = MagicMock()
            register_modules(app, host_api)

            assert "test_record" in ml._module_tool_names
            assert "test_record_echo" in ml._module_tool_names["test_record"]

        finally:
            ml._module_tool_names.clear()
            ml._module_tool_names.update(original_names)
            ml._MODULES_DIR_CACHE = original_mod_dir
            registry.deregister("test_record_echo")
            registry._tools.clear()
            registry._tools.update(original_tools)


# ---------------------------------------------------------------------------
# reload_module_tools
# ---------------------------------------------------------------------------

class TestReloadModuleTools:
    def setup_test_module(self, tmpdir, tool_output="v1"):
        """Create a test module with a tool that returns tool_output."""
        mod_dir = tmpdir / "test_reload"
        (mod_dir / "backend").mkdir(parents=True)
        (mod_dir / "module.yaml").write_text(f"""
name: test_reload
display_name: Test Reload
version: 1.0.0
backend:
  entry: backend/blueprint.py
  tools_entry: backend/tools.py
""")
        # blueprint.py with a no-op register_to (load_module_pyd requires backend_entry)
        (mod_dir / "backend" / "blueprint.py").write_text("""
def register_to(app, host_api=None):
    pass
""")
        (mod_dir / "backend" / "tools.py").write_text(f"""
from tools.registry import registry
def register_tools(host_api):
    def handler(args):
        return {{"ok": True, "version": "{tool_output}"}}
    registry.register(
        name="test_reload_echo",
        toolset="test_reload",
        schema={{"type": "object", "properties": {{}}}},
        handler=handler,
        emoji="🧪",
    )
""")
        return mod_dir

    def test_reload_picks_up_new_content(self, tmp_path):
        """After reload, the tool should return the new version."""
        from agent import module_loader as ml
        from tools.registry import registry

        original_names = dict(ml._module_tool_names)
        original_tools = dict(registry._tools)
        original_mod_dir = ml._MODULES_DIR_CACHE

        try:
            mod_dir = self.setup_test_module(tmp_path, "v1")
            ml._MODULES_DIR_CACHE = tmp_path

            from agent.module_loader import register_modules, HostAPI, reload_module_tools
            host_api = HostAPI()
            app = MagicMock()
            register_modules(app, host_api)

            # Verify v1
            entry = registry.get_entry("test_reload_echo")
            assert entry is not None
            result = entry.handler({})
            assert result["version"] == "v1"

            # Rewrite tools.py to v2
            (mod_dir / "backend" / "tools.py").write_text("""
from tools.registry import registry
def register_tools(host_api):
    def handler(args):
        return {"ok": True, "version": "v2"}
    registry.register(
        name="test_reload_echo",
        toolset="test_reload",
        schema={"type": "object", "properties": {}},
        handler=handler,
        emoji="🧪",
    )
""")

            # Reload
            result = reload_module_tools("test_reload")
            assert result["ok"] is True
            assert result["state"] == "reloaded"
            assert result["tools_loaded"] == 1

            # Verify v2
            entry = registry.get_entry("test_reload_echo")
            assert entry is not None
            result = entry.handler({})
            assert result["version"] == "v2"

        finally:
            ml._module_tool_names.clear()
            ml._module_tool_names.update(original_names)
            ml._MODULES_DIR_CACHE = original_mod_dir
            registry.deregister("test_reload_echo")
            registry._tools.clear()
            registry._tools.update(original_tools)

    def test_reload_deregisters_old_tools(self, tmp_path):
        """reload should deregister old tools before registering new ones."""
        from agent import module_loader as ml
        from tools.registry import registry

        original_names = dict(ml._module_tool_names)
        original_tools = dict(registry._tools)
        original_mod_dir = ml._MODULES_DIR_CACHE

        try:
            mod_dir = self.setup_test_module(tmp_path, "v1")
            ml._MODULES_DIR_CACHE = tmp_path

            from agent.module_loader import register_modules, HostAPI, reload_module_tools
            host_api = HostAPI()
            app = MagicMock()
            register_modules(app, host_api)

            assert "test_reload_echo" in ml._module_tool_names.get("test_reload", set())

            # Rewrite to remove the tool entirely
            (mod_dir / "backend" / "tools.py").write_text("""
from tools.registry import registry
def register_tools(host_api):
    pass  # no tools registered
""")

            result = reload_module_tools("test_reload")
            assert result["ok"] is True
            assert result["tools_loaded"] == 0
            assert registry.get_entry("test_reload_echo") is None

        finally:
            ml._module_tool_names.clear()
            ml._module_tool_names.update(original_names)
            ml._MODULES_DIR_CACHE = original_mod_dir
            registry._tools.clear()
            registry._tools.update(original_tools)

    def test_reload_nonexistent_module(self):
        from agent.module_loader import reload_module_tools
        result = reload_module_tools("does_not_exist_xyz")
        assert result["ok"] is False
        assert result["state"] == "not_found"

    def test_reload_generation_bumps(self, tmp_path):
        """registry._generation should increase after reload."""
        from agent import module_loader as ml
        from tools.registry import registry

        original_names = dict(ml._module_tool_names)
        original_tools = dict(registry._tools)
        original_mod_dir = ml._MODULES_DIR_CACHE

        try:
            mod_dir = self.setup_test_module(tmp_path, "v1")
            ml._MODULES_DIR_CACHE = tmp_path

            from agent.module_loader import register_modules, HostAPI, reload_module_tools
            host_api = HostAPI()
            app = MagicMock()
            register_modules(app, host_api)

            gen_before = registry._generation

            # Rewrite with same content (still triggers deregister+register)
            (mod_dir / "backend" / "tools.py").write_text("""
from tools.registry import registry
def register_tools(host_api):
    registry.register(
        name="test_reload_echo",
        toolset="test_reload",
        schema={"type": "object", "properties": {}},
        handler=lambda args: {"ok": True, "version": "v2"},
        emoji="🧪",
    )
""")

            reload_module_tools("test_reload")
            gen_after = registry._generation
            assert gen_after > gen_before

        finally:
            ml._module_tool_names.clear()
            ml._module_tool_names.update(original_names)
            ml._MODULES_DIR_CACHE = original_mod_dir
            registry.deregister("test_reload_echo")
            registry._tools.clear()
            registry._tools.update(original_tools)
