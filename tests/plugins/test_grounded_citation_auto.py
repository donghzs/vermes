"""Tests for ② Grounded Citations 注③ auto-tracing observer hook.

Locks: split_claims heuristic, config gating (default off), and that the
observer routes into ground_claims with extracted claims. The production
hook is fire-and-forget (thread/asyncio.run); we exercise the real dispatch
path (no running loop → asyncio.run) so behavior stays deterministic.

Note: ``plugins/`` is a plugin-discovery dir, not a Python package, so the
module is loaded by file path via importlib.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
from unittest.mock import patch

import pytest

_PLUGIN_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "plugins"
    / "grounded_citation_auto"
    / "__init__.py"
)


def _load_plugin():
    spec = importlib.util.spec_from_file_location("gc_auto_test", _PLUGIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gc = _load_plugin()


# ── split_claims ────────────────────────────────────────────────

def test_split_claims_filters_questions_and_short():
    text = (
        "AlphaFold 在 2020 年 CASP14 中夺冠。这是什么？"
        "Transformer 由 Google 于 2017 年提出。\n"
        "，。？"
    )
    claims = gc.split_claims(text)
    assert "AlphaFold 在 2020 年 CASP14 中夺冠。" in claims
    assert "Transformer 由 Google 于 2017 年提出。" in claims
    assert all("？" not in c for c in claims)  # question dropped
    assert len(claims) == 2


def test_split_claims_empty():
    assert gc.split_claims("") == []
    assert gc.split_claims("   ") == []


def test_split_claims_caps_at_max():
    text = "。".join(
        f"主张 {i} 号内容足够长用于测试切片逻辑。" for i in range(20)
    )
    claims = gc.split_claims(text, max_claims=5)
    assert len(claims) == 5


# ── config gating ───────────────────────────────────────────────

def test_auto_ground_enabled_reads_config(monkeypatch):
    monkeypatch.setattr(
        "vermes_cli.config.load_config",
        lambda: {"grounded_citation": {"auto": True}},
    )
    assert gc._auto_ground_enabled() is True
    monkeypatch.setattr(
        "vermes_cli.config.load_config",
        lambda: {},  # missing section → default False
    )
    assert gc._auto_ground_enabled() is False


def test_hook_noop_when_config_off(monkeypatch):
    monkeypatch.setattr(gc, "_auto_ground_enabled", lambda: False)
    counter = {"n": 0}

    async def fake_ground(*a, **k):
        counter["n"] += 1
        return []

    with patch.object(gc, "_run_auto_ground", fake_ground):
        gc.on_post_llm_call(
            assistant_response="AlphaFold 在 2020 年 CASP14 中夺冠。"
        )
    assert counter["n"] == 0  # config off → never reaches ground_claims


def test_hook_noop_when_empty_response(monkeypatch):
    monkeypatch.setattr(gc, "_auto_ground_enabled", lambda: True)
    counter = {"n": 0}

    async def fake_ground(*a, **k):
        counter["n"] += 1
        return []

    with patch.object(gc, "_run_auto_ground", fake_ground):
        gc.on_post_llm_call(assistant_response="")
    assert counter["n"] == 0


# ── routes into ground_claims with extracted claims ────────────

def test_hook_routes_to_ground_claims_when_enabled(monkeypatch):
    monkeypatch.setattr(gc, "_auto_ground_enabled", lambda: True)
    captured = {}

    async def fake_ground_claims(claims, **kw):
        captured["claims"] = claims
        return [{"verdict": "supported", "claim": c} for c in claims]

    with patch(
        "vermes_cli.scholarforge.grounded_citation.ground_claims",
        fake_ground_claims,
    ):
        gc.on_post_llm_call(
            assistant_response=(
                "AlphaFold 在 2020 年 CASP14 中夺冠。"
                "Transformer 由 Google 于 2017 年提出。"
            )
        )
    assert len(captured["claims"]) == 2
    assert "AlphaFold 在 2020 年 CASP14 中夺冠。" in captured["claims"]


# ── register wires the hook ────────────────────────────────────

def test_register_wires_hook():
    calls = []

    class FakeCtx:
        def register_hook(self, name, cb):
            calls.append((name, cb))

    gc.register(FakeCtx())
    assert ("post_llm_call", gc.on_post_llm_call) in calls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
