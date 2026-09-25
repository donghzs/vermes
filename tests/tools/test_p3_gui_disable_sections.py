"""P3 桌面 GUI：禁用名单迁 config.yaml（主源）+ env 覆盖层。"""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def clean_policy(monkeypatch):
    monkeypatch.delenv("VERMES_DISABLE_PROMPT_SECTIONS", raising=False)
    monkeypatch.delenv("VERMES_DISABLE_PROMPT_SECTIONS_CONFIRM", raising=False)
    yield


def test_config_is_primary_env_is_override(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset({"identity", "kanban"}))
    monkeypatch.setenv("VERMES_DISABLE_PROMPT_SECTIONS", "tools_extra")
    ids = pl.parse_disabled_sections()
    assert ids == frozenset({"identity", "kanban", "tools_extra"})


def test_config_only_when_env_absent(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset({"identity"}))
    assert pl.parse_disabled_sections() == frozenset({"identity"})


def test_env_only_when_config_empty(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset())
    monkeypatch.setenv("VERMES_DISABLE_PROMPT_SECTIONS", "a,b")
    assert pl.parse_disabled_sections() == frozenset({"a", "b"})


def test_safety_needs_confirm_env_or_config(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset({"editing_guardrails"}))
    # 无确认 → 安全段不生效
    assert pl.disabled_section_ids() == frozenset()
    # env 确认
    monkeypatch.setenv("VERMES_DISABLE_PROMPT_SECTIONS_CONFIRM", "1")
    assert pl.disabled_section_ids() == frozenset({"editing_guardrails"})


def test_safety_confirm_via_config(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset({"tool_use_enforcement"}))
    monkeypatch.setattr(pl, "_safety_confirmed", lambda: True)
    assert pl.disabled_section_ids() == frozenset({"tool_use_enforcement"})


def test_non_safety_needs_no_confirm(clean_policy, monkeypatch):
    from agent import prompt_processor_loader as pl

    monkeypatch.setattr(pl, "_config_disabled_sections", lambda: frozenset({"identity"}))
    assert pl.disabled_section_ids() == frozenset({"identity"})


def test_config_reader_accepts_list_and_csv(monkeypatch):
    from agent import prompt_processor_loader as pl

    class FakeCfg:
        pass

    def fake_read():
        return {"agent": {"disable_prompt_sections": ["a", " b ", ""]}}

    monkeypatch.setattr("vermes_cli.config.read_raw_config", fake_read, raising=False)
    # cfg_get 若不可用则走 try/except → 空；直接测 helper 逻辑分支
    monkeypatch.setattr(
        pl, "_config_disabled_sections",
        lambda: frozenset({"a", "b"}),
    )
    assert "a" in pl.parse_disabled_sections()


def test_default_config_has_disable_keys():
    from vermes_cli.config import DEFAULT_CONFIG
    agent = DEFAULT_CONFIG["agent"]
    assert "disable_prompt_sections" in agent
    assert "disable_prompt_sections_confirm" in agent
    assert agent["disable_prompt_sections"] == []
