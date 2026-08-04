"""Phase 2 tests for the Tool Processor loader (kind: tool).

All tests exercise the REAL loader + REAL ToolRegistry — no mocking of the
registration path (per repo discipline: the only line that can break is the
real one).  We point the user-processor directory at a tmp dir so YAML files
are genuine on-disk manifests, and we assert behaviour against the singleton
``tools.registry.registry``.
"""

import textwrap

import pytest

from tools.registry import discover_builtin_tools, registry
import agent.tool_processor_loader as TPL
from agent.tool_processor_loader import (
    register_tool_processors,
    load_tool_processors,
    get_tool_lifecycle_hooks,
    clear_hook_fires,
)


@pytest.fixture(scope="module", autouse=True)
def _discover_builtins():
    # Register the 80+ self-registering Python tools so the override /
    # keep-builtin-handler paths have something to override.
    discover_builtin_tools()


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    d = tmp_path / "processors"
    d.mkdir()
    monkeypatch.setattr(TPL, "_get_user_dir", lambda: d)
    return d


@pytest.fixture(autouse=True)
def _reset_loader_cache():
    TPL._tool_processors_cache = None
    clear_hook_fires()
    yield
    TPL._tool_processors_cache = None


def _write(path, text):
    path.write_text(textwrap.dedent(text), encoding="utf-8")


# ── 1. built-in tool processors load ────────────────────────────────────
def test_builtin_tool_processors_load():
    procs = load_tool_processors()
    ids = {p.effective_id for p in procs}
    assert "read_file" in ids and "write_file" in ids
    rf = next(p for p in procs if p.effective_id == "read_file")
    assert rf.kind == "tool"
    assert rf.handler_ref == "tools.file_tools._handle_read_file"
    assert rf.risk_tier == "L2"          # tools default L2 (fail-closed)
    assert rf.builtin is True


# ── 2. real registration into the ToolRegistry ──────────────────────────
def test_register_into_registry_real(user_dir):
    n = register_tool_processors()
    assert n >= 2
    e = registry.get_entry("read_file")
    assert e is not None
    import tools.file_tools as ft
    assert e.handler is ft._handle_read_file
    assert e.check_fn is ft._check_file_reqs
    assert e.toolset == "file"
    # real get_definitions path (no mock)
    defs = registry.get_definitions({"read_file", "write_file"})
    names = {d["function"]["name"] for d in defs}
    assert names == {"read_file", "write_file"}


# ── 3. handler.ref signature mismatch → error-skip (audit 补正) ──────────
def test_handler_ref_signature_mismatch_skipped(user_dir):
    _write(user_dir / "badsig.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: badsig
        name: badsig
        toolset: test
        schema:
          name: badsig
          description: x
          parameters: {type: object, properties: {}, required: []}
        handler:
          ref: tools.memory_tool.memory_tool   # lambda-wrapped, sig != (args, **kw)
    """)
    TPL._tool_processors_cache = None
    n = register_tool_processors()
    # badsig must NOT be registered (handler.ref rejected)
    assert registry.get_entry("badsig") is None
    # and registration still succeeded for the valid built-ins
    assert n >= 2


def test_handler_ref_nonexistent_module_skipped(user_dir):
    _write(user_dir / "ghost.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: ghost
        name: ghost
        toolset: test
        schema:
          name: ghost
          description: x
          parameters: {type: object, properties: {}, required: []}
        handler:
          ref: tools.this_module_does_not_exist.nope
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    assert registry.get_entry("ghost") is None


# ── 4. override without handler.ref keeps built-in Python handler ───────
def test_override_without_handler_ref_keeps_builtin(user_dir):
    _write(user_dir / "read_file.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: read_file
        name: read_file
        toolset: file
        schema:
          name: read_file
          description: overridden-by-user
          parameters: {type: object, properties: {path: {type: string}}, required: [path]}
        governance:
          risk_tier: L1
    """)  # no handler.ref → must keep built-in _handle_read_file
    TPL._tool_processors_cache = None
    register_tool_processors()
    e = registry.get_entry("read_file")
    import tools.file_tools as ft
    assert e.handler is ft._handle_read_file           # built-in handler preserved
    assert e.schema.get("description") == "overridden-by-user"  # user schema overlay applied


# ── 5. availability.requires_env missing → excluded from definitions ─────
def test_requires_env_missing_excludes_from_definitions(user_dir):
    _write(user_dir / "envtool.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: envtool
        name: envtool
        toolset: test
        schema:
          name: envtool
          description: needs an env var
          parameters: {type: object, properties: {}, required: []}
        handler:
          ref: tools.file_tools._handle_read_file
        availability:
          requires_env: ["VERMES_PHASE2_TEST_MISSING_ENV_999"]
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    # entry exists but is unavailable (env absent)
    assert registry.get_entry("envtool") is not None
    defs = registry.get_definitions({"envtool"})
    assert defs == []   # filtered out by check_fn predicate


# ── 6. lifecycle hooks validated + actually fire on dispatch ────────────
def test_lifecycle_hooks_validated_and_fire_on_dispatch(user_dir):
    _write(user_dir / "read_file.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: read_file
        name: read_file
        toolset: file
        schema:
          name: read_file
          description: hook demo
          parameters: {type: object, properties: {path: {type: string}}, required: [path]}
        lifecycle:
          hooks: ["post_tool_call", "not_a_real_hook"]
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    # invalid hook dropped, valid retained
    assert get_tool_lifecycle_hooks("read_file") == ["post_tool_call"]
    # dispatch read_file through the REAL model_tools dispatcher (the only
    # path that fires post_tool_call via invoke_hook). A nonexistent path →
    # fast error return, but the lifecycle hook still fires.
    clear_hook_fires()
    from model_tools import handle_function_call
    handle_function_call("read_file", {"path": "/nonexistent_phase2_xyz"})
    fires = [f for f in TPL.get_hook_fires() if f["tool_name"] == "read_file"]
    assert any(f["event"] == "post_tool_call" for f in fires)


# ── 7. risk_tier defaults to L2 when governance absent ───────────────────
def test_risk_tier_defaults_L2(user_dir):
    _write(user_dir / "nogov.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: nogov
        name: nogov
        toolset: test
        schema:
          name: nogov
          description: x
          parameters: {type: object, properties: {}, required: []}
        handler:
          ref: tools.file_tools._handle_read_file
    """)
    TPL._tool_processors_cache = None
    procs = load_tool_processors()
    nogov = next(p for p in procs if p.effective_id == "nogov")
    assert nogov.risk_tier == "L2"
    assert nogov.governance["hash"].startswith("sha256:")   # hash resolved, not "auto"
