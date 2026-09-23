"""Hermes 2026-09-23 收口契约测：missing 段可见性 + 37 YAML 完整性。

S2.4 退役 `_PROCESSOR_FALLBACK` 后，YAML 是唯一真源。缺段不得静默消失：
- `_resolve_section` 返回可见占位文本（非空串）+ source=missing
- `list_prompt_sections` 列出 missing 核心段
- `missing_core_sections` 供 doctor 探测
- 打包金名单 `EXPECTED_PROCESSOR_YAMLS` 全在场（防漏拷）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.prompt_processor_loader import (
    CORE_PROMPT_SECTION_IDS,
    EXPECTED_PROCESSOR_YAMLS,
    clear_plugin_processors,
    invalidate_cache,
    list_prompt_sections,
    missing_core_sections,
)
from agent import system_prompt as sp

ROOT = Path(__file__).resolve().parents[2]
PROC_DIR = ROOT / "vermes_cli" / "processors"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_plugin_processors()
    invalidate_cache()
    yield
    clear_plugin_processors()
    invalidate_cache()


def test_expected_processor_yamls_all_present():
    """打包金名单 37 个 YAML 必须全在源码树（防漏拷/防误删）。"""
    actual = {p.name for p in PROC_DIR.glob("*.yaml")}
    missing = sorted(EXPECTED_PROCESSOR_YAMLS - actual)
    assert not missing, f"缺失 processors YAML：{missing}"
    assert len(EXPECTED_PROCESSOR_YAMLS) == 37, "金名单数量漂移须显式改此断言"


def test_core_section_ids_match_15_injection_keys():
    """核心 15 键与 S2.x 注入面一致。"""
    assert len(CORE_PROMPT_SECTION_IDS) == 15
    for sid in CORE_PROMPT_SECTION_IDS:
        assert (PROC_DIR / f"{sid}.yaml").exists(), f"核心段缺 YAML：{sid}"


def test_no_missing_core_sections_when_yaml_intact():
    assert missing_core_sections() == []


def test_missing_core_sections_detected_when_yaml_gone(monkeypatch):
    """hide processors → missing_core_sections 报出核心键。"""
    monkeypatch.setattr(
        "agent.prompt_processor_loader.load_all_processors", lambda: []
    )
    missing = missing_core_sections()
    assert "editing_guardrails" in missing
    assert "identity" in missing
    assert len(missing) == 15


def test_list_prompt_sections_includes_missing_rows(monkeypatch):
    """doctor 出口：缺段必须出现在清单里，source=missing。"""
    monkeypatch.setattr(
        "agent.prompt_processor_loader.load_all_processors", lambda: []
    )
    rows = list_prompt_sections()
    missing_rows = [r for r in rows if r["source"] == "missing"]
    assert missing_rows, "缺段必须以 source=missing 列出"
    ids = {r["id"] for r in missing_rows}
    assert "editing_guardrails" in ids
    for r in missing_rows:
        assert r["enabled"] is False
        assert r["path"] == "-"


def test_resolve_section_missing_is_visible_not_empty(monkeypatch):
    """③ 极小可见兜底：空串会让安全段静默消失。"""
    monkeypatch.setattr(sp, "load_all_processors", lambda: [])
    content, source, content_hash = sp._resolve_section("editing_guardrails")
    assert source == "missing"
    assert content, "missing 不得返回空串"
    assert "[prompt-section missing: editing_guardrails]" in content
    assert content_hash.startswith("sha256:") or len(content_hash) == 64
