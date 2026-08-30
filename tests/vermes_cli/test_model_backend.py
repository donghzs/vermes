"""P3-1 ModelBackend facade — composes existing provider/transport state.

Locks the contract without a parallel registry (transport registration is
owned by ``agent.transports`` + ``ProviderDef``).  No network dependency:
ProviderDef is constructed directly and ``probe_api_models``/``get_provider``
are monkeypatched so the tests stay hermetic + fast.
"""

from vermes_cli.providers import ModelBackend, ProviderDef, TRANSPORT_TO_API_MODE


def _pdef(transport: str, pid: str = "x") -> ProviderDef:
    return ProviderDef(
        id=pid,
        name=pid,
        transport=transport,
        api_key_env_vars=("DUMMY_KEY",),
    )


def test_model_backend_api_mode_maps_known_transports():
    assert ModelBackend(_pdef("anthropic_messages")).api_mode == "anthropic_messages"
    assert ModelBackend(_pdef("openai_chat")).api_mode == "chat_completions"
    assert ModelBackend(_pdef("codex_responses")).api_mode == "codex_responses"
    assert ModelBackend(_pdef("bedrock_converse")).api_mode == "bedrock_converse"


def test_model_backend_api_mode_unknown_transport_is_none():
    assert ModelBackend(_pdef("no_such_transport")).api_mode is None


def test_model_backend_transport_exposes_provider_transport():
    assert ModelBackend(_pdef("anthropic_messages")).transport == "anthropic_messages"


def test_model_backend_probe_delegates_to_probe_api_models(monkeypatch):
    captured = {}

    def fake_probe(api_key=None, base_url=None, timeout=5.0, api_mode=None):
        captured.update(
            api_key=api_key, base_url=base_url, timeout=timeout, api_mode=api_mode
        )
        return {"models": ["m1"], "probed_url": base_url}

    import vermes_cli.models as models_mod

    monkeypatch.setattr(models_mod, "probe_api_models", fake_probe)

    mb = ModelBackend(_pdef("anthropic_messages"))
    result = mb.probe_models(
        api_key="k", base_url="https://api.example.com/v1", timeout=3.0
    )

    assert result["models"] == ["m1"]
    assert captured["api_mode"] == "anthropic_messages"  # facade passes api_mode through
    assert captured["api_key"] == "k"
    assert captured["timeout"] == 3.0


def test_model_backend_for_id_wraps_resolved_provider(monkeypatch):
    fake = _pdef("anthropic_messages", "anthropic")
    monkeypatch.setattr("vermes_cli.providers.get_provider", lambda _id: fake)

    mb = ModelBackend.for_id("anthropic")
    assert mb is not None
    assert mb.transport == "anthropic_messages"
    assert mb.api_mode == "anthropic_messages"


def test_model_backend_for_id_unknown_is_none():
    assert ModelBackend.for_id("__no_such_provider__") is None


def test_model_backend_uses_single_source_transport_map():
    # Guard against accidental drift: every known transport must map to an api_mode.
    for transport in ("openai_chat", "anthropic_messages", "codex_responses", "bedrock_converse"):
        assert TRANSPORT_TO_API_MODE.get(transport) is not None
