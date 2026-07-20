"""Regression test: POST /api/provider/add must not wipe an existing key.

When the desktop Settings UI saves all providers in one pass, previously
configured (masked) providers are sent with an empty/missing api_key.
add_provider used to write that empty value to .env, silently clearing a
working provider's credentials — verified regression: saving provider B
cleared provider A's settings.
"""

from __future__ import annotations

import asyncio

from hermes_cli.blueprints.providers import ProviderAddRequest, add_provider


def _run(coro):
    return asyncio.run(coro)


def test_empty_api_key_does_not_touch_existing_key(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("hermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("hermes_cli.blueprints.providers.save_config", lambda cfg: None)

    # Provider A already has a key in .env (masked in the UI) and is saved with
    # an empty api_key + base_url — exactly what the desktop UI sends.
    _run(add_provider(ProviderAddRequest(
        provider_id="deepseek", api_key="", base_url="https://api.deepseek.com")))

    assert not any(k == "DEEPSEEK_API_KEY" for (k, _) in calls), (
        f"empty api_key must not write DEEPSEEK_API_KEY, got {calls}"
    )


def test_omitted_api_key_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("hermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("hermes_cli.blueprints.providers.save_config", lambda cfg: None)

    # api_key omitted entirely (Optional default None).
    _run(add_provider(ProviderAddRequest(
        provider_id="qwen", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")))

    assert not any(k == "QWEN_API_KEY" for (k, _) in calls)


def test_real_api_key_is_written(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("hermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("hermes_cli.blueprints.providers.save_config", lambda cfg: None)

    _run(add_provider(ProviderAddRequest(
        provider_id="openai", api_key="sk-real-123", base_url="https://api.openai.com/v1")))

    assert ("OPENAI_API_KEY", "sk-real-123") in calls
