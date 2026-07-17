"""Tests for harness.recoverable (B1 — recoverable tool-failure feedback)."""

from __future__ import annotations

import json

import pytest

from harness.recoverable import RecoverableFeedback, classify_failure, recoverable_tool


# ── classify_failure vocabulary ────────────────────────────────────────────
def test_classify_missing_dependency():
    etype, _ = classify_failure(ModuleNotFoundError("no mod"))
    assert etype == "missing_dependency"


def test_classify_network_error():
    etype, _ = classify_failure(ConnectionError("connect refused"))
    assert etype == "network_error"


def test_classify_invalid_input():
    etype, _ = classify_failure(ValueError("bad arg"))
    assert etype == "invalid_input"


def test_classify_missing_key():
    etype, _ = classify_failure(KeyError("field"))
    assert etype == "missing_key"


def test_classify_permission_denied():
    etype, _ = classify_failure(PermissionError("nope"))
    assert etype == "permission_denied"


def test_classify_unexpected():
    etype, _ = classify_failure(RuntimeError("boom"))
    assert etype == "runtime_error"


# ── decorator: sync, returns dict ──────────────────────────────────────────
def test_sync_tool_failure_returns_dict():
    @recoverable_tool(tool_name="demo", missing_hint="do X first")
    def tool():
        raise FileNotFoundError("config.yaml not found")

    out = tool()
    assert isinstance(out, dict)
    assert out["ok"] is False
    assert out["tool"] == "demo"
    assert out["error_type"] == "missing_file"
    assert "do X first" in out["suggested_next"]
    assert out["detail"]  # traceback captured


def test_sync_tool_success_passes_through():
    @recoverable_tool(tool_name="demo")
    def tool():
        return {"ok": True, "value": 42}

    assert tool() == {"ok": True, "value": 42}


def test_sync_tool_returns_json_string():
    @recoverable_tool(tool_name="demo", returns="json")
    def tool():
        raise ValueError("bad")

    out = tool()
    assert isinstance(out, str)
    payload = json.loads(out)
    assert payload["ok"] is False
    assert payload["error_type"] == "invalid_input"


# ── decorator: async, returns dict + json ──────────────────────────────────
@pytest.mark.asyncio
async def test_async_tool_failure_returns_dict():
    @recoverable_tool(tool_name="demo_async")
    async def tool():
        raise ConnectionError("connect refused")

    out = await tool()
    assert out["error_type"] == "network_error"
    assert out["tool"] == "demo_async"


@pytest.mark.asyncio
async def test_async_tool_failure_returns_json():
    @recoverable_tool(tool_name="demo_async", returns="json")
    async def tool():
        raise KeyError("missing")

    out = await tool()
    payload = json.loads(out)
    assert payload["error_type"] == "missing_key"


@pytest.mark.asyncio
async def test_async_tool_success_passes_through():
    @recoverable_tool(tool_name="demo_async")
    async def tool():
        return {"ok": True}

    assert await tool() == {"ok": True}


# ── preserves signature / name for callers + test patching ────────────────
def test_decorator_preserves_wrapped_name():
    @recoverable_tool(tool_name="demo")
    def my_tool(x):
        return x

    assert my_tool.__name__ == "my_tool"
    assert my_tool(7) == 7


def test_feedback_to_json_roundtrip():
    fb = RecoverableFeedback(tool="t", error_type="network_error", what_failed="x")
    payload = json.loads(fb.to_json())
    assert payload["tool"] == "t"
    assert payload["ok"] is False
