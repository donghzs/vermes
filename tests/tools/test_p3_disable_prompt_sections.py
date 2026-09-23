"""P3 契约测：VERMES_DISABLE_PROMPT_SECTIONS 禁用开关（工单 §9 P3 定案）。

过滤点 = `load_all_processors` 出口（user/builtin/plugin 一视同仁）。
Hermes 可见性补点：禁用时显式打印「已按 env 禁用 N 段：…」，禁止静默失效；
`list_prompt_sections` 列出 disabled_by_env 行；doctor 同步。
"""

from __future__ import annotations

import pytest

from agent.prompt_processor_loader import (
    DISABLE_ENV,
    CORE_PROMPT_SECTION_IDS,
    clear_plugin_processors,
    invalidate_cache,
    list_prompt_sections,
    load_all_processors,
    missing_core_sections,
    parse_disabled_sections,
    register_plugin_processor,
)
from agent import system_prompt as sp


@pytest.fixture(autouse=True)
def _clean():
    clear_plugin_processors()
    invalidate_cache()
    yield
    clear_plugin_processors()
    invalidate_cache()


def test_parse_disabled_sections_handles_whitespace_and_empty():
    assert parse_disabled_sections() == frozenset()
    # monkeypatch env via pytest
    import os

    os.environ[DISABLE_ENV] = " identity , ,kanban,"
    try:
        assert parse_disabled_sections() == frozenset({"identity", "kanban"})
    finally:
        os.environ.pop(DISABLE_ENV, None)


def test_load_all_filters_builtin_section(monkeypatch, caplog):
    """builtin YAML 命中禁用名单 → 不进 load_all，且显式打印。"""
    monkeypatch.setenv(DISABLE_ENV, "identity")
    invalidate_cache()
    with caplog.at_level("WARNING", logger="agent.prompt_processor_loader"):
        procs = load_all_processors()
    ids = {p.effective_id for p in procs}
    assert "identity" not in ids
    assert "editing_guardrails" in ids, "未禁用段必须仍在"
    joined = " ".join(r.getMessage() or "" for r in caplog.records)
    assert "已按 env 禁用" in joined
    assert "identity" in joined


def test_resolve_section_goes_missing_when_disabled(monkeypatch):
    """禁用核心段后 _resolve_section 走 missing 可见占位（非静默空串）。"""
    monkeypatch.setenv(DISABLE_ENV, "editing_guardrails")
    invalidate_cache()
    content, source, _h = sp._resolve_section("editing_guardrails")
    assert source == "missing"
    assert "[prompt-section missing: editing_guardrails]" in content


def test_disabled_core_is_not_reported_missing(monkeypatch):
    """禁用 ≠ 缺失：missing_core_sections 不得把 env 禁用算成 MISSING。"""
    monkeypatch.setenv(DISABLE_ENV, "identity")
    invalidate_cache()
    assert "identity" not in missing_core_sections()
    assert len(missing_core_sections()) == 0


def test_list_prompt_sections_shows_disabled_rows(monkeypatch):
    monkeypatch.setenv(DISABLE_ENV, "kanban,identity")
    invalidate_cache()
    rows = list_prompt_sections()
    by_id = {r["id"]: r for r in rows}
    for sid in ("kanban", "identity"):
        assert sid in by_id
        assert by_id[sid]["disabled_by_env"] is True
        assert by_id[sid]["enabled"] is False
        assert by_id[sid]["path"].startswith("env:")
    # 未禁用的不得误标
    assert by_id["editing_guardrails"]["disabled_by_env"] is False


def test_register_plugin_processor_skips_disabled(monkeypatch):
    """register 侧顺手拒登：命中禁用名单的插件段不进注册表。"""
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
    assert "plugin.demo" not in ids


def test_env_unset_is_full_load():
    import os

    os.environ.pop(DISABLE_ENV, None)
    invalidate_cache()
    ids = {p.effective_id for p in load_all_processors()}
    for sid in CORE_PROMPT_SECTION_IDS:
        assert sid in ids, f"默认全开：{sid} 必须在"
