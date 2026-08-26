"""Tests for the Nous-Vermes-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"Vermes"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``Vermes-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "Vermes" tag namespace.

``is_nous_vermes_non_agentic`` should only match the actual
Vermes-3 / Vermes-4 chat family.
"""

from __future__ import annotations

import pytest

from vermes_cli.model_switch import (
    _vermes_MODEL_WARNING,
    _check_vermes_model_warning,
    is_nous_vermes_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "donghzs/Vermes-3-Llama-3.1-70B",
        "donghzs/Vermes-3-Llama-3.1-405B",
        "Vermes-3",
        "Vermes-3",
        "Vermes-4",
        "Vermes-4-405b",
        "VERMES_4_70b",
        "openrouter/vermes3:70b",
        "openrouter/donghzs/Vermes-4-405b",
        "donghzs/vermes3",
        "Vermes-3.1",
    ],
)
def test_matches_real_nous_vermes_chat_models(model_name: str) -> None:
    assert is_nous_vermes_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous Vermes 3/4"
    )
    assert _check_vermes_model_warning(model_name) == _vermes_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "Vermes-brain:qwen3-14b-ctx16k",
        "Vermes-brain:qwen3-14b-ctx32k",
        "Vermes-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat Vermes models we don't warn about
        "Vermes-llm-2",
        "vermes2-pro",
        "nous-Vermes-2-mistral",
        # Edge cases
        "",
        "Vermes",  # bare "Vermes" isn't the 3/4 family
        "Vermes-brain",
        "brain-Vermes-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_vermes_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous Vermes 3/4"
    )
    assert _check_vermes_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_vermes_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_vermes_model_warning("") == ""
