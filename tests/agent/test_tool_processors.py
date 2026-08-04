"""Phase 2 tests for the Tool Processor loader (kind: tool).

All tests exercise the REAL loader + REAL ToolRegistry — no mocking of the
registration path (per repo discipline: the only line that can break is the
real one).  We point the user-processor directory at a tmp dir so YAML files
are genuine on-disk manifests, and we assert behaviour against the singleton
``tools.registry.registry``.
"""

import os
import textwrap
import threading
import time

import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.registry import discover_builtin_tools, registry
from tools.approval import _resolve_processor_tier
import agent.tool_processor_loader as TPL
from agent.tool_processor_loader import (
    register_tool_processors,
    load_tool_processors,
    get_tool_lifecycle_hooks,
    clear_hook_fires,
    _make_inline_handler,
    is_inline_processor_content,
)
from agent.prompt_processor_loader import start_processor_watcher, stop_processor_watcher


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


# ── Phase 2.5: handler.inline declarative execution (no-mock real path) ────
class _InlineEchoHandler(BaseHTTPRequestHandler):
    """Tiny local server so the http-inline test does a REAL round-trip."""
    def do_GET(self):
        body = b"hello-from-inline-server"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


@pytest.fixture
def local_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _InlineEchoHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}/hello"
    server.shutdown()


# ── 8. http inline real round-trip (no mock, real local server) ──────────
def test_inline_http_real_roundtrip(local_http_server, user_dir):
    _write(user_dir / "my_http.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: my_http
        name: my_http
        toolset: test
        schema:
          name: my_http
          description: fetch
          parameters: {type: object, properties: {url: {type: string}}, required: [url]}
        handler:
          inline:
            type: http
            url_arg: url
            timeout: 10
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    e = registry.get_entry("my_http")
    assert e is not None                       # real registration into registry
    result = registry.dispatch("my_http", {"url": local_http_server})
    assert '"body"' in result                  # returns JSON via tool_result
    assert "hello-from-inline-server" in result  # REAL http response, not mocked


# ── 9. shell inline: argv list → no shell injection ───────────────────────
def test_inline_shell_argv_no_injection(user_dir, monkeypatch):
    monkeypatch.setattr(TPL, "_allow_inline_shell", lambda: True)
    _write(user_dir / "my_shell.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: my_shell
        name: my_shell
        toolset: test
        schema:
          name: my_shell
          description: echo
          parameters: {type: object, properties: {msg: {type: string}}, required: [msg]}
        handler:
          inline:
            type: shell
            command: ["printf", "%s", "{msg}"]
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    e = registry.get_entry("my_shell")
    assert e is not None
    pwn = "a; rm -rf /; touch /tmp/vermes_inline_pwned"
    result = registry.dispatch("my_shell", {"msg": pwn})
    assert '"output"' in result
    # The injection payload is echoed back LITERALLY — it was an argv element,
    # never interpreted by a shell. The file it tried to create does NOT exist.
    assert pwn in result
    assert not os.path.exists("/tmp/vermes_inline_pwned")


# ── 10. shell inline blocked when approvals.allow_inline_shell is False ────
def test_inline_shell_blocked_when_disabled(user_dir, monkeypatch):
    monkeypatch.setattr(TPL, "_allow_inline_shell", lambda: False)
    _write(user_dir / "my_shell2.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: my_shell2
        name: my_shell2
        toolset: test
        schema:
          name: my_shell2
          description: echo
          parameters: {type: object, properties: {msg: {type: string}}, required: [msg]}
        handler:
          inline:
            type: shell
            command: ["echo", "{msg}"]
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    # shell inline must NOT be registered without explicit opt-in
    assert registry.get_entry("my_shell2") is None


# ── 11. inline risk_tier always clamped to L2 (not downgradable) ──────────
def test_inline_risk_tier_clamped_to_L2(user_dir):
    _write(user_dir / "inline_l1.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: inline_l1
        name: inline_l1
        toolset: test
        schema:
          name: inline_l1
          description: x
          parameters: {type: object, properties: {}, required: []}
        handler:
          inline:
            type: http
            url_arg: url
        governance:
          risk_tier: L1
    """)
    TPL._tool_processors_cache = None
    procs = load_tool_processors()
    p = next(p for p in procs if p.effective_id == "inline_l1")
    assert p.inline_spec is not None
    assert p.risk_tier == "L2"        # declared L1 → force-clamped to L2


# ── 12. malformed inline spec → error-skip (never silent) ─────────────────
def test_inline_invalid_spec_skipped(user_dir):
    _write(user_dir / "badinline.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: badinline
        name: badinline
        toolset: test
        schema:
          name: badinline
          description: x
          parameters: {type: object, properties: {}, required: []}
        handler:
          inline:
            type: bogus
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    assert registry.get_entry("badinline") is None


# ── 13. http inline scheme whitelist (no file:// / gopher:// etc.) ────────
def test_inline_http_scheme_whitelist(user_dir):
    _write(user_dir / "fsget.yaml", """
        api: vermes.processor/v1
        kind: tool
        id: fsget
        name: fsget
        toolset: test
        schema:
          name: fsget
          description: x
          parameters: {type: object, properties: {url: {type: string}}, required: [url]}
        handler:
          inline:
            type: http
            url_arg: url
    """)
    TPL._tool_processors_cache = None
    register_tool_processors()
    e = registry.get_entry("fsget")
    assert e is not None
    result = registry.dispatch("fsget", {"url": "file:///etc/passwd"})
    assert '"error"' in result         # rejected by scheme whitelist


# ── Phase 2.5 audit corrections (P1 approval gate, P2 truncation) ────────────

def test_is_inline_processor_content():
    inline = textwrap.dedent("""
        api: vermes.processor/v1
        kind: tool
        handler:
          inline: {type: http, url_arg: url}
    """)
    plain = textwrap.dedent("""
        api: vermes.processor/v1
        kind: tool
        handler: {ref: os.getcwd}
    """)
    assert is_inline_processor_content(inline) is True
    assert is_inline_processor_content(plain) is False
    # Malformed/unreadable content → fail-closed (force L2 review, never slip through).
    assert is_inline_processor_content("not: yaml: [broken") is True
    assert is_inline_processor_content("") is False
    assert is_inline_processor_content(None) is False


def test_approval_gate_forces_L2_for_inline(tmp_path):
    # A tool processor declaring handler.inline but risk_tier L1 must be forced
    # to L2 by the approval gate — otherwise the manifest self-attests its way
    # past human confirmation (the loophole the audit caught).
    inline_l1 = textwrap.dedent("""
        api: vermes.processor/v1
        kind: tool
        id: inline1
        name: inline1
        schema: {name: inline1, description: x, parameters: {type: object, properties: {}}}
        handler: {inline: {type: http, url_arg: url}}
        governance: {risk_tier: L1}
    """)
    plain_l1 = textwrap.dedent("""
        api: vermes.processor/v1
        kind: tool
        id: plain1
        name: plain1
        schema: {name: plain1, description: x, parameters: {type: object, properties: {}}}
        handler: {ref: tools.file_tools._handle_read_file}
        governance: {risk_tier: L1}
    """)
    p_inline = tmp_path / "inline.yaml"
    p_plain = tmp_path / "plain.yaml"
    p_inline.write_text(inline_l1)
    p_plain.write_text(plain_l1)

    # Non-inline (plain) processor, incoming L1 → L1 (self-attest allowed; no
    # regression).  This also proves the inline-detection import succeeded —
    # if it had failed, the fail-closed except would return L2 here.
    assert _resolve_processor_tier(str(p_plain), {"new_content": plain_l1}) == "L1"
    # Inline processor, incoming L1 → L2 (forced; cannot self-attest to L1).
    assert _resolve_processor_tier(str(p_inline), {"new_content": inline_l1}) == "L2"
    # Plain on disk + inline incoming → L2 (incoming inline forces it).
    assert _resolve_processor_tier(str(p_plain), {"new_content": inline_l1}) == "L2"
    # Inline on disk + plain incoming → L2 (on-disk inline forces it).
    assert _resolve_processor_tier(str(p_inline), {"new_content": plain_l1}) == "L2"


def test_inline_closure_truncates_to_max_result_size():
    # Real subprocess (no mock). Payload is 500 chars; capped at 20 by
    # max_result_size_chars at the closure level.
    spec = {"type": "shell", "command": ["printf", "%s", "A" * 500]}
    handler = _make_inline_handler(spec, 20)
    result = handler({}, **{})
    # Result is a JSON envelope; the closure guarantees the whole string is
    # bounded by max_result_size_chars (+ small envelope slack).
    assert len(result) <= 20 + 64
    assert "AAAA" in result  # content actually flowed through the executor


def test_watcher_invalidates_tool_cache(tmp_path, monkeypatch):
    # Phase 2 audit P1: editing a tool processor YAML must hot-reload the tool
    # loader cache AND re-populate ToolRegistry (no restart needed).
    import tools.file_tools as FT

    monkeypatch.setattr(
        "agent.prompt_processor_loader._get_user_dir", lambda: tmp_path
    )
    monkeypatch.setattr(TPL, "_get_user_dir", lambda: tmp_path)
    stop_processor_watcher()  # ensure a clean singleton
    g0 = TPL.get_generation()

    # Seed a user tool processor BEFORE starting the watcher, so the registry
    # has a known entry we can later mutate via the watcher.
    proc = tmp_path / "myshift" / "processor.yaml"
    proc.parent.mkdir(exist_ok=True)
    proc.write_text(textwrap.dedent("""
        api: vermes.processor/v1
        kind: tool
        id: myshift
        name: myshift
        schema: {name: myshift, description: x, parameters: {type: object, properties: {}}}
        handler: {ref: tools.file_tools._handle_read_file}
        governance: {risk_tier: L2}
    """))
    TPL._tool_processors_cache = None
    register_tool_processors()
    h0 = registry.get_entry("myshift").handler
    assert h0 is FT._handle_read_file

    start_processor_watcher(poll_interval=0.2)
    try:
        time.sleep(0.35)  # let the first scan baseline the directory
        # Swap the handler on disk → watcher must re-register it into
        # ToolRegistry WITHOUT a restart, picking up the new handler.
        proc.write_text(textwrap.dedent("""
            api: vermes.processor/v1
            kind: tool
            id: myshift
            name: myshift
            schema: {name: myshift, description: x, parameters: {type: object, properties: {}}}
            handler: {ref: tools.file_tools._handle_write_file}
            governance: {risk_tier: L2}
        """))
        for _ in range(50):
            e = registry.get_entry("myshift")
            if TPL.get_generation() > g0 and e is not None and e.handler is FT._handle_write_file:
                break
            time.sleep(0.1)
        assert TPL.get_generation() > g0
        # Hot reload actually swapped the handler — not just a cache bump.
        assert registry.get_entry("myshift").handler is FT._handle_write_file
    finally:
        stop_processor_watcher()
