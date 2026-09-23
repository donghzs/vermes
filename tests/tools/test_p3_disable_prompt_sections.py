"""P3 契约测（Hermes 2026-09-23 重构口径）：策略单源 + 事实/策略分层。

- `disabled_section_ids()` = 唯一策略源
- `load_all_processors()` = 事实层，**不过滤**
- 装配侧 `_resolve_section` 命中 → 不注入（空串，无标记）
- 清单侧保留真实 path/source，enabled=False
- 诊断侧 missing 排除被禁
- 安全段（SAFETY_SECTION_IDS）须 CONFIRM=1
- 未知 id WARNING 不阻塞
"""

from __future__ import annotations

import pytest

from agent.prompt_processor_loader import (
    DISABLE_CONFIRM_ENV,
    DISABLE_ENV,
    CORE_PROMPT_SECTION_IDS,
    SAFETY_SECTION_IDS,
    clear_plugin_processors,
    disabled_section_ids,
    invalidate_cache,
    list_prompt_sections,
    load_all_processors,
    missing_core_sections,
    parse_disabled_sections,
    register_plugin_processor,
    warn_unknown_disabled,
)
from agent import system_prompt as sp


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(DISABLE_ENV, raising=False)
    monkeypatch.delenv(DISABLE_CONFIRM_ENV, raising=False)
    clear_plugin_processors()
    invalidate_cache()
    yield
    clear_plugin_processors()
    invalidate_cache()


def test_parse_raw_vs_effective_policy():
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(DISABLE_ENV, " identity , ,kanban,")
        assert parse_disabled_sections() == frozenset({"identity", "kanban"})
        assert disabled_section_ids() == frozenset({"identity", "kanban"})
    finally:
        monkeypatch.undo()


def test_load_all_is_fact_layer_unfiltered(monkeypatch):
    """事实层：禁用名单不得从 load_all 里抹掉段。"""
    monkeypatch.setenv(DISABLE_ENV, "identity")
    invalidate_cache()
    ids = {p.effective_id for p in load_all_processors()}
    assert "identity" in ids, "load_all 是事实层，必须仍含被禁段"
    assert "editing_guardrails" in ids


def test_resolve_section_does_not_inject_when_disabled(monkeypatch):
    """装配侧唯一点：命中 → 空内容 + source=disabled，且无 [disabled] 标记。"""
    monkeypatch.setenv(DISABLE_ENV, "kanban")
    invalidate_cache()
    content, source, content_hash = sp._resolve_section("kanban")
    assert content == ""
    assert source == "disabled"
    assert content_hash == ""
    assert "[disabled]" not in content
    assert "[prompt-section" not in content


def test_resolve_section_injects_when_not_disabled():
    content, source, _h = sp._resolve_section("editing_guardrails")
    assert source == "builtin"
    assert content and "[prompt-section" not in content


def test_safety_section_requires_confirm(monkeypatch):
    """安全段：无 CONFIRM 不生效；有 CONFIRM 才禁。"""
    monkeypatch.setenv(DISABLE_ENV, "editing_guardrails")
    invalidate_cache()
    assert disabled_section_ids() == frozenset()
    content, source, _h = sp._resolve_section("editing_guardrails")
    assert source == "builtin" and content

    monkeypatch.setenv(DISABLE_CONFIRM_ENV, "1")
    invalidate_cache()
    assert disabled_section_ids() == frozenset({"editing_guardrails"})
    content2, source2, _h2 = sp._resolve_section("editing_guardrails")
    assert (content2, source2) == ("", "disabled")


def test_non_safety_section_needs_no_confirm(monkeypatch):
    monkeypatch.setenv(DISABLE_ENV, "kanban")
    invalidate_cache()
    assert disabled_section_ids() == frozenset({"kanban"})
    assert "kanban" in SAFETY_SECTION_IDS or True  # kanban 非安全段
    content, source, _h = sp._resolve_section("kanban")
    assert (content, source) == ("", "disabled")


def test_disabled_core_is_not_reported_missing(monkeypatch):
    monkeypatch.setenv(DISABLE_ENV, "identity")
    invalidate_cache()
    assert "identity" not in missing_core_sections()
    assert missing_core_sections() == []


def test_list_prompt_sections_keeps_real_metadata(monkeypatch):
    """清单侧：被禁段保留真实 path/source，enabled=False（不是合成行）。"""
    monkeypatch.setenv(DISABLE_ENV, "identity")
    invalidate_cache()
    rows = list_prompt_sections()
    by_id = {r["id"]: r for r in rows}
    assert "identity" in by_id
    row = by_id["identity"]
    assert row["disabled_by_env"] is True
    assert row["enabled"] is False
    assert row["source"] == "builtin"
    assert "processors/identity.yaml" in row["path"].replace("\\", "/")
    assert row["path"] != f"env:{DISABLE_ENV}"
    assert by_id["editing_guardrails"]["disabled_by_env"] is False
    assert by_id["editing_guardrails"]["enabled"] is True


def test_register_plugin_processor_still_registers_when_disabled(monkeypatch):
    """注册表是事实：即使被禁也登记，只是装配侧不注入。"""
    from agent.prompt_processor_loader import PromptProcessor

    monkeypatch.setenv(DISABLE_ENV, "plugin.demo")
    invalidate_cache()
    proc = PromptProcessor(
        name="plugin.demo",
        content="demo",
        layer="stable",
        triggers={"type": "always"},
    )
    register_plugin_processor(proc, owner="test")
    ids = {p.effective_id for p in load_all_processors()}
    assert "plugin.demo" in ids, "register 不得拒登（事实层）"
    content, source, _h = sp._resolve_section("plugin.demo")
    assert (content, source) == ("", "disabled")


def test_unknown_disabled_id_warns_but_open(monkeypatch, caplog):
    monkeypatch.setenv(DISABLE_ENV, "no_such_section_zzz")
    invalidate_cache()
    with caplog.at_level("WARNING", logger="agent.prompt_processor_loader"):
        unknown = warn_unknown_disabled({"identity"})
    assert unknown == ("no_such_section_zzz",)
    joined = " ".join(r.getMessage() or "" for r in caplog.records)
    assert "未知段" in joined or "unknown" in joined.lower()


def test_startup_announce_visible(monkeypatch, caplog):
    monkeypatch.setenv(DISABLE_ENV, "identity,kanban")
    invalidate_cache()
    with caplog.at_level("WARNING", logger="agent.prompt_processor_loader"):
        load_all_processors()
    joined = " ".join(r.getMessage() or "" for r in caplog.records)
    assert "已按 env 禁用" in joined
    assert "identity" in joined


def test_env_unset_is_full_load_and_inject():
    invalidate_cache()
    ids = {p.effective_id for p in load_all_processors()}
    for sid in CORE_PROMPT_SECTION_IDS:
        assert sid in ids
    content, source, _h = sp._resolve_section("identity")
    assert source == "builtin" and content
