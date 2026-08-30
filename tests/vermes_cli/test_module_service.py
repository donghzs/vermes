"""P3-2 module_service.invoke 回归测试（全 hermetic，无网络 / 无真实工具注册）。

锁住：cap → 工具解析（route_toolset + select_tool） → model_capable 单 if
→ tier 单维决策 → 复用 registry.dispatch 单一真相源。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from vermes_cli.adapters import discovery
from vermes_cli.adapters.discovery_registry import CAPABILITY_REGISTRY
from vermes_cli.capabilities import manifest as cap_manifest
from vermes_cli.capabilities import module_service
from vermes_cli.blueprints.invoke import router as invoke_router
from vermes_cli import runtime_provider
from tools.registry import registry as tool_registry


class _ToolSummary:
    def __init__(self, name: str) -> None:
        self.name = name


def _allow_choice(name: str) -> SimpleNamespace:
    return SimpleNamespace(decision="allow_tool", tool=_ToolSummary(name), score=0.9, reason="")


@pytest.fixture
def patched(monkeypatch):
    ref = SimpleNamespace(
        toolset="cadir", domain="3d", operation_mechanism="cli",
        score=0.9, matched_keywords=[],
    )
    monkeypatch.setattr(discovery, "route_toolset", lambda cap: [ref])
    monkeypatch.setattr(
        discovery, "select_tool",
        lambda tools, cap, ctx=None, llm_chooser=None: _allow_choice(cap),
    )
    idx = SimpleNamespace(tools=[_ToolSummary("cadir_build")])
    monkeypatch.setattr(
        module_service, "CAPABILITY_REGISTRY", SimpleNamespace(get=lambda ts: idx)
    )
    monkeypatch.setattr(
        tool_registry, "dispatch",
        lambda name, args, **kw: '{"ok": true, "tool": "%s"}' % name,
    )
    monkeypatch.setattr(
        cap_manifest, "build_provider_capability_index",
        lambda: {"openai": ["tools", "reasoning"], "auto": []},
    )
    monkeypatch.setattr(
        runtime_provider, "resolve_requested_provider", lambda requested=None: "openai"
    )
    yield


def test_invoke_local_dispatches(patched):
    out = module_service.invoke("cadir_build", payload={"args": {"thickness_mm": 10}})
    assert out["cap"] == "cadir_build"
    assert out["tool"] == "cadir_build"
    assert out["result"] == {"ok": True, "tool": "cadir_build"}


def test_invoke_no_tool_for_cap(patched, monkeypatch):
    monkeypatch.setattr(discovery, "route_toolset", lambda cap: [])
    out = module_service.invoke("nonexistent_cap")
    assert out["error"] == "no_tool_for_cap"


def test_model_capable_satisfied(patched):
    chk = module_service.model_capable("cadir_build", "openai")
    assert chk["ok"] is True
    assert chk["missing"] == []


def test_model_capable_not_satisfied(patched, monkeypatch):
    monkeypatch.setattr(
        cap_manifest, "build_provider_capability_index", lambda: {"anthropic": ["vision"]}
    )
    monkeypatch.setattr(
        runtime_provider, "resolve_requested_provider", lambda requested=None: "anthropic"
    )
    chk = module_service.model_capable("cadir_build")
    assert chk["ok"] is False
    assert "tools" in chk["missing"]


def test_invoke_capability_not_satisfied_blocks_execution(patched, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        cap_manifest, "build_provider_capability_index", lambda: {"anthropic": ["vision"]}
    )
    monkeypatch.setattr(
        runtime_provider, "resolve_requested_provider", lambda requested=None: "anthropic"
    )
    monkeypatch.setattr(
        tool_registry, "dispatch", lambda name, args, **kw: dispatched.append(name)
    )
    out = module_service.invoke("cadir_build", payload={"args": {}})
    assert out["capability_check"] == "not_satisfied"
    assert "tools" in out["missing"]
    assert dispatched == []  # 不满足则不执行


def test_invoke_remote_tier_degrade_seam(patched):
    out = module_service.invoke("cadir_build", payload={"args": {}, "tier": "remote"})
    assert out["tier"] == "remote"
    assert out["degraded"] is True


def test_unknown_provider_fail_open(patched, monkeypatch):
    monkeypatch.setattr(cap_manifest, "build_provider_capability_index", lambda: {})
    monkeypatch.setattr(
        runtime_provider, "resolve_requested_provider", lambda requested=None: "auto"
    )
    chk = module_service.model_capable("cadir_build")
    assert chk["ok"] is True  # fail-open，未知 provider 不拦截


def test_invoke_router_registers_routes():
    paths = {r.path for r in invoke_router.routes}
    assert "/api/invoke" in paths
    assert "/api/invoke/capable" in paths
    assert "/api/model-change" in paths
    assert "/api/model-change/stream" in paths


def test_get_capable_satisfied(patched):
    out = module_service.get_capable("cadir_build")
    assert out["cap"] == "cadir_build"
    assert out["satisfied"] is True
    assert out["missing_dims"] == []
    assert out["required_dims"] == ["tools"]


def test_get_capable_not_satisfied(patched, monkeypatch):
    monkeypatch.setattr(
        cap_manifest, "build_provider_capability_index", lambda: {"anthropic": ["vision"]}
    )
    monkeypatch.setattr(
        runtime_provider, "resolve_requested_provider", lambda requested=None: "anthropic"
    )
    out = module_service.get_capable("cadir_build")
    assert out["satisfied"] is False
    assert out["missing_dims"] == ["tools"]


def test_get_capable_unknown_cap_fail_open(patched, monkeypatch):
    monkeypatch.setattr(discovery, "route_toolset", lambda cap: [])
    out = module_service.get_capable("nonexistent_cap")
    assert out["satisfied"] is True  # 无匹配工具 → fail-open 不灰显
    assert out["reason"] == "no_tool_for_cap_fail_open"


def test_broadcast_model_change_pubsub():
    q = module_service.subscribe_model_change()
    try:
        module_service.broadcast_model_change("gpt-4o", "openai")
        event = q.get(timeout=1)
        assert event == {"event": "vermes-model-change", "model": "gpt-4o", "provider": "openai"}
    finally:
        module_service.unsubscribe_model_change(q)
