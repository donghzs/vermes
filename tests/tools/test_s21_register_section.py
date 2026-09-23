"""S2.1 adapter 契约测：register_system_prompt_section（API 同形、禁 Callable 进 stable）。

工单 §3/§5：只加 API、不改现有注入点；行为零变化由 test_s2_gold 把关。
本文件钉住 L-014 三护栏 + Callable→volatile 缓存防护 + A4 同名优先级。
"""

from __future__ import annotations

import pytest

from agent.prompt_processor_loader import (
    DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS,
    clear_plugin_processors,
    is_valid_system_prompt_section_id,
    load_all_processors,
    register_plugin_processor,
    PromptProcessor,
)


@pytest.fixture(autouse=True)
def _clean_plugin_registry():
    clear_plugin_processors()
    yield
    clear_plugin_processors()


def _ctx():
    """最小 PluginContext（不跑真实插件发现）。"""
    from types import SimpleNamespace
    from vermes_cli.plugins import PluginContext, PluginManifest

    manifest = PluginManifest(name="test-plugin", key="test-plugin", path=None)
    manager = SimpleNamespace(_hooks={}, _plugin_tool_names=set())
    return PluginContext(manifest, manager)


def test_api_surface_matches_upstream_shape():
    """签名含 id/content/position/max_chars（上游同形），并多 layer/conditions。"""
    import inspect

    from vermes_cli.plugins import PluginContext

    sig = inspect.signature(PluginContext.register_system_prompt_section)
    params = sig.parameters
    assert "id" in params and "content" in params
    assert "position" in params and "max_chars" in params
    assert "layer" in params and "conditions" in params
    assert "stable_reason" in params  # 工单 §3 约束 1 的显式理由口


def test_id_validation_l014():
    assert is_valid_system_prompt_section_id("help.extra")
    assert is_valid_system_prompt_section_id("a")
    assert is_valid_system_prompt_section_id("my-section_1")
    assert not is_valid_system_prompt_section_id("")
    assert not is_valid_system_prompt_section_id("Has-Upper")
    assert not is_valid_system_prompt_section_id("_leading")
    assert not is_valid_system_prompt_section_id("x" * 129)

    ctx = _ctx()
    with pytest.raises(ValueError, match="Invalid system prompt section id"):
        ctx.register_system_prompt_section("BadId", "hello")


def test_max_chars_l014():
    ctx = _ctx()
    with pytest.raises(ValueError, match="exceeds max_chars"):
        ctx.register_system_prompt_section("big", "x" * (DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS + 1))
    # 边界内可注册
    reg = ctx.register_system_prompt_section("ok", "x" * DEFAULT_SYSTEM_PROMPT_SECTION_MAX_CHARS)
    assert reg.id == "ok"


def test_duplicate_registration_rejected_l014():
    ctx = _ctx()
    ctx.register_system_prompt_section("dup", "one", layer="volatile")
    with pytest.raises(ValueError, match="already registered by"):
        ctx.register_system_prompt_section("dup", "two", layer="volatile")


def test_callable_defaults_to_volatile_or_requires_reason():
    """工单 §3 约束 1：Callable 不许无理由进 stable（会打穿 prompt cache）。"""
    ctx = _ctx()
    with pytest.raises(ValueError, match="layer=volatile"):
        ctx.register_system_prompt_section("dyn", lambda info: "x", layer="stable")

    # 无 layer 显式给 stable 时，Callable 仍拒
    with pytest.raises(ValueError, match="layer=volatile"):
        ctx.register_system_prompt_section("dyn2", lambda info: "x")

    # 给 stable_reason 后允许进 stable（内容只依赖 session 常量）
    reg = ctx.register_system_prompt_section(
        "dyn3", lambda info: "session-const", layer="stable",
        stable_reason="only depends on session-level constants",
    )
    assert reg.is_callable is True
    assert reg.layer == "stable"

    # 显式 volatile 可直接注册
    reg2 = ctx.register_system_prompt_section("dyn4", lambda info: "turn", layer="volatile")
    assert reg2.layer == "volatile"


def test_callable_instance_carries_fn_and_cap():
    ctx = _ctx()
    ctx.register_system_prompt_section("dyn", lambda info: "hi", layer="volatile", max_chars=100)
    procs = {p.effective_id: p for p in load_all_processors()}
    p = procs["dyn"]
    assert callable(getattr(p, "_plugin_callable", None))
    assert p._plugin_max_chars == 100
    assert p.metadata.get("plugin_callable") is True


def test_priority_plugin_lt_builtin_lt_user(tmp_path, monkeypatch):
    """A4：同名时 plugin 被 builtin 覆盖（builtin 已在 load 路径中）。"""
    ctx = _ctx()
    # identity 是 builtin YAML 里的键
    ctx.register_system_prompt_section("identity", "PLUGIN-IDENTITY", layer="stable")
    procs = {p.effective_id: p for p in load_all_processors()}
    assert "identity" in procs
    assert procs["identity"].content != "PLUGIN-IDENTITY", "plugin 不得盖过 builtin"
    # 独有 id 仍在
    ctx.register_system_prompt_section("plugin.only", "PLUGIN-ONLY", layer="volatile")
    procs = {p.effective_id: p for p in load_all_processors()}
    assert procs["plugin.only"].content == "PLUGIN-ONLY"


def test_str_content_stable_allowed():
    ctx = _ctx()
    reg = ctx.register_system_prompt_section("const", "fixed text", layer="stable")
    assert reg.is_callable is False
    assert reg.layer == "stable"


def test_invalid_layer_rejected():
    ctx = _ctx()
    with pytest.raises(ValueError, match="Invalid layer"):
        ctx.register_system_prompt_section("x", "y", layer="middle")


def test_register_plugin_processor_owner_named_in_error():
    register_plugin_processor(
        PromptProcessor(name="t", content="c", id="t", layer="volatile"),
        owner="plugin-a",
    )
    with pytest.raises(ValueError, match="plugin-a"):
        register_plugin_processor(
            PromptProcessor(name="t", content="c", id="t", layer="volatile"),
            owner="plugin-b",
        )
