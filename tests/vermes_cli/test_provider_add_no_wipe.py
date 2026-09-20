"""Regression test: POST /api/provider/add must not wipe an existing key.

When the desktop Settings UI saves all providers in one pass, previously
configured (masked) providers are sent with an empty/missing api_key.
add_provider used to write that empty value to .env, silently clearing a
working provider's credentials — verified regression: saving provider B
cleared provider A's settings.
"""

from __future__ import annotations

import asyncio

from vermes_cli.blueprints.providers import ProviderAddRequest, add_provider


def _run(coro):
    return asyncio.run(coro)


def test_empty_api_key_does_not_touch_existing_key(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "vermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("vermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("vermes_cli.blueprints.providers.save_config", lambda cfg: None)

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
        "vermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("vermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("vermes_cli.blueprints.providers.save_config", lambda cfg: None)

    # api_key omitted entirely (Optional default None).
    _run(add_provider(ProviderAddRequest(
        provider_id="qwen", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")))

    assert not any(k == "QWEN_API_KEY" for (k, _) in calls)


def test_real_api_key_is_written(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "vermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("vermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("vermes_cli.blueprints.providers.save_config", lambda cfg: None)

    _run(add_provider(ProviderAddRequest(
        provider_id="openai", api_key="sk-real-123", base_url="https://api.openai.com/v1")))

    assert ("OPENAI_API_KEY", "sk-real-123") in calls


def test_custom_provider_key_written_to_env_not_plaintext(monkeypatch):
    """Non-template providers must persist key to .env + key_env, never inline.

    Regression for the plaintext-key cleanup (issue #15803): a provider absent
    from PROVIDER_TEMPLATES (e.g. agnes) used to write the raw ``api_key``
    inline into config.yaml.  It must instead write the secret to .env via
    ``save_env_value`` and record only ``key_env`` in config.yaml.
    """
    calls = []
    saved_cfg = {}
    monkeypatch.setattr(
        "vermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("vermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("vermes_cli.blueprints.providers.save_config", lambda cfg: saved_cfg.update(cfg))

    _run(add_provider(ProviderAddRequest(
        provider_id="agnes", api_key="cpk-secret-123", base_url="https://apihub.agnes-ai.cn/v1")))

    # Secret went to .env with a derived key name.
    assert ("AGNES_API_KEY", "cpk-secret-123") in calls

    # config.yaml entry records key_env and base_url, but NO inline api_key.
    entry = saved_cfg["providers"]["agnes"]
    assert entry["key_env"] == "AGNES_API_KEY"
    assert entry["base_url"] == "https://apihub.agnes-ai.cn/v1"
    assert "api_key" not in entry


def test_custom_provider_empty_key_still_records_key_env(monkeypatch):
    """Saving base_url for an already-configured custom provider (masked key)
    must not drop its ``key_env`` pointer."""
    calls = []
    saved_cfg = {}
    monkeypatch.setattr(
        "vermes_cli.blueprints.providers.save_env_value",
        lambda k, v: calls.append((k, v)),
    )
    monkeypatch.setattr("vermes_cli.blueprints.providers.load_config", lambda: {"providers": {}})
    monkeypatch.setattr("vermes_cli.blueprints.providers.save_config", lambda cfg: saved_cfg.update(cfg))

    _run(add_provider(ProviderAddRequest(
        provider_id="agnes", api_key="", base_url="https://apihub.agnes-ai.cn/v1")))

    assert not any(k == "AGNES_API_KEY" for (k, _) in calls)
    entry = saved_cfg["providers"]["agnes"]
    assert entry["key_env"] == "AGNES_API_KEY"
    assert "api_key" not in entry
